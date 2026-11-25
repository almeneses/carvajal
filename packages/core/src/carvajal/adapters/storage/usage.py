"""Cupo diario sobre Postgres: una sola sentencia, sin carreras."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables

__all__ = ["SqlUsageRepository"]


class SqlUsageRepository:
    """Implementación SQL de ``UsageRepository`` con reserva atómica.

    El ``INSERT ... ON CONFLICT DO UPDATE ... WHERE requests < limit`` concede o
    deniega en una sola sentencia: dos peticiones simultáneas del mismo
    visitante no pueden superar el cupo. No hace ``commit()``: la unidad de
    trabajo que abre la sesión decide cuándo confirmar.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def try_consume(self, owner_key: str, day: date, limit: int) -> bool:
        """Reserva una petición si queda cupo; devuelve si se concedió."""

        if limit <= 0:
            return False
        statement = (
            insert(tables.usage_day)
            .values(owner_key=owner_key, day=day, requests=1, tokens=0)
            .on_conflict_do_update(
                index_elements=[tables.usage_day.c.owner_key, tables.usage_day.c.day],
                set_={"requests": tables.usage_day.c.requests + 1},
                where=tables.usage_day.c.requests < limit,
            )
            .returning(tables.usage_day.c.requests)
        )
        return self._session.execute(statement).first() is not None

    def has_room(self, owner_key: str, day: date, limit: int) -> bool:
        """Indica si queda cupo sin reservarlo (solo lectura)."""

        if limit <= 0:
            return False
        used = self._session.scalar(
            select(tables.usage_day.c.requests).where(
                tables.usage_day.c.owner_key == owner_key,
                tables.usage_day.c.day == day,
            )
        )
        return (used or 0) < limit
