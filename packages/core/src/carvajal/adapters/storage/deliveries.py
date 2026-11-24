"""Reserva SQL de entregas del webhook (ver :mod:`carvajal.application.deliveries`).

La reserva es un ``INSERT ... ON CONFLICT DO NOTHING`` sobre la clave primaria
``(owner_key, delivery_id)``: dos intentos simultáneos del mismo mensaje, en el mismo
worker o en otro, no pueden tomarla los dos. Cada operación es su propia unidad de trabajo
y confirma al terminar, porque la reserva tiene que verse en los demás workers antes de que
empiece el turno.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from carvajal.adapters.storage import tables
from carvajal.application.deliveries import (
    PENDING_TTL,
    RETENTION,
    DeliveryClaim,
    utc_now,
)

__all__ = ["SqlDeliveryStore"]


def _insert(session: Session) -> Any:
    """``insert`` con ``on_conflict_do_nothing`` del dialecto (Postgres o SQLite)."""

    return postgresql.insert if session.get_bind().dialect.name == "postgresql" else sqlite.insert


class SqlDeliveryStore:
    """Implementación SQL de ``DeliveryStore`` sobre ``webhook_deliveries``."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        now: Callable[[], datetime] = utc_now,
        pending_ttl: timedelta = PENDING_TTL,
        retention: timedelta = RETENTION,
    ) -> None:
        self._factory = session_factory
        self._now = now
        self._pending_ttl = pending_ttl
        self._retention = retention

    def claim(self, owner_key: str, delivery_id: str) -> DeliveryClaim:
        """Reserva la entrega, toma una reserva pendiente caducada o informa su estado."""

        table = tables.webhook_deliveries
        now = self._now()
        key = (table.c.owner_key == owner_key, table.c.delivery_id == delivery_id)
        with self._factory.begin() as session:
            session.execute(delete(table).where(table.c.updated_at < now - self._retention))
            inserted = session.execute(
                _insert(session)(table)
                .values(
                    owner_key=owner_key,
                    delivery_id=delivery_id,
                    status="pending",
                    reply=None,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[table.c.owner_key, table.c.delivery_id])
                .returning(table.c.delivery_id)
            ).first()
            if inserted is not None:
                return DeliveryClaim("acquired")
            taken = session.execute(
                update(table)
                .where(
                    *key,
                    table.c.status == "pending",
                    table.c.updated_at < now - self._pending_ttl,
                )
                .values(updated_at=now)
                .returning(table.c.delivery_id)
            ).first()
            if taken is not None:
                return DeliveryClaim("acquired")
            row = session.execute(select(table.c.status, table.c.reply).where(*key)).first()
        if row is None:
            # Se purgó entre las dos sentencias: que el relé reintente.
            return DeliveryClaim("pending")
        return DeliveryClaim("done" if row.status == "done" else "pending", row.reply)

    def finish(self, owner_key: str, delivery_id: str, reply: str | None) -> None:
        """Marca la entrega como atendida."""

        table = tables.webhook_deliveries
        with self._factory.begin() as session:
            session.execute(
                update(table)
                .where(table.c.owner_key == owner_key, table.c.delivery_id == delivery_id)
                .values(status="done", reply=reply, updated_at=self._now())
            )

    def release(self, owner_key: str, delivery_id: str) -> None:
        """Borra la reserva si sigue pendiente; una entrega atendida no se toca."""

        table = tables.webhook_deliveries
        with self._factory.begin() as session:
            session.execute(
                delete(table).where(
                    table.c.owner_key == owner_key,
                    table.c.delivery_id == delivery_id,
                    table.c.status == "pending",
                )
            )
