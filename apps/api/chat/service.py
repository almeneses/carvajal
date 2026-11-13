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

from collections.abc import Iterator, Sequence
from dataclasses import asdict
from functools import cache
from time import perf_counter
from typing import Any, Protocol, runtime_checkable

import structlog
from sqlalchemy.orm import Session, sessionmaker

from apps.api.chat.agent import AgentRunner, AgentTurn, default_agent_runner
from apps.api.chat.streaming import ChatEvent
from carvajal.adapters.storage.chat import ChatMessage, Conversation, SqlChatRepository
from carvajal.agent.state import Turn
from carvajal.application.ask import NO_CONTEXT_ANSWER, Answer, Source
from carvajal.application.ports import Embedder, LLMProvider

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


class ThreadAccessError(PermissionError):
    """El hilo existe pero pertenece a otro dueño (o no existe)."""


class ChatFailure(RuntimeError):
    """Fallo de chat ya traducido; la vista lo convierte en 503."""


@runtime_checkable
class ChatProvider(Protocol):
    """Contrato que consume la vista; el servicio real y los dobles lo cumplen."""

    def history(self, owner_key: str) -> Sequence[ChatMessage]:
        """Devuelve el historial de la conversación activa del dueño."""

        ...

    def ask(self, owner_key: str, question: str) -> Answer:
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
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._llm = llm
        self._agent = agent

    def _runner(self) -> AgentRunner:
        """Runner inyectado o el real, construido en la primera pregunta."""

        return self._agent or default_agent_runner()

    def history(self, owner_key: str) -> Sequence[ChatMessage]:
        """Lee el historial sin crear conversación si el dueño aún no tiene."""

        with self._session_factory() as session:
            repository = SqlChatRepository(session)
            conversation = repository.active(owner_key)
            if conversation is None:
                return []
            return repository.history(conversation.id)

    def new_conversation(self, owner_key: str) -> Conversation:
        """Crea un hilo nuevo y lo deja como activo para el dueño."""

        with self._session_factory() as session:
            conversation = SqlChatRepository(session).create(owner_key)
            session.commit()
            logger.info("chat.thread_created", thread_id=conversation.id)
            return conversation

    def ask(self, owner_key: str, question: str) -> Answer:
        """Responde y persiste el turno; los errores se elevan para el 503."""

        text = ""
        sources: tuple[Source, ...] = ()
        error: str | None = None
        for event in self.stream(owner_key, question):
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
                    yield from _replay(replay)
                    return
                # El hilo debe existir antes de generar; si el LLM falla, la UI
                # puede seguir mostrando la conversación sin un turno a medias.
                session.commit()
            turn = runner.prepare(owner_key, conversation.id, text, seed)
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

            if turn.prompt is None:
                fragments.append(NO_CONTEXT_ANSWER)
                yield ChatEvent("token", {"text": NO_CONTEXT_ANSWER})
            else:
                try:
                    for fragment in self._llm.stream(system=turn.system, prompt=turn.prompt):
                        fragments.append(fragment)
                        yield ChatEvent("token", {"text": fragment})
                except Exception as error:
                    if "".join(fragments).strip():
                        raise
                    logger.warning("chat.stream_fallback", error=str(error))
                if not "".join(fragments).strip():
                    answer = self._llm.complete(system=turn.system, prompt=turn.prompt)
                    fragments = [answer]
                    yield ChatEvent(
                        "fallback",
                        {"text": answer, "sources": _sources(turn.sources)},
                    )

            answer_text = "".join(fragments)
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
            yield ChatEvent(
                "done",
                {
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "thread_id": conversation.id,
                    "route": turn.route,
                    "elapsed_ms": elapsed,
                },
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
            if partial and conversation is not None and turn is not None and not persisted:
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
    ) -> tuple[str, str]:
        """Guarda el turno y confirma una sola vez."""

        repository = SqlChatRepository(session)
        _, assistant = repository.append_turn(
            conversation.id,
            question=question,
            answer=answer,
            sources=_sources(sources),
            status=status,
            client_turn_id=client_turn_id,
        )
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
                )
        except Exception as error:  # la UI ya recibirá el evento de error
            logger.warning("chat.incomplete_persist_failed", error=str(error))


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


def _replay(turn: tuple[ChatMessage, ChatMessage | None]) -> Iterator[ChatEvent]:
    """Reemite un turno ya persistido para un reintento idempotente."""

    user, assistant = turn
    if assistant is None:
        yield ChatEvent("trace", {"thread_id": user.conversation_id, "route": "replay"})
        yield ChatEvent("error", {"message": SERVICE_UNAVAILABLE, "incomplete": True})
        return
    sources = () if assistant.sources is None else _sources_from_payload(assistant.sources)
    yield ChatEvent("trace", {"thread_id": assistant.conversation_id, "route": "replay"})
    yield ChatEvent("meta", {"sources": _sources(sources)})
    if assistant.content:
        yield ChatEvent("token", {"text": assistant.content})
    if assistant.status == "incomplete":
        yield ChatEvent("error", {"message": SERVICE_UNAVAILABLE, "incomplete": True})
    else:
        yield ChatEvent(
            "done",
            {
                "conversation_id": assistant.conversation_id,
                "message_id": assistant.id,
                "thread_id": assistant.conversation_id,
                "route": "replay",
                "elapsed_ms": 0,
            },
        )


def _sources(sources: Sequence[Source]) -> list[dict[str, Any]]:
    """Serializa las fuentes para la columna JSONB y para los eventos."""

    return [asdict(source) for source in sources]


def _sources_from_payload(payload: Any) -> tuple[Source, ...]:
    """Reconstruye citas desde JSONB; ignora formas desconocidas."""

    if not isinstance(payload, list):
        return ()
    result = []
    for item in payload:
        if not isinstance(item, dict):
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
