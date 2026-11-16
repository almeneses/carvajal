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
"""

from dataclasses import dataclass
from typing import Literal

from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import Family, MaterialRule, Reference

__all__ = [
    "HOT_MIN_C",
    "RENEWABLE",
    "CommercialRequest",
    "Discarded",
    "check_material",
    "search_packaging",
]

# Temperatura máxima que debe soportar una referencia para contenido caliente. Coincide con
# el EPS de la guía (-20 °C / 60 °C), que la guía recomienda para comida caliente.
HOT_MIN_C = 60.0
RENEWABLE = frozenset({"PAPEL", "PULPA"})
CAPACITY_TOLERANCE_OZ = 0.25
_DRINK_FAMILY: dict[str, Family] = {"bebida_caliente": "vaso", "bebida_fria": "vaso"}


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

    hot = req.temperature == "caliente"
    if hot and _declared_opposite(reference, "caliente"):
        return f"{reference.name}: la ficha la declara para bebida fría, no caliente."
    if req.temperature == "fria" and _declared_opposite(reference, "fria"):
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
