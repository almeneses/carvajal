"""Borrador de cotización de una conversación (spec 054).

Una conversación arma **una** cotización con varias líneas antes de confirmarla. El borrador
guarda las líneas (cada una es el payload de una cotización del motor, sin folio), lo último
que se le mostró a la persona (opciones, comparativo de cantidades o las referencias que
coinciden con un nombre ambiguo) y **qué respuesta espera** el asistente (``awaiting``). Con
eso, un «sí», un «no» o «la 2», dichos o escritos, se interpretan con reglas y sin el modelo.

Las candidatas de un nombre ambiguo (``choices``) se guardan con lo demás de la solicitud
(``choice_request``: cantidad, unidad, ciudad, domicilio, uso, temperatura y microondas) para
cotizar la elegida con el motor sin volver a preguntar, y con ``resume_awaiting``, la pregunta
del carrito que la lista dejó en espera. Un borrador guardado antes de estos campos se lee sin
candidatas.

El módulo es puro: no lee base de datos ni el catálogo. Las cifras del total parcial y del
resumen son sumas de los importes que el motor ya calculó; no se estiman precios.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

__all__ = [
    "DRAFT_DAYS",
    "MAX_ALTERNATIVES",
    "MAX_LINES",
    "MAX_QUANTITIES",
    "Awaiting",
    "DraftTotals",
    "QuoteDraft",
    "add_line",
    "draft_quote_payload",
    "expired_lines",
    "is_quote",
    "line_family",
    "line_label",
    "line_subtotal",
    "remove_line",
    "same_sku_line",
    "totals",
    "units_of",
]

MAX_LINES: Final = 10
MAX_QUANTITIES: Final = 4
MAX_ALTERNATIVES: Final = 4
DRAFT_DAYS: Final = 7

Awaiting = Literal[
    "eleccion",  # opciones mostradas: «esa», «la 2»
    "cantidad",  # comparativo mostrado: «con mil», «la de 500»
    "algo_mas",  # línea agregada: «¿Algo más?»
    "confirmar",  # resumen mostrado: «¿La confirmo?»
    "cambio_producto",  # «¿Cancelo los platos o los conservo?»
    "oferta",  # «está caro»: se ofreció la opción más económica
    "referencia",  # nombre ambiguo: «¿Cuál quieres?» con las candidatas numeradas
    "pedido",  # cotización registrada: «¿Hacemos el pedido?» (spec 055)
    "datos_pedido",  # pedido aceptado: nombre y correo de quien compra (spec 055)
]
State = Literal["explorando", "carrito", "resumen", "registrada", "pausada", "cancelada"]

# Lo que no hace falta guardar de cada cotización en el borrador: listas largas que solo
# sirven para la tarjeta del turno en que se calcularon.
_HEAVY_KEYS: Final = ("alternatives", "providers", "discarded", "reply")


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def is_quote(payload: Any) -> bool:
    """Cotización calculada por el motor: ``kind == "cotizacion"`` con producto principal."""

    return (
        isinstance(payload, Mapping)
        and payload.get("kind") == "cotizacion"
        and bool(payload.get("primary"))
    )


def _presentation(line: Mapping[str, Any]) -> Mapping[str, Any]:
    primary = _map(line.get("primary"))
    return _map(primary.get("presentation")) or _map(
        _map(line.get("commercial")).get("presentation")
    )


def line_subtotal(line: Mapping[str, Any]) -> Decimal | None:
    """Subtotal de productos de la línea (sin envío), tal como lo calculó el motor."""

    found = _decimal(_presentation(line).get("subtotal"))
    if found is None:
        found = _decimal(_map(line.get("commercial")).get("subtotal"))
    return found


def line_label(line: Mapping[str, Any]) -> str:
    primary = _map(line.get("primary"))
    return str(primary.get("name") or primary.get("sku") or "Producto")


def line_family(line: Mapping[str, Any]) -> str | None:
    primary = _map(line.get("primary"))
    family = primary.get("family") or _map(line.get("request")).get("family")
    return str(family) if family else None


def _units_requested(line: Mapping[str, Any]) -> int:
    primary = _map(line.get("primary"))
    value = _decimal(_presentation(line).get("units_requested") or primary.get("units_requested"))
    return int(value) if value is not None else 0


def _slim(line: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in line.items() if key not in _HEAVY_KEYS}


@dataclass(frozen=True)
class DraftTotals:
    """Totales del borrador; ``None`` donde falta un importe para afirmarlo."""

    currency: str
    products: Decimal  # suma de los subtotales con precio
    shipping: Decimal | None  # envío confirmado (``None`` si alguno está por confirmar)
    shipping_requested: bool
    total: Decimal | None
    priced: int  # líneas con precio
    unpriced: tuple[str, ...]  # líneas sin precio (no entran al total)
    tax: str  # «IVA incluido (19 %)», «IVA según cada línea»…


@dataclass(frozen=True)
class QuoteDraft:
    """Estado de la cotización en curso de una conversación (inmutable)."""

    lines: tuple[dict[str, Any], ...] = ()
    state: State = "explorando"
    awaiting: Awaiting | None = None
    # Lo último que se mostró y contra lo que se resuelven las respuestas cortas.
    options: tuple[dict[str, Any], ...] = ()  # cotizaciones: recomendada + alternativas
    comparison: tuple[dict[str, Any], ...] = ()  # cotizaciones del mismo SKU, otras cantidades
    # Referencias que coinciden con un nombre ambiguo ({"sku", "name", ...}), en el orden en
    # que se listaron, y la solicitud con la que se cotiza la que se elija.
    choices: tuple[dict[str, Any], ...] = ()
    choice_request: dict[str, Any] | None = None
    # Pregunta del carrito («¿Algo más?» o «¿La confirmo?») que la lista dejó en espera:
    # vuelve si la respuesta a la lista no elige ninguna.
    resume_awaiting: Awaiting | None = None
    current: dict[str, Any] | None = None  # producto en curso {"family", "label"}
    pending_request: str | None = None  # pedido nuevo mientras se pregunta el cambio
    switch_drops_last: bool = False  # el cambio cancela la última línea, no un producto en curso
    offer: dict[str, Any] | None = None  # opción más económica ofrecida tras «está caro»
    cheaper_offered: bool = False
    off_flow: int = 0  # turnos seguidos fuera del flujo de cotización
    folio: str | None = None
    # Spec 055: número del pedido en el CRM (``S00012``) y datos de quien compra mientras se
    # piden (``{"name", "email"}``); se vacían al crear el pedido.
    order: str | None = None
    buyer: dict[str, Any] | None = None
    revision: int = 0
    updated_at: str = ""

    # --- estado -------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """Hay algo en curso que vale la pena retomar o recordar."""

        if self.state in {"registrada", "cancelada"}:
            return False
        return bool(self.lines or self.options or self.comparison or self.choices or self.current)

    def expired(self, now: datetime | None = None) -> bool:
        if not self.updated_at:
            return False
        try:
            moment = datetime.fromisoformat(self.updated_at)
        except ValueError:
            return True
        current = now or datetime.now(UTC)
        return current - moment > timedelta(days=DRAFT_DAYS)

    def touch(self, now: datetime | None = None, **changes: Any) -> "QuoteDraft":
        """Copia con ``changes``, nueva revisión y fecha de actualización."""

        moment = (now or datetime.now(UTC)).isoformat()
        return replace(self, **changes, revision=self.revision + 1, updated_at=moment)

    @property
    def city(self) -> str | None:
        for line in self.lines:
            if line.get("city"):
                return str(line["city"])
        return None

    # --- serialización ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["lines"] = list(self.lines)
        data["options"] = list(self.options)
        data["comparison"] = list(self.comparison)
        data["choices"] = list(self.choices)
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> "QuoteDraft":
        if not isinstance(data, Mapping):
            return cls()
        known = set(cls.__dataclass_fields__)
        values = {key: value for key, value in data.items() if key in known}
        for key in ("lines", "options", "comparison", "choices"):
            raw = values.get(key) or ()
            values[key] = tuple(dict(item) for item in raw if isinstance(item, Mapping))
        request = values.get("choice_request")
        values["choice_request"] = dict(request) if isinstance(request, Mapping) else None
        buyer = values.get("buyer")
        values["buyer"] = dict(buyer) if isinstance(buyer, Mapping) else None
        try:
            return cls(**values)
        except TypeError:
            return cls()


def add_line(draft: QuoteDraft, quote: Mapping[str, Any]) -> tuple[QuoteDraft, str]:
    """Agrega la cotización como línea: ``(borrador, "agregada" | "sumada" | "tope")``.

    El mismo SKU en la misma ciudad no se duplica: la línea nueva reemplaza a la anterior
    con la cantidad sumada ya calculada por quien llama (``"sumada"``); se cuenta como
    reemplazo cuando el SKU coincide. Más allá de :data:`MAX_LINES` no se agrega nada.
    """

    sku = str(_map(quote.get("primary")).get("sku") or "")
    for index, line in enumerate(draft.lines):
        if sku and str(_map(line.get("primary")).get("sku") or "") == sku:
            lines = (*draft.lines[:index], _slim(quote), *draft.lines[index + 1 :])
            return replace(draft, lines=lines), "sumada"
    if len(draft.lines) >= MAX_LINES:
        return draft, "tope"
    return replace(draft, lines=(*draft.lines, _slim(quote))), "agregada"


def same_sku_line(draft: QuoteDraft, quote: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """La línea del borrador con el mismo SKU que ``quote``, si existe."""

    sku = str(_map(quote.get("primary")).get("sku") or "")
    for line in draft.lines:
        if sku and str(_map(line.get("primary")).get("sku") or "") == sku:
            return line
    return None


def units_of(line: Mapping[str, Any]) -> int:
    """Unidades pedidas de una línea."""

    return _units_requested(line)


def remove_line(draft: QuoteDraft, index: int) -> QuoteDraft:
    if not 0 <= index < len(draft.lines):
        return draft
    return replace(draft, lines=(*draft.lines[:index], *draft.lines[index + 1 :]))


def totals(lines: Sequence[Mapping[str, Any]]) -> DraftTotals:
    """Total parcial y final: suma de subtotales y del envío confirmado por vendedor."""

    currency = "COP"
    products = Decimal(0)
    priced = 0
    unpriced: list[str] = []
    shipping = Decimal(0)
    shipping_known = True
    shipping_requested = False
    sellers: set[str] = set()
    taxes: set[str] = set()
    for line in lines:
        primary = _map(line.get("primary"))
        commercial = _map(line.get("commercial"))
        currency = str(primary.get("currency") or commercial.get("currency") or currency)
        subtotal = line_subtotal(line)
        if subtotal is None:
            unpriced.append(line_label(line))
            continue
        products += subtotal
        priced += 1
        tax = _map(primary.get("tax")) or _map(commercial.get("tax"))
        taxes.add(f"{tax.get('status') or 'no_indicado'}:{tax.get('rate') or ''}")
        ship = _map(commercial.get("shipping"))
        if ship.get("requested") or _map(line.get("delivery")).get("requested"):
            shipping_requested = True
            seller = str(_map(primary.get("provider")).get("id") or primary.get("seller") or "")
            if seller in sellers:
                continue  # un envío por vendedor
            sellers.add(seller)
            cost = _decimal(ship.get("cost"))
            if ship.get("status") == "confirmed" and (cost is not None or ship.get("free")):
                shipping += cost or Decimal(0)
            else:
                shipping_known = False
    shipping_total = shipping if shipping_known else None
    if not shipping_requested:
        total: Decimal | None = products
    else:
        total = None if shipping_total is None else products + shipping_total
    return DraftTotals(
        currency=currency,
        products=products,
        shipping=shipping_total if shipping_requested else Decimal(0),
        shipping_requested=shipping_requested,
        total=total if priced else None,
        priced=priced,
        unpriced=tuple(unpriced),
        tax=_tax_label(taxes),
    )


def _tax_label(taxes: set[str]) -> str:
    if len(taxes) != 1:
        return "IVA según cada producto" if taxes else "IVA no indicado"
    status, _, rate = next(iter(taxes)).partition(":")
    if status == "incluido":
        number = _decimal(rate)
        if number is not None:
            percent = number * 100 if number < 1 else number
            return f"IVA incl. ({percent.normalize():f} %)"
        return "IVA incl."
    if status == "no_incluido":
        return "IVA no incluido"
    return "IVA no indicado"


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def draft_quote_payload(draft: QuoteDraft) -> dict[str, Any]:
    """Una sola cotización (``kind == "cotizacion"``) con todas las líneas del borrador.

    Conserva ``primary``, ``commercial`` y ``city`` de la primera línea para los lectores de
    la 042-053 (tarjeta, consultar_cotizacion, PDF de una línea) y agrega ``lines`` con cada
    cotización y los totales del conjunto. El folio lo pone quien registra.
    """

    if not draft.lines:
        raise ValueError("El borrador no tiene líneas.")
    first = draft.lines[0]
    summary = totals(draft.lines)
    commercial = dict(_map(first.get("commercial")))
    valid = [
        str(_map(line.get("commercial")).get("valid_until"))
        for line in draft.lines
        if _map(line.get("commercial")).get("valid_until")
    ]
    commercial.update(
        {
            "subtotal": _text(summary.products),
            "total": _text(summary.total),
            "valid_until": min(valid) if valid else None,
            "pending_lines": [],
        }
    )
    return {
        **{key: value for key, value in first.items() if key != "status"},
        "commercial": commercial,
        "total": _text(summary.total),
        "lines": [dict(line) for line in draft.lines],
        "totals": {
            "currency": summary.currency,
            "products": _text(summary.products),
            "shipping": _text(summary.shipping),
            "shipping_requested": summary.shipping_requested,
            "total": _text(summary.total),
            "tax": summary.tax,
            "unpriced": list(summary.unpriced),
        },
    }


def expired_lines(draft: QuoteDraft, today: date) -> list[str]:
    """Líneas cuya vigencia del vendedor ya pasó (hay que recalcularlas antes de registrar)."""

    stale: list[str] = []
    for line in draft.lines:
        valid = str(_map(line.get("commercial")).get("valid_until") or "")[:10]
        if valid and valid < today.isoformat():
            stale.append(line_label(line))
    return stale
