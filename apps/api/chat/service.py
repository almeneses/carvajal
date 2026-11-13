"""Servicio del chat: historial, turnos y stream con persistencia atómica.

El servicio es el único punto que mezcla el caso de uso ``AskQuestion`` con la
base de datos: si la respuesta falla, la transacción revierte la conversación y
los mensajes. La vista solo conoce este contrato, de modo que las pruebas HTTP
pueden inyectar un doble sin tocar Postgres ni la red.

El stream mantiene una sesión explícita durante todo el turno y confirma una
sola vez al final. Si el cliente corta la conexión, el ``finally`` cierra la
sesión sin publicar el turno.
"""

from collections.abc import Iterator, Sequence
from dataclasses import asdict
from functools import cache
from time import perf_counter
from typing import Any, Protocol, runtime_checkable

import structlog
from sqlalchemy.orm import Session, sessionmaker

from apps.api import runtime
from apps.api.chat.streaming import ChatEvent
from carvajal.adapters.storage.chat import ChatMessage, SqlChatRepository
from carvajal.adapters.vector.pgvector_store import PgVectorStore
from carvajal.application.ask import NO_CONTEXT_ANSWER, Answer, AskQuestion, Source
from carvajal.application.ports import Embedder, LLMProvider

__all__ = ["ChatProvider", "ChatService", "default_chat_service"]

logger = structlog.get_logger(__name__)

SERVICE_UNAVAILABLE = "El servicio de chat no está disponible en este momento."


@runtime_checkable
class ChatProvider(Protocol):
    """Contrato que consume la vista; el servicio real y los dobles lo cumplen."""

    def history(self, owner_key: str) -> Sequence[ChatMessage]:
        """Devuelve el historial de la conversación activa del dueño."""

        ...

    def ask(self, owner_key: str, question: str) -> Answer:
        """Responde la pregunta y persiste el turno completo."""

        ...

    def stream(self, owner_key: str, question: str) -> Iterator[ChatEvent]:
        """Emite el turno como eventos y lo persiste una sola vez al cerrar bien."""

        ...


class ChatService:
    """Implementación real: ``AskQuestion`` + repositorio en una transacción."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embedder: Embedder,
        llm: LLMProvider,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._llm = llm

    def history(self, owner_key: str) -> Sequence[ChatMessage]:
        """Lee el historial sin crear conversación si el dueño aún no tiene."""

        with self._session_factory() as session:
            repository = SqlChatRepository(session)
            conversation = repository.active(owner_key)
            if conversation is None:
                return []
            return repository.history(conversation.id)

    def ask(self, owner_key: str, question: str) -> Answer:
        """Responde y guarda usuario y asistente en una sola transacción.

        La recuperación se hace en una sesión corta; el LLM se llama fuera de
        la transacción para no retener una conexión del pool mientras genera.
        El turno se persiste después, en una transacción nueva.
        """

        text = question.strip()
        session = self._session_factory()
        try:
            ask = AskQuestion(PgVectorStore(session), self._embedder, self._llm)
            prepared = ask.prepare(text)
            # La lectura termina aquí: no retenemos la transacción mientras el
            # modelo genera, que puede tardar minutos.
            session.rollback()
            answer = ask.answer_prepared(prepared)
            self._persist(session, owner_key, text, answer.text, answer.sources)
            return answer
        except Exception:
            _safe_rollback(session)
            raise
        finally:
            session.close()

    def stream(self, owner_key: str, question: str) -> Iterator[ChatEvent]:
        """Emite ``open``, ``meta``, ``token``… y persiste el turno al final.

        Si el LLM falla antes del primer token se completa la respuesta con
        ``complete`` y se emite ``fallback``; si falla después, se emite
        ``error`` con ``incomplete: true`` y no se persiste nada. La sesión se
        cierra siempre en el ``finally``; el corte del cliente revierte el turno.
        """

        started = perf_counter()
        yield ChatEvent("open", {"stream": "chat"})

        text = question.strip()
        fragments: list[str] = []
        session: Session | None = None
        try:
            if not text:
                raise ValueError("La pregunta no puede estar vacía.")
            session = self._session_factory()
            ask = AskQuestion(PgVectorStore(session), self._embedder, self._llm)
            prepared = ask.prepare(text)
            # La lectura termina aquí: no retenemos la transacción mientras el
            # modelo genera, que puede tardar minutos.
            session.rollback()
            yield ChatEvent("meta", {"sources": _sources(prepared.sources)})

            if prepared.prompt is None:
                fragments.append(NO_CONTEXT_ANSWER)
                yield ChatEvent("token", {"text": NO_CONTEXT_ANSWER})
            else:
                try:
                    for fragment in ask.stream_prepared(prepared):
                        fragments.append(fragment)
                        yield ChatEvent("token", {"text": fragment})
                except Exception as error:
                    if "".join(fragments).strip():
                        raise
                    logger.warning("chat.stream_fallback", error=str(error))
                if not "".join(fragments).strip():
                    answer = ask.answer_prepared(prepared)
                    fragments = [answer.text]
                    yield ChatEvent(
                        "fallback",
                        {"text": answer.text, "sources": _sources(answer.sources)},
                    )

            conversation_id, message_id = self._persist(
                session,
                owner_key,
                text,
                "".join(fragments),
                prepared.sources,
            )
            yield ChatEvent(
                "done",
                {
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "elapsed_ms": _milliseconds(started),
                },
            )
        except Exception as error:
            logger.warning("chat.stream_failed", error=str(error))
            _safe_rollback(session)
            yield ChatEvent(
                "error",
                {
                    "message": SERVICE_UNAVAILABLE,
                    "incomplete": bool("".join(fragments).strip()),
                },
            )
        finally:
            if session is not None:
                session.close()

    def _persist(
        self,
        session: Session,
        owner_key: str,
        question: str,
        answer: str,
        sources: Sequence[Source],
    ) -> tuple[str, str]:
        """Guarda el turno completo y confirma una sola vez."""

        repository = SqlChatRepository(session)
        conversation = repository.get_or_create(owner_key)
        _, assistant = repository.append_turn(
            conversation.id,
            question=question,
            answer=answer,
            sources=_sources(sources),
        )
        session.commit()
        return conversation.id, assistant.id


def _sources(sources: Sequence[Source]) -> list[dict[str, Any]]:
    """Serializa las fuentes para la columna JSONB y para los eventos."""

    return [asdict(source) for source in sources]


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
        logger.debug("chat.rollback_failed", exc_info=True)


class _DeferredEmbedder:
    """Embedder que resuelve el cliente real en la primera llamada a ``embed``."""

    def embed(self, text: str) -> Sequence[float]:
        return runtime.ask_components()[0].embed(text)


class _DeferredLLM:
    """LLM que resuelve el cliente real en la primera generación."""

    def complete(self, *, system: str, prompt: str) -> str:
        return runtime.ask_components()[1].complete(system=system, prompt=prompt)

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        return runtime.ask_components()[1].stream(system=system, prompt=prompt)


@cache
def default_chat_service() -> ChatService:
    """Servicio real con la sesión y los clientes diferidos de ``runtime``.

    El embedder y el LLM se resuelven en la primera pregunta, no al abrir
    ``/chat``: leer el historial no debe importar LangChain ni exigir la clave
    del proveedor.
    """

    return ChatService(runtime.session_factory(), _DeferredEmbedder(), _DeferredLLM())
