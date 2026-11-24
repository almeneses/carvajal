"""Servicio del chat: agente, historial, turnos y stream con persistencia atómica.

El servicio es el único punto que mezcla el agente LangGraph con la base de
datos: si la respuesta falla antes de empezar no se guarda nada; si se
interrumpe después del primer token, se guarda marcada como ``incomplete``.
Cada turno usa el id de la conversación como ``thread_id`` y verifica que el
hilo pertenezca al dueño de la cookie.

La sesión de base de datos se cierra antes de llamar al LLM: generar puede
tardar minutos y no debe retener una conexión del pool. La persistencia y el
checkpoint se escriben después, en transacciones cortas.

``AGENT_MODE`` (spec 044) elige el camino del turno: ``routes`` prepara el turno con el
grafo por rutas y genera con el LLM del servicio; ``tools`` transmite el grafo con
herramientas (``ToolStreamRunner``), que emite estados, tokens y un cierre con fuentes,
tarjetas y cotizaciones. La persistencia, el replay y el monitoreo son los mismos.

En modo ``tools``, si el proveedor falla, vence el plazo o devuelve una decisión inválida
antes del primer token, el turno se atiende con el respaldo determinista (FR-009): el grafo
``routes`` (con el enrutador de reglas) prepara el turno y ``fallback.render`` redacta sin
LLM; si venció el plazo con resultados ya obtenidos, se redacta con ellos y se declara lo
que faltó (R9, FR-010). Cada cotización mostrada recibe su folio y su fila ``quotes`` en la
transacción del turno, que se guarda antes de mostrar los folios (FR-012a).

Spec 045 (US1): justo después de guardar cada turno (completo o ``incomplete``, en ambos
modos y en el respaldo), el servicio encola el resumen acumulado del hilo en el
``SummaryWorker`` sin esperarlo; la réplica idempotente no encola nada. La semilla del
turno trae ``agent_history_turns`` más la holgura del resumen y el total de turnos del hilo
(``SeedTurns.turn_count``). ``trace`` lleva ``memory`` (categorías con valor) y
``summary`` (``covered_turns`` y ``method``, nunca el texto).
"""

import atexit
from collections.abc import Generator, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cache
from hashlib import sha256
from time import perf_counter
from typing import Any, Literal, Protocol, TypeGuard, runtime_checkable
from uuid import uuid4

import openai
import structlog
from langchain_core.exceptions import OutputParserException
from langgraph.errors import GraphRecursionError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from apps.api.chat.agent import (
    AgentRunner,
    AgentTurn,
    ToolAgentFinal,
    ToolAgentSources,
    ToolAgentStatus,
    ToolAgentToken,
    ToolStreamRunner,
    TurnTimeout,
    default_agent_runner,
    default_tool_agent_runner,
)
from apps.api.chat.products import has_cards, result_quotes, safe_render_products
from apps.api.chat.streaming import ChatEvent
from apps.api.chat.summary_worker import SummaryWorker, build_summary_worker
from carvajal.adapters.storage.chat import (
    ChatMessage,
    Conversation,
    ConversationSummary,
    SqlChatRepository,
)
from carvajal.adapters.storage.quotes import SqlQuoteRepository
from carvajal.agent.gatekeeper.model import map_route
from carvajal.agent.memory.summary import SUMMARY_SLACK_TURNS, SeedTurns, pair_turns
from carvajal.agent.state import Turn
from carvajal.agent.toolagent import fallback
from carvajal.agent.toolagent.state import UNKNOWN_TOOL
from carvajal.agent.toolagent.tools import DECLARATION_TOOLS, TOOL_NAMES
from carvajal.application.ask import NO_CONTEXT_ANSWER, Answer, Source
from carvajal.application.errors import LLMError
from carvajal.application.ports import Embedder, LLMProvider
from carvajal.infrastructure.monitoring import (
    TurnTracer,
    build_tracer,
    record_judge_calls,
    record_tool_calls,
    thread_hash,
    token_metadata,
)
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

# Motivo del respaldo (FR-009), para ``trace`` y el monitoreo.
FallbackReason = Literal["provider", "timeout", "invalid_decision", "empty_answer"]


class ThreadAccessError(PermissionError):
    """El hilo existe pero pertenece a otro dueño (o no existe)."""


class ChatFailure(RuntimeError):
    """Fallo de chat ya traducido; la vista lo convierte en 503."""


