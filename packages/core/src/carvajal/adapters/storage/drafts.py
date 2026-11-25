"""Repositorio del borrador de cotización de cada conversación (spec 054).

Una fila por hilo. La lectura devuelve un borrador vacío si no hay fila o si el payload no
se puede leer; la escritura reemplaza la fila. Igual que el resto de repositorios, recibe una
``Session`` ajena y solo hace ``flush()``. Funciona en PostgreSQL y en el SQLite de pruebas
(sin ``ON CONFLICT``).
"""

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
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
        values = {
            "owner_key": owner_key,
            "payload": draft.to_json(),
            "updated_at": datetime.now(UTC),
        }
        exists = self._session.execute(
            select(tables.quote_drafts.c.conversation_id).where(
                tables.quote_drafts.c.conversation_id == conversation_id
            )
        ).first()
        if exists is not None:
            self._session.execute(
                update(tables.quote_drafts)
                .where(tables.quote_drafts.c.conversation_id == conversation_id)
                .values(**values)
            )
        else:
            self._session.execute(
                tables.quote_drafts.insert().values(conversation_id=conversation_id, **values)
            )
        self._session.flush()

    def delete(self, conversation_id: str) -> None:
        self._session.execute(
            delete(tables.quote_drafts).where(
                tables.quote_drafts.c.conversation_id == conversation_id
            )
        )
        self._session.flush()
