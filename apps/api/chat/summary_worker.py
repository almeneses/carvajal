"""Resumen acumulado en segundo plano (spec 045, US1; research R1 a R3 y R16).

``ChatService`` llama a :meth:`SummaryWorker.submit` justo después de persistir cada turno
(en la web y en WhatsApp por igual). ``submit`` solo encola: el trabajo corre en un hilo
propio (un ``ThreadPoolExecutor`` de un hilo, con a lo sumo 32 trabajos pendientes) y nunca
retrasa el primer texto ni el final de la respuesta (FR-003).

Cada trabajo:

1. cuenta los turnos del hilo con su propia sesión (``count_turns``, la misma cuenta que
   la semilla del servicio) y lee el borrador y la lápida del Store;
2. si no hay turnos fuera de la ventana o el borrador ya los cubre todos, termina sin leer
   mensajes ni llamar al modelo (idempotencia, FR-005);
3. lee solo los turnos que faltan por resumir (``history_turns``), los empareja con
   ``paired_messages`` y los resume con el modelo (``AGENT_SUMMARY_MODE=llm``) o con el respaldo
   determinista; cualquier falla del modelo cae al determinista (FR-004);
4. relee la lápida de «olvidar todo» y descarta su resultado si es posterior a la lectura
   del historial; si no, escribe el borrador en el Store (``recall`` lo adopta en el turno
   siguiente).

Los turnos anteriores a la lápida no se resumen (R16). Una falla del Store solo deja una
advertencia (FR-009). Los eventos de log nunca llevan texto de la conversación.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Final

import structlog
from sqlalchemy.orm import Session, sessionmaker

from carvajal.adapters.storage.chat import SqlChatRepository
from carvajal.agent.memory.store import forget_all_requested
from carvajal.agent.memory.summary import (
    TOMBSTONE_KEY,
    ConversationSummary,
    SummaryModel,
    paired_messages,
    parse_time,
    summarize,
    summary_namespace,
)
from carvajal.infrastructure.agent_mode import agent_mode_status
from carvajal.infrastructure.settings import Settings

__all__ = ["MAX_PENDING", "SummaryWorker", "build_summary_worker"]

logger = structlog.get_logger(__name__)

# Trabajos pendientes como máximo (en cola o en curso); el resto se descarta (R1).
MAX_PENDING: Final = 32


class SummaryWorker:
    """Calcula el resumen de los turnos fuera de la ventana sin bloquear el turno.

    ``store`` o ``store_factory`` dan el Store de LangGraph del worker (en producción, un
    ``PostgresStore`` propio: el del grafo usa una sola conexión con candado, R17).
    ``model`` o ``model_factory`` dan el ``ChatOpenAI`` de DeepSeek del resumidor; con
    ``AGENT_SUMMARY_MODE=llm`` sin DeepSeek configurado, el modo se degrada a
    ``deterministic`` al construir el worker (``agent.summary_mode_degraded``).
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        store: Any = None,
        store_factory: Callable[[], Any] | None = None,
        model: SummaryModel | None = None,
        model_factory: Callable[[], SummaryModel] | None = None,
        max_queue: int = MAX_PENDING,
        timeout_s: float | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._store = store
        self._store_factory = store_factory
        self._model = model
        self._model_factory = model_factory
        self._max_queue = max_queue
        self._on_close = on_close
        self._timeout_s = settings.agent_summary_timeout_s if timeout_s is None else timeout_s
        self._lock = threading.Lock()
        self._pending = 0
        # Hilos con un trabajo en cola que aún no arrancó: otro envío del mismo hilo se
        # fusiona con él. Se retira al arrancar el trabajo, no al terminar, para que un
        # turno que llega con el trabajo en curso encole otro y no se pierda.
        self._queued: set[tuple[str, str]] = set()
        self._closed = False
        self._executor: ThreadPoolExecutor | None = None
        mode = settings.agent_summary_mode
        if mode == "llm" and model is None and agent_mode_status(settings).expected != "tools":
            # Sin DeepSeek (otro proveedor, sin clave o ``AGENT_MODE=routes``): determinista.
            logger.warning(
                "agent.summary_mode_degraded",
                configured="llm",
                mode="deterministic",
                reason=agent_mode_status(settings).reason or "agent_mode_routes",
            )
            mode = "deterministic"
        self.mode: str = mode

    # -- Encolar ------------------------------------------------------------------------------

    def submit(self, owner_key: str, conversation_id: str) -> bool:
        """Encola el resumen del hilo y vuelve de inmediato; ``False`` si no se encoló.

        Si el hilo ya tiene un trabajo en cola que no arrancó, el envío se fusiona con él
        (``agent.summary_coalesced``) y devuelve ``True``: ese trabajo leerá el turno nuevo.
        """

        if self.mode == "off" or not owner_key or not conversation_id:
            return False
        key = (owner_key, conversation_id)
        with self._lock:
            if self._closed:
                return False
            if key in self._queued:
                logger.info("agent.summary_coalesced")
                return True
            if self._pending >= self._max_queue:
                logger.warning("agent.summary_dropped", pending=self._pending)
                return False
            self._pending += 1
            self._queued.add(key)
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="summary-worker"
                )
            executor = self._executor
        try:
            executor.submit(self._run, owner_key, conversation_id)
        except RuntimeError:  # el ejecutor se cerró entre medias
            with self._lock:
                self._pending -= 1
                self._queued.discard(key)
            return False
        logger.info("agent.summary_scheduled")
        return True

    def _run(self, owner_key: str, conversation_id: str) -> None:
        with self._lock:
            self._queued.discard((owner_key, conversation_id))
        try:
            self.summarize_now(owner_key, conversation_id)
        except Exception as error:  # el worker nunca tumba el proceso
            logger.warning("agent.summary_failed", cause="error", error=type(error).__name__)
        finally:
            with self._lock:
                self._pending -= 1

    def close(self, *, wait: bool = False) -> None:
        """Deja de aceptar trabajos; con ``wait`` espera a que terminen los pendientes."""

        with self._lock:
            self._closed = True
            executor = self._executor
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=not wait)
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception as error:  # cerrar conexiones al salir nunca debe fallar
                logger.warning("agent.summary_close_failed", error=type(error).__name__)

    # -- Trabajo ------------------------------------------------------------------------------

    def _get_store(self) -> Any:
        if self._store is None and self._store_factory is not None:
            self._store = self._store_factory()
        return self._store

    def _get_model(self) -> SummaryModel | None:
        if self.mode != "llm":
            return None
        if self._model is None and self._model_factory is not None:
            try:
                self._model = self._model_factory()
            except Exception as error:
                logger.warning("agent.summary_failed", cause="no_model", error=type(error).__name__)
                return None
        return self._model

    def summarize_now(self, owner_key: str, conversation_id: str) -> ConversationSummary | None:
        """Resume el hilo de forma síncrona (lo usa el hilo del worker y las pruebas).

        Devuelve el borrador vigente (el nuevo o, si ya estaba al día, el anterior), o
        ``None`` si no hay turnos fuera de la ventana, el Store falló o una lápida posterior
        a la lectura descartó el resultado.
        """

        if self.mode == "off":
            return None
        started = perf_counter()
        read_at = datetime.now(UTC)
        with self._session_factory() as session:
            total = SqlChatRepository(session).count_turns(conversation_id)
        needed = total - self._settings.agent_history_turns
        if needed <= 0:
            return None
        store = self._get_store()
        if store is None:
            return None
        namespace = summary_namespace(owner_key)
        try:
            draft_item = store.get(namespace, conversation_id)
            tomb_item = store.get(namespace, TOMBSTONE_KEY)
        except Exception as error:
            logger.warning(
                "agent.summary_store_failed", operation="worker_read", error=type(error).__name__
            )
            return None
        draft = ConversationSummary.from_value(getattr(draft_item, "value", None))
        tombstone = getattr(tomb_item, "value", None)
        tomb_at = parse_time(tombstone.get("at")) if isinstance(tombstone, dict) else None
        if draft is not None and tomb_at is not None:
            updated = parse_time(draft.updated_at)
            if updated is None or updated < tomb_at:
                draft = None
        if draft is not None and draft.covered_turns >= needed:
            return draft
        forgotten = draft.forgotten_through if draft is not None else 0
        with self._session_factory() as session:
            repository = SqlChatRepository(session)
            if tomb_at is not None:
                forgotten = max(forgotten, _forgotten_turns(repository, conversation_id, tomb_at))
            start = max(draft.covered_turns if draft is not None else 0, forgotten)
            # Solo los turnos por resumir; si todo estaba olvidado, el último basta para
            # ``last_message_id``.
            first = min(start, needed - 1)
            messages = repository.history_turns(conversation_id, start=first, stop=needed)
        paired = paired_messages(messages)
        if not paired:
            return None
        previous = draft if draft is not None and draft.covered_turns >= forgotten else None
        last_id = str(getattr(paired[-1][1], "id", "") or "") or None
        if start >= needed:
            # Todo lo que salió de la ventana estaba olvidado: solo avanza la marca.
            summary = replace(
                previous or ConversationSummary(method="forgotten"),
                covered_turns=needed,
                last_message_id=last_id,
                updated_at=datetime.now(UTC).isoformat(),
            )
        else:
            turns = [turn for turn, _user, _answer in paired[start - first :]]
            summary, cause = summarize(
                previous,
                turns,
                start=start,
                mode=self.mode,
                model=self._get_model(),
                timeout_s=self._timeout_s,
                max_chars=self._settings.agent_summary_max_chars,
                last_message_id=last_id,
            )
            if cause is not None and self.mode == "llm":
                logger.warning("agent.summary_failed", cause=cause)
        summary = replace(summary, forgotten_through=max(summary.forgotten_through, forgotten))
        try:
            if _forgotten_since(store, namespace, read_at):
                # «Olvidar todo» llegó mientras se resumía: el resultado ya no vale.
                logger.info("agent.summary_discarded", reason="forgotten")
                return None
            store.put(namespace, conversation_id, summary.to_value())
            if _forgotten_since(store, namespace, read_at):
                # «Olvidar todo» se intercaló entre la comprobación y el ``put``: su borrado
                # pudo correr antes de este ``put``. La lápida se escribe antes de borrar
                # (``store._forget_all``), así que aquí se ve y el borrador se retira.
                store.delete(namespace, conversation_id)
                logger.info("agent.summary_discarded", reason="forgotten")
                return None
        except Exception as error:
            logger.warning(
                "agent.summary_store_failed", operation="worker_write", error=type(error).__name__
            )
            return None
        logger.info(
            "agent.summary_done",
            method=summary.method,
            covered_turns=summary.covered_turns,
            elapsed_ms=int((perf_counter() - started) * 1000),
        )
        return summary


