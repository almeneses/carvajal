"""Tarjetas de producto de la ruta comercial (spec 036, T028-T029).

El nodo ``commercial`` deja su resultado estructurado en ``tool_result``; aquí se convierte en
el HTML de ``_productos.html``, que el servicio envía en el evento SSE ``products``. Se
renderiza en el servidor con el autoescape de Jinja para que el navegador solo inserte HTML
ya escapado.

Spec 044 (T043): un turno del modo ``tools`` puede traer varias cotizaciones en ``quotes``
(una tarjeta y un folio por cada una) y ``quote`` = la primera, por compatibilidad. Los
resultados del modo ``routes`` y los guardados antes solo tienen ``quote``:
:func:`result_quotes` cae a ``[quote]``.

En el modo ``tools``, ``buscar_producto`` deja hasta ocho opciones en las tarjetas aunque la
respuesta presente menos: :func:`presented_options` conserva solo las que el texto nombra.
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import structlog

from apps.api.runtime import templates
from carvajal.agent.commercial_flow import MAX_OPTIONS
from carvajal.agent.toolagent.tools.builtin import MAX_PRESENTED_OPTIONS

__all__ = [
    "has_cards",
    "presented_options",
    "render_products",
    "result_quotes",
    "safe_render_products",
]

logger = structlog.get_logger(__name__)


def result_quotes(result: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Cotizaciones del resultado en orden: ``quotes`` si trae alguna, si no ``[quote]``."""

    if not isinstance(result, Mapping):
        return []
    listed = result.get("quotes")
    candidates = listed if isinstance(listed, list) and listed else [result.get("quote")]
    return [dict(item) for item in candidates if isinstance(item, Mapping) and item]


def has_cards(result: dict[str, Any]) -> bool:
    """Indica si el resultado tiene algo que mostrar como tarjeta."""

    channel = result.get("channel") or {}
    return bool(
        result.get("options")
        or result.get("contacts")
        or result.get("catalog_note")
        or result.get("ficha")
        or result.get("offers")
        or result.get("sites")
        or result.get("quote")
        or result.get("quotes")
        or result.get("cities")
        or result.get("clarifications")
        or (result.get("flow") or {}).get("actions")
        or channel.get("distributors")
        or channel.get("cities_with_distributors")
    )


def render_products(result: dict[str, Any]) -> str:
    """HTML de las tarjetas para un resultado de la ruta comercial."""

    return templates.env.get_template("_productos.html").render(resultado=result).strip()


def safe_render_products(result: dict[str, Any]) -> str:
    """Como ``render_products``, pero ``""`` si el parcial falla.

    Las tarjetas son un adorno: un parcial roto no debe tumbar la respuesta de texto que
    se está transmitiendo. El fallo queda en el log para corregir la plantilla.
    """

    try:
        return render_products(result)
    except Exception as error:
        logger.warning("chat.products_render_failed", error=str(error))
        return ""


# --- Opciones presentadas en la respuesta --------------------------------------------------

_NON_WORD = re.compile(r"[^0-9a-z]+")
_DIGIT_LETTER = re.compile(r"(?<=[0-9])(?=[a-z])|(?<=[a-z])(?=[0-9])")


def _plain(text: str) -> str:
    """Texto comparable: sin tildes, en minúsculas y sin signos (markdown, tablas o guiones).

    Cada palabra queda separada por un espacio, así «**Plato hondo 12 oz**» y
    «| plato hondo 12 oz |» dan lo mismo y la búsqueda con espacios es de palabra completa.
    Las cifras se separan de las letras pegadas: «16oz» del catálogo de México es «16 oz».
    """

    decomposed = unicodedata.normalize("NFKD", text.casefold())
    bare = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(_DIGIT_LETTER.sub(" ", _NON_WORD.sub(" ", bare)).split())


def _spans(text: str, term: str) -> list[tuple[int, int]]:
    """Posiciones de ``term`` como palabras completas dentro de ``text`` (ambos ``_plain``).

    ``text`` llega rodeado de espacios: «12 oz» no coincide dentro de «112 oz».
    """

    needle = f" {term} "
    found: list[tuple[int, int]] = []
    start = text.find(needle)
    while start != -1:
        found.append((start, start + len(needle)))
        start = text.find(needle, start + len(needle) - 1)
    return found


