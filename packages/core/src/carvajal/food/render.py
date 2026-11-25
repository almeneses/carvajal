"""Respuesta de la foto sin LLM (spec 051, FR-014 a FR-017; spec 053, FR-001 a FR-004).

El texto se arma con la clasificación, la tabla y las fichas del catálogo: una referencia
recomendada y hasta cuatro alternativas numeradas, cada una con su ref. y el enlace a su
ficha; la razón de la aptitud sale de la guía de materiales. Las tarjetas usan el formato de
``buscar_producto`` y marcan cada opción con su elección (``choice``: ``"recomendada"`` o el
número de la alternativa), así la web las pinta como recomendada + cuadrícula, se
reconstruyen al recargar y la elección por voz o texto («la 2») se resuelve contra ellas.
"""

from dataclasses import asdict
from datetime import date
from typing import Any, Final

from carvajal.agent.commercial_flow import _channel, _option
from carvajal.catalog.loader import Catalog
from carvajal.catalog.models import Reference
from carvajal.food.profile import FoodKind
from carvajal.food.recommend import (
    KIND_RULES,
    Recommendation,
    application_level,
    commercial_request,
)

__all__ = [
    "PHOTO_CHOICE_HINT",
    "PHOTO_QUESTION",
    "PHOTO_REJECTED",
    "PHOTO_UNAVAILABLE",
    "choice_label",
    "photo_question",
    "recommendation_cards",
    "render_recommendation",
]

PHOTO_QUESTION: Final = "📷 Foto de un alimento"
PHOTO_UNAVAILABLE: Final = (
    "No pude analizar la foto en este momento. Cuéntame qué alimento quieres empacar "
    "(por ejemplo «sopa caliente» o «jugo frío») y te sugiero el empaque."
)
PHOTO_REJECTED: Final = (
    "No pude abrir esa foto. Envíala de nuevo como imagen JPEG o PNG, o cuéntame qué alimento "
    "quieres empacar."
)

PHOTO_CHOICE_HINT: Final = (
    "¿Te cotizo alguna? Di «dame esa» para la recomendada o el número de una alternativa "
    "(por ejemplo, «la 2»)."
)

_COUNTRY: Final = {"CO": "Colombia", "MX": "México"}
_CONSISTENCY: Final = {"solido": "sólida", "semiliquido": "semilíquida", "liquido": "líquida"}
_TEMPERATURE: Final = {
    "caliente": "caliente",
    "fria": "frío",
    "ambiente": "a temperatura ambiente",
}
_USE_TEXT: Final = {
    "sopas": "sopas",
    "comida_caliente": "comida caliente",
    "comida_fria": "comida fría",
    "frituras": "frituras",
    "ensaladas": "ensaladas",
    "salsas": "salsas",
    "panaderia": "panadería",
    "frutas": "frutas",
    "bebida_fria": "bebidas frías",
    "bebida_caliente": "bebidas calientes",
}
_KIND_NAME: Final[dict[FoodKind, str]] = {
    "sopas": "una sopa",
    "plato_fuerte": "un plato fuerte",
    "frituras": "una fritura (empanadas, papas…)",
    "ensaladas": "una ensalada",
    "salsas": "una salsa o untable",
    "panaderia": "un producto de panadería",
    "frutas": "fruta",
    "postres_helados": "un postre o helado",
    "bebida_fria": "una bebida fría",
    "bebida_caliente": "una bebida caliente",
    "no_es_comida": "algo que no es comida",
}


def photo_question(note: str) -> str:
    """Lo que el hilo guarda en lugar de la foto (FR-004)."""

    text = " ".join((note or "").split())
    return f"{PHOTO_QUESTION}: {text}" if text else PHOTO_QUESTION


def _seen(rec: Recommendation) -> str:
    profile = rec.profile
    rule = KIND_RULES[profile.kind]
    return f"Veo {rule.label} de consistencia {_CONSISTENCY[profile.consistency]}"


def choice_label(index: int) -> str:
    """Etiqueta de la opción ``index`` (0 = recomendada; 1-4 = alternativas)."""

    return "Recomendada" if index == 0 else f"Alternativa {index}"


def _reference_text(reference: Reference) -> str:
    details = [reference.material]
    if reference.capacity_oz is not None:
        details.append(f"{reference.capacity_oz:g} oz")
    return (
        f"**{reference.name}** (ref. {reference.sku}): {', '.join(details)}. "
        f"[Ficha]({reference.source_url})"
    )


