"""Ampliación del catálogo de Colombia con las referencias de México.

Decisión del equipo del 2026-11-25 (uso académico), que enmienda la spec 039: las referencias
de México se copian a Colombia con su precio convertido de MXN a COP con una tasa fechada.

Lo que no cambia: el precio convertido nunca se presenta como publicado en Colombia. Lleva la
procedencia ``sistema`` (lo calcula el proyecto), no tiene vendedor, sede ni existencias, y
su fuente es la ficha de México de la que sale el importe original. El IVA queda
``no_indicado``: el precio de origen incluye el IVA de México, no el de Colombia.

La tasa vive en ``fuentes/tasa_mxn_cop.json``: cambiarla y volver a correr
``scripts/adaptar_catalogo_mx.py`` recalcula todo el bloque.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from carvajal.catalog.models import ADAPTED_PREFIX, Reference, ReferencePrice

__all__ = [
    "RATE_FILE",
    "SOURCE_NAME",
    "ExchangeRate",
    "adapt_all",
    "adapted_reference",
    "adapted_sku",
    "convert_price",
    "converted_price",
    "load_rate",
]

RATE_FILE = Path("fuentes") / "tasa_mxn_cop.json"
SOURCE_NAME = "Conversión del catálogo de México"


@dataclass(frozen=True)
class ExchangeRate:
    """Pesos colombianos por peso mexicano, por cruce de dos tasas publicadas y fechadas."""

    date: date
    cop_per_usd: Decimal
    mxn_per_usd: Decimal

    @property
    def cop_per_mxn(self) -> Decimal:
        """Tasa cruzada con cuatro decimales: la misma que se muestra y con la que se calcula."""

        return (self.cop_per_usd / self.mxn_per_usd).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )


def load_rate(directory: Path) -> ExchangeRate:
    """Tasa versionada en ``fuentes/tasa_mxn_cop.json``."""

    data = json.loads((directory / RATE_FILE).read_text(encoding="utf-8"))
    rate = ExchangeRate(
        date=date.fromisoformat(data["date"]),
        cop_per_usd=Decimal(data["cop_per_usd"]["value"]),
        mxn_per_usd=Decimal(data["mxn_per_usd"]["value"]),
    )
    if rate.cop_per_usd <= 0 or rate.mxn_per_usd <= 0:
        raise ValueError(f"{RATE_FILE}: las tasas deben ser positivas")
    return rate


def adapted_sku(sku: str) -> str:
    """SKU de la copia: propio, para no confundirla con la referencia de México."""

    return f"{ADAPTED_PREFIX}{sku}"


def adapted_reference(reference: Reference) -> Reference:
    """Copia de una referencia de México para Colombia, sin el precio inline en MXN."""

    if reference.country != "MX":
        raise ValueError(f"{reference.sku} no es una referencia de México")
    return reference.model_copy(
        update={
            "sku": adapted_sku(reference.sku),
            "country": "CO",
            "price": None,
            "currency": None,
            "tax_included": None,
            "price_valid_until": None,
        }
    )


def convert_price(amount: Decimal, rate: ExchangeRate) -> Decimal:
    """Importe en COP, redondeado al peso."""

    return (amount * rate.cop_per_mxn).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def converted_price(reference: Reference, rate: ExchangeRate) -> ReferencePrice | None:
    """Precio por caja convertido de una referencia de México; ``None`` si no publica precio.

    Sin ``valid_until``: la vigencia de la ficha es del precio en MXN, no de la conversión.
    """

    if reference.price is None or reference.currency != "MXN":
        return None
    return ReferencePrice(
        sku=adapted_sku(reference.sku),
        country="CO",
        amount=convert_price(reference.price, rate),
        currency="COP",
        tax_status="no_indicado",
        source_type="sistema",
        source_name=SOURCE_NAME,
        source_url=reference.source_url,
        captured_at=rate.date,
    )


def adapt_all(
    references: Iterable[Reference], rate: ExchangeRate
) -> tuple[list[Reference], list[ReferencePrice]]:
    """Copias y precios convertidos de todas las referencias de México, en orden de SKU."""

    mexico = sorted((item for item in references if item.country == "MX"), key=lambda r: r.sku)
    prices = (converted_price(item, rate) for item in mexico)
    return [adapted_reference(item) for item in mexico], [item for item in prices if item]
