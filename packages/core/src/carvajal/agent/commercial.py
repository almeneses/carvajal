"""Herramientas deterministas de la ruta comercial de empaques (spec 036).

Contrato compartido con la pista B (`docs/agentes/036-pista-a.md`, sección 4): el router
llena una ``CommercialRequest`` y el nodo ``commercial`` llama a estas funciones en una
secuencia fija. Ninguna consulta la red ni el LLM.

Validación de material (FR-003):

- Si el material tiene regla en la guía (``materiales.csv``), manda la guía.
- Si no la tiene (papel y pulpa), manda la ficha del producto: su temperatura de uso
  declarada y sus usos.
- En ambos casos, una ficha que se declara para el uso contrario (vaso «bebida fría» pedido
  para bebida caliente) se descarta.

Precios (spec 039): cada referencia puede tener varias ofertas, una por fuente. ``quote_offer``
calcula el total de una oferta; nunca se estima, se convierte ni se promedia un precio.
"""

import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal

from carvajal.catalog.loader import Catalog, inline_offer, load_catalog
from carvajal.catalog.models import Distributor, Family, MaterialRule, Reference, ReferencePrice

__all__ = [
    "BULK_BOXES",
    "HOT_MIN_C",
    "RENEWABLE",
    "STALE_DAYS",
    "BoxCount",
    "CommercialRequest",
    "Discarded",
    "NoPriceError",
    "Quote",
    "boxes_for",
    "check_material",
    "find_distributors",
    "quote",
    "quote_offer",
    "search_packaging",
]

# Temperatura máxima que debe soportar una referencia para contenido caliente. Coincide con
# el EPS de la guía (-20 °C / 60 °C), que la guía recomienda para comida caliente.
HOT_MIN_C = 60.0
# A partir de este volumen se calcula igual, pero se sugiere contacto comercial (spec 036).
BULK_BOXES = 100
# Un precio capturado hace más de estos días se muestra con aviso de desactualización (FR-007).
STALE_DAYS = 90
RENEWABLE = frozenset({"PAPEL", "PULPA"})
CAPACITY_TOLERANCE_OZ = 0.25
_DRINK_FAMILY: dict[str, Family] = {"bebida_caliente": "vaso", "bebida_fria": "vaso"}
_HOT_USES = frozenset({"bebida_caliente", "comida_caliente"})
_COLD_USES = frozenset({"bebida_fria"})


@dataclass(frozen=True)
class CommercialRequest:
    """Necesidad del cliente extraída por el router; ``None`` es «no lo dijo»."""

    use: str | None
    temperature: Literal["caliente", "fria", "ambiente"] | None
    microwave: bool | None
    capacity_oz: float | None
    family: Family | None
    quantity: int | None
    country: Literal["MX", "CO"] | None
    city: str | None
    prefers_renewable: bool = False


@dataclass(frozen=True)
class Discarded:
    """Referencia descartada y el motivo, en palabras para el cliente."""

    sku: str
    reason: str


@dataclass(frozen=True)
class BoxCount:
    """Cajas que hay que pedir y unidades que sobran al redondear hacia arriba."""

    boxes: int
    surplus: int


@dataclass(frozen=True)
class Quote:
    """Precio de referencia de un pedido; nunca es una oferta comercial (FR-005)."""

    total: str
    currency: str
    tax_included: bool | None
    captured_at: str
    valid_until: str | None
    notice: str
    tax_status: str = "incluido"
    source_type: str = "oficial"
    source_name: str = ""
    source_url: str = ""
    source_city: str | None = None


class NoPriceError(ValueError):
    """La referencia no publica precio: se declara, no se estima (FR-006)."""


def search_packaging(req: CommercialRequest, *, catalog: Catalog | None = None) -> list[Reference]:
    """Candidatas por familia, capacidad y país; el ajuste al uso lo decide ``check_material``.

    Las referencias que la ficha declara para el uso pedido van primero.
    """

    data = catalog or load_catalog()
    family = req.family or _DRINK_FAMILY.get(req.use or "")
    found = [
        reference
        for reference in data.references
        if (family is None or reference.family == family)
        and (req.country is None or reference.country == req.country)
        and (
            req.capacity_oz is None
            or (
                reference.capacity_oz is not None
                and abs(reference.capacity_oz - req.capacity_oz) <= CAPACITY_TOLERANCE_OZ
            )
        )
    ]
    return sorted(found, key=lambda reference: (req.use not in reference.uses, reference.sku))


def check_material(
    refs: list[Reference], req: CommercialRequest, *, catalog: Catalog | None = None
) -> tuple[list[Reference], list[Discarded]]:
    """Separa las referencias aptas para el uso pedido de las que no lo son (FR-003)."""

    rules = (catalog or load_catalog()).materials
    valid: list[Reference] = []
    discarded: list[Discarded] = []
    for reference in refs:
        reason = _reject_reason(reference, req, rules.get(reference.material))
        if reason is None:
            valid.append(reference)
        else:
            discarded.append(Discarded(sku=reference.sku, reason=reason))
    if req.prefers_renewable:
        valid.sort(key=lambda reference: reference.material not in RENEWABLE)
    return valid, discarded