@dataclass
class _ToolTurn:
    """Progreso de un turno del modo ``tools`` que el manejo de errores necesita leer.

    ``fragments`` es la misma lista que acumula ``stream()``: lo ya enviado al cliente.
    """

    runner: ToolStreamRunner
    fragments: list[str]
    seed: Sequence[Turn]
    route: str = "agent"
    persist_attempted: bool = False
    # Fuentes ya enviadas en ``meta`` antes del primer token; ``None`` si aún no.
    announced: tuple[Source, ...] | None = None


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
        tool_agent: ToolStreamRunner | None = None,
        tracer: TurnTracer | None = None,
        settings: Settings | None = None,
        summary_worker: SummaryWorker | None = None,
    ) -> None:
        self._session_factory = session_factory
        # Spec 045 (US1): resumen en segundo plano tras persistir; ``None`` no resume.
        self._summary_worker = summary_worker
        self._embedder = embedder
        self._llm = llm
        self._agent = agent
        self._tool_agent = tool_agent
        # El tracer y la configuración se resuelven en el primer turno: importar
        # el módulo no lee el entorno ni construye clientes.
        self._tracer = tracer
        self._settings = settings

    def _runner(self) -> AgentRunner:
        """Runner inyectado o el real, construido en la primera pregunta."""

        return self._agent or default_agent_runner()

    def _tool_runner(self) -> ToolStreamRunner | None:
        """Runner del modo ``tools`` o ``None`` si el turno va por ``routes``.

        Con ``AGENT_MODE=tools`` se usa el inyectado o el del runtime. Si solo se inyectó
        el runner ``routes`` (pruebas), no se construye el runtime real. Si el runtime no
        pudo construir el grafo ``tools`` (``LLM_PROVIDER`` distinto de ``deepseek`` o sin
        ``DEEPSEEK_API_KEY``), el turno sigue por ``routes``.
        """

        if self._config().agent_mode != "tools":
            return None
        if self._tool_agent is not None:
            return self._tool_agent
        if self._agent is not None:
            return None
        return default_tool_agent_runner()

    def _config(self) -> Settings:
        """Configuración del proceso, leída de forma perezosa una sola vez."""

        if self._settings is None:
            self._settings = Settings()
        return self._settings

    def _summarize(self, owner_key: str, conversation_id: str) -> None:
        """Encola el resumen del hilo sin esperarlo (FR-003); un fallo no toca el turno."""

        if self._summary_worker is None:
            return
        try:
            self._summary_worker.submit(owner_key, conversation_id)
        except Exception as error:  # el resumen nunca rompe la respuesta
            logger.warning("agent.summary_failed", cause="submit", error=type(error).__name__)

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
        tools: _ToolTurn | None = None
        try:
            if not text:
                raise ValueError("La pregunta no puede estar vacía.")
            if client_turn_id is not None and len(client_turn_id) > MAX_CLIENT_TURN_ID:
                raise ValueError("El identificador del turno es demasiado largo.")

            # El runtime del agente se inicializa antes de abrir una sesión:
            # PostgresSaver.setup() crea índices CONCURRENTLY y se bloquearía
            # si esta petición retuviera una transacción abierta.
            tool_runner = self._tool_runner()
            if tool_runner is None:
                runner = self._runner()
            # Fase de preparación: resolver el hilo y sembrar el checkpointer.
            # La sesión se cierra antes de decidir la ruta y de generar.
            with self._session_factory() as session:
                repository = SqlChatRepository(session)
                conversation = _owned_conversation(repository, owner_key, thread_id)
                seed = _seed_turns(repository, conversation.id, self._config().agent_history_turns)
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
            if tool_runner is not None:
                tools = _ToolTurn(runner=tool_runner, fragments=fragments, seed=seed)
                yield from self._stream_tools(
                    tools,
                    monitor,
                    owner_key=owner_key,
                    conversation=conversation,
                    question=text,
                    client_turn_id=client_turn_id,
                    request_id=request_id,
                    started=started,
                )
                return
            if runner is None:  # el modo ``routes`` lo resolvió arriba; acota el tipo
                raise RuntimeError("Sin runner para el turno.")
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
                        "memory": _memory_count(turn.preferences),
                        "summary": _summary_trace(turn.summary),
                        "retrieved": len(turn.sources),
                        # Spec 046 (FR-009): el modo ``routes`` no usa Jev; la traza muestra
                        # la decisión de las reglas con la forma del gatekeeper.
                        "gatekeeper": _routes_gatekeeper(turn.route, turn.intent),
                        "claims": {"status": "off"},
                    },
                )
                span.add_metadata(
                    **_gate_metadata(_routes_gatekeeper(turn.route, turn.intent), {"status": "off"})
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
                        quotes=[quote_record] if quote_record else (),
                    )
                    persisted = True
                self._summarize(owner_key, conversation.id)
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
                        "quote_ids": [quote_record["id"]] if quote_record else [],
                    },
                )
            yield done_event
        except GeneratorExit:
            # «Detener» o desconexión (research R2): antes del primer token no se guarda
            # nada; durante el texto, lo emitido queda como ``incomplete`` sin fuentes ni
            # cotizaciones. El modo ``routes`` conserva su comportamiento.
            if (
                tools is not None
                and conversation is not None
                and not tools.persist_attempted
                and "".join(fragments).strip()
            ):
                logger.info("chat.turn_cancelled", thread_id=conversation.id, mode="tools")
                self._persist_tools_partial(tools, conversation, text, client_turn_id)
            raise
        except IntegrityError as error:
            # Dos reenvíos simultáneos con el mismo ``client_turn_id``: el primero
            # confirmó; el segundo recupera ese turno en vez de duplicarlo. Otra
            # violación de integridad no se enmascara con un turno anterior.
            existing = (
                self._find_turn(conversation.id, client_turn_id)
                if conversation is not None and client_turn_id and _is_duplicate_turn(error)
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
                route=getattr(turn, "route", None) or (tools.route if tools else None),
                mode="tools" if tools is not None else "routes",
                incomplete=bool(partial),
            )
            if (
                partial
                and conversation is not None
                and tools is not None
                and not tools.persist_attempted
            ):
                # Corte durante el texto del modo ``tools``: se guarda lo emitido como
                # ``incomplete``, sin fuentes ni cotizaciones (aún no hay cierre del agente).
                self._persist_tools_partial(tools, conversation, text, client_turn_id)
            if (
                partial
                and conversation is not None
                and turn is not None
                and not persisted
                and not persist_attempted
            ):
                self._persist_partial(
                    conversation,
                    text,
                    "".join(fragments),
                    client_turn_id,
                    sources=turn.sources,
                    products=turn.products,
                )
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

    def _persist_tools_partial(
        self,
        tools: _ToolTurn,
        conversation: Conversation,
        question: str,
        client_turn_id: str | None,
    ) -> None:
        """Guarda lo emitido del modo ``tools`` como ``incomplete``, sin fuentes ni folios."""

        answer = "".join(tools.fragments)
        self._persist_partial(
            conversation,
            question,
            answer,
            client_turn_id,
            sources=(),
            products=None,
        )
        tools.runner.record(
            conversation.id,
            question=question,
            answer=answer,
            incomplete=True,
            turns=tools.seed,
        )

    def _stream_tools(
        self,
        tools: _ToolTurn,
        monitor: TurnTracer,
        *,
        owner_key: str,
        conversation: Conversation,
        question: str,
        client_turn_id: str | None,
        request_id: str,
        started: float,
    ) -> Iterator[ChatEvent]:
        """Turno del modo ``tools``: estados y tokens en vivo; cierre y persistencia al final.

        ``meta`` sale antes del primer token, con las fuentes que el runner anuncia
        (``ToolAgentSources``), como en ``routes``: el cliente sella cada párrafo con las
        fuentes que conoce al recibirlo. Si el cierre trae otra lista (o no hubo tokens),
        ``meta`` se reemite al final. ``trace`` y ``products`` dependen del
        ``ToolAgentFinal`` y salen tras el último token, antes de ``done``.

        Respaldo (FR-009): si el proveedor falla, vence el plazo o devuelve una decisión
        inválida (o un cierre sin texto) antes del primer token, el turno lo atiende
        :meth:`_fallback_tools`; un cierre sin texto pero con evidencia redacta con lo que
        trae, como el plazo vencido. Después del primer token, el error sigue a ``stream()``,
        que guarda lo emitido como ``incomplete``. Los demás errores y la cancelación los
        maneja ``stream()``.
        """

        config = self._config()
        fragments = tools.fragments
        with monitor.turn(
            request_id=request_id,
            thread_hash=thread_hash(conversation.id),
            router=config.agent_router,
            question=question,
        ) as span:
            span.add_metadata(mode="tools", question_chars=len(question))
            structlog.contextvars.bind_contextvars(thread_id=conversation.id, mode="tools")
            closing: ToolAgentFinal | None = None
            reason: FallbackReason | None = None
            error: Exception | None = None
            try:
                closing = yield from self._relay_tool_events(
                    tools,
                    span,
                    owner_key=owner_key,
                    thread_id=conversation.id,
                    question=question,
                )
            except Exception as failure:
                reason = _fallback_reason(failure)
                if reason is None or "".join(fragments).strip():
                    raise
                error = failure
                closing = failure.final if isinstance(failure, TurnTimeout) else None
            else:
                if not "".join(fragments).strip():
                    # Un cierre sin texto es una salida inválida del proveedor.
                    reason = "empty_answer"
            if reason is not None:
                done_event = yield from self._fallback_tools(
                    tools,
                    span,
                    owner_key=owner_key,
                    conversation=conversation,
                    question=question,
                    client_turn_id=client_turn_id,
                    request_id=request_id,
                    started=started,
                    reason=reason,
                    error=error,
                    obtained=closing,
                )
            else:
                if closing is None:  # ``_relay_tool_events`` siempre devuelve un cierre
                    raise RuntimeError("El agente terminó sin cierre del turno.")
                done_event = yield from self._close_tools(
                    tools,
                    span,
                    closing,
                    owner_key=owner_key,
                    conversation=conversation,
                    question=question,
                    client_turn_id=client_turn_id,
                    request_id=request_id,
                    started=started,
                )
        # Como en ``routes``: ``done`` sale ya cerrada la traza.
        yield done_event

    def _close_tools(
        self,
        tools: _ToolTurn,
        span: Any,
        closing: ToolAgentFinal,
        *,
        owner_key: str,
        conversation: Conversation,
        question: str,
        client_turn_id: str | None,
        request_id: str,
        started: float,
    ) -> Generator[ChatEvent, None, ChatEvent]:
        """Cierre normal del modo ``tools``: guardado, ``meta`` si cambió, ``trace`` y tarjetas.

        Cada cotización del cierre recibe su folio y su fila ``quotes`` en la transacción
        del turno (FR-012a). El turno se guarda antes de emitir las tarjetas: si la persona
        se desconecta tras ver un folio, ese folio ya tiene su fila y el turno queda
        completo (no ``incomplete``). Devuelve el ``done`` para emitirlo fuera de la traza.
        """

        config = self._config()
        fragments = tools.fragments
        route = "shortcut" if closing.mode_path == "shortcut" else "agent"
        tools.route = route
        sources: tuple[Source, ...] = () if closing.abstained else closing.sources
        products, quote_records = _issue_quotes(
            closing.products,
            conversation_id=conversation.id,
            client_turn_id=client_turn_id,
        )
        monitored, no_query = _monitored_results(closing.results)
        tool_names = [str(r["tool"]) for r in monitored]
        gatekeeper = _gatekeeper_trace(closing.gatekeeper)
        claims = _claims_trace(closing.claims)
        span.add_metadata(
            route=route,
            path=closing.mode_path,
            gate=closing.gate,
            abstained=closing.abstained,
            tool_calls=len(tool_names),
            tools=tool_names,
            no_query=no_query,
            preferences_count=len(closing.preferences),
            retrieved=len(sources),
            quote_count=len(quote_records),
            **token_metadata(closing.usage),
            **_gate_metadata(gatekeeper, claims),
        )
        record_tool_calls(span, monitored, include_content=config.monitoring_include_content)
        record_judge_calls(span, _judge_calls(closing.gatekeeper, closing.claims))
        structlog.contextvars.bind_contextvars(route=route)

        answer_text = "".join(fragments)
        span.add_outputs(answer=answer_text)
        tools.persist_attempted = True
        conversation_id, message_id = self._persist_owned(
            owner_key,
            conversation,
            question,
            answer_text,
            sources,
            client_turn_id=client_turn_id,
            products=products,
            quotes=quote_records,
        )
        self._summarize(owner_key, conversation.id)
        tools.runner.record(
            conversation.id,
            question=question,
            answer=answer_text,
            incomplete=False,
            turns=tools.seed,
        )

        if tools.announced != sources:
            tools.announced = sources
            yield ChatEvent("meta", {"sources": _sources(sources)})
        yield ChatEvent(
            "trace",
            {
                "thread_id": conversation.id,
                "route": route,
                "intent": "",
                "tool": ",".join(tool_names),
                "reason": "",
                "memory": _memory_count(closing.preferences),
                "summary": _summary_trace(closing.summary),
                "retrieved": len(sources),
                "mode": "tools",
                "path": closing.mode_path,
                "gate": closing.gate,
                "gatekeeper": gatekeeper,
                "claims": claims,
            },
        )
        if products and has_cards(products):
            html = safe_render_products(products)
            if html:
                yield ChatEvent("products", {"html": html})
        elapsed = _milliseconds(started)
        logger.info(
            "chat.turn_completed",
            thread_id=conversation.id,
            mode="tools",
            route=route,
            path=closing.mode_path,
            gate=closing.gate,
            tool=",".join(tool_names) or None,
            sources=len(sources),
            retrieved=len(sources),
            quotes=len(quote_records),
            duration_ms=elapsed,
            result="ok",
        )
        span.add_metadata(answer_chars=len(answer_text))
        return _done_event(
            conversation_id,
            message_id,
            thread_id=conversation.id,
            route=route,
            elapsed=elapsed,
            request_id=request_id,
            folios=[str(record["id"]) for record in quote_records],
        )

    def _fallback_tools(
        self,
        tools: _ToolTurn,
        span: Any,
        *,
        owner_key: str,
        conversation: Conversation,
        question: str,
        client_turn_id: str | None,
        request_id: str,
        started: float,
        reason: FallbackReason,
        error: Exception | None,
        obtained: ToolAgentFinal | None,
    ) -> Generator[ChatEvent, None, ChatEvent]:
        """Respaldo determinista del modo ``tools`` (FR-009, research R8 y R9).

        Sin ningún LLM. Por defecto, el grafo ``routes`` prepara el turno en el mismo hilo
        (mismo ``thread_id`` y checkpointer, así conserva el contexto comercial); en
        producción ese grafo usa el enrutador de reglas (``agent_runtime``). Su ``prompt``
        se descarta y ``fallback.render`` redacta el aviso fijo con sus tarjetas, su
        cotización o hasta tres fragmentos citados.

        Si venció el plazo (``reason == "timeout"``) o el cierre llegó sin texto
        (``"empty_answer"``) y ``obtained`` (el ``final`` del ``TurnTimeout`` o el cierre
        mismo) ya trae resultados con fuentes o tarjetas, el respaldo redacta con lo obtenido
        en vez de repetir el camino de reglas, así no duplica consultas ni descarta la
        cotización calculada; ``trace`` lo marca con ``fallback_route: "tools"``. Solo el
        plazo vencido cierra con :func:`fallback.timeout_note`, que declara lo que faltó
        (R9, FR-010). Si no, ``obtained`` solo alimenta el monitoreo.

        El turno se guarda (mensajes y cotizaciones en una transacción, FR-012a) antes de
        emitir nada: el texto no depende del stream y lleva folios, así que una desconexión
        tras el evento ``fallback`` nunca deja un folio sin su fila ni un turno
        ``incomplete``. Se registra como ``complete`` con ``path: "fallback"`` en ``trace``
        y en el monitoreo (no en el estado del grafo, ver ``data-model.md``). Devuelve el
        ``done``.
        """

        config = self._config()
        logger.warning(
            "chat.tool_agent_fallback",
            thread_id=conversation.id,
            reason=reason,
            error_type=type(error).__name__ if error is not None else None,
            error=str(error) if error is not None else None,
        )
        tools.route = "agent"
        monitored, no_query = _monitored_results(obtained.results if obtained else ())
        tool_names = [str(r["tool"]) for r in monitored]
        if (
            reason in ("timeout", "empty_answer")
            and obtained is not None
            and _has_evidence(obtained)
        ):
            products, quote_records = _issue_quotes(
                obtained.products,
                conversation_id=conversation.id,
                client_turn_id=client_turn_id,
            )
            sources = obtained.sources
            # Solo el plazo vencido declara lo que faltó; un cierre vacío no venció nada.
            note = fallback.timeout_note(len(obtained.missing)) if reason == "timeout" else ""
            answer_text = fallback.render(products, sources, note=note)
            fallback_route, intent, preferences = "tools", "", obtained.preferences
            summary = obtained.summary
            tool = ",".join(tool_names)
            recorder: ToolStreamRunner | AgentRunner = tools.runner
            turns: Sequence[Turn] = tools.seed
        else:
            runner = self._runner()
            turn = runner.prepare(owner_key, conversation.id, question, tools.seed)
            products, quote_record = _issue_quote(
                turn.products,
                conversation_id=conversation.id,
                client_turn_id=client_turn_id,
            )
            quote_records = [quote_record] if quote_record else []
            sources = turn.sources
            answer_text = fallback.render(products, sources)
            fallback_route, intent, tool, preferences = (
                turn.route,
                turn.intent,
                turn.tool,
                turn.preferences,
            )
            summary = turn.summary
            recorder, turns = runner, turn.turns
        # Spec 046: la decisión del gatekeeper del intento (si la hubo) o la de las reglas; el
        # respaldo determinista no se juzga (``reemplazo``).
        if obtained is not None and obtained.gatekeeper:
            gatekeeper = _gatekeeper_trace(obtained.gatekeeper)
        else:
            gatekeeper = _routes_gatekeeper(*_rules_route(question, tools.seed))
        claims_active = obtained is not None and (obtained.claims or {}).get("status") not in (
            None,
            "off",
        )
        claims = (
            {"status": "skipped", "p": None, "reason": "reemplazo", "substituted": False}
            if claims_active
            else {"status": "off", "p": None, "reason": "", "substituted": False}
        )
        span.add_metadata(
            route="agent",
            path="fallback",
            fallback_reason=reason,
            fallback_route=fallback_route,
            tool_calls=len(tool_names),
            tools=tool_names,
            no_query=no_query,
            preferences_count=len(preferences),
            retrieved=len(sources),
            quote_count=len(quote_records),
            **token_metadata(obtained.usage if obtained is not None else None),
            **_gate_metadata(gatekeeper, claims),
        )
        if error is not None:
            span.add_metadata(error_type=type(error).__name__)
        record_tool_calls(span, monitored, include_content=config.monitoring_include_content)
        if obtained is not None:
            record_judge_calls(span, _judge_calls(obtained.gatekeeper, obtained.claims))
        structlog.contextvars.bind_contextvars(route="agent")
        span.add_outputs(answer=answer_text)

        tools.persist_attempted = True
        conversation_id, message_id = self._persist_owned(
            owner_key,
            conversation,
            question,
            answer_text,
            sources,
            client_turn_id=client_turn_id,
            products=products,
            quotes=quote_records,
        )
        self._summarize(owner_key, conversation.id)
        recorder.record(
            conversation.id,
            question=question,
            answer=answer_text,
            incomplete=False,
            turns=turns,
        )
        elapsed = _milliseconds(started)
        logger.info(
            "chat.turn_completed",
            thread_id=conversation.id,
            mode="tools",
            route="agent",
            path="fallback",
            fallback_reason=reason,
            fallback_route=fallback_route,
            sources=len(sources),
            retrieved=len(sources),
            quotes=len(quote_records),
            duration_ms=elapsed,
            result="ok",
        )
        span.add_metadata(answer_chars=len(answer_text))

        tools.announced = sources
        yield ChatEvent("meta", {"sources": _sources(sources)})
        tools.fragments.append(answer_text)
        yield ChatEvent("fallback", {"text": answer_text, "sources": _sources(sources)})
        yield ChatEvent(
            "trace",
            {
                "thread_id": conversation.id,
                "route": "agent",
                "intent": intent,
                "tool": tool,
                "reason": reason,
                "memory": _memory_count(preferences),
                "summary": _summary_trace(summary),
                "retrieved": len(sources),
                "mode": "tools",
                "path": "fallback",
                "gate": "",
                "fallback_route": fallback_route,
                "gatekeeper": gatekeeper,
                "claims": claims,
            },
        )
        if products and has_cards(products):
            html = safe_render_products(products)
            if html:
                yield ChatEvent("products", {"html": html})
        return _done_event(
            conversation_id,
            message_id,
            thread_id=conversation.id,
            route="agent",
            elapsed=elapsed,
            request_id=request_id,
            folios=[str(record["id"]) for record in quote_records],
        )

    def _persist_owned(
        self,
        owner_key: str,
        conversation: Conversation,
        question: str,
        answer: str,
        sources: Sequence[Source],
        *,
        client_turn_id: str | None,
        products: Mapping[str, Any] | None,
        quotes: Sequence[Mapping[str, Any]],
    ) -> tuple[str, str]:
        """Guarda el turno completo si el hilo sigue siendo del dueño (una transacción)."""

        with self._session_factory() as session:
            repository = SqlChatRepository(session)
            current = repository.get_owned(conversation.id, owner_key)
            if current is None:
                raise ThreadAccessError("El hilo dejó de pertenecer al visitante.")
            return self._persist(
                session,
                current,
                question,
                answer,
                sources,
                status="complete",
                client_turn_id=client_turn_id,
                products=products,
                quotes=quotes,
            )

    def _relay_tool_events(
        self,
        tools: _ToolTurn,
        span: Any,
        *,
        owner_key: str,
        thread_id: str,
        question: str,
    ) -> Generator[ChatEvent, None, ToolAgentFinal]:
        """Traduce los eventos del runner a ``status`` y ``token``; devuelve el cierre.

        El modelo corre dentro del grafo: el hijo ``llm`` cubre todo el bucle y registra
        los caracteres emitidos y los tokens sumados; el detalle por herramienta lo abre
        ``record_tool_calls`` al cerrar el turno (T044).
        Al cancelar o fallar, se cierra el generador del runner: ``ToolAgentRunner`` activa
        su ``TurnCancel`` y el grafo corta el trozo siguiente del modelo y las herramientas
        pendientes, sin esperar a que termine el paso.
        """

        config = self._config()
        fragments = tools.fragments
        events = tools.runner.stream(owner_key, thread_id, question, tools.seed)
        closing: ToolAgentFinal | None = None
        try:
            with span.child(
                "llm",
                run_type="llm",
                metadata={"provider": "deepseek", "model": config.deepseek_model},
            ) as llm_span:
                answer_chars = 0
                while True:
                    # Igual que en ``routes``: el contexto se reabre en cada avance porque
                    # Starlette mueve el generador SSE entre hilos (FR-003).
                    with span.generation():
                        try:
                            event = next(events)
                        except StopIteration:
                            break
                    if isinstance(event, ToolAgentStatus):
                        yield ChatEvent("status", {"text": event.text, "step": event.step})
                    elif isinstance(event, ToolAgentSources):
                        tools.announced = event.sources
                        yield ChatEvent("meta", {"sources": _sources(event.sources)})
                    elif isinstance(event, ToolAgentToken):
                        if not event.text:
                            continue
                        fragments.append(event.text)
                        answer_chars += len(event.text)
                        llm_span.add_outputs(answer_chars=answer_chars)
                        yield ChatEvent("token", {"text": event.text})
                    elif isinstance(event, ToolAgentFinal):
                        closing = event
                if closing is not None:
                    llm_span.add_outputs(usage=dict(closing.usage))
        except GeneratorExit:
            # Cancelación: lo emitido queda en la traza con una sola unión (T012).
            span.add_outputs(answer="".join(fragments))
            raise
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()
        if closing is None:
            raise RuntimeError("El agente terminó sin cierre del turno.")
        return closing

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
        quotes: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[str, str]:
        """Guarda mensajes, resultado comercial y cotizaciones en una sola transacción.

        Todas las filas ``quotes`` del turno comparten ``message_id`` y se confirman con
        los mensajes en el mismo ``commit`` (FR-012a): si una falla, no queda ninguna.
        """

        repository = SqlChatRepository(session)
        _, assistant = repository.append_turn(
            conversation.id,
            question=question,
            answer=answer,
            sources=_message_sources(sources, products),
            status=status,
            client_turn_id=client_turn_id,
        )
        for quote in quotes:
            _record_quote(session, conversation, quote, message_id=assistant.id)
        session.commit()
        return conversation.id, assistant.id

    def _persist_partial(
        self,
        conversation: Conversation,
        question: str,
        answer: str,
        client_turn_id: str | None,
        *,
        sources: Sequence[Source],
        products: Mapping[str, Any] | None,
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
                    sources,
                    status="incomplete",
                    client_turn_id=client_turn_id,
                    products=products,
                )
        except Exception as error:  # la UI ya recibirá el evento de error
            logger.warning("chat.incomplete_persist_failed", error=str(error))
        else:
            self._summarize(conversation.owner_key, conversation.id)


