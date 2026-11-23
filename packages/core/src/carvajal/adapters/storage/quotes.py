"""Repositorio de cotizaciones emitidas (spec 042; contrato versionado de la 043).

La fila guarda lo que se mostró, no lo que el motor volvería a calcular: si un
precio cambia mañana, la cotización de ayer sigue siendo auditable. Las filas de
la 042 (``version=1``) se leen con el mismo contrato normalizado que las nuevas
(``version=2``), aunque su payload no separara importes ni vigencias.

Igual que el resto de repositorios, recibe una ``Session`` ajena y solo hace
``flush()``.
"""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables

__all__ = ["Provenance", "QuoteRecord", "SqlQuoteRepository", "classify_quote_payload"]

Provenance = Literal["real", "simulado", "ambiguo"]
_SIMULATED_FIELDS = frozenset({"simulated", "price_simulated", "stock_simulated", "tax_simulated"})
_DEMO_MARKERS = ("data/demo/cotizador", "COTIZADOR_DEMO", "Cotizador demo")


def _has_simulation(value: Any) -> bool:
    """Cualquier marca de simulación en el payload, sin importar su profundidad."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _SIMULATED_FIELDS and item is True:
                return True
            if key == "origin" and item == "demo":
                return True
            if key in {"type", "source_type"} and item == "demo":
                return True
            if key == "kind" and item == "oferta_demo":
                return True
            if _has_simulation(item):
                return True
        return False
    if isinstance(value, list | tuple):
        return any(_has_simulation(item) for item in value)
    if isinstance(value, str):
        return any(marker in value for marker in _DEMO_MARKERS)
    return False


def _has_published_source(payload: Mapping[str, Any]) -> bool:
    """Un precio publicado debe poder atribuirse a una URL y una fecha."""

    primary = payload.get("primary") or {}
    commercial = payload.get("commercial") or {}
    source = commercial.get("source") or {}
    return bool(
        (primary.get("source_url") and primary.get("captured_at"))
        or (source.get("url") and source.get("captured_at"))
    )


def classify_quote_payload(
    payload: Mapping[str, Any] | None,
    *,
    owner_key: str | None,
    folio: str | None = None,
) -> Provenance:
    """Clasifica una cotización histórica con la matriz de ``data-model.md``.

    ``simulado`` gana si cualquier campo o alternativa lleva marca demo; ``ambiguo``
    cubre la falta de dueño, folio o fuente; solo entonces el registro es ``real``.
    """

    if not isinstance(payload, Mapping):
        return "ambiguo"
    if _has_simulation(payload):
        return "simulado"
    quote = payload.get("quote") or {}
    resolved = folio or quote.get("id") or quote.get("folio")
    if not owner_key or not resolved:
        return "ambiguo"
    if payload.get("kind") == "cotizacion" and not _has_published_source(payload):
        return "ambiguo"
    return "real"


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


class QuoteRecord(dict):
    """Cotización guardada, normalizada para la vista y la auditoría.

    Las propiedades no dependen de la versión: una fila histórica de la 042 y una
    emitida con el contrato de la 043 devuelven los mismos nombres. Un importe
    ausente se conserva como ``None``; nunca se rellena con una estimación.
    """

    @property
    def version(self) -> int:
        """Versión del payload; las filas anteriores a la 043 son la 1."""

        column = int(self.get("version") or 1)
        payload = int(self.payload.get("version") or 0)
        return max(column, payload)

    @property
    def payload(self) -> Mapping[str, Any]:
        """Payload mostrado en el turno."""

        return self.get("payload") or {}

    @property
    def folio(self) -> str:
        """Folio de la cotización."""

        return str(self.get("id") or "")

    @property
    def owner_key(self) -> str:
        """Dueño firmado que emitió la cotización."""

        return str(self.get("owner_key") or "")

    @property
    def provenance(self) -> Provenance:
        """Estado real/simulado/ambiguo para la lectura autorizada."""

        return classify_quote_payload(
            self.payload,
            owner_key=self.owner_key,
            folio=self.folio,
        )

    @property
    def commercial(self) -> Mapping[str, Any]:
        """Contrato versionado; para la 042 se deriva del payload y las columnas."""

        stored = self.payload.get("commercial")
        if self.version >= 2 and isinstance(stored, Mapping):
            return stored
        primary = self.payload.get("primary") or {}
        delivery = self.payload.get("delivery") or {}
        return {
            "presentation": primary.get("presentation") or {},
            "tax": {
                "status": self.get("tax_status") or primary.get("tax_status"),
                "rate": self.get("tax_rate"),
            },
            "shipping": {
                "requested": bool(delivery.get("requested")),
                "status": "confirmed" if delivery.get("covered") else "pending",
                "cost": delivery.get("ship_cost"),
            },
            "subtotal": self.get("subtotal") or (primary.get("presentation") or {}).get("subtotal"),
            "total": self.get("total"),
            "valid_until": None,
        }

    @property
    def currency(self) -> str | None:
        value = self.get("currency") or (self.payload.get("primary") or {}).get("currency")
        return None if value is None else str(value)

    @property
    def presentation(self) -> Mapping[str, Any]:
        return self.commercial.get("presentation") or {}

    @property
    def subtotal(self) -> Decimal | None:
        return _decimal(self.commercial.get("subtotal"))

    @property
    def total(self) -> Decimal | None:
        """Total final; ``None`` cuando falta un componente necesario."""

        return _decimal(self.commercial.get("total"))

    @property
    def shipping_cost(self) -> Decimal | None:
        shipping = self.commercial.get("shipping") or {}
        return _decimal(shipping.get("cost"))

    @property
    def tax_status(self) -> str | None:
        tax = self.commercial.get("tax") or {}
        value = tax.get("status")
        return None if value is None else str(value)

    @property
    def tax_rate(self) -> Decimal | None:
        tax = self.commercial.get("tax") or {}
        return _decimal(tax.get("rate"))

    @property
    def issued_at(self) -> datetime | None:
        value = self.get("issued_at") or self.get("created_at")
        return value if isinstance(value, datetime) else None

    @property
    def commercial_valid_until(self) -> date | None:
        """Vigencia publicada por el vendedor; ausente si no la declaró."""

        commercial = self.payload.get("commercial") or {}
        return _date(self.get("commercial_valid_until") or commercial.get("valid_until"))

    @property
    def amounts(self) -> dict[str, Decimal | None]:
        """Importes separados, listos para una vista o una exportación."""

        return {
            "subtotal": self.subtotal,
            "tax_rate": self.tax_rate,
            "shipping_cost": self.shipping_cost,
            "total": self.total,
        }


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
        total: Decimal | None,
        currency: str,
        valid_until: date,
        version: int = 1,
        issued_at: datetime | None = None,
        subtotal: Decimal | None = None,
        tax_status: str | None = None,
        tax_rate: Decimal | None = None,
        shipping_cost: Decimal | None = None,
        commercial_valid_until: date | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        """Guarda la cotización con sus importes separados y el payload mostrado."""

        self._session.execute(
            insert(tables.quotes).values(
                id=quote_id,
                owner_key=owner_key,
                conversation_id=conversation_id,
                message_id=message_id,
                version=version,
                issued_at=issued_at,
                valid_until=valid_until,
                commercial_valid_until=commercial_valid_until,
                subtotal=subtotal,
                tax_status=tax_status,
                tax_rate=tax_rate,
                shipping_cost=shipping_cost,
                total=total,
                currency=currency,
                payload=dict(payload),
            )
        )
        self._session.flush()

    def _row(self, *conditions: Any) -> QuoteRecord | None:
        row = (
            self._session.execute(select(tables.quotes).where(*conditions).limit(1))
            .mappings()
            .one_or_none()
        )
        return None if row is None else QuoteRecord(row)

    def get(self, quote_id: str, owner_key: str) -> QuoteRecord | None:
        """Cotización del dueño por folio; nunca cruza identidades."""

        return self._row(
            tables.quotes.c.id == quote_id,
            tables.quotes.c.owner_key == owner_key,
        )

    def by_message(self, message_id: str, owner_key: str) -> QuoteRecord | None:
        """Cotización vinculada a un mensaje concreto del dueño."""

        return self._row(
            tables.quotes.c.message_id == message_id,
            tables.quotes.c.owner_key == owner_key,
        )

    def for_conversation(self, conversation_id: str, owner_key: str) -> list[QuoteRecord]:
        """Todas las cotizaciones del hilo y dueño, para clasificar su historial."""

        rows = (
            self._session.execute(
                select(tables.quotes)
                .where(
                    tables.quotes.c.conversation_id == conversation_id,
                    tables.quotes.c.owner_key == owner_key,
                )
                .order_by(tables.quotes.c.created_at)
            )
            .mappings()
            .all()
        )
        return [QuoteRecord(row) for row in rows]

    def resolve(
        self,
        quote_id: str,
        owner_key: str,
        *,
        message_id: str | None = None,
    ) -> QuoteRecord | None:
        """Lee por mensaje y cae al folio y dueño cuando la relación falta.

        Los históricos de la 042 no siempre tienen ``message_id``; el folio del
        payload y el dueño firmado siguen identificando la cotización.
        """

        if message_id:
            related = self.by_message(message_id, owner_key)
            if related is not None:
                return related
        return self.get(quote_id, owner_key)