def _reject_reason(
    reference: Reference, req: CommercialRequest, rule: MaterialRule | None
) -> str | None:
    """Motivo de descarte o ``None`` si la referencia es apta."""

    hot = req.temperature == "caliente" or req.use in _HOT_USES
    cold = req.temperature == "fria" or req.use in _COLD_USES
    if hot and _declared_opposite(reference, "caliente"):
        return f"{reference.name}: la ficha la declara para bebida fría, no caliente."
    if cold and _declared_opposite(reference, "fria"):
        return f"{reference.name}: la ficha la declara para bebida caliente, no fría."

    if rule is not None:  # material de la guía: manda la guía
        if hot and not rule.hot_content:
            return f"{reference.name}: el {rule.material} no es apto para contenido caliente."
        if req.microwave and not rule.microwave:
            return f"{reference.name}: el {rule.material} no es apto para microondas."
        level = _application_level(rule, req.use)
        if level == "no_apto":
            return (
                f"{reference.name}: la guía marca el {rule.material} como no apto para {req.use}."
            )
        return None

    # Material sin regla en la guía (papel, pulpa): manda la ficha.
    if req.microwave:
        return f"{reference.name}: la ficha no declara que sea apta para microondas."
    if hot:
        declares_hot = any(use in reference.uses for use in ("bebida_caliente", "comida_caliente"))
        tolerates = reference.temp_max_c is not None and reference.temp_max_c >= HOT_MIN_C
        if not (declares_hot or tolerates):
            return (
                f"{reference.name}: la ficha no declara temperatura ni uso para contenido caliente."
            )
    return None


def _declared_opposite(reference: Reference, wanted: Literal["caliente", "fria"]) -> bool:
    """La ficha se declara solo para el uso contrario al pedido."""

    hot = "bebida_caliente" in reference.uses
    cold = "bebida_fria" in reference.uses
    return (cold and not hot) if wanted == "caliente" else (hot and not cold)


def _application_level(rule: MaterialRule, use: str | None) -> str | None:
    """Nivel de la guía (recomendable, aplicable, no_apto) para un uso, si lo tiene."""

    if not use or not rule.applications:
        return None
    levels = dict(part.split("=", 1) for part in rule.applications.split("|") if "=" in part)
    return levels.get(use)


def boxes_for(ref: Reference, units: int) -> BoxCount:
    """Cajas completas para cubrir ``units``, con aritmética entera (FR-004)."""

    if units <= 0:
        raise ValueError("La cantidad debe ser un número positivo de unidades.")
    boxes = -(-units // ref.units_per_box)
    return BoxCount(boxes=boxes, surplus=boxes * ref.units_per_box - units)


def quote(ref: Reference, boxes: int, *, today: date | None = None) -> Quote:
    """Total de referencia con el precio de la ficha (spec 036): envoltorio de ``quote_offer``."""

    if boxes <= 0:
        raise ValueError("El número de cajas debe ser positivo.")
    offer = inline_offer(ref)
    if offer is None:
        raise NoPriceError(
            f"{ref.name}: el catálogo no publica precio para esta referencia; "
            "no se estima. Consulta un distribuidor."
        )
    return quote_offer(offer, boxes, today=today)


_TAX_NOTES = {
    "incluido": "con impuestos incluidos",
    "no_incluido": "sin impuestos",
    "no_indicado": "sin que la fuente indique si incluye impuestos",
}


def quote_offer(offer: ReferencePrice, boxes: int, *, today: date | None = None) -> Quote:
    """Total = cajas x precio por caja de una oferta, con fuente, fecha y avisos (FR-004, FR-007).

    El aviso nombra a quien publica el precio: si es un tercero (un distribuidor o un
    comercio), queda dicho que no es un precio de Carvajal.
    """

    if boxes <= 0:
        raise ValueError("El número de cajas debe ser positivo.")
    taxes = _TAX_NOTES[offer.tax_status]
    captured = offer.captured_at.isoformat()
    if offer.source_type == "oficial":
        origin = f"capturado el {captured} de la ficha publicada"
    else:
        origin = (
            f"publicado por {offer.source_name} (tercero, no es un precio de Carvajal) "
            f"y capturado el {captured}"
        )
    notes = [
        f"Precio de referencia {taxes}, {origin}; no confirma existencias ni condiciones de venta."
    ]
    current = today or date.today()
    if offer.valid_until is not None and offer.valid_until < current:
        notes.append(
            f"La vigencia publicada del precio terminó el {offer.valid_until.isoformat()}; "
            "puede haber cambiado."
        )
    if (current - offer.captured_at).days > STALE_DAYS:
        notes.append(
            f"El precio se capturó hace más de {STALE_DAYS} días; puede estar desactualizado."
        )
    if boxes > BULK_BOXES:
        notes.append(
            f"Para más de {BULK_BOXES} cajas conviene pedir una cotización comercial por volumen."
        )
    return Quote(
        total=str(offer.amount * boxes),
        currency=offer.currency,
        tax_included=offer.tax_included,
        captured_at=captured,
        valid_until=offer.valid_until.isoformat() if offer.valid_until else None,
        notice=" ".join(notes),
        tax_status=offer.tax_status,
        source_type=offer.source_type,
        source_name=offer.source_name,
        source_url=offer.source_url,
        source_city=offer.source_city,
    )


def find_distributors(
    city: str, *, catalog: Catalog | None = None
) -> tuple[list[Distributor], list[str]]:
    """Distribuidores de la ciudad y la lista de ciudades que sí tienen (FR-008).

    La ciudad se compara sin tildes ni mayúsculas. Si no hay distribuidores, la primera
    lista viene vacía y la segunda permite ofrecer alternativas.
    """

    distributors = (catalog or load_catalog()).distributors
    wanted = _plain(city)
    found = [distributor for distributor in distributors if _plain(distributor.city) == wanted]
    cities = sorted({distributor.city for distributor in distributors}, key=_plain)
    return found, cities


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