def _mentions(text: str, names: set[str]) -> dict[str, int]:
    """Veces que aparece cada nombre, sin contar las que caen dentro de un nombre más largo.

    Con «Vaso térmico 12 oz Wau» y «Vaso térmico 12 oz Wau Maestro» en las opciones, nombrar
    la segunda no cuenta como nombrar la primera.
    """

    spans = {name: _spans(text, name) for name in names}
    counts: dict[str, int] = {}
    for name, own in spans.items():
        longer = [
            span for other, found in spans.items() if len(other) > len(name) for span in found
        ]
        counts[name] = sum(
            1 for start, end in own if not any(s <= start and end <= e for s, e in longer)
        )
    return counts


def presented_options(result: dict[str, Any] | None, answer: str) -> dict[str, Any] | None:
    """El resultado con solo las opciones que la respuesta nombra (por SKU o nombre exacto).

    El modelo ve hasta ocho opciones de ``buscar_producto`` y suele presentar menos; las
    tarjetas deben coincidir con lo que dice el texto. Una opción cuenta como presentada si
    la respuesta trae su SKU o su nombre completo, sin importar tildes, mayúsculas ni
    markdown, y como palabras completas.

    Si varias opciones comparten nombre (referencias gemelas), se conservan las que traen su
    SKU y, de las demás y en el orden de la herramienta, tantas como menciones del nombre
    sobren tras descontar esas. Cada línea (o ítem de lista) cuenta como una mención como
    mucho: repetir el nombre en el mismo párrafo no suma tarjetas.

    Las tarjetas siguen el orden en que el texto nombra cada opción y son como mucho
    ``MAX_PRESENTED_OPTIONS``: si el modelo lista más, se quedan las primeras que nombra.

    Si la respuesta no nombra ninguna, se conservan las primeras ``MAX_OPTIONS``. La ficha
    (``ficha``) y la recomendación por foto (``origin == "foto"``) no se tocan: ya son
    exactamente lo presentado. Devuelve el mismo objeto si no cambia nada.
    """

    if not isinstance(result, Mapping):
        return result
    options = result.get("options")
    if (
        not isinstance(options, list)
        or not options
        or result.get("ficha")
        or result.get("origin") == "foto"
    ):
        return result
    groups: dict[str, list[int]] = defaultdict(list)
    skus: dict[int, str] = {}
    for index, option in enumerate(options):
        if not isinstance(option, Mapping):
            continue
        sku = _plain(str(option.get("sku") or ""))
        if sku:
            skus[index] = sku
        name = _plain(str(option.get("name") or ""))
        if name:
            groups[name].append(index)
    text = f" {_plain(answer)} "
    sku_named = {index for index, sku in skus.items() if f" {sku} " in text}
    # Los nombres cuentan una vez por línea (o ítem de lista) como mucho.
    name_lines: dict[str, int] = defaultdict(int)
    for line in answer.splitlines():
        for name, count in _mentions(f" {_plain(line)} ", set(groups)).items():
            name_lines[name] += min(count, 1)
    kept = set(sku_named)
    for name, members in groups.items():
        by_sku = [index for index in members if index in sku_named]
        rest = [index for index in members if index not in sku_named]
        kept.update(rest[: max(0, name_lines[name] - len(by_sku))])
    if not kept:
        kept = set(range(min(len(options), MAX_OPTIONS)))

    def first_mention(index: int) -> tuple[int, int]:
        # Posición de la primera mención (SKU o nombre); empata el orden de la herramienta.
        option = options[index]
        terms = (_plain(str(option.get(key) or "")) for key in ("sku", "name"))
        found = [text.find(f" {term} ") for term in terms if term]
        positions = [position for position in found if position != -1]
        return (min(positions) if positions else len(text), index)

    ordered = sorted(kept, key=first_mention)[:MAX_PRESENTED_OPTIONS]
    if ordered == list(range(len(options))):
        return result
    return {**result, "options": [options[index] for index in ordered]}
