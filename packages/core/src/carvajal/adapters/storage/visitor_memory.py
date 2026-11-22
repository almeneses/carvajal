"""Repositorio de la memoria silenciosa del visitante (spec 042, FR-022).

Una fila por dueño firmado. La lectura descarta los perfiles caducados y los borra;
la escritura es un upsert idempotente y el olvido elimina la fila completa. Igual que
el resto de repositorios, recibe una ``Session`` ajena y solo hace ``flush()``.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.quotation.memory import EMPTY, is_fresh, normalize

__all__ = ["SqlVisitorMemory"]


class SqlVisitorMemory:
    """Perfil recordado por visitante, con caducidad y borrado."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, owner_key: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Perfil vigente; ``{}`` si no hay o si caducó (y lo elimina)."""

        row = (
            self._session.execute(
                select(tables.visitor_memory).where(tables.visitor_memory.c.owner_key == owner_key)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return dict(EMPTY)
        if not is_fresh(row["updated_at"], now=now or datetime.now(UTC)):
            self.forget(owner_key)
            return dict(EMPTY)
        return normalize(
            {
                "name": row["name"],
                "workplace": row["workplace"],
                "city": row["city"],
                "delivery": row["delivery"],
                "preferences": row["preferences"] or [],
            }
        )

    def save(self, owner_key: str, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Guarda el perfil normalizado y devuelve lo que quedó."""

        clean = normalize(dict(profile))
        values = {**clean, "owner_key": owner_key, "updated_at": datetime.now(UTC)}
        self._session.execute(
            insert(tables.visitor_memory)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[tables.visitor_memory.c.owner_key],
                set_={key: values[key] for key in (*clean, "updated_at")},
            )
        )
        self._session.flush()
        return clean

    def forget(self, owner_key: str) -> None:
        """Borra todo lo recordado del visitante."""

        self._session.execute(
            delete(tables.visitor_memory).where(tables.visitor_memory.c.owner_key == owner_key)
        )
        self._session.flush()
