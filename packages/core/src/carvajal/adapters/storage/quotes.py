"""Repositorio de cotizaciones emitidas (spec 042).

La fila guarda lo que se mostró, no lo que el motor volvería a calcular: si un
precio cambia mañana, la cotización de ayer sigue siendo auditable. Igual que el
resto de repositorios, recibe una ``Session`` ajena y solo hace ``flush()``.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables

__all__ = ["QuoteRecord", "SqlQuoteRepository"]


class QuoteRecord(dict):
    """Cotización guardada, como diccionario listo para la vista."""


class SqlQuoteRepository:
    """Emite y recupera cotizaciones de un dueño."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        quote_id: str,
        owner_key: str,
        *,
        payload: Mapping[str, Any],
        total: Decimal,
        currency: str,
        valid_until: date,
        conversation_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        """Guarda la cotización con su vigencia y el payload mostrado."""

        self._session.execute(
            insert(tables.quotes).values(
                id=quote_id,
                owner_key=owner_key,
                conversation_id=conversation_id,
                message_id=message_id,
                valid_until=valid_until,
                total=total,
                currency=currency,
                payload=dict(payload),
            )
        )
        self._session.flush()

    def get(self, quote_id: str, owner_key: str) -> QuoteRecord | None:
        """Cotización del dueño; nunca cruza identidades."""

        row = (
            self._session.execute(
                select(tables.quotes)
                .where(
                    tables.quotes.c.id == quote_id,
                    tables.quotes.c.owner_key == owner_key,
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else QuoteRecord(row)
