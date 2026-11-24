"""Reserva SQL de entregas del webhook (ver :mod:`carvajal.application.deliveries`).

La reserva es un ``INSERT ... ON CONFLICT DO NOTHING`` sobre la clave primaria
``(owner_key, delivery_id)``: dos intentos simultáneos del mismo mensaje, en el mismo
worker o en otro, no pueden tomarla los dos. Cada operación es su propia unidad de trabajo
y confirma al terminar, porque la reserva tiene que verse en los demás workers antes de que
empiece el turno. Cada toma escribe su ``attempt``: ``finish`` y ``release`` filtran por él.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from carvajal.adapters.storage import tables
from carvajal.application.deliveries import (
    PENDING_TTL,
    RETENTION,
    DeliveryClaim,
    new_token,
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
        """Reserva la entrega, toma una reserva pendiente libre o caducada, o informa su estado."""

        table = tables.webhook_deliveries
        now = self._now()
        token = new_token()
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
                    attempt=token,
                    quota_charged=False,
                    thread_opened=False,
                )
                .on_conflict_do_nothing(index_elements=[table.c.owner_key, table.c.delivery_id])
                .returning(table.c.delivery_id)
            ).first()
            if inserted is not None:
                return DeliveryClaim("acquired", token=token)
            taken = session.execute(
                update(table)
                .where(
                    *key,
                    table.c.status == "pending",
                    or_(
                        table.c.attempt.is_(None),
                        table.c.updated_at < now - self._pending_ttl,
                    ),
                )
                .values(updated_at=now, attempt=token)
                .returning(table.c.quota_charged, table.c.thread_opened)
            ).first()
            if taken is not None:
                return DeliveryClaim(
                    "acquired",
                    token=token,
                    quota_charged=bool(taken.quota_charged),
                    thread_opened=bool(taken.thread_opened),
                )
            row = session.execute(select(table.c.status, table.c.reply).where(*key)).first()
        if row is None:
            # Se purgó entre las dos sentencias: que el relé reintente.
            return DeliveryClaim("pending")
        return DeliveryClaim("done" if row.status == "done" else "pending", row.reply)

    def finish(
        self, owner_key: str, delivery_id: str, reply: str | None, token: str | None = None
    ) -> None:
        """Marca la entrega como atendida (con ``token``, solo si la reserva es suya)."""

        table = tables.webhook_deliveries
        conditions = [table.c.owner_key == owner_key, table.c.delivery_id == delivery_id]
        if token is not None:
            conditions.append(table.c.attempt == token)
        with self._factory.begin() as session:
            session.execute(
                update(table)
                .where(*conditions)
                .values(status="done", reply=reply, updated_at=self._now(), attempt=None)
            )

    def release(
        self,
        owner_key: str,
        delivery_id: str,
        token: str | None = None,
        *,
        quota_charged: bool = False,
        thread_opened: bool = False,
    ) -> None:
        """Suelta la reserva pendiente; una entrega atendida o ajena no se toca.

        Sin efectos anotados se borra la fila; con ``quota_charged`` o ``thread_opened`` la
        fila queda pendiente y libre (``attempt`` nulo) con esas marcas para el reintento.
        """

        table = tables.webhook_deliveries
        conditions = [
            table.c.owner_key == owner_key,
            table.c.delivery_id == delivery_id,
            table.c.status == "pending",
        ]
        if token is not None:
            conditions.append(table.c.attempt == token)
        with self._factory.begin() as session:
            if not (quota_charged or thread_opened):
                session.execute(delete(table).where(*conditions))
                return
            values: dict[str, Any] = {"attempt": None, "updated_at": self._now()}
            if quota_charged:
                values["quota_charged"] = True
            if thread_opened:
                values["thread_opened"] = True
            session.execute(update(table).where(*conditions).values(**values))
