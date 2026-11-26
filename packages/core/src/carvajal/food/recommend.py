"""Del perfil del alimento al empaque del catálogo (spec 051, research R4 y R5).

Todo es determinista: una tabla fija traduce el tipo de alimento a requisitos y la aptitud
de cada referencia la decide :func:`~carvajal.agent.commercial.check_material`, la misma
regla del flujo comercial (la guía de materiales manda: contenido caliente exige un
material apto para caliente y la guía no puede marcarlo «no apto» para el uso).
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from carvajal.agent.commercial import CommercialRequest, check_material
from carvajal.catalog.loader import Catalog
from carvajal.catalog.models import Family, MaterialRule, Reference
from carvajal.food.profile import FoodKind, FoodProfile, Temperature

__all__ = [
    "DEFAULT_COUNTRY",
    "FOOD_THRESHOLD",
    "KIND_RULES",
    "MAX_ALTERNATIVES",
    "MAX_SUGGESTIONS",
    "KindRule",
    "NoteHints",
    "Recommendation",
    "Requirements",
    "application_level",
    "read_note",
    "recommend",
]

Country = Literal["CO", "MX"]
Status = Literal["ok", "not_food", "ask_kind", "ask_temperature", "no_match"]

DEFAULT_COUNTRY: Final[Country] = "CO"
FOOD_THRESHOLD: Final = 0.5
# Spec 053 (FR-001): una recomendada y hasta cuatro alternativas.
MAX_ALTERNATIVES: Final = 4
MAX_SUGGESTIONS: Final = 1 + MAX_ALTERNATIVES
_LEVEL_RANK: Final = {"recomendable": 0, "aplicable": 1}
# Capacidad esperada (oz) por nivel de porción (0-3). Una referencia con menos de la mitad
# de la capacidad esperada no se sugiere (una salsera no sirve para una sopa).
_FOOD_OZ: Final = (4.0, 8.0, 16.0, 32.0)
_SCALES: Final[dict[str, tuple[float, ...]]] = {
    "sopas": (8.0, 12.0, 16.0, 32.0),
    "salsas": (1.0, 2.0, 4.0, 8.0),
    "postres_helados": (4.0, 6.0, 8.0, 12.0),
    "bebida_fria": (4.0, 8.0, 12.0, 16.0),
    "bebida_caliente": (4.0, 8.0, 12.0, 16.0),
}


@dataclass(frozen=True)
class KindRule:
    """Fila de la tabla: cómo se empaca un tipo de alimento."""

    label: str
    use: str
    families: tuple[Family, ...]
    temperature: Temperature
    closure: bool
    drink: bool = False


KIND_RULES: Final[dict[FoodKind, KindRule]] = {
    "sopas": KindRule("una sopa", "sopas", ("contenedor", "envase", "vaso"), "caliente", True),
    "plato_fuerte": KindRule(
        "un plato fuerte", "comida_caliente", ("contenedor", "charola", "plato"), "caliente", False
    ),
    "frituras": KindRule(
        "una fritura", "frituras", ("contenedor", "charola", "plato"), "caliente", False
    ),
    "ensaladas": KindRule(
        "una ensalada", "ensaladas", ("contenedor", "plato", "charola"), "fria", False
    ),
    "salsas": KindRule("una salsa", "salsas", ("envase", "contenedor"), "ambiente", True),
    "panaderia": KindRule(
        "un producto de panadería",
        "panaderia",
        ("contenedor", "charola", "plato"),
        "ambiente",
        False,
    ),
    "frutas": KindRule("fruta", "frutas", ("contenedor", "vaso", "plato"), "fria", False),
    "postres_helados": KindRule(
        "un postre o helado", "comida_fria", ("vaso", "contenedor"), "fria", False
    ),
    "bebida_fria": KindRule("una bebida fría", "bebida_fria", ("vaso",), "fria", True, True),
    "bebida_caliente": KindRule(
        "una bebida caliente", "bebida_caliente", ("vaso",), "caliente", True, True
    ),
}
_SOLID_DESSERT_FAMILIES: Final[tuple[Family, ...]] = ("contenedor", "charola", "plato")
# Los tipos que ya fijan la temperatura no la preguntan aunque la foto dude.
_TEMPERATURE_BY_KIND: Final = frozenset({"bebida_fria", "bebida_caliente", "postres_helados"})


@dataclass(frozen=True)
class Requirements:
    use: str
    families: tuple[Family, ...]
    temperature: Temperature
    closure: bool
    target_oz: float


@dataclass(frozen=True)
class Recommendation:
    status: Status
    profile: FoodProfile
    country: Country
    requirements: Requirements | None = None
    references: tuple[Reference, ...] = ()
    alternative: Reference | None = None

    @property
    def rule(self) -> KindRule | None:
        return KIND_RULES.get(self.profile.kind)


@dataclass(frozen=True)
class NoteHints:
    """Lo que el texto que acompaña la foto aclara (palabras clave, sin modelo)."""

    temperature: Temperature | None = None
    country: Country | None = None


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


_HOT: Final = re.compile(r"\b(caliente|calientes|calientico|calientito|tibi[oa]s?)\b")
_COLD: Final = re.compile(
    r"\b(fri[oa]s?|frescos?|helad[oa]s?|refrigerad[oa]s?|congelad[oa]s?|nevera)\b"
)
_ROOM: Final = re.compile(r"\b(ambiente|temperatura ambiente|al clima)\b")


def read_note(text: str) -> NoteHints:
    """Temperatura y país dichos en el texto; ambiguo (dos temperaturas) → ``None``."""

    plain = _plain(text or "")
    found: list[Temperature] = []
    if _HOT.search(plain):
        found.append("caliente")
    if _COLD.search(plain):
        found.append("fria")
    if _ROOM.search(plain):
        found.append("ambiente")
    country: Country | None = None
    if re.search(r"\bmexico\b", plain):
        country = "MX"
    elif re.search(r"\bcolombia\b", plain):
        country = "CO"
    return NoteHints(temperature=found[0] if len(found) == 1 else None, country=country)


def application_level(rule: MaterialRule | None, use: str) -> str | None:
    """Nivel de la guía (recomendable, aplicable, no_apto) del material para el uso."""

    if rule is None or not rule.applications:
        return None
    levels = dict(part.split("=", 1) for part in rule.applications.split("|") if "=" in part)
    return levels.get(use)


def _target_oz(profile: FoodProfile) -> float:
    scale = _SCALES.get(profile.kind, _FOOD_OZ)
    level = min(len(scale) - 1, max(0, round(profile.portion)))
    return scale[level]


def _size_band(capacity_oz: float | None, target_oz: float) -> float:
    """Escalones de tamaño (cada uno duplica) entre la capacidad y la porción esperada.

    0 para lo que está entre ~0,7 y ~1,4 veces la porción; 1 para la mitad o el doble. Sin
    capacidad declarada, lo último.
    """

    if capacity_oz is None or capacity_oz <= 0 or target_oz <= 0:
        return math.inf
    return round(abs(math.log2(capacity_oz / target_oz)))


def _rank(reference: Reference, req: Requirements, catalog: Catalog) -> tuple[object, ...]:
    level = application_level(catalog.materials.get(reference.material), req.use)
    distance = (
        abs(reference.capacity_oz - req.target_oz)
        if reference.capacity_oz is not None
        else float("inf")
    )
    return (
        req.use not in reference.uses,
        _LEVEL_RANK.get(level or "", 2),
        req.families.index(reference.family),
        # El tamaño para la porción manda sobre el precio: un contenedor de 8 oz con precio
        # no pasa por delante de uno de 32 oz cuando se esperan 32 (revisión de la PR #275).
        _size_band(reference.capacity_oz, req.target_oz),
        # Dentro del mismo escalón de tamaño, primero lo que tiene precio: una
        # recomendación que no se puede cotizar deja a la persona sin total (2026-11-26).
        not catalog.offers(reference),
        distance,
        reference.is_adapted,  # a igual aptitud, primero el portafolio de Colombia
        reference.sku,
    )


def _fits(reference: Reference, req: Requirements) -> bool:
    """La capacidad, si la ficha la declara, no es mucho menor que la porción."""

    return reference.capacity_oz is None or reference.capacity_oz >= req.target_oz / 2


def _pick(ranked: list[Reference], req: Requirements, catalog: Catalog) -> tuple[Reference, ...]:
    """Hasta cinco en orden (la primera es la recomendada); dentro de un mismo nivel de
    aptitud, primero materiales distintos.

    El nivel es (la ficha declara el uso, nivel de la guía): la variedad nunca pasa una
    referencia menos apta por delante de una más apta.
    """

    chosen: list[Reference] = []
    tiers: dict[tuple[object, ...], list[Reference]] = {}
    for reference in ranked:
        tiers.setdefault(_rank(reference, req, catalog)[:2], []).append(reference)
    for tier in tiers.values():
        materials: set[str] = set()
        for reference in tier:
            if len(chosen) == MAX_SUGGESTIONS:
                return tuple(chosen)
            if reference.material not in materials:
                chosen.append(reference)
                materials.add(reference.material)
        for reference in tier:
            if len(chosen) == MAX_SUGGESTIONS:
                return tuple(chosen)
            if reference not in chosen:
                chosen.append(reference)
    return tuple(chosen)


def requirements_for(
    profile: FoodProfile, temperature: Temperature, rule: KindRule
) -> Requirements:
    """Requisitos del empaque para el perfil y la temperatura ya decidida."""

    use = rule.use
    families = rule.families
    if profile.kind == "plato_fuerte" and temperature != "caliente":
        use = "comida_fria"
    if profile.kind == "postres_helados" and profile.consistency == "solido":
        # Una porción de torta va en contenedor o charola; el vaso es para helado o postre
        # de cuchara (evaluación 051: la torta recibía vasos térmicos).
        families = _SOLID_DESSERT_FAMILIES
    closure = rule.closure or profile.consistency in ("liquido", "semiliquido")
    return Requirements(
        use=use,
        families=families,
        temperature=temperature,
        closure=closure,
        target_oz=_target_oz(profile),
    )


def commercial_request(
    req: Requirements, country: Country, *, use: bool = True
) -> CommercialRequest:
    """La necesidad como la entiende el flujo comercial (sin cantidad: explorar no es cotizar)."""

    return CommercialRequest(
        use=req.use if use else None,
        temperature=req.temperature,
        microwave=None,
        capacity_oz=None,
        family=None,
        quantity=None,
        country=country,
        city=None,
    )


def recommend(
    profile: FoodProfile,
    *,
    catalog: Catalog,
    note: str = "",
    country: Country | None = None,
    min_confidence: float = 0.6,
) -> Recommendation:
    """Decide qué responder: no es comida, preguntar, sugerir o «no hay empaque»."""

    hints = read_note(note)
    chosen_country: Country = hints.country or country or DEFAULT_COUNTRY
    if profile.food_p < FOOD_THRESHOLD or profile.kind == "no_es_comida":
        return Recommendation("not_food", profile, chosen_country)
    rule = KIND_RULES[profile.kind]
    if profile.kind_confidence < min_confidence:
        return Recommendation("ask_kind", profile, chosen_country)
    if profile.kind in _TEMPERATURE_BY_KIND:
        temperature = rule.temperature
    elif hints.temperature is not None:
        temperature = hints.temperature
    elif profile.temperature_confidence >= min_confidence:
        temperature = profile.temperature
    else:
        return Recommendation("ask_temperature", profile, chosen_country)
    req = requirements_for(profile, temperature, rule)
    candidates = [
        reference
        for reference in catalog.references
        if reference.country == chosen_country and reference.family in req.families
    ]
    valid, _discarded = check_material(
        candidates, commercial_request(req, chosen_country), catalog=catalog
    )
    sized = [reference for reference in valid if _fits(reference, req)] or valid
    if sized:
        ranked = sorted(sized, key=lambda reference: _rank(reference, req, catalog))
        return Recommendation("ok", profile, chosen_country, req, _pick(ranked, req, catalog))
    # Sin aptas para el uso: la mejor que al menos soporta la temperatura, como alternativa.
    loose, _ = check_material(
        candidates, commercial_request(req, chosen_country, use=False), catalog=catalog
    )
    alternative = (
        min(loose, key=lambda reference: _rank(reference, req, catalog)) if loose else None
    )
    return Recommendation("no_match", profile, chosen_country, req, (), alternative)
