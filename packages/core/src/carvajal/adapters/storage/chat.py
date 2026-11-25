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

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables

__all__ = [
    "HISTORY_LIMIT",
    "ChatMessage",
    "Conversation",
    "ConversationSummary",
    "SqlChatRepository",
]

HISTORY_LIMIT = 50


@dataclass(frozen=True)
class Conversation:
    """Conversación de un dueño anónimo, con sus marcas de tiempo."""

    id: str
    owner_key: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConversationSummary:
    """Resumen de un hilo para el panel lateral de conversaciones."""

    id: str
    preview: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ChatMessage:
    """Mensaje de una conversación.

    En las respuestas, ``sources`` lleva las citas y el resultado comercial; en las
    preguntas solo existe si la persona adjuntó una foto (spec 053): la miniatura,
    ``{"result_type": "photo", "thumbnail": "data:image/jpeg;base64,…"}``.

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

    def summarize(self, owner_key: str, *, limit: int = 50) -> list[ConversationSummary]:
        """Lista los hilos del dueño, del más reciente al más antiguo.

        La vista previa es el último mensaje del hilo, recortado para el panel.
        Un hilo recién creado se conserva con la vista previa vacía.
        """

        if limit < 1:
            raise ValueError("limit debe ser mayor que cero.")
        ultimo = select(
            tables.messages.c.conversation_id,
            tables.messages.c.content,
            func.row_number()
            .over(
                partition_by=tables.messages.c.conversation_id,
                order_by=(
                    tables.messages.c.created_at.desc(),
                    tables.messages.c.id.desc(),
                ),
            )
            .label("posicion"),
        ).subquery()
        rows = (
            self._session.execute(
                select(
                    tables.conversations.c.id,
                    tables.conversations.c.created_at,
                    tables.conversations.c.updated_at,
                    ultimo.c.content,
                )
                .select_from(tables.conversations)
                .outerjoin(
                    ultimo,
                    (ultimo.c.conversation_id == tables.conversations.c.id)
                    & (ultimo.c.posicion == 1),
                )
                .where(tables.conversations.c.owner_key == owner_key)
                .order_by(
                    tables.conversations.c.updated_at.desc(),
                    tables.conversations.c.id.desc(),
                )
                .limit(min(limit, 100))
            )
            .mappings()
            .all()
        )
        return [
            ConversationSummary(
                id=row["id"],
                preview=" ".join((row["content"] or "").split())[:160],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_or_create(self, owner_key: str) -> Conversation:
        """Devuelve la conversación activa o crea una si el dueño no tiene.

        El primer envío puede llegar dos veces en paralelo. Un lock de transacción
        por dueño serializa la comprobación y la creación: la segunda petición ve
        el hilo que confirmó la primera en vez de abrir otro.
        """

        bind = self._session.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", "") == "postgresql":
            self._session.execute(
                select(func.pg_advisory_xact_lock(func.hashtext(f"chat-thread:{owner_key}")))
            )
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
    ) -> tuple[ChatMessage, ChatMessage | None] | None:
        """Recupera un turno ya persistido, incluso si quedó a medias."""

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
        if not rows:
            return None
        first = ChatMessage(**rows[0])
        if len(rows) == 1:
            return first, None
        return first, ChatMessage(**rows[1])

    def find_turn_thread(self, owner_key: str, client_turn_id: str) -> str | None:
        """Hilo del dueño que ya guarda el turno ``client_turn_id``, en cualquier hilo.

        ``find_turn`` busca dentro de un hilo conocido; un reintento del webhook puede
        llegar después de abrir un hilo nuevo, así que aquí se busca por dueño.
        """

        if not client_turn_id:
            return None
        return self._session.scalar(
            select(tables.messages.c.conversation_id)
            .join(
                tables.conversations,
                tables.conversations.c.id == tables.messages.c.conversation_id,
            )
            .where(
                tables.conversations.c.owner_key == owner_key,
                tables.messages.c.client_turn_id == client_turn_id,
            )
            .limit(1)
        )

    def owned_message(self, message_id: str, owner_key: str) -> ChatMessage | None:
        """Mensaje ``message_id`` solo si su hilo es del dueño; nunca cruza identidades.

        Lo usa la lectura en voz del servidor (spec 047): el texto que se sintetiza sale de
        la base, no del navegador.
        """

        if not message_id:
            return None
        row = (
            self._session.execute(
                select(tables.messages)
                .join(
                    tables.conversations,
                    tables.conversations.c.id == tables.messages.c.conversation_id,
                )
                .where(
                    tables.messages.c.id == message_id,
                    tables.conversations.c.owner_key == owner_key,
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else ChatMessage(**row)

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

    def history_all(self, conversation_id: str) -> list[ChatMessage]:
        """Todos los mensajes del hilo en orden ascendente, sin tope.

        :meth:`history` exige un ``limit`` y lo acota a ``HISTORY_LIMIT``. El
        ``SummaryWorker`` no la usa: lee solo los turnos que le faltan con
        :meth:`history_turns`.
        """

        rows = (
            self._session.execute(
                select(tables.messages)
                .where(tables.messages.c.conversation_id == conversation_id)
                .order_by(tables.messages.c.created_at, tables.messages.c.id)
            )
            .mappings()
            .all()
        )
        return [ChatMessage(**row) for row in rows]

    def count_turns(self, conversation_id: str) -> int:
        """Turnos persistidos del hilo: cada turno guarda una respuesta del asistente."""

        total = self._session.execute(
            select(func.count())
            .select_from(tables.messages)
            .where(
                tables.messages.c.conversation_id == conversation_id,
                tables.messages.c.role == "assistant",
            )
        ).scalar_one()
        return int(total or 0)

    def history_turns(self, conversation_id: str, *, start: int, stop: int) -> list[ChatMessage]:
        """Mensajes de los turnos ``[start, stop)`` del hilo, en orden ascendente (spec 045, R1).

        Los turnos se cuentan como en :meth:`count_turns` (una respuesta del asistente por
        turno). Los límites del rango salen de las respuestas que cierran el turno
        ``start - 1`` y el turno ``stop - 1`` (orden ``(created_at, id)``), así que solo se
        cargan los mensajes de esos turnos y nunca el hilo entero.
        """

        if start < 0 or stop < start:
            raise ValueError(f"rango de turnos inválido: [{start}, {stop}).")
        if stop == start:
            return []
        upper = self._assistant_key(conversation_id, stop - 1)
        if upper is None:
            return []
        column = tables.messages.c
        query = select(tables.messages).where(
            column.conversation_id == conversation_id,
            or_(
                column.created_at < upper[0],
                and_(column.created_at == upper[0], column.id <= upper[1]),
            ),
        )
        if start > 0:
            lower = self._assistant_key(conversation_id, start - 1)
            if lower is None:
                return []
            query = query.where(
                or_(
                    column.created_at > lower[0],
                    and_(column.created_at == lower[0], column.id > lower[1]),
                )
            )
        rows = self._session.execute(query.order_by(column.created_at, column.id)).mappings().all()
        return [ChatMessage(**row) for row in rows]

    def _assistant_key(self, conversation_id: str, index: int) -> tuple[datetime, str] | None:
        """``(created_at, id)`` de la respuesta del asistente que cierra el turno ``index``."""

        column = tables.messages.c
        row = self._session.execute(
            select(column.created_at, column.id)
            .where(column.conversation_id == conversation_id, column.role == "assistant")
            .order_by(column.created_at, column.id)
            .offset(index)
            .limit(1)
        ).first()
        return None if row is None else (row[0], row[1])

    def count_questions_until(self, conversation_id: str, moment: datetime) -> int:
        """Preguntas de la persona guardadas hasta ``moment`` inclusive (un ``COUNT``)."""

        column = tables.messages.c
        total = self._session.execute(
            select(func.count())
            .select_from(tables.messages)
            .where(
                column.conversation_id == conversation_id,
                column.role == "user",
                column.created_at <= _utc(moment),
            )
        ).scalar_one()
        return int(total or 0)

    def first_question_after(self, conversation_id: str, moment: datetime) -> ChatMessage | None:
        """Primera pregunta de la persona guardada después de ``moment``."""

        column = tables.messages.c
        row = (
            self._session.execute(
                select(tables.messages)
                .where(
                    column.conversation_id == conversation_id,
                    column.role == "user",
                    column.created_at > _utc(moment),
                )
                .order_by(column.created_at, column.id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return None if row is None else ChatMessage(**row)

    def append_turn(
        self,
        conversation_id: str,
        *,
        question: str,
        answer: str,
        sources: Sequence[Mapping[str, Any]] | None = None,
        status: str = "complete",
        client_turn_id: str | None = None,
        question_sources: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[ChatMessage, ChatMessage]:
        """Inserta el turno usuario→asistente y toca ``updated_at``.

        ``question_sources`` acompaña a la pregunta (la miniatura de la foto, spec 053).

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
            sources=None if not question_sources else [dict(item) for item in question_sources],
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


def _utc(moment: datetime) -> datetime:
    """El momento en UTC (sin zona se toma como UTC), como se guarda ``created_at``."""

    return moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _message_ids() -> tuple[str, str]:
    """Ids ordenables por inserción, para desempatar ``created_at`` iguales."""

    stamp = time_ns()
    return (
        f"{stamp:020d}{uuid4().hex[:12]}",
        f"{stamp + 1:020d}{uuid4().hex[:12]}",
    )