def _forgotten_since(store: Any, namespace: tuple[str, ...], read_at: datetime) -> bool:
    """Hay una lápida de «olvidar todo» posterior a la lectura del historial."""

    latest = store.get(namespace, TOMBSTONE_KEY)
    value = getattr(latest, "value", None)
    latest_at = parse_time(value.get("at")) if isinstance(value, dict) else None
    return latest_at is not None and latest_at > read_at


def _forgotten_turns(repository: SqlChatRepository, conversation_id: str, tomb_at: datetime) -> int:
    """Turnos que «olvidar todo» deja fuera del resumen (R16).

    Son los turnos cuya pregunta se guardó antes de la lápida y, además, el propio turno que
    pidió olvidar: ``append_turn`` lo persiste después de que el grafo escribiera la lápida,
    pero el grafo ya lo marcó como olvidado (``forgotten_through = turn_count + 1``). Se
    reconoce con el mismo patrón que exigen el atajo y ``olvidar_preferencia(todo)``. Se
    resuelve con un ``COUNT`` y la primera pregunta posterior, sin leer el hilo entero.
    """

    before = repository.count_questions_until(conversation_id, tomb_at)
    after = repository.first_question_after(conversation_id, tomb_at)
    if after is not None and forget_all_requested(str(after.content)):
        before += 1
    return before


