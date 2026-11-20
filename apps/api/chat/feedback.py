"""Marcas del visitante sobre una respuesta (spec 038, US4).

La marca se identifica por el ``request_id`` del turno y es idempotente: volver
a marcar actualiza el valor en lugar de duplicar la fila. Nunca guarda texto ni
fuerza el muestreo del monitoreo.
"""

from dataclasses import dataclass

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables

__all__ = ["Feedback", "SqlFeedbackRepository"]


@dataclass(frozen=True)
class Feedback:
    """Marca guardada: identificador del turno y si la respuesta sirvió."""

    request_id: str
    useful: bool


class SqlFeedbackRepository:
    """Altas y lecturas de ``chat_feedback`` sobre una sesión síncrona."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, request_id: str, useful: bool) -> None:
        """Guarda o actualiza la marca del turno (upsert idempotente)."""

        tabla = tables.chat_feedback
        existente = self._session.execute(
            select(tabla.c.request_id).where(tabla.c.request_id == request_id)
        ).first()
        if existente is not None:
            self._session.execute(
                update(tabla).where(tabla.c.request_id == request_id).values(useful=useful)
            )
            return
        try:
            with self._session.begin_nested():
                self._session.execute(insert(tabla).values(request_id=request_id, useful=useful))
        except IntegrityError:
            # Otra petición insertó la misma marca entre la lectura y el alta.
            self._session.execute(
                update(tabla).where(tabla.c.request_id == request_id).values(useful=useful)
            )

    def get(self, request_id: str) -> Feedback | None:
        """Lee la marca de un turno para las pruebas y el diagnóstico."""

        tabla = tables.chat_feedback
        fila = (
            self._session.execute(
                select(tabla.c.request_id, tabla.c.useful).where(tabla.c.request_id == request_id)
            )
            .mappings()
            .first()
        )
        if fila is None:
            return None
        return Feedback(request_id=str(fila["request_id"]), useful=bool(fila["useful"]))
