"""Línea de estado del modo ``tools`` (spec 044, FR-018, ``contracts/sse-events.md``).

Cada herramienta, al empezar, publica ``{"type": "status", "text", "step"}`` por el canal
``custom`` de LangGraph (research R1). El texto sale de plantillas fijas en el tono del
asistente: nunca nombra herramientas, ni adelanta resultados, precios o fuentes. Solo
puede llevar el dato principal que aportó la persona (producto, cantidad, ciudad), saneado
aquí aunque el cliente lo pinte con ``textContent``.

``sin_consulta`` y las herramientas desconocidas no publican nada. Desde la spec 045 la
plantilla de cada herramienta sale del registro (``tools/registry.py``,
``STATUS_TEMPLATES``); aquí viven las de las seis de la 044 (:data:`BUILTIN_TEMPLATES`) y
los saneadores. Las plantillas de las herramientas nuevas no interpolan valores.
"""

import re
import unicodedata
from collections.abc import Callable, Mapping
from types import ModuleType
from typing import Any, Final, Literal, TypedDict

from langgraph.config import get_stream_writer

from carvajal.agent.toolagent.state import (
    TOOL_BUSCAR_CORPUS,
    TOOL_BUSCAR_PRODUCTO,
    TOOL_BUSCAR_SEDES,
    TOOL_COTIZAR,
    TOOL_INDICADOR_FINANCIERO,
)

__all__ = [
    "BUILTIN_TEMPLATES",
    "MAX_VALUE_CHARS",
    "StatusEvent",
    "emit_status",
    "status_text",
]

# Largo máximo del dato de la persona dentro del texto de estado.
MAX_VALUE_CHARS: Final = 40
# Cantidades por encima de este tope se consideran ruido y no se muestran.
_MAX_QUANTITY: Final = 10**12

# Claves posibles de cada dato: nombres en español de los esquemas y los de QuoteRequest.
_PRODUCT_KEYS: Final = ("familia", "family", "producto")
_QUANTITY_KEYS: Final = ("cantidad", "quantity")
_CITY_KEYS: Final = ("ciudad", "city")

# Familias del catálogo (``catalog.models.Family``) en singular y plural para la persona.
_FAMILY_FORMS: Final[dict[str, tuple[str, str]]] = {
    "vaso": ("vaso", "vasos"),
    "tapa": ("tapa", "tapas"),
    "contenedor": ("contenedor", "contenedores"),
    "charola": ("charola", "charolas"),
    "cubierto": ("cubierto", "cubiertos"),
    "envase": ("envase", "envases"),
    "plato": ("plato", "platos"),
}

# Conectores que se dejan en minúscula al capitalizar una ciudad («Santa Fe de Antioquia»).
_CITY_CONNECTORS: Final = frozenset({"de", "del", "la", "las", "los", "el", "y"})
# Caracteres de marcado que nunca hacen falta en un producto o una ciudad.
_MARKUP_CHARS: Final = str.maketrans("", "", "<>`")
_GROUPED_DIGITS: Final = re.compile(r"\d{1,3}(?:[.\s]\d{3})+|\d+")


class StatusEvent(TypedDict):
    """Evento ``status`` del canal ``custom``; el servicio lo reemite como SSE."""

    type: Literal["status"]
    text: str
    step: int


def _clean(value: object) -> str | None:
    """Sanea un dato de la persona: solo texto, una línea, sin control ni marcado, corto.

    Quita caracteres de control y de formato (incluye saltos de línea, ``NUL``, escapes
    ANSI y marcas bidireccionales), colapsa espacios, cambia ``_`` por espacio para que
    un identificador no se lea como jerga y descarta el valor si nombra una herramienta.
    """

    if not isinstance(value, str):
        return None
    chars = (
        " " if char.isspace() else char
        for char in value
        if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf"}
    )
    text = "".join(chars).translate(_MARKUP_CHARS)
    lowered = text.lower()
    if any(name in lowered for name in _registry().TOOL_NAMES):
        return None
    text = " ".join(text.replace("_", " ").split())
    text = text[:MAX_VALUE_CHARS].rstrip()
    return text or None


def _first(args: Mapping[str, Any], keys: tuple[str, ...]) -> object:
    """Primer valor no nulo entre las claves aceptadas para un dato."""

    for key in keys:
        value = args.get(key)
        if value is not None:
            return value
    return None


def _quantity(args: Mapping[str, Any]) -> int | None:
    """Cantidad entera positiva o ``None``; acepta ``2000``, ``2000.0`` y ``"2.000"``."""

    value = _first(args, _QUANTITY_KEYS)
    number: int | None = None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if _GROUPED_DIGITS.fullmatch(text):
            number = int(re.sub(r"[.\s]", "", text))
    if number is None or not 0 < number < _MAX_QUANTITY:
        return None
    return number


