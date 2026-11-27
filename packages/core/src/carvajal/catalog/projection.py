"""Precios proyectados para las referencias propias de Colombia sin precio (2026-11-26).

Decisión del equipo para la demo: ninguna referencia del catálogo de Colombia debe quedar
sin precio. Las que nadie publica (73 de 102) reciben un precio de referencia **proyectado**
desde las referencias que sí tienen precio en Colombia (EDEXA y las copias de México). No es
preciso ni pretende serlo: es un orden de magnitud coherente con el resto del catálogo.

Método, en este orden (``project``):

1. **Misma línea** (misma ficha oficial): el hermano con precio de capacidad más cercana,
   escalado por capacidad, o la mediana de la línea si no declaran capacidad.
2. **Misma familia y material**: igual que la línea; la mediana exige al menos dos muestras.
3. **Misma familia**: el de capacidad más cercana, escalado y con un factor de material
   (mediana del material frente a la del material de la base, acotado entre 0,67 y 1,5), o
   la mediana de la familia con ese factor.

El escalado por capacidad usa el exponente 2/3: el material de un envase crece con su
superficie, no con su volumen. El precio por unidad queda acotado al rango que ya existe en
la familia, y el de la caja es ese precio por las unidades de la referencia, redondeado al
peso. La fuente citada es la ficha de la referencia en que se basa la proyección; el método y
la base de cada una quedan en ``fuentes/proyecciones_co.csv``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from carvajal.catalog.conversion import SOURCE_NAME
from carvajal.catalog.models import Reference, ReferencePrice

__all__ = ["CAPACITY_EXPONENT", "PROJECTION_FILE", "Projection", "project", "project_prices"]

PROJECTION_FILE = "fuentes/proyecciones_co.csv"
CAPACITY_EXPONENT = Decimal("0.6667")
_MATERIAL_FACTOR = (Decimal("0.67"), Decimal("1.5"))
_MIN_MEDIAN = 2  # muestras para usar la mediana de un grupo


@dataclass(frozen=True)
class Projection:
    """Precio proyectado de una referencia y su sustento."""

    reference: Reference
    base: Reference
    method: str
    base_unit: Decimal
    unit: Decimal

    @property
    def box(self) -> Decimal:
        return (self.unit * self.reference.units_per_box).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )


def _unit_prices(
    references: Sequence[Reference], offers: Iterable[ReferencePrice]
) -> dict[str, Decimal]:
    """Precio por unidad de cada referencia de Colombia con precio (Bogotá si hay varios)."""

    by_sku = {reference.sku: reference for reference in references}
    found: dict[str, tuple[bool, Decimal]] = {}
    for offer in offers:
        reference = by_sku.get(offer.sku)
        if offer.country != "CO" or reference is None:
            continue
        preferred = offer.source_city in (None, "Bogotá")
        unit = offer.amount / Decimal(reference.units_per_box)
        current = found.get(offer.sku)
        if current is None or (preferred and not current[0]):
            found[offer.sku] = (preferred, unit)
    return {sku: unit for sku, (_, unit) in found.items()}


def project(references: Sequence[Reference], offers: Iterable[ReferencePrice]) -> list[Projection]:
    """Proyección para cada referencia de Colombia sin ningún precio, en orden de SKU."""

    units = _unit_prices(references, offers)
    colombia = [reference for reference in references if reference.country == "CO"]
    pool = [reference for reference in colombia if reference.sku in units]
    missing = sorted(
        (reference for reference in colombia if reference.sku not in units),
        key=lambda item: item.sku,
    )

    def unit(reference: Reference) -> Decimal:
        return units[reference.sku]

    def median_ref(items: Sequence[Reference]) -> Reference:
        ordered = sorted(items, key=lambda item: (unit(item), item.sku))
        return ordered[len(ordered) // 2]

    def nearest(items: Sequence[Reference], capacity: float) -> Reference:
        return min(items, key=lambda item: (abs((item.capacity_oz or 0) - capacity), item.sku))

    def scale(target: Reference, base: Reference) -> Decimal:
        ratio = Decimal(str(target.capacity_oz)) / Decimal(str(base.capacity_oz))
        return ratio**CAPACITY_EXPONENT

    def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
        return max(low, min(high, value))

    result: list[Projection] = []
    for reference in missing:
        family = [item for item in pool if item.family == reference.family]
        if not family:
            continue
        family_units = sorted(unit(item) for item in family)
        low, high = family_units[0], family_units[-1]
        same_material = [item for item in family if item.material == reference.material]
        line = [item for item in pool if item.source_url == reference.source_url]
        chosen: tuple[Reference, Decimal, str] | None = None
        for label, items, minimum in (
            ("misma línea", line, 1),
            ("misma familia y material", same_material, _MIN_MEDIAN),
        ):
            with_capacity = [item for item in items if item.capacity_oz]
            if reference.capacity_oz and with_capacity:
                base = nearest(with_capacity, reference.capacity_oz)
                chosen = (base, unit(base) * scale(reference, base), f"{label}, por capacidad")
                break
            if not reference.capacity_oz and len(items) >= minimum:
                base = median_ref(items)
                chosen = (base, unit(base), f"{label}, mediana")
                break
        if chosen is None:
            with_capacity = [item for item in family if item.capacity_oz]
            if reference.capacity_oz and with_capacity:
                base = nearest(with_capacity, reference.capacity_oz)
                base_material = [item for item in family if item.material == base.material]
                factor = Decimal(1)
                if same_material and base.material != reference.material:
                    factor = clamp(
                        unit(median_ref(same_material)) / unit(median_ref(base_material)),
                        *_MATERIAL_FACTOR,
                    )
                chosen = (
                    base,
                    unit(base) * scale(reference, base) * factor,
                    f"misma familia, por capacidad, factor de material {factor:.2f}",
                )
            else:
                base = median_ref(family)
                factor = Decimal(1)
                if same_material:
                    factor = clamp(unit(median_ref(same_material)) / unit(base), *_MATERIAL_FACTOR)
                chosen = (
                    base,
                    unit(base) * factor,
                    f"mediana de la familia, factor de material {factor:.2f}",
                )
        base, value, method = chosen
        result.append(
            Projection(
                reference=reference,
                base=base,
                method=method,
                base_unit=unit(base),
                unit=clamp(value, low, high),
            )
        )
    return result


def project_prices(
    projections: Iterable[Projection], cities: Iterable[str], captured_at: date
) -> list[ReferencePrice]:
    """Un precio de referencia por proyección y ciudad, con la ficha de su base como fuente."""

    places = sorted(set(cities))
    return [
        ReferencePrice(
            sku=item.reference.sku,
            country="CO",
            amount=item.box,
            currency="COP",
            tax_status="no_indicado",
            source_type="sistema",
            source_name=SOURCE_NAME,
            source_city=city,
            source_url=item.base.source_url,
            captured_at=captured_at,
        )
        for item in projections
        for city in places
    ]
