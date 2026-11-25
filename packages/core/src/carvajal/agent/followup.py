"""Respuestas cortas que siguen a un turno: elegir una opción y confirmar (spec 053).

Se resuelven sin el modelo, con reglas, porque de ellas depende qué se cotiza y si una
cotización se registra:

- :func:`resolve_choice` entiende «dame esa», «la recomendada», «vamos con la alternativa 1»,
  «la 2» u «opción 3» sobre una recomendación de foto (una recomendada y alternativas
  numeradas). Los números y ordinales nombran alternativas («la primera» = alternativa 1);
  los deícticos («esa», «dame esa») y «la recomendada» o «la mejor» nombran la recomendada.
- :func:`is_confirmation` reconoce un «sí» a una propuesta de cotización: el mensaje entero
  debe ser asentimiento («sí», «confírmala», «dale», «listo», «de una», «sale»); «sí, pero
  mejor 800» no confirma, porque cambia la propuesta.

Ambas trabajan sobre texto: una nota de voz llega ya transcrita.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

__all__ = [
    "Choice",
    "ChoiceOutOfRange",
    "is_confirmation",
    "resolve_choice",
]


@dataclass(frozen=True)
class Choice:
    """Opción elegida: ``0`` es la recomendada; ``1``-``4``, la alternativa con ese número."""

    index: int


@dataclass(frozen=True)
class ChoiceOutOfRange:
    """La persona nombró una alternativa que no existe (``asked``)."""

    asked: int


_NUMBERS: Final = {
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "primera": 1, "primero": 1, "primer": 1, "1a": 1, "1ra": 1, "1ro": 1,
    "segunda": 2, "segundo": 2, "2a": 2, "2da": 2, "2do": 2,
    "tercera": 3, "tercero": 3, "tercer": 3, "3a": 3, "3ra": 3, "3ro": 3,
    "cuarta": 4, "cuarto": 4, "4a": 4, "4ta": 4, "4to": 4,
    "quinta": 5, "quinto": 5,
}  # fmt: skip
_NUMBER: Final = "|".join(sorted(_NUMBERS, key=len, reverse=True))
# Lo que puede ir antes de la elección: asentimiento y verbos de elegir.
_LEAD: Final = (
    r"(?:(?:si|ok|okay|listo|bueno|bien|perfecto|dale|entonces|pues|mejor|y)\s+)*"
    r"(?P<verb>(?:vamos|voy|vamonos)\s+(?:con|por)|me\s+quedo\s+con|me\s+voy\s+por|"
    r"quiero|prefiero|elijo|escojo|selecciono|tomo|dame|deme|damela|regalame|mandame|"
    r"cotizame|cotiza|me\s+sirve|me\s+gusta|me\s+interesa|me\s+llevo|llevo|seria|sera)?\s*"
)
_ARTICLE: Final = r"(?P<article>(?:la|el)\s+)?"
_NOUN: Final = r"(?P<noun>(?:alternativa|opcion|numero|referencia|#)\s*)?"
_ALTERNATIVE: Final = re.compile(
    rf"^{_LEAD}{_ARTICLE}{_NOUN}(?P<n>{_NUMBER})\b(?:\s+(?:alternativa|opcion))?(?P<rest>.*)$"
)
# Tras el número, una unidad dice que es una cantidad («2 cajas»), no una opción.
_QUANTITY: Final = re.compile(
    r"^\s*(?:mil|cajas?|paquetes?|unidades|pacas?|docenas?|bolsas?|millares?|oz|onzas)\b"
)
# «alternativa 2» en cualquier parte del mensaje: la palabra no deja dudas.
_ALTERNATIVE_ANYWHERE: Final = re.compile(rf"\balternativa\s*(?:#\s*)?(?P<n>{_NUMBER})\b")
_RECOMMENDED: Final = re.compile(
    rf"^{_LEAD}(?:"
    r"(?:esa|ese|eso|esta|este)(?:\s+(?:misma|mismo|opcion|referencia|recomendada|"
    r"recomendacion))?"
    r"|(?:la|el|lo)\s+(?:recomendada|recomendado|que\s+recomiendas|que\s+me\s+recomiendas|"
    r"mejor|primera\s+que\s+me\s+(?:diste|mostraste))"
    r"|(?:tu|su)\s+recomendacion|la\s+recomendacion"
    r")\b(?P<rest>.*)$"
)
# Un cambio a lo elegido («la 2 pero en 12 oz») es otra búsqueda, no una elección literal.
_CHANGE: Final = re.compile(
    r"\b(?:pero|aunque|en\s+vez|en\s+lugar)\b.*\b(?:oz|onzas|ml|litro|grande|pequen|chic|"
    r"material|otr[oa]|tapa|color|carton|plastico|icopor|pulpa|papel)"
)


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    without = "".join(ch for ch in folded if not unicodedata.combining(ch))
    cleaned = re.sub(r"[¡!¿.,;:()\"«»“”]+", " ", without)
    return " ".join(cleaned.split())


def resolve_choice(text: str, alternatives: int) -> Choice | ChoiceOutOfRange | None:
    """Opción que nombra ``text`` entre una recomendada y ``alternatives`` alternativas.

    Devuelve ``None`` si el mensaje no es una elección (una pregunta, otra búsqueda o un
    cambio a la opción), :class:`ChoiceOutOfRange` si nombra una alternativa que no existe
    y :class:`Choice` en otro caso.
    """

    if "?" in text:
        return None  # «¿la 2 sirve para microondas?» es una pregunta, no una elección
    plain = _plain(text)
    if not plain:
        return None
    match = _ALTERNATIVE.match(plain)
    if match is not None:
        rest = match["rest"]
        named = match["verb"] or match["article"] or match["noun"]
        # Un número suelto solo elige si es todo el mensaje («2»); «2 cajas» es cantidad.
        if _QUANTITY.match(rest) or (not named and rest.strip()):
            match = None
    if match is None:
        match = _ALTERNATIVE_ANYWHERE.search(plain)
    if match is not None:
        rest = match.groupdict().get("rest") or plain[match.end() :]
        if _CHANGE.search(rest):
            return None
        asked = _NUMBERS[match["n"]]
        if not 1 <= asked <= alternatives:
            return ChoiceOutOfRange(asked)
        return Choice(asked)
    match = _RECOMMENDED.match(plain)
    if match is not None and not _CHANGE.search(match["rest"]):
        return Choice(0)
    return None


_AFFIRM: Final = frozenset(
    {
        "si", "sip", "confirmo", "confirma", "confirmala", "confirmalo", "confirmar",
        "confirmada", "confirmado", "dale", "listo", "lista", "ok", "okay", "oki", "va",
        "perfecto", "correcto", "adelante", "claro", "hagale", "hagamosle", "sale", "orale",
        "simon", "registrala", "registralo", "registra", "registrar", "procede", "proceder",
        "afirmativo", "bien", "deuna", "exacto", "eso", "acepto", "aceptada", "hazlo",
        "genera", "generala", "emitela", "emite", "sirve",
    }
)  # fmt: skip
_FILLER: Final = frozenset(
    {
        "por", "favor", "porfa", "porfavor", "gracias", "muchas", "pues", "entonces", "la",
        "lo", "esa", "esta", "esto", "cotizacion", "propuesta", "asi", "todo", "senor",
        "senora", "ya", "y", "que", "me", "nos", "parece", "de", "una", "bueno", "vale",
        "super", "excelente", "mil", "le", "el", "con",
    }
)  # fmt: skip
_DOUBT: Final = frozenset({"no", "nop", "nel", "pero", "aunque", "mejor", "cambia", "otra"})


def is_confirmation(text: str) -> bool:
    """Indica si ``text`` es un «sí» a la propuesta, sin cambios ni dudas."""

    if "?" in text:
        return False
    plain = _plain(text).replace("de una", "deuna").replace("va que va", "va")
    words = plain.split()
    if not words or len(words) > 8:
        return False
    if any(word in _DOUBT for word in words):
        return False
    return all(word in _AFFIRM or word in _FILLER for word in words) and any(
        word in _AFFIRM for word in words
    )
