"""Ayudantes comunes de las tres herramientas de memoria (spec 045, contrato *Memoria*).

- :func:`said_in_question`: regla «dicho en la pregunta» de ``recordar_preferencia``
  (defensa ante inyección indirecta, research R7).
- :func:`forget_requested`: regla «pedido en la pregunta» de ``olvidar_preferencia``.
- :func:`memory_update`: el ``state_update`` del turno tras una escritura (canales
  ``memory`` y ``preferences``).
- :func:`store_error`: el ``error`` declarado sin dueño o sin Store.

El dueño sale siempre de ``ToolContext.owner_key`` (estado del grafo, firmado por el
servidor); ningún esquema de memoria tiene campos de identidad.
"""

import re
import unicodedata
from typing import Any, Final

from carvajal.agent.memory.store import MemoryProfile, normalize_format, valid_owner
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Draft

__all__ = [
    "SENSITIVE_LABELS",
    "forget_requested",
    "memory_update",
    "owner_error",
    "said_in_question",
]

# Nombre legible de cada tipo de dato sensible: el mensaje lo nombra sin repetir el dato.
SENSITIVE_LABELS: Final = {
    "documento": "documento de identidad",
    "telefono": "teléfono",
    "correo": "correo",
    "tarjeta": "datos de tarjeta",
    "cuenta": "datos de cuenta bancaria",
    "clave": "claves",
}
# Palabras que no hacen falta para saber que la persona dijo el valor.
_STOPWORDS: Final = frozenset(
    {
        "a",
        "al",
        "con",
        "de",
        "del",
        "el",
        "en",
        "la",
        "las",
        "lo",
        "los",
        "mi",
        "mis",
        "me",
        "para",
        "por",
        "que",
        "se",
        "su",
        "sus",
        "un",
        "una",
        "y",
        "o",
        "prefiere",
        "prefiero",
        "persona",
        "usuario",
        "usuaria",
        "cliente",
    }
)
_WORDS: Final = re.compile(r"[^\W_]+")
_FORGET_VERB: Final = re.compile(r"\b(?:olvid\w*|borr\w*|elimin\w*|no\s+recuerdes)\b")


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text).casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _words(text: str) -> list[str]:
    return _WORDS.findall(_plain(text))


def _close(word: str, said: set[str]) -> bool:
    """La palabra está dicha, o dicha con otro número o género («compostable(s)»)."""

    if word in said:
        return True
    if len(word) < 5:
        return False
    return any(
        len(other) >= 5 and other.startswith(word[:-2]) and word.startswith(other[:-2])
        for other in said
    )


def said_in_question(category: str, value: str, context: ToolContext) -> bool:
    """Las palabras significativas de ``value`` aparecen en la pregunta del turno (R7).

    Un fragmento del corpus o un resultado de herramienta que «pide» guardar algo no basta:
    solo cuenta lo que escribió la persona en este turno. Para ``formato_preferido`` basta
    con que la pregunta nombre el mismo estilo («respuestas cortas» → ``corto``).
    """

    question = context.question or ""
    if category == "formato_preferido":
        style = normalize_format(value)
        if style is not None and normalize_format(question) == style:
            return True
    said = set(_words(question))
    words = _words(value)
    significant = [word for word in words if word not in _STOPWORDS] or words
    return bool(significant) and all(_close(word, said) for word in significant)


def forget_requested(context: ToolContext) -> bool:
    """La pregunta pide olvidar («olvida», «olvídate», «borra», «no recuerdes»)."""

    return _FORGET_VERB.search(" ".join(_words(context.question or ""))) is not None


def memory_update(profile: MemoryProfile | None) -> dict[str, Any]:
    """Canales del turno tras escribir: instantánea y líneas de ``<preferencias>``."""

    current = profile or MemoryProfile()
    return {"memory": current.snapshot(), "preferences": current.lines()}


def owner_error(deps: ToolDeps, context: ToolContext) -> _Draft | None:
    """``error`` declarado si no hay dueño válido o Store; ``None`` si se puede seguir."""

    if not valid_owner(context.owner_key):
        return _Draft(
            "error",
            "No hay una identidad de la persona en este turno; la memoria no se usa.",
            data={"motivo": "sin_dueno"},
        )
    if deps.memory is None:
        return _Draft(
            "error",
            "La memoria no está disponible en este momento.",
            data={"motivo": "store_no_disponible"},
        )
    return None