def _reference_line(index: int, reference: Reference) -> str:
    return f"{index}. {_reference_text(reference)}"


def _why(rec: Recommendation, catalog: Catalog) -> str:
    req = rec.requirements
    if req is None:
        return ""
    use = _USE_TEXT.get(req.use, req.use)
    parts: list[str] = []
    for material in dict.fromkeys(reference.material for reference in rec.references):
        rule = catalog.materials.get(material)
        level = application_level(rule, req.use)
        if rule is None:
            parts.append(f"la ficha de {material} lo declara para este uso")
            continue
        text = f"el {material} es {level} para {use}" if level else f"el {material} sirve"
        if req.temperature == "caliente" and rule.temp_max_c is not None:
            text += f" y soporta contenido caliente (hasta {rule.temp_max_c:g} °C)"
        parts.append(text)
    if not parts:
        return ""
    sentence = "; ".join(parts)
    return f"Según la guía de materiales, {sentence}."


def render_recommendation(rec: Recommendation, catalog: Catalog) -> str:
    """Texto final del turno de foto (markdown; WhatsApp lo convierte a su formato)."""

    profile = rec.profile
    if rec.status == "not_food":
        return (
            "No veo comida ni bebida en la foto. Envíame una foto del alimento que quieres "
            "empacar (de cerca, con buena luz y sin personas) y te sugiero el empaque."
        )
    if rec.status == "ask_kind":
        first = _KIND_NAME[profile.kind]
        second = _KIND_NAME.get(profile.kind_runner_up) if profile.kind_runner_up else None
        options = f"{first} o {second}" if second and second != first else first
        return (
            f"Veo un alimento, pero no estoy seguro de qué es: ¿es {options}? "
            "Cuéntame qué es y te sugiero el empaque."
        )
    if rec.status == "ask_temperature":
        return (
            f"{_seen(rec)}. ¿Se entrega caliente, frío o a temperatura ambiente? De eso "
            "depende qué materiales sirven."
        )
    req = rec.requirements
    assert req is not None  # ok y no_match siempre traen requisitos
    country = _COUNTRY.get(rec.country, rec.country)
    use = _USE_TEXT.get(req.use, req.use)
    intro = f"{_seen(rec)}, que normalmente se entrega {_TEMPERATURE[req.temperature]}."
    if rec.status == "no_match":
        lines = [
            intro,
            "",
            f"En el catálogo de {country} no tenemos un empaque específico para {use}.",
        ]
        if rec.alternative is not None:
            lines += [
                "",
                "Como alternativa, esta referencia soporta la temperatura, aunque la guía no la "
                "declara para este uso:",
                "",
                _reference_line(1, rec.alternative),
            ]
        lines += ["", "Si me cuentas más del producto, busco otra opción."]
        return "\n".join(lines)
    best, *alternatives = rec.references
    lines = [intro, "", f"Del catálogo de {country}, te recomiendo:", "", _reference_text(best)]
    if alternatives:
        lines += ["", "**Alternativas:**", ""]
        lines += [_reference_line(i, ref) for i, ref in enumerate(alternatives, start=1)]
    why = _why(rec, catalog)
    if why:
        lines += ["", why]
    if req.closure:
        lines += ["", "Como se puede derramar, pide también la tapa de la referencia que elijas."]
    hint = PHOTO_CHOICE_HINT if alternatives else "¿Te la cotizo? Dime «dame esa»."
    lines += ["", hint]
    return "\n".join(lines)


def recommendation_cards(
    rec: Recommendation, catalog: Catalog, today: date | None = None
) -> dict[str, Any] | None:
    """Tarjetas en el formato de ``buscar_producto`` o ``None`` si no hay referencias."""

    references = rec.references or ((rec.alternative,) if rec.alternative else ())
    if rec.requirements is None or not references:
        return None
    req = commercial_request(rec.requirements, rec.country)
    options = [_option(reference, req, today, catalog) for reference in references]
    if rec.references:
        # Spec 053: la primera es la recomendada; las demás, alternativas numeradas.
        for index, option in enumerate(options):
            option["choice"] = "recomendada" if index == 0 else index
            option["choice_label"] = choice_label(index)
    return {
        "request": asdict(req),
        # Origen de las opciones: la web las pinta como recomendada + cuadrícula y la
        # elección «la 2» se resuelve solo contra una recomendación de foto (FR-005).
        "origin": "foto",
        "options": options,
        "discarded": [],
        "discarded_total": 0,
        "channel": _channel(req, catalog),
    }