def _is_duplicate_turn(error: IntegrityError) -> bool:
    """Solo la unicidad del turno habilita la reproducción idempotente."""

    original = getattr(error, "orig", None)
    diag = getattr(original, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    if constraint:
        return str(constraint) in {"uq_messages_turn", "uq_messages_turn_user"}
    return "uq_messages_turn" in str(original)


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


def _seed_turns(
    repository: SqlChatRepository, conversation_id: str, history_turns: int
) -> SeedTurns:
    """Semilla del turno: la ventana más la holgura del resumen y el total de turnos.

    Pide el doble de ``agent_history_turns + SUMMARY_SLACK_TURNS`` mensajes (spec 045, FR-008),
    los empareja con ``pair_turns`` (la misma cuenta que el ``SummaryWorker``) y añade
    ``turn_count`` (un ``COUNT`` en la misma sesión): con él ``recall`` sabe qué índice
    tiene el primer turno de la semilla.
    """

    window = history_turns + SUMMARY_SLACK_TURNS
    messages = repository.history(conversation_id, limit=2 * window)
    turns = pair_turns(messages)[-window:]
    return SeedTurns(turns, turn_count=repository.count_turns(conversation_id))


def _memory_count(preferences: Sequence[str]) -> int:
    """Categorías de la memoria con valor (las notas cuentan como una), para ``trace``."""

    return len({"nota" if str(line).startswith("nota:") else str(line) for line in preferences})


# Campos de la decisión del gatekeeper que viajan en ``trace`` (contrato §6): sin texto.
_GATEKEEPER_TRACE_KEYS = (
    "gate_class",
    "scope",
    "source",
    "confidence",
    "cautious",
    "rule",
    "reason",
    "jev_status",
    "llm_class",
)


def _gatekeeper_trace(decision: Mapping[str, Any] | None) -> dict[str, Any]:
    """``trace.gatekeeper`` (spec 046): clase, alcance, fuente, confianza, cautela y regla."""

    decision = decision or {}
    if not decision:
        return {
            "gate_class": None,
            "scope": "",
            "source": "",
            "confidence": None,
            "cautious": False,
            "rule": "",
            "reason": "",
            "jev_status": "off",
            "llm_class": None,
        }
    return {key: decision.get(key) for key in _GATEKEEPER_TRACE_KEYS}


def _routes_gatekeeper(route: str, intent: str) -> dict[str, Any]:
    """Decisión de las reglas del modo ``routes`` con la forma del gatekeeper (FR-009)."""

    return {
        "gate_class": map_route(route, intent),
        "scope": "",
        "source": "reglas",
        "confidence": None,
        "cautious": False,
        "rule": intent,
        "reason": "",
        "jev_status": "off",
        "llm_class": None,
    }


def _claims_trace(claims: Mapping[str, Any] | None) -> dict[str, Any]:
    """``trace.claims`` (spec 046): estado, probabilidad, motivo y si se sustituyó."""

    claims = claims or {}
    return {
        "status": str(claims.get("status") or "off"),
        "p": claims.get("p"),
        "reason": str(claims.get("reason") or ""),
        "substituted": bool(claims.get("substituted")),
    }


def _gate_metadata(gatekeeper: Mapping[str, Any], claims: Mapping[str, Any]) -> dict[str, Any]:
    """Metadatos de la raíz del turno en LangSmith (contrato §6); claves en ``SAFE_KEYS``."""

    return {
        "gatekeeper_route": gatekeeper.get("gate_class"),
        "gatekeeper_scope": gatekeeper.get("scope") or None,
        "gatekeeper_source": gatekeeper.get("source") or None,
        "gatekeeper_llm_class": gatekeeper.get("llm_class"),
        "gatekeeper_confidence": gatekeeper.get("confidence"),
        "gatekeeper_cautious": bool(gatekeeper.get("cautious")),
        "gatekeeper_rule": gatekeeper.get("rule") or None,
        "gatekeeper_reason": gatekeeper.get("reason") or None,
        "jev_status": gatekeeper.get("jev_status"),
        "claims_status": claims.get("status"),
        "claims_p": claims.get("p"),
        "claims_substituted": bool(claims.get("substituted")),
    }


def _judge_calls(gatekeeper: Mapping[str, Any], claims: Mapping[str, Any]) -> list[Any]:
    """Llamadas a Jev del turno (metadatos de ``JudgeCall`` y la decisión o el veredicto)."""

    return [
        call for call in (gatekeeper.get("jev"), claims.get("jev")) if isinstance(call, Mapping)
    ]


def _rules_route(question: str, seed: Sequence[Turn]) -> tuple[str, str]:
    """Ruta e intención de las reglas (sin red), para la traza del respaldo."""

    from carvajal.agent.router import decide_route

    decision = decide_route(question, list(seed))
    return decision.route, decision.intent


def _summary_trace(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    """``trace.summary``: solo ``covered_turns`` y ``method``, nunca el texto."""

    summary = summary or {}
    return {
        "covered_turns": int(summary.get("covered_turns") or 0),
        "method": str(summary.get("method") or ""),
    }


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
        # Mismos folios y en el mismo orden que el turno original (FR-012a): ``quotes`` o,
        # en resultados del modo ``routes``, ``[quote]``.
        folios = [
            str(issued["id"])
            for quote in result_quotes(result)
            if isinstance(issued := quote.get("quote"), Mapping) and issued.get("id")
        ]
        yield _done_event(
            assistant.conversation_id,
            assistant.id,
            thread_id=assistant.conversation_id,
            route="replay",
            elapsed=0,
            request_id=request_id,
            folios=folios,
        )


def _done_event(
    conversation_id: str,
    message_id: str,
    *,
    thread_id: str,
    route: str,
    elapsed: int,
    request_id: str,
    folios: Sequence[str],
) -> ChatEvent:
    """Evento ``done``: ``quote_ids`` con todos los folios y ``quote_id`` = el primero."""

    return ChatEvent(
        "done",
        {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "thread_id": thread_id,
            "route": route,
            "elapsed_ms": elapsed,
            "request_id": request_id,
            "quote_id": folios[0] if folios else None,
            "quote_ids": list(folios),
        },
    )


def _fallback_reason(error: BaseException) -> FallbackReason | None:
    """Motivo del respaldo si ``error`` es del proveedor; ``None`` si es otro fallo.

    - ``timeout``: el plazo del turno (``TurnTimeout``) o el del cliente del proveedor.
    - ``provider``: ``LLMError`` o errores de la API de DeepSeek (cliente compatible con
      OpenAI, que envuelve los de red: conexión, límite, 5xx, 4xx) o de conexión.
    - ``invalid_decision``: un bucle de decisiones que agota el ``recursion_limit`` del
      grafo. ``OutputParserException`` se mapea por defensa (parsers de LangChain que sí la
      lanzan), pero ``ChatOpenAI`` con DeepSeek no la produce: unos argumentos rotos llegan
      como ``invalid_tool_calls`` o, en streaming, como una ``tool_call`` con ``args={}``.
      El grafo responde esas llamadas con un ``ToolMessage`` de error sin consultar nada,
      las cuenta para el límite y la compuerta decide el texto (sin evidencia, abstención;
      ver ``test_argumentos_rotos_en_streaming_no_consultan_ni_inventan``).

    Un error de programación (``RuntimeError``, ``KeyError``…) no se enmascara con el
    respaldo: sigue al manejo genérico de ``stream()``.
    """

    if isinstance(error, (TimeoutError, openai.APITimeoutError)):
        return "timeout"
    if isinstance(error, (LLMError, openai.APIError, ConnectionError)):
        return "provider"
    if isinstance(error, (OutputParserException, GraphRecursionError)):
        return "invalid_decision"
    return None


def _has_evidence(obtained: ToolAgentFinal) -> bool:
    """Lo obtenido antes del plazo alcanza para el respaldo: un resultado ``ok`` de una
    consulta real y fuentes o tarjetas que mostrar."""

    queried = any(
        r.get("status") == "ok"
        and r.get("tool") in TOOL_NAMES
        and r.get("tool") not in DECLARATION_TOOLS
        for r in obtained.results
    )
    return queried and (bool(obtained.sources) or has_cards(obtained.products))


def _monitored_results(
    results: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Consultas del turno para el monitoreo (FR-014) y si hubo ``sin_consulta``.

    ``sin_consulta`` no es una consulta (FR-004a): no entra en ``tool_calls``, ``tools`` ni
    en los hijos ``tool``; se registra aparte como ``no_query``. Un nombre fuera del
    catálogo se registra como ``UNKNOWN_TOOL``: lo escribió el modelo y puede llevar datos
    de la persona.
    """

    monitored: list[dict[str, Any]] = []
    no_query = False
    for result in results:
        name = str(result.get("tool") or "")
        if name in DECLARATION_TOOLS:
            no_query = True
            continue
        monitored.append({**result, "tool": name if name in TOOL_NAMES else UNKNOWN_TOOL})
    return monitored, no_query


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


def _quote_folio(conversation_id: str, client_turn_id: str | None, index: int = 0) -> str:
    """Folio estable por turno y posición; aleatorio sin identificador de reenvío.

    ``data-model.md`` (Quote): ``i = 0`` → ``sha256("{conversation_id}:{client_turn_id}")``
    (el de siempre) e ``i ≥ 1`` → ``sha256("{conversation_id}:{client_turn_id}:{i}")``,
    ambos recortados a 12 caracteres hexadecimales.
    """

    if not client_turn_id:
        return uuid4().hex[:12]
    seed = f"{conversation_id}:{client_turn_id}"
    if index:
        seed = f"{seed}:{index}"
    return sha256(seed.encode()).hexdigest()[:12]


def _is_quotation(quoted: Any) -> TypeGuard[Mapping[str, Any]]:
    """Cotización emitible: ``kind == "cotizacion"`` con producto principal."""

    return (
        isinstance(quoted, Mapping)
        and quoted.get("kind") == "cotizacion"
        and bool(quoted.get("primary"))
    )


def _issue_quote(
    products: Mapping[str, Any],
    *,
    conversation_id: str,
    client_turn_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Añade folio y vigencia de emisión a la cotización comercial del turno.

    Una sola cotización (``quote``): modo ``routes`` y respaldo del modo ``tools``; el
    modo ``tools`` usa :func:`_issue_quotes`. El folio se deriva del hilo y del
    ``client_turn_id`` cuando existe: dos reenvíos simultáneos del mismo turno emiten el
    mismo folio antes de que el segundo detecte el duplicado y reproduzca el turno
    guardado.
    """

    quoted = (products or {}).get("quote")
    if not _is_quotation(quoted):
        return dict(products or {}), None
    payload, record = _stamp_quote(
        quoted,
        folio=_quote_folio(conversation_id, client_turn_id),
        moment=datetime.now(UTC),
    )
    return {**products, "quote": payload}, record


def _issue_quotes(
    products: Mapping[str, Any],
    *,
    conversation_id: str,
    client_turn_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Folio y vigencia para cada cotización del turno del modo ``tools`` (FR-012a).

    Lee ``quotes`` (con caída a ``[quote]``) en el orden del grafo; la posición ``i`` entre
    las cotizaciones emitidas fija el folio (:func:`_quote_folio`), así un reenvío con el
    mismo ``client_turn_id`` repite folios y orden. Devuelve el resultado con ``quotes``
    sellado y ``quote`` = el primero (lectores actuales) y un registro por fila.
    """

    listed = result_quotes(products)
    if not any(_is_quotation(item) for item in listed):
        return dict(products or {}), []
    moment = datetime.now(UTC)
    issued: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for item in listed:
        if not _is_quotation(item):
            issued.append(item)
            continue
        folio = _quote_folio(conversation_id, client_turn_id, len(records))
        payload, record = _stamp_quote(item, folio=folio, moment=moment)
        issued.append(payload)
        records.append(record)
    return {**products, "quotes": issued, "quote": issued[0]}, records


def _stamp_quote(
    quoted: Mapping[str, Any], *, folio: str, moment: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cotización con su sello de emisión y la fila de ``quotes`` que la registra."""

    primary = quoted["primary"]
    commercial = quoted.get("commercial") or {}
    shipping = commercial.get("shipping") or {}
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
    return payload, record


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

    session_factory = runtime.session_factory()
    # El worker no abre conexiones hasta su primer trabajo y se cierra al salir.
    worker = build_summary_worker(session_factory)
    atexit.register(worker.close)
    return ChatService(session_factory, _DeferredEmbedder(), _DeferredLLM(), summary_worker=worker)