def _format_quantity(number: int) -> str:
    """Cantidad con punto como separador de miles («2.000»)."""

    return f"{number:,}".replace(",", ".")


def _product(args: Mapping[str, Any], *, singular: bool = False) -> str | None:
    """Producto para la persona: familia del catálogo en plural (o singular) o texto libre."""

    text = _clean(_first(args, _PRODUCT_KEYS))
    if text is None:
        return None
    forms = _FAMILY_FORMS.get(text.lower())
    if forms is None:
        return text
    return forms[0] if singular else forms[1]


def _city(args: Mapping[str, Any]) -> str | None:
    """Ciudad saneada; si llegó toda en minúscula, se capitaliza por palabras."""

    text = _clean(_first(args, _CITY_KEYS))
    if text is None or text != text.lower():
        return text
    words = text.split(" ")
    return " ".join(
        word if index and word in _CITY_CONNECTORS else word[:1].upper() + word[1:]
        for index, word in enumerate(words)
    )


def _buscar_corpus(_args: Mapping[str, Any]) -> str:
    return "Estoy buscando en la información de Carvajal…"


def _buscar_producto(args: Mapping[str, Any]) -> str:
    product = _product(args)
    if product is None:
        return "Estoy revisando el catálogo…"
    return f"Estoy revisando qué {product} te sirven…"


def _cotizar(args: Mapping[str, Any]) -> str:
    quantity = _quantity(args)
    product = _product(args, singular=quantity == 1)
    city = _city(args)
    where = f" en {city}" if city else ""
    if quantity is not None:
        verb = "sale" if quantity == 1 else "salen"
        what = product or ("unidad" if quantity == 1 else "unidades")
        return f"Estoy calculando cuánto te {verb} {_format_quantity(quantity)} {what}{where}…"
    if product is not None:
        return f"Estoy calculando cuánto te salen tus {product}{where}…"
    return f"Estoy calculando tu cotización{where}…"


def _buscar_sedes(args: Mapping[str, Any]) -> str:
    city = _city(args)
    if city is None:
        return "Estoy buscando dónde puedes comprar…"
    return f"Estoy buscando dónde comprar en {city}…"


def _indicador_financiero(_args: Mapping[str, Any]) -> str:
    return "Estoy consultando las cifras de Carvajal…"


# Plantillas de las seis herramientas de la 044 (``sin_consulta`` no tiene); el registro
# las asigna a cada ``ToolSpec``.
BUILTIN_TEMPLATES: Final[Mapping[str, Callable[[Mapping[str, Any]], str]]] = {
    TOOL_BUSCAR_CORPUS: _buscar_corpus,
    TOOL_BUSCAR_PRODUCTO: _buscar_producto,
    TOOL_COTIZAR: _cotizar,
    TOOL_BUSCAR_SEDES: _buscar_sedes,
    TOOL_INDICADOR_FINANCIERO: _indicador_financiero,
}


def _registry() -> ModuleType:
    """Registro de herramientas, importado al usarlo: el registro importa este módulo."""

    from carvajal.agent.toolagent.tools import registry

    return registry


def status_text(tool_name: str, args: Mapping[str, Any] | None) -> str | None:
    """Texto amigable de la línea de estado para una herramienta que empieza.

    Función pura. Devuelve ``None`` para ``sin_consulta`` (no consulta nada) y para
    nombres desconocidos. Los argumentos ausentes o mal formados no fallan: se usa la
    variante sin datos.
    """

    template = _registry().STATUS_TEMPLATES.get(tool_name)
    if template is None:
        return None
    return template(args if isinstance(args, Mapping) else {})


def emit_status(tool_name: str, args: Mapping[str, Any] | None, step: int) -> StatusEvent | None:
    """Publica el estado de la herramienta por el canal ``custom`` y devuelve el evento.

    ``step`` es el orden de la herramienta en el turno (desde 1). Fuera de un grafo de
    LangGraph no hay escritor: el evento se devuelve sin publicarse y no se lanza error.
    Devuelve ``None`` cuando la herramienta no tiene línea de estado.
    """

    if step < 1:
        raise ValueError(f"step debe ser >= 1, llegó {step}")
    text = status_text(tool_name, args)
    if text is None:
        return None
    event: StatusEvent = {"type": "status", "text": text, "step": step}
    try:
        writer = get_stream_writer()
    except RuntimeError:  # fuera de un contexto ejecutable de LangGraph
        return event
    writer(event)
    return event
