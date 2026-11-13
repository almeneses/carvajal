"""Servicio del chat: historial y turnos con persistencia atómica.

El servicio es el único punto que mezcla el caso de uso ``AskQuestion`` con la
base de datos: si la respuesta falla, la transacción revierte la conversación y
los mensajes. La vista solo conoce este contrato, de modo que las pruebas HTTP
pueden inyectar un doble sin tocar Postgres ni la red.
"""

from collections.abc import Sequence
from dataclasses import asdict
from functools import cache
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session, sessionmaker

from apps.api import runtime
from carvajal.adapters.storage.chat import ChatMessage, SqlChatRepository
from carvajal.adapters.vector.pgvector_store import PgVectorStore
from carvajal.application.ask import Answer, AskQuestion
from carvajal.application.ports import Embedder, LLMProvider

__all__ = ["ChatProvider", "ChatService", "default_chat_service"]


@runtime_checkable
class ChatProvider(Protocol):
    """Contrato que consume la vista; el servicio real y los dobles lo cumplen."""

    def history(self, owner_key: str) -> Sequence[ChatMessage]:
        """Devuelve el historial de la conversación activa del dueño."""

        ...

    def ask(self, owner_key: str, question: str) -> Answer:
        """Responde la pregunta y persiste el turno completo."""

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

        La conversación se crea dentro de la misma transacción: si el embedder,
        el LLM o la base fallan, no queda ni conversación ni mensaje.
        """

        text = question.strip()
        with self._session_factory.begin() as session:
            answer = AskQuestion(
                PgVectorStore(session),
                self._embedder,
                self._llm,
            ).answer(text)
            repository = SqlChatRepository(session)
            conversation = repository.get_or_create(owner_key)
            repository.append_turn(
                conversation.id,
                question=text,
                answer=answer.text,
                sources=_sources(answer),
            )
        return answer


def _sources(answer: Answer) -> list[dict[str, Any]]:
    """Serializa las fuentes para la columna JSONB."""

    return [asdict(source) for source in answer.sources]


@cache
def default_chat_service() -> ChatService:
    """Servicio real con los singletons perezosos de ``runtime``."""

    return ChatService(runtime.session_factory(), runtime.embedder(), runtime.llm_provider())