def build_summary_worker(
    session_factory: sessionmaker[Session], settings: Settings | None = None
) -> SummaryWorker:
    """Worker de producción: Store y modelo propios, construidos en el primer trabajo.

    Con ``AGENT_CHECKPOINTER=sqlite`` (demo local) el Store es el ``InMemoryStore`` del
    runtime, el mismo que lee ``recall``; con Postgres, un ``PostgresStore`` propio.
    """

    config = settings or Settings()
    stack = ExitStack()

    def store_factory() -> Any:
        if config.agent_checkpointer.lower() == "sqlite":
            from apps.api.agent_runtime import agent_runtime

            return agent_runtime().store
        from langgraph.store.postgres import PostgresStore

        from apps.api.agent_runtime import _plain_postgres_url

        store = stack.enter_context(
            PostgresStore.from_conn_string(_plain_postgres_url(config.database_url))
        )
        store.setup()
        return store

    def model_factory() -> SummaryModel:
        from carvajal.adapters.llm.deepseek import build_deepseek_chat

        return build_deepseek_chat(
            config, for_tools=True, timeout_s=config.agent_summary_timeout_s, max_retries=0
        )

    return SummaryWorker(
        session_factory,
        config,
        store_factory=store_factory,
        model_factory=model_factory,
        on_close=stack.close,
    )
