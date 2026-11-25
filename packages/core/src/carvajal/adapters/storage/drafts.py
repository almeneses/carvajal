"""Repositorio del borrador de cotización de cada conversación (spec 054).

Una fila por hilo. La lectura devuelve un borrador vacío si no hay fila o si el payload no
se puede leer; la escritura reemplaza la fila con un solo ``INSERT … ON CONFLICT DO UPDATE``,
atómico frente a dos turnos simultáneos del mismo hilo (web y WhatsApp, o reintentos). Igual
que el resto de repositorios, recibe una ``Session`` ajena y solo hace ``flush()``. Funciona
en PostgreSQL y en el SQLite de pruebas, que también entiende ``ON CONFLICT``.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.quotation.draft import QuoteDraft

__all__ = ["SqlDraftRepository"]


class SqlDraftRepository:
    """Borrador por conversación: leer, guardar y borrar."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, conversation_id: str) -> QuoteDraft:
        row = (
            self._session.execute(
                select(tables.quote_drafts.c.payload).where(
                    tables.quote_drafts.c.conversation_id == conversation_id
                )
            )
            .scalars()
            .one_or_none()
        )
        return QuoteDraft.from_json(row) if row else QuoteDraft()

    def save(self, conversation_id: str, owner_key: str, draft: QuoteDraft) -> None:
        statement = insert(tables.quote_drafts).values(
            conversation_id=conversation_id,
            owner_key=owner_key,
            payload=draft.to_json(),
            updated_at=datetime.now(UTC),
        )
        # Sin «select y luego insert»: dos turnos que guardan a la vez el primer borrador
        # del hilo no chocan en la clave primaria; gana la última escritura.
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[tables.quote_drafts.c.conversation_id],
                set_={
                    "owner_key": statement.excluded.owner_key,
                    "payload": statement.excluded.payload,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
        self._session.flush()

    def delete(self, conversation_id: str) -> None:
        self._session.execute(
            delete(tables.quote_drafts).where(
                tables.quote_drafts.c.conversation_id == conversation_id
            )
        )
        self._session.flush()
