"""Servicio del chat: agente, historial, turnos y stream con persistencia atómica.

El servicio es el único punto que mezcla el agente LangGraph con la base de
datos: si la respuesta falla antes de empezar no se guarda nada; si se
interrumpe después del primer token, se guarda marcada como ``incomplete``.
Cada turno usa el id de la conversación como ``thread_id`` y verifica que el
hilo pertenezca al dueño de la cookie.

La sesión de base de datos se cierra antes de llamar al LLM: generar puede
tardar minutos y no debe retener una conexión del pool. La persistencia y el
checkpoint se escriben después, en transacciones cortas.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cache
from hashlib import sha256
from time import perf_counter
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from apps.api.chat.agent import AgentRunner, AgentTurn, default_agent_runner
from apps.api.chat.products import has_cards, safe_render_products
from apps.api.chat.streaming import ChatEvent
from carvajal.adapters.storage.chat import (
    ChatMessage,
    Conversation,
    ConversationSummary,
    SqlChatRepository,
)
from carvajal.adapters.storage.quotes import SqlQuoteRepository
from carvajal.agent.state import Turn
from carvajal.application.ask import NO_CONTEXT_ANSWER, Answer, Source
from carvajal.application.ports import Embedder, LLMProvider
from carvajal.infrastructure.monitoring import TurnTracer, build_tracer, thread_hash
from carvajal.infrastructure.settings import Settings
from carvajal.quotation.engine import QUOTE_VERSION

__all__ = [
    "ChatFailure",
    "ChatProvider",
    "ChatService",
    "ThreadAccessError",
    "default_chat_service",
]

logger = structlog.get_logger(__name__)

SERVICE_UNAVAILABLE = "El servicio de chat no está disponible en este momento."
MAX_CLIENT_TURN_ID = 64
QUOTE_DAYS = 7


class ThreadAccessError(PermissionError):
    """El hilo existe pero pertenece a otro dueño (o no existe)."""


class ChatFailure(RuntimeError):
    """Fallo de chat ya traducido; la vista lo convierte en 503."""


@runtime_checkable
class ChatProvider(Protocol):
    """Contrato que consume la vista; el servicio real y los dobles lo cumplen."""

    def history(
        self,
        owner_key: str,
        thread_id: str | None = None,
    ) -> Sequence[ChatMessage]:
        """Devuelve el historial del hilo indicado o del activo."""

        ...

    def conversations(self, owner_key: str) -> Sequence[ConversationSummary]:
        """Lista los hilos del dueño para el panel lateral."""

        ...

    def ask(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Answer:
        """Responde la pregunta y persiste el turno completo."""

        ...

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        """Emite el turno como eventos y lo persiste una sola vez al cerrar bien."""

        ...

    def new_conversation(self, owner_key: str) -> Conversation:
        """Crea un hilo nuevo del dueño para la demo de memoria."""

        ...


class ChatService:
    """Implementación real: agente + repositorio en transacciones cortas."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embedder: Embedder,
        llm: LLMProvider,
        agent: AgentRunner | None = None,
        *,
        tracer: TurnTracer | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._llm = llm
        self._agent = agent
        # El tracer y la configuración se resuelven en el primer turno: importar
        # el módulo no lee el entorno ni construye clientes.
        self._tracer = tracer
        self._settings = settings

    def _runner(self) -> AgentRunner:
        """Runner inyectado o el real, construido en la primera pregunta."""

        return self._agent or default_agent_runner()

    def _config(self) -> Settings:
        """Configuración del proceso, leída de forma perezosa una sola vez."""

        if self._settings is None:
            self._settings = Settings()
        return self._settings

    def _modelo(self) -> str:
        """Modelo configurado, para los metadatos de la llamada al LLM."""

        config = self._config()
        if config.llm_provider == "deepseek":
            return config.deepseek_model
        if config.llm_provider == "claude":
            return config.claude_model
        return config.ollama_model

    def _monitoring(self) -> TurnTracer:
        """Tracer inyectado o el que dicta la configuración (apagado por defecto)."""

        if self._tracer is None:
            self._tracer = build_tracer(self._config())
        return self._tracer

    def history(
        self,
        owner_key: str,
        thread_id: str | None = None,
    ) -> Sequence[ChatMessage]:
        """Lee el historial del hilo indicado o del activo sin crear conversación."""

        with self._session_factory() as session:
            repository = SqlChatRepository(session)
            if thread_id:
                conversation = repository.get_owned(thread_id, owner_key)
                if conversation is None:
                    raise ThreadAccessError("El hilo no pertenece a este visitante.")
                return repository.history(conversation.id)
            conversation = repository.active(owner_key)
            if conversation is None:
                return []
            return repository.history(conversation.id)

    def conversations(self, owner_key: str) -> Sequence[ConversationSummary]:
        """Lista los hilos del dueño, con una vista previa del último mensaje."""

        with self._session_factory() as session:
            return SqlChatRepository(session).summarize(owner_key)

    def new_conversation(self, owner_key: str) -> Conversation:
        """Crea un hilo nuevo y lo deja como activo para el dueño."""

        with self._session_factory() as session:
            conversation = SqlChatRepository(session).create(owner_key)
            session.commit()
            logger.info("chat.thread_created", thread_id=conversation.id)
            return conversation

    def ask(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Answer:
        """Responde y persiste el turno; los errores se elevan para el 503."""

        text = ""
        sources: tuple[Source, ...] = ()
        error: str | None = None
        for event in self.stream(
            owner_key,
            question,
            client_turn_id=client_turn_id,
            thread_id=thread_id,
        ):
            if event.name == "meta":
                sources = _sources_from_payload(event.data.get("sources", []))
            elif event.name in {"token", "fallback"}:
                text += str(event.data.get("text", ""))
            elif event.name == "error":
                error = str(event.data.get("message", SERVICE_UNAVAILABLE))
        if error is not None:
            raise ChatFailure(error)
        return Answer(text=text, sources=sources)

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        """Emite ``open``, ``trace``, ``meta``, tokens, ``done`` o ``error``.

        El turno se persiste una sola vez, con ``client_turn_id`` para que un
        reintento del navegador no duplique mensajes. Si el corte ocurre tras
        el primer token, se guarda como ``incomplete`` y el evento ``error`` lo
        declara.
        """

        started = perf_counter()
        yield ChatEvent("open", {"stream": "chat"})

        text = question.strip()
        client_turn_id = (client_turn_id or "").strip() or None
        fragments: list[str] = []
        conversation: Conversation | None = None
        turn: AgentTurn | None = None
        runner: AgentRunner | None = None
        persisted = False
        persist_attempted = False
        done_event: ChatEvent | None = None
        try:
            if not text:
                raise ValueError("La pregunta no puede estar vacía.")
            if client_turn_id is not None and len(client_turn_id) > MAX_CLIENT_TURN_ID:
                raise ValueError("El identificador del turno es demasiado largo.")

            # El runtime del agente se inicializa antes de abrir una sesión:
            # PostgresSaver.setup() crea índices CONCURRENTLY y se bloquearía
            # si esta petición retuviera una transacción abierta.
            runner = self._runner()
            # Fase de preparación: resolver el hilo y sembrar el checkpointer.
            # La sesión se cierra antes de decidir la ruta y de generar.
            with self._session_factory() as session:
                repository = SqlChatRepository(session)
                conversation = _owned_conversation(repository, owner_key, thread_id)
                seed = _seed_turns(repository, conversation.id)
                replay = (
                    repository.find_turn(conversation.id, client_turn_id)
                    if client_turn_id
                    else None
                )
                if replay is not None:
                    yield from _replay(replay, request_id=_request_id())
                    return
                # El hilo debe existir antes de generar; si el LLM falla, la UI
                # puede seguir mostrando la conversación sin un turno a medias.
                session.commit()
            request_id = _request_id()
            monitor = self._monitoring()
            with monitor.turn(
                request_id=request_id,
                thread_hash=thread_hash(conversation.id),
                router=self._config().agent_router,
                question=text,
            ) as span:
                turn = runner.prepare(owner_key, conversation.id, text, seed)
                products, quote_record = _issue_quote(
                    turn.products,
                    conversation_id=conversation.id,
                    client_turn_id=client_turn_id,
                )
                if products is not turn.products:
                    turn = replace(turn, products=products)
                # La ruta ya se conoce aquí: registrarla ahora deja trazas
                # útiles aunque el turno falle o se cancele después (T010).
                span.add_metadata(
                    route=turn.route,
                    question_chars=len(text),
                    intent=turn.intent,
                    tool=turn.tool or None,
                    tool_period=turn.tool_period or None,
                    preferences_count=len(turn.preferences),
                )
                structlog.contextvars.bind_contextvars(
                    thread_id=conversation.id,
                    route=turn.route,
                    tool=turn.tool or None,
                )
                yield ChatEvent(
                    "trace",
                    {
                        "thread_id": conversation.id,
                        "route": turn.route,
                        "intent": turn.intent,
                        "tool": turn.tool,
                        "reason": turn.reason,
                        "memory": len(turn.preferences),
                        "retrieved": len(turn.sources),
                    },
                )
                yield ChatEvent("meta", {"sources": _sources(turn.sources)})
                if turn.products and has_cards(turn.products):
                    html = safe_render_products(turn.products)
                    if html:
                        yield ChatEvent("products", {"html": html})

                if turn.prompt is None:
                    fragments.append(NO_CONTEXT_ANSWER)
                    span.add_outputs(answer=NO_CONTEXT_ANSWER)
                    yield ChatEvent("token", {"text": NO_CONTEXT_ANSWER})
                else:
                    try:
                        # El hijo explícito no depende de los contextvars: el
                        # generador SSE avanza en hilos del threadpool y ahí el
                        # contexto de tracing no viaja (FR-003).
                        with span.child(
                            "llm",
                            run_type="llm",
                            metadata={
                                "provider": self._config().llm_provider,
                                "model": self._modelo(),
                            },
                        ) as llm_span:
                            fragmentos = iter(
                                self._llm.stream(system=turn.system, prompt=turn.prompt)
                            )
                            answer_chars = 0
                            try:
                                while True:
                                    # El contexto se reabre en cada avance:
                                    # Starlette mueve el generador SSE entre
                                    # hilos y los contextvars no viajan con él
                                    # (FR-003).
                                    with span.generation():
                                        try:
                                            fragment = next(fragmentos)
                                        except StopIteration:
                                            break
                                    fragments.append(fragment)
                                    # El largo se acumula en O(1): unir la
                                    # respuesta completa en cada token sería
                                    # O(n²) sobre respuestas largas (T010).
                                    answer_chars += len(fragment)
                                    llm_span.add_outputs(answer_chars=answer_chars)
                                    yield ChatEvent("token", {"text": fragment})
                            except GeneratorExit:
                                # Cancelación: lo generado queda en la traza con
                                # una sola unión, no una por token (T012).
                                span.add_outputs(answer="".join(fragments))
                                raise
                    except Exception as error:
                        if "".join(fragments).strip():
                            raise
                        logger.warning("chat.stream_fallback", error=str(error))
                    if not "".join(fragments).strip():
                        with (
                            span.child(
                                "llm",
                                run_type="llm",
                                metadata={
                                    "provider": self._config().llm_provider,
                                    "model": self._modelo(),
                                },
                            ) as llm_span,
                            span.generation(),
                        ):
                            answer = self._llm.complete(system=turn.system, prompt=turn.prompt)
                            llm_span.add_outputs(answer_chars=len(answer))
                        fragments = [answer]
                        span.add_outputs(answer=answer)
                        yield ChatEvent(
                            "fallback",
                            {"text": answer, "sources": _sources(turn.sources)},
                        )

                answer_text = "".join(fragments)
                span.add_outputs(answer=answer_text)
                persist_attempted = True
                with self._session_factory() as session:
                    repository = SqlChatRepository(session)
                    current = repository.get_owned(conversation.id, owner_key)
                    if current is None:
                        raise ThreadAccessError("El hilo dejó de pertenecer al visitante.")
                    conversation_id, message_id = self._persist(
                        session,
                        current,
                        text,
                        answer_text,
                        turn.sources,
                        status="complete",
                        client_turn_id=client_turn_id,
                        products=turn.products,
                        quote=quote_record,
                    )
                    persisted = True
                runner.record(
                    conversation.id,
                    question=text,
                    answer=answer_text,
                    incomplete=False,
                    turns=turn.turns,
                )
                elapsed = _milliseconds(started)
                logger.info(
                    "chat.turn_completed",
                    thread_id=conversation.id,
                    route=turn.route,
                    tool=turn.tool or None,
                    sources=len(turn.sources),
                    retrieved=len(turn.sources),
                    duration_ms=elapsed,
                    result="ok",
                )
                span.add_metadata(answer_chars=len(answer_text))
                # El ``done`` se emite ya cerrada la traza: si el consumidor
                # cierra el generador justo después, el turno sigue siendo ok.
                done_event = ChatEvent(
                    "done",
                    {
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "thread_id": conversation.id,
                        "route": turn.route,
                        "elapsed_ms": elapsed,
                        "request_id": request_id,
                        "quote_id": quote_record["id"] if quote_record else None,
                    },
                )
            yield done_event
        except IntegrityError as error:
            # Dos reenvíos simultáneos con el mismo ``client_turn_id``: el primero
            # confirmó; el segundo recupera ese turno en vez de duplicarlo.
            existing = (
                self._find_turn(conversation.id, client_turn_id)
                if conversation is not None and client_turn_id
                else None
            )
            if existing is not None:
                yield from _replay(existing, request_id=_request_id())
                return
            logger.warning("chat.duplicate_turn_failed", error=str(error))
            yield ChatEvent(
                "error",
                {"message": SERVICE_UNAVAILABLE, "incomplete": bool("".join(fragments).strip())},
            )
        except Exception as error:
            partial = "".join(fragments).strip()
            logger.warning(
                "chat.stream_failed",
                error=str(error),
                thread_id=getattr(conversation, "id", None),
                route=getattr(turn, "route", None),
                incomplete=bool(partial),
            )
            if (
                partial
                and conversation is not None
                and turn is not None
                and not persisted
                and not persist_attempted
            ):
                self._persist_partial(conversation, text, "".join(fragments), turn, client_turn_id)
                if runner is not None:
                    runner.record(
                        conversation.id,
                        question=text,
                        answer="".join(fragments),
                        incomplete=True,
                        turns=turn.turns,
                    )
            yield ChatEvent(
                "error",
                {"message": SERVICE_UNAVAILABLE, "incomplete": bool(partial)},
            )

    def _find_turn(
        self, conversation_id: str, client_turn_id: str
    ) -> tuple[ChatMessage, ChatMessage | None] | None:
        """Lee un turno ya confirmado en una transacción corta y nueva."""

        with self._session_factory() as session:
            return SqlChatRepository(session).find_turn(conversation_id, client_turn_id)

    def _persist(
        self,
        session: Session,
        conversation: Conversation,
        question: str,
        answer: str,
        sources: Sequence[Source],
        *,
        status: str,
        client_turn_id: str | None,
        products: Mapping[str, Any] | None = None,
        quote: Mapping[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Guarda mensajes, resultado comercial y cotización en una sola transacción."""

        repository = SqlChatRepository(session)
        _, assistant = repository.append_turn(
            conversation.id,
            question=question,
            answer=answer,
            sources=_message_sources(sources, products),
            status=status,
            client_turn_id=client_turn_id,
        )
        if quote is not None:
            _record_quote(session, conversation, quote, message_id=assistant.id)
        session.commit()
        return conversation.id, assistant.id

    def _persist_partial(
        self,
        conversation: Conversation,
        question: str,
        answer: str,
        turn: AgentTurn,
        client_turn_id: str | None,
    ) -> None:
        """Guarda una respuesta interrumpida en una sesión nueva."""

        try:
            with self._session_factory() as session:
                repository = SqlChatRepository(session)
                current = repository.get_owned(conversation.id, conversation.owner_key)
                if current is None:
                    return
                self._persist(
                    session,
                    current,
                    question,
                    answer,
                    turn.sources,
                    status="incomplete",
                    client_turn_id=client_turn_id,
                    products=turn.products,
                )
        except Exception as error:  # la UI ya recibirá el evento de error
            logger.warning("chat.incomplete_persist_failed", error=str(error))


def _request_id() -> str:
    """Identificador de la petición que ya fija el middleware de logs.

    La traza usa el mismo valor para que una traza y sus líneas de log se
    crucen por ``request_id`` (FR-004). Fuera de una petición HTTP se genera
    uno propio, de modo que la CLI y las pruebas también puedan trazar.
    """

    actual = structlog.contextvars.get_contextvars().get("request_id")
    return str(actual) if actual else uuid4().hex


def _owned_conversation(
    repository: SqlChatRepository,
    owner_key: str,
    thread_id: str | None,
) -> Conversation:
    """Recupera el hilo del dueño o crea el activo; nunca acepta uno ajeno."""

    if thread_id:
        conversation = repository.get_owned(thread_id, owner_key)
        if conversation is None:
            raise ThreadAccessError("El hilo no pertenece a este visitante.")
        return conversation
    return repository.get_or_create(owner_key)


def _seed_turns(repository: SqlChatRepository, conversation_id: str) -> list[Turn]:
    """Convierte el historial persistido en turnos para sembrar el checkpointer."""

    messages = repository.history(conversation_id, limit=16)
    turns: list[Turn] = []
    pending: ChatMessage | None = None
    for message in messages:
        if message.role == "user":
            pending = message
        elif pending is not None:
            turns.append(
                {
                    "question": pending.content,
                    "answer": message.content,
                    "incomplete": message.status == "incomplete",
                }
            )
            pending = None
    return turns


def _replay(
    turn: tuple[ChatMessage, ChatMessage | None],
    *,
    request_id: str = "",
) -> Iterator[ChatEvent]:
    """Reemite un turno ya persistido para un reintento idempotente."""

    user, assistant = turn
    if assistant is None:
        yield ChatEvent("trace", {"thread_id": user.conversation_id, "route": "replay"})
        yield ChatEvent("error", {"message": SERVICE_UNAVAILABLE, "incomplete": True})
        return
    sources = () if assistant.sources is None else _sources_from_payload(assistant.sources)
    result = _result_from_payload(assistant.sources)
    yield ChatEvent("trace", {"thread_id": assistant.conversation_id, "route": "replay"})
    yield ChatEvent("meta", {"sources": _sources(sources)})
    if result and has_cards(result):
        html = safe_render_products(result)
        if html:
            yield ChatEvent("products", {"html": html})
    if assistant.content:
        yield ChatEvent("token", {"text": assistant.content})
    if assistant.status == "incomplete":
        yield ChatEvent("error", {"message": SERVICE_UNAVAILABLE, "incomplete": True})
    else:
        quote = (result or {}).get("quote") or {}
        folio = (quote.get("quote") or {}).get("id") if isinstance(quote, Mapping) else None
        yield ChatEvent(
            "done",
            {
                "conversation_id": assistant.conversation_id,
                "message_id": assistant.id,
                "thread_id": assistant.conversation_id,
                "route": "replay",
                "elapsed_ms": 0,
                "request_id": request_id,
                "quote_id": folio,
            },
        )


def _sources(sources: Sequence[Source]) -> list[dict[str, Any]]:
    """Serializa las fuentes para la columna JSONB y para los eventos."""

    return [asdict(source) for source in sources]


def _message_sources(
    sources: Sequence[Source], products: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Citas más el resultado comercial, para reconstruir las tarjetas al recargar."""

    payload = _sources(sources)
    if products:
        payload.append({"result_type": "commercial", "result": dict(products)})
    return payload


def _result_from_payload(payload: Any) -> dict[str, Any]:
    """Extrae el resultado comercial guardado junto a las citas, si existe."""

    if not isinstance(payload, list):
        return {}
    for item in payload:
        if isinstance(item, dict) and item.get("result_type") == "commercial":
            result = item.get("result")
            return dict(result) if isinstance(result, dict) else {}
    return {}


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _quote_folio(conversation_id: str, client_turn_id: str | None) -> str:
    """Folio estable por turno; aleatorio cuando no hay identificador de reenvío."""

    if not client_turn_id:
        return uuid4().hex[:12]
    seed = f"{conversation_id}:{client_turn_id}".encode()
    return sha256(seed).hexdigest()[:12]


def _issue_quote(
    products: Mapping[str, Any],
    *,
    conversation_id: str,
    client_turn_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Añade folio y vigencia de emisión a la cotización comercial del turno.

    El folio se deriva del hilo y del ``client_turn_id`` cuando existe: dos
    reenvíos simultáneos del mismo turno emiten el mismo folio antes de que el
    segundo detecte el duplicado y reproduzca el turno guardado.
    """

    quoted = (products or {}).get("quote")
    if (
        not isinstance(quoted, Mapping)
        or quoted.get("kind") != "cotizacion"
        or not quoted.get("primary")
    ):
        return dict(products or {}), None
    primary = quoted["primary"]
    commercial = quoted.get("commercial") or {}
    shipping = commercial.get("shipping") or {}
    moment = datetime.now(UTC)
    folio = _quote_folio(conversation_id, client_turn_id)
    retention = (moment.date() + timedelta(days=QUOTE_DAYS)).isoformat()
    payload = {
        **quoted,
        "version": QUOTE_VERSION,
        "quote": {
            "id": folio,
            "folio": folio,
            "issued_at": moment.isoformat(),
            "retention_until": retention,
            "valid_until": retention,
            "days": QUOTE_DAYS,
        },
    }
    tax = commercial.get("tax") or {}
    record = {
        "id": folio,
        "payload": payload,
        "version": QUOTE_VERSION,
        "issued_at": moment,
        "subtotal": _decimal(commercial.get("subtotal")),
        "tax_status": tax.get("status"),
        "tax_rate": _decimal(tax.get("rate")),
        "shipping_cost": _decimal(shipping.get("cost")),
        "total": _decimal(payload.get("total")),
        "currency": primary.get("currency", "COP"),
        "valid_until": retention,
        "commercial_valid_until": commercial.get("valid_until"),
    }
    return {**products, "quote": payload}, record


def _record_quote(
    session: Session,
    conversation: Conversation,
    quote: Mapping[str, Any],
    *,
    message_id: str,
) -> None:
    """Guarda la cotización en la misma transacción del turno."""

    valid_until = quote.get("valid_until")
    commercial_valid_until = quote.get("commercial_valid_until")
    SqlQuoteRepository(session).record(
        str(quote["id"]),
        conversation.owner_key,
        payload=quote["payload"],
        total=quote.get("total"),
        currency=str(quote["currency"]),
        valid_until=date.fromisoformat(str(valid_until)),
        version=int(quote.get("version", 1)),
        issued_at=quote.get("issued_at"),
        subtotal=quote.get("subtotal"),
        tax_status=quote.get("tax_status"),
        tax_rate=quote.get("tax_rate"),
        shipping_cost=quote.get("shipping_cost"),
        commercial_valid_until=(
            None
            if commercial_valid_until is None
            else date.fromisoformat(str(commercial_valid_until))
        ),
        conversation_id=conversation.id,
        message_id=message_id,
    )


def _sources_from_payload(payload: Any) -> tuple[Source, ...]:
    """Reconstruye citas desde JSONB; ignora el resultado comercial y formas desconocidas."""

    if not isinstance(payload, list):
        return ()
    result = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("result_type") == "commercial":
            continue
        try:
            result.append(Source(**item))
        except TypeError:
            continue
    return tuple(result)


def _milliseconds(started: float) -> int:
    """Milisegundos transcurridos desde ``started``."""

    return round((perf_counter() - started) * 1000)


def _safe_rollback(session: Session | None) -> None:
    """Revierte sin enmascarar el error original si la conexión ya no responde."""

    if session is None:
        return
    try:
        session.rollback()
    except Exception:
        logger.warning("chat.rollback_failed", exc_info=True)


class _DeferredEmbedder:
    """Embedder que resuelve el cliente real en la primera llamada a ``embed``."""

    def embed(self, text: str) -> Sequence[float]:
        from apps.api import runtime

        return runtime.ask_components()[0].embed(text)


class _DeferredLLM:
    """LLM que resuelve el cliente real en la primera generación."""

    def complete(self, *, system: str, prompt: str) -> str:
        from apps.api import runtime

        return runtime.ask_components()[1].complete(system=system, prompt=prompt)

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        from apps.api import runtime

        return runtime.ask_components()[1].stream(system=system, prompt=prompt)


@cache
def default_chat_service() -> ChatService:
    """Servicio real; agente, Qdrant y LLM se resuelven en la primera pregunta."""

    from apps.api import runtime

    return ChatService(runtime.session_factory(), _DeferredEmbedder(), _DeferredLLM())
