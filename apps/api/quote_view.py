"""Vista de lectura de una cotización emitida, común a WhatsApp y al PDF.

Lee el payload guardado en ``quotes`` (el mismo que pinta la web) y lo reduce a campos de
presentación ya formateados. No calcula, no estima y no convierte: si un importe falta en
el payload, el campo queda ``None`` y quien lo muestra declara que está pendiente.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = ["QuoteLine", "QuoteView", "format_date", "format_money", "quote_view"]

_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_BASIS = {
    "caja": ("caja", "cajas"),
    "paquete": ("paquete", "paquetes"),
    "paca": ("paca", "pacas"),
    "unidad": ("unidad", "unidades"),
    "bolsa": ("bolsa", "bolsas"),
}
_TAX = {"incluido": "Incluido", "no_incluido": "No incluido", "no_indicado": "No indicado"}


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _decimal(value)
    return None if number is None else int(number)


def format_money(value: Any, currency: str = "COP") -> str | None:
    """``$204.370`` (COP sin decimales cuando son cero); ``None`` si no hay importe."""

    number = _decimal(value)
    if number is None:
        return None
    if number == number.to_integral_value():
        text = f"{int(number):,}".replace(",", ".")
    else:
        whole, _, cents = f"{number:,.2f}".partition(".")
        text = f"{whole.replace(',', '.')},{cents}"
    return f"${text}" if currency.upper() in {"COP", "MXN", "USD"} else f"{text} {currency}"


def format_date(value: Any) -> str | None:
    """``18 nov 2026`` desde ``date``, ``datetime`` o ISO; ``None`` si no se puede leer."""

    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    elif isinstance(value, str) and value.strip():
        try:
            day = date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    else:
        return None
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _plural(basis: str, count: int | None) -> str:
    singular, plural = _BASIS.get(basis.lower(), (basis, f"{basis}s"))
    return singular if count == 1 else plural


@dataclass(frozen=True)
class QuoteLine:
    """Una línea de una cotización con varios productos (spec 054)."""

    product: str
    sku: str = ""
    units_requested: int | None = None
    presentations: int | None = None
    presentations_label: str = ""
    units_per_presentation: int | None = None
    subtotal: str | None = None
    seller: str = ""


@dataclass(frozen=True)
class QuoteView:
    """Campos de presentación de una cotización emitida."""

    folio: str
    currency: str
    product: str
    sku: str = ""
    brand: str = ""
    material: str = ""
    city: str = ""
    seller: str = ""
    seller_address: str = ""
    seller_phone: str = ""
    basis: str = ""
    units_per_presentation: int | None = None
    price_per_presentation: str | None = None
    units_requested: int | None = None
    presentations: int | None = None
    presentations_label: str = ""
    units_bought: int | None = None
    units_surplus: int | None = None
    unit_price: str | None = None
    subtotal: str | None = None
    tax: str = "No indicado"
    shipping: str = "No solicitado"
    total: str | None = None
    source_name: str = ""
    source_url: str = ""
    price_date: str | None = None
    issued: str | None = None
    valid_until: str | None = None
    valid_until_date: date | None = None
    warning: str = ""
    # Precio de referencia del catálogo, sin vendedor (``price_kind == "convertido"``).
    converted: bool = False
    pending: Sequence[str] = field(default_factory=tuple)
    # Spec 054: con dos o más productos, una línea por producto bajo el mismo folio.
    lines: tuple[QuoteLine, ...] = ()


def _shipping(commercial: Mapping[str, Any], delivery: Mapping[str, Any], currency: str) -> str:
    shipping = _map(commercial.get("shipping"))
    status = str(shipping.get("status") or "")
    if shipping.get("free") or delivery.get("free"):
        return "Gratis"
    cost = format_money(shipping.get("cost"), currency)
    if cost:
        return cost
    if status == "not_requested" or shipping.get("requested") is False:
        return "No solicitado"
    return "Por confirmar con el vendedor"


def _tax(tax: Mapping[str, Any]) -> str:
    text = _TAX.get(str(tax.get("status") or "no_indicado"), "No indicado")
    rate = _decimal(tax.get("rate"))
    if rate is not None and text != "No indicado":
        percent = rate * 100 if rate < 1 else rate
        text += f" ({percent.normalize():f} %)"
    return text


def _pending(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    lines: list[str] = []
    for item in values:
        if isinstance(item, str) and item.strip():
            lines.append(item.strip())
        elif isinstance(item, Mapping):
            text = item.get("label") or item.get("reason") or item.get("text")
            if text:
                lines.append(str(text))
    return tuple(lines)


def quote_view(payload: Mapping[str, Any]) -> QuoteView | None:
    """``QuoteView`` del payload guardado; ``None`` si no es una cotización emitida."""

    primary = _map(payload.get("primary"))
    if payload.get("kind") != "cotizacion" or not primary:
        return None
    commercial = _map(payload.get("commercial"))
    presentation = _map(primary.get("presentation")) or _map(commercial.get("presentation"))
    issued = _map(payload.get("quote"))
    source = _map(primary.get("source")) or _map(commercial.get("source"))
    provider = _map(primary.get("provider"))
    currency = str(primary.get("currency") or commercial.get("currency") or "COP")
    basis = str(presentation.get("basis") or "")
    count = _int(presentation.get("count"))
    valid_raw = issued.get("valid_until") or issued.get("retention_until")
    try:
        valid_date = date.fromisoformat(str(valid_raw)[:10]) if valid_raw else None
    except ValueError:
        valid_date = None
    return QuoteView(
        folio=str(issued.get("folio") or issued.get("id") or ""),
        currency=currency,
        product=str(primary.get("name") or primary.get("sku") or "Producto"),
        sku=str(primary.get("sku") or ""),
        brand=str(primary.get("brand") or ""),
        material=str(primary.get("material") or ""),
        city=str(payload.get("city") or provider.get("city") or ""),
        seller=str(primary.get("seller") or provider.get("name") or source.get("name") or ""),
        seller_address=str(provider.get("address") or ""),
        seller_phone=str(provider.get("phone") or ""),
        basis=basis,
        units_per_presentation=_int(presentation.get("units")),
        price_per_presentation=format_money(presentation.get("price_per_presentation"), currency),
        units_requested=_int(presentation.get("units_requested") or primary.get("units_requested")),
        presentations=count,
        presentations_label=_plural(basis, count) if basis else "",
        units_bought=_int(presentation.get("units_bought")),
        units_surplus=_int(presentation.get("units_surplus")),
        unit_price=format_money(presentation.get("unit_price"), currency),
        subtotal=format_money(
            # Spec 054: con varias líneas, el subtotal es el del conjunto.
            commercial.get("subtotal")
            if isinstance(payload.get("lines"), list) and len(payload["lines"]) > 1
            else presentation.get("subtotal", commercial.get("subtotal")),
            currency,
        ),
        tax=_tax(_map(primary.get("tax")) or _map(commercial.get("tax"))),
        shipping=_shipping(commercial, _map(payload.get("delivery")), currency),
        total=format_money(payload.get("total"), currency),
        source_name=str(source.get("name") or ""),
        source_url=str(source.get("url") or primary.get("source_url") or ""),
        price_date=format_date(source.get("captured_at") or primary.get("captured_at")),
        issued=format_date(issued.get("issued_at")),
        valid_until=format_date(valid_date),
        valid_until_date=valid_date,
        warning=str(primary.get("warning") or ""),
        converted=primary.get("price_kind") == "convertido",
        pending=_pending(payload.get("pending_lines") or commercial.get("pending_lines")),
        lines=_lines(payload.get("lines"), currency),
    )


def _lines(raw: Any, currency: str) -> tuple[QuoteLine, ...]:
    """Líneas de una cotización de varios productos; ``()`` si es de uno solo."""

    if not isinstance(raw, list) or len(raw) < 2:
        return ()
    lines: list[QuoteLine] = []
    for item in raw:
        line = _map(item)
        primary = _map(line.get("primary"))
        presentation = _map(primary.get("presentation"))
        count = _int(presentation.get("count"))
        basis = str(presentation.get("basis") or "")
        lines.append(
            QuoteLine(
                product=str(primary.get("name") or primary.get("sku") or "Producto"),
                sku=str(primary.get("sku") or ""),
                units_requested=_int(
                    presentation.get("units_requested") or primary.get("units_requested")
                ),
                presentations=count,
                presentations_label=_plural(basis, count) if basis else "",
                units_per_presentation=_int(presentation.get("units")),
                subtotal=format_money(presentation.get("subtotal"), currency),
                seller=str(
                    primary.get("seller") or _map(primary.get("provider")).get("name") or ""
                ),
            )
        )
    return tuple(lines)
