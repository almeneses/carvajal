"""Ampliación del catálogo de Colombia con las referencias de México.

Decisión del equipo del 2026-11-25 (uso académico), que enmienda la spec 039: las referencias
de México se copian a Colombia con su precio convertido de MXN a COP con una tasa fechada.

El precio convertido lleva la procedencia ``sistema`` (lo calcula el proyecto): no se atribuye
a ningún distribuidor, no tiene sede ni existencias y su fuente es la ficha de México de la que
sale el importe original. Cada referencia se cotiza en Bogotá (``REFERENCE_CITY``) y en otras
``EXTRA_CITIES`` ciudades del directorio, repartidas por familia (``reference_cities``).
Los campos vacíos de la copia se completan con ``fuentes/mx_atributos.json`` (marca,
temperatura y paquete leídos de la ficha de México), solo si la ficha los declara.

Las cotizaciones lo muestran como «precio de referencia del catálogo», sin nombrar el origen
(decisión del equipo del 2026-11-25); el origen queda en los datos, en las fuentes y en el
manifiesto para sustentarlo si alguien lo pregunta. El IVA queda ``no_indicado``: el precio
de origen incluye el IVA de México, no el de Colombia.

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
    "ATTRIBUTES_FILE",
    "EXTRA_CITIES",
    "RATE_FILE",
    "REFERENCE_CITY",
    "SOURCE_NAME",
    "ExchangeRate",
    "adapt_all",
    "adapted_reference",
    "adapted_sku",
    "convert_price",
    "converted_price",
    "load_attributes",
    "load_rate",
    "reference_cities",
]

RATE_FILE = Path("fuentes") / "tasa_mxn_cop.json"
ATTRIBUTES_FILE = Path("fuentes") / "mx_atributos.json"
SOURCE_NAME = "Precio de referencia del catálogo"
# Ciudades donde se cotiza el precio convertido (decisión del equipo del 2026-11-25): todas en
# Bogotá y cada una en otras ``EXTRA_CITIES`` del directorio.
REFERENCE_CITY = "Bogotá"
EXTRA_CITIES = 2
# Campos que ``mx_atributos.json`` puede completar, solo si la copia los tiene vacíos.
_ATTRIBUTE_FIELDS = ("brand", "temp_min_c", "temp_max_c", "units_per_pack", "packs_per_box")


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


def load_attributes(directory: Path) -> dict[str, dict[str, object]]:
    """Atributos curados de las fichas de México, por SKU de México (vacío si no hay archivo)."""

    path = directory / ATTRIBUTES_FILE
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["sku"]): {key: item[key] for key in _ATTRIBUTE_FIELDS if key in item}
        for item in data["items"]
    }


def reference_cities(
    references: Iterable[Reference], cities: Iterable[str]
) -> dict[str, list[str]]:
    """Ciudades de cada copia: Bogotá y ``EXTRA_CITIES`` más, rotando dentro de cada familia.

    La rotación por familia hace que cada ciudad tenga referencias de todas las familias. Es
    determinista: el mismo catálogo y el mismo directorio dan el mismo reparto.
    """

    others = sorted({city for city in cities if city != REFERENCE_CITY})
    by_family: dict[str, list[Reference]] = {}
    for reference in sorted(references, key=lambda item: item.sku):
        by_family.setdefault(reference.family, []).append(reference)
    result: dict[str, list[str]] = {}
    slot = 0
    for family in sorted(by_family):
        for reference in by_family[family]:
            extra = (
                {others[(slot + step) % len(others)] for step in range(EXTRA_CITIES)}
                if others
                else set()
            )
            slot += EXTRA_CITIES
            result[reference.sku] = [REFERENCE_CITY, *sorted(extra)]
    return result


def adapted_sku(sku: str) -> str:
    """SKU de la copia: propio, para no confundirla con la referencia de México."""

    return f"{ADAPTED_PREFIX}{sku}"


def adapted_reference(
    reference: Reference, attributes: dict[str, object] | None = None
) -> Reference:
    """Copia de una referencia de México para Colombia, sin el precio inline en MXN.

    ``attributes`` completa solo los campos vacíos; la copia se vuelve a validar.
    """

    if reference.country != "MX":
        raise ValueError(f"{reference.sku} no es una referencia de México")
    filled = {
        key: value for key, value in (attributes or {}).items() if getattr(reference, key) is None
    }
    data = reference.model_dump()
    data.update(filled)
    return Reference.model_validate(
        data
        | {
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


def converted_price(
    reference: Reference, rate: ExchangeRate, city: str = REFERENCE_CITY
) -> ReferencePrice | None:
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
        source_city=city,
        source_url=reference.source_url,
        captured_at=rate.date,
    )


def adapt_all(
    references: Iterable[Reference],
    rate: ExchangeRate,
    *,
    cities: Iterable[str] = (REFERENCE_CITY,),
    attributes: dict[str, dict[str, object]] | None = None,
) -> tuple[list[Reference], list[ReferencePrice]]:
    """Copias y precios convertidos (uno por ciudad) de las referencias de México."""

    mexico = sorted((item for item in references if item.country == "MX"), key=lambda r: r.sku)
    copies = [adapted_reference(item, (attributes or {}).get(item.sku)) for item in mexico]
    places = reference_cities(copies, cities)
    prices = [
        price
        for item in mexico
        for city in places[adapted_sku(item.sku)]
        if (price := converted_price(item, rate, city)) is not None
    ]
    return copies, prices
