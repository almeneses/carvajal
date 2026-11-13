"""Repositorio SQL de conversaciones y mensajes del chat.

Cada método recibe una ``Session`` ajena y solo hace ``flush()``: quien abre la
unidad de trabajo decide cuándo confirmar. El turno usuario→asistente se inserta
en una sola transacción, de modo que un fallo a mitad no deja mensajes sueltos.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from time import time_ns
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables

__all__ = ["HISTORY_LIMIT", "ChatMessage", "Conversation", "SqlChatRepository"]

HISTORY_LIMIT = 50


@dataclass(frozen=True)
class Conversation:
    """Conversación de un dueño anónimo, con sus marcas de tiempo."""

    id: str
    owner_key: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ChatMessage:
    """Mensaje de una conversación; ``sources`` solo existe en respuestas.

    ``status`` distingue una respuesta completa de una interrumpida y
    ``client_turn_id`` permite reconocer un reintento del mismo turno.
    """

    id: str
    conversation_id: str
    role: str
    content: str
    sources: list[dict[str, Any]] | None
    created_at: datetime
    status: str = "complete"
    client_turn_id: str | None = None


class SqlChatRepository:
    """Implementación PostgreSQL del almacén de conversaciones."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def active(self, owner_key: str) -> Conversation | None:
        """Devuelve la conversación activa del dueño (la última actualizada)."""

        row = (
            self._session.execute(
                select(tables.conversations)
                .where(tables.conversations.c.owner_key == owner_key)
                .order_by(
                    tables.conversations.c.updated_at.desc(),
                    tables.conversations.c.id.desc(),
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else Conversation(**row)

    def create(self, owner_key: str) -> Conversation:
        """Crea una conversación nueva para el dueño y la devuelve."""

        now = datetime.now(UTC)
        conversation = Conversation(
            id=uuid4().hex,
            owner_key=owner_key,
            created_at=now,
            updated_at=now,
        )
        self._session.execute(insert(tables.conversations).values(asdict(conversation)))
        self._session.flush()
        return conversation

    def get_or_create(self, owner_key: str) -> Conversation:
        """Devuelve la conversación activa o crea una si el dueño no tiene."""

        return self.active(owner_key) or self.create(owner_key)

    def get_owned(self, conversation_id: str, owner_key: str) -> Conversation | None:
        """Devuelve el hilo solo si pertenece al dueño; nunca cruza identidades."""

        row = (
            self._session.execute(
                select(tables.conversations)
                .where(
                    tables.conversations.c.id == conversation_id,
                    tables.conversations.c.owner_key == owner_key,
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else Conversation(**row)

    def find_turn(
        self,
        conversation_id: str,
        client_turn_id: str,
    ) -> tuple[ChatMessage, ChatMessage] | None:
        """Recupera un turno ya persistido para un reintento idempotente."""

        if not client_turn_id:
            return None
        rows = (
            self._session.execute(
                select(tables.messages)
                .where(
                    tables.messages.c.conversation_id == conversation_id,
                    tables.messages.c.client_turn_id == client_turn_id,
                )
                .order_by(tables.messages.c.created_at)
            )
            .mappings()
            .all()
        )
        if len(rows) != 2:
            return None
        return ChatMessage(**rows[0]), ChatMessage(**rows[1])

    def history(
        self,
        conversation_id: str,
        *,
        limit: int = HISTORY_LIMIT,
    ) -> list[ChatMessage]:
        """Devuelve hasta ``limit`` mensajes, los más recientes en orden ascendente."""

        if limit < 0:
            raise ValueError(f"limit debe ser no negativo; recibido {limit!r}.")
        if limit == 0:
            return []
        rows = (
            self._session.execute(
                select(tables.messages)
                .where(tables.messages.c.conversation_id == conversation_id)
                .order_by(
                    tables.messages.c.created_at.desc(),
                    tables.messages.c.id.desc(),
                )
                .limit(min(limit, HISTORY_LIMIT))
            )
            .mappings()
            .all()
        )
        return [ChatMessage(**row) for row in reversed(rows)]

    def append_turn(
        self,
        conversation_id: str,
        *,
        question: str,
        answer: str,
        sources: Sequence[Mapping[str, Any]] | None = None,
        status: str = "complete",
        client_turn_id: str | None = None,
    ) -> tuple[ChatMessage, ChatMessage]:
        """Inserta el turno usuario→asistente y toca ``updated_at``.

        Bloquea la conversación para serializar turnos concurrentes. Los dos
        mensajes se escriben dentro de la transacción del llamador; si algo
        falla después, ninguno queda publicado.
        """

        locked = self._session.execute(
            select(tables.conversations.c.id)
            .where(tables.conversations.c.id == conversation_id)
            .with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            raise LookupError(f"No existe la conversación {conversation_id!r}.")

        now = datetime.now(UTC)
        user_id, assistant_id = _message_ids()
        if status not in {"complete", "incomplete"}:
            raise ValueError(f"estado de mensaje inválido: {status!r}")
        user = ChatMessage(
            id=user_id,
            conversation_id=conversation_id,
            role="user",
            content=question,
            sources=None,
            created_at=now,
            status="complete",
            client_turn_id=client_turn_id,
        )
        assistant = ChatMessage(
            id=assistant_id,
            conversation_id=conversation_id,
            role="assistant",
            content=answer,
            sources=None if sources is None else [dict(source) for source in sources],
            created_at=now + timedelta(microseconds=1),
            status=status,
            client_turn_id=client_turn_id,
        )
        self._session.execute(insert(tables.messages).values([asdict(user), asdict(assistant)]))
        self.touch(conversation_id, when=assistant.created_at)
        self._session.flush()
        return user, assistant

    def touch(self, conversation_id: str, *, when: datetime | None = None) -> None:
        """Actualiza ``updated_at``; falla si la conversación ya no existe."""

        moment = when or datetime.now(UTC)
        updated = self._session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == conversation_id)
            .values(updated_at=moment)
            .returning(tables.conversations.c.id)
        ).scalar_one_or_none()
        if updated is None:
            raise LookupError(f"No existe la conversación {conversation_id!r}.")
        self._session.flush()


def _message_ids() -> tuple[str, str]:
    """Ids ordenables por inserción, para desempatar ``created_at`` iguales."""

    stamp = time_ns()
    return (
        f"{stamp:020d}{uuid4().hex[:12]}",
        f"{stamp + 1:020d}{uuid4().hex[:12]}",
    )
