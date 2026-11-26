"""Pedido a partir de una cotización registrada (spec 055): comprador y líneas.

Después del folio, el asistente ofrece hacer el pedido. Si la persona acepta, pide nombre y
correo; con los dos, el servicio del chat crea el pedido de venta en el CRM (Odoo) y el CRM
envía la confirmación por correo.

El módulo es puro: interpreta el texto de la persona y traduce las líneas del borrador a
líneas de pedido con los importes que el motor ya calculó (precio por presentación y número
de presentaciones). No llama al CRM ni estima precios.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from carvajal.agent.commercial_intent import product_family
from carvajal.agent.followup import parse_quantities, plain
from carvajal.quotation.draft import line_label, totals

__all__ = [
    "SHIPPING_CODE",
    "Buyer",
    "OrderLine",
    "OrderRequest",
    "declines_order",
    "merge_buyer",
    "order_lines",
    "wants_purchase",
]

SHIPPING_CODE: Final = "ENVIO-CARVAJAL"
MAX_NAME: Final = 80

_EMAIL: Final = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Lo que acompaña al nombre y no es parte de él: «me llamo», «mi correo es», «a nombre de»…
_NAME_NOISE: Final = re.compile(
    r"\b(?:hola|buenas|listo|ok|dale|claro|si|sí|mi\s+nombre\s+es|me\s+llamo|soy|"
    r"a\s+nombre\s+de|nombre|y\s+mi\s+correo\s+es|mi\s+correo\s+es|mi\s+correo|correo|"
    r"electronico|electrónico|email|e-mail|mail|es|y)\b[:,]?",
    re.IGNORECASE,
)
_LETTERS: Final = re.compile(r"[^\W\d_]", re.UNICODE)

# «Quiero comprarla», «hagamos el pedido», «la compro»: aceptar el pedido con otras palabras.
_PURCHASE: Final = re.compile(
    r"\b(?:compr(?:o|ar|arla|arlo|arlas|arlos|amos|emos)|la\s+compro|lo\s+compro|"
    r"(?:hac|ha[gz])\w*\s+(?:el\s+|un\s+)?pedido|pedirl[ao]s?|ordenar|ordenarl[ao]s?|"
    r"orden\s+de\s+compra|quiero\s+(?:el\s+)?pedido|confirm\w*\s+(?:la\s+)?compra)\b"
)
_DECLINE: Final = frozenset(
    {
        "no",
        "nop",
        "no gracias",
        "ahora no",
        "por ahora no",
        "no por ahora",
        "todavia no",
        "aun no",
        "despues",
        "luego",
        "mas tarde",
        "lo pienso",
        "lo voy a pensar",
        "cancelar",
        "cancela",
    }
)


@dataclass(frozen=True)
class Buyer:
    """Datos de quien compra: los dos son obligatorios para crear el pedido."""

    name: str | None = None
    email: str | None = None

    @property
    def complete(self) -> bool:
        return bool(self.name and self.email)

    def to_json(self) -> dict[str, str | None]:
        return {"name": self.name, "email": self.email}

    @classmethod
    def from_json(cls, data: Any) -> "Buyer":
        if not isinstance(data, Mapping):
            return cls()
        name = data.get("name")
        email = data.get("email")
        return cls(
            name=str(name) if name else None,
            email=str(email) if email else None,
        )


@dataclass(frozen=True)
class OrderLine:
    """Línea del pedido: ``quantity`` presentaciones a ``price_unit`` cada una."""

    code: str  # SKU (referencia interna del producto en el CRM)
    name: str  # nombre del producto, para crearlo si el CRM no lo tiene
    description: str  # texto de la línea en el pedido
    quantity: Decimal
    price_unit: Decimal
    service: bool = False  # el envío es un servicio, no un producto


@dataclass(frozen=True)
class OrderRequest:
    """Todo lo que el CRM necesita para el pedido; ``folio`` lo hace idempotente."""

    folio: str
    buyer: Buyer
    channel: str  # «chat web» o «WhatsApp»
    city: str | None
    lines: tuple[OrderLine, ...]
    total: Decimal | None
    notes: tuple[str, ...] = ()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def wants_purchase(text: str) -> bool:
    """«Quiero comprarla», «hagamos el pedido»: aceptar el pedido sin decir «sí».

    «Quiero comprar 500 vasos» es otra cotización, no el pedido de la registrada.
    """

    if "?" in text or product_family(text) or parse_quantities(text):
        return False
    return _PURCHASE.search(plain(text)) is not None


def declines_order(text: str) -> bool:
    """«No», «ahora no», «lo voy a pensar»: la cotización queda registrada, sin pedido."""

    return plain(text).strip(" .!") in _DECLINE


def _name_from(text: str) -> str | None:
    rest = _EMAIL.sub(" ", text)
    rest = _NAME_NOISE.sub(" ", rest)
    rest = re.sub(r"[\s,;:|/()<>\"'.!-]+", " ", rest).strip()
    if len(_LETTERS.findall(rest)) < 2 or any(char.isdigit() for char in rest):
        return None
    words = rest.split()
    if len(words) > 6:  # una frase larga no es un nombre
        return None
    return " ".join(word if word.isupper() else word.capitalize() for word in words)[:MAX_NAME]


def merge_buyer(previous: Buyer, text: str) -> Buyer:
    """Los datos de ``previous`` completados con lo que trae ``text`` (correo y nombre)."""

    found = _EMAIL.search(text)
    email = found.group(0).lower() if found else previous.email
    name = _name_from(text) or previous.name
    return Buyer(name=name, email=email)


def _plural(basis: str, count: Decimal) -> str:
    if count == 1 or not basis:
        return basis or "unidad"
    return basis + ("es" if basis.endswith(("n", "r", "l", "d")) else "s")


def _line(line: Mapping[str, Any]) -> OrderLine:
    primary = _map(line.get("primary"))
    commercial = _map(line.get("commercial"))
    presentation = _map(primary.get("presentation")) or _map(commercial.get("presentation"))
    sku = str(primary.get("sku") or "SIN-SKU")
    name = line_label(line)
    count = _decimal(presentation.get("count")) or Decimal(1)
    price = _decimal(presentation.get("price_per_presentation"))
    units = presentation.get("units")
    bought = presentation.get("units_bought")
    basis = str(presentation.get("basis") or "caja")
    parts = [f"[{sku}] {name}"]
    if units and bought:
        parts.append(f"{count:f} {_plural(basis, count)} de {units} u. ({bought} unidades)")
    seller = primary.get("seller")
    if seller:
        parts.append(f"Vendedor: {seller}")
    if line.get("city"):
        parts.append(f"Ciudad: {line['city']}")
    if price is None:
        parts.append("Precio por confirmar con el vendedor")
    tax = _map(primary.get("tax"))
    if tax.get("status") == "incluido":
        parts.append("IVA incluido")
    elif tax.get("status") == "no_incluido":
        parts.append("IVA no incluido")
    return OrderLine(
        code=sku,
        name=name,
        description=" · ".join(parts),
        quantity=count,
        price_unit=price if price is not None else Decimal(0),
    )


def order_lines(lines: Sequence[Mapping[str, Any]]) -> tuple[tuple[OrderLine, ...], list[str]]:
    """Líneas del pedido (productos y, si está confirmado, el envío) y notas pendientes."""

    result = [_line(line) for line in lines]
    notes: list[str] = []
    summary = totals(lines)
    if summary.unpriced:
        notes.append("Sin precio publicado: " + ", ".join(summary.unpriced) + ".")
    if summary.shipping_requested:
        if summary.shipping is None:
            notes.append("Envío por confirmar con el vendedor.")
        elif summary.shipping > 0:
            result.append(
                OrderLine(
                    code=SHIPPING_CODE,
                    name="Envío",
                    description="Envío a domicilio (costo declarado por el vendedor)",
                    quantity=Decimal(1),
                    price_unit=summary.shipping,
                    service=True,
                )
            )
    notes.append(f"Impuestos: {summary.tax}.")
    return tuple(result), notes
