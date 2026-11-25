"""Respuestas cortas que siguen a un turno: elegir, confirmar y conducir la cotización.

Spec 053 (elegir y confirmar) y spec 054 (flujos de cotización). Se resuelven sin el modelo,
con reglas, porque de ellas depende qué se cotiza y si una cotización se registra:

- :func:`resolve_choice` entiende «dame esa», «la recomendada», «vamos con la alternativa 1»,
  «la 2» u «opción 3» sobre una recomendación de foto (una recomendada y alternativas
  numeradas). Los números y ordinales nombran alternativas («la primera» = alternativa 1);
  los deícticos («esa», «dame esa») y «la recomendada» o «la mejor» nombran la recomendada.
- :func:`is_confirmation` reconoce un «sí» a una propuesta de cotización: el mensaje entero
  debe ser asentimiento («sí», «confírmala», «dale», «listo», «de una», «sale»); «sí, pero
  mejor 800» no confirma, porque cambia la propuesta. «Sí, mándame el PDF» sí confirma.
- Spec 054: :func:`resolve_more` (respuesta a «¿Algo más?»), :func:`resolve_switch`
  (cancelar o conservar el producto anterior), :func:`parse_quantities` (hasta cuatro
  cantidades, también dictadas con palabras), :func:`resolve_quantity_choice`,
  :func:`resolve_remove`, :func:`wants_switch` y los detectores de salida, retomar,
  cancelar todo, «está caro» y el rechazo con insulto (:func:`is_price_insult`).

Todas trabajan sobre texto: una nota de voz llega ya transcrita. Ante la duda devuelven
``None`` y el turno sigue el camino normal (o pasa por Jev, ver ``carvajal.quotation.flow``).
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from carvajal.numbers import word_numbers as _word_numbers
from carvajal.numbers import words_to_number

__all__ = [
    "Choice",
    "ChoiceOutOfRange",
    "MoreAnswer",
    "SwitchAnswer",
    "is_cancel_all",
    "is_confirmation",
    "is_leaving",
    "is_price_insult",
    "is_resume",
    "is_too_expensive",
    "parse_quantities",
    "plain",
    "resolve_choice",
    "resolve_more",
    "resolve_quantity_choice",
    "resolve_remove",
    "resolve_switch",
    "strip_assent",
    "wants_switch",
    "words_to_number",
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


def plain(text: str) -> str:
    """Minúsculas sin tildes ni puntuación, con un espacio entre palabras."""

    folded = unicodedata.normalize("NFKD", text.casefold())
    without = "".join(ch for ch in folded if not unicodedata.combining(ch))
    cleaned = re.sub(r"[¡!¿.,;:()\"«»“”]+", " ", without)
    return " ".join(cleaned.split())


_plain = plain


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
        "genera", "generala", "emitela", "emite", "sirve", "confirmamos", "confirmemos",
    }
)  # fmt: skip
_FILLER: Final = frozenset(
    {
        "por", "favor", "porfa", "porfavor", "gracias", "muchas", "pues", "entonces", "la",
        "lo", "esa", "esta", "esto", "cotizacion", "propuesta", "asi", "todo", "senor",
        "senora", "ya", "y", "que", "me", "nos", "parece", "de", "una", "bueno", "vale",
        "super", "excelente", "mil", "le", "el", "con",
        # Spec 054: «sí, mándame el PDF» confirma y pide el documento a la vez.
        "pdf", "mandame", "manda", "mandala", "enviame", "envia", "enviala", "envíamela",
        "mandamela", "enviamela", "pasame", "pasamela", "en",
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


# --- Spec 054: flujos de cotización --------------------------------------------------------

MoreAnswer = Literal["add", "close", "close_confirm"]
SwitchAnswer = Literal["cancel", "keep"]

_DIGITS: Final = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d+)(?:\s*(mil)\b)?")
# Un número seguido de una capacidad o de un precio no es una cantidad.
_NOT_QUANTITY: Final = re.compile(r"^\s*(?:oz|onzas?|ml|mililitros?|litros?|l|pesos|cop|%|gr)\b")
_PRICE_BEFORE: Final = re.compile(r"\$\s*$")


def parse_quantities(text: str, limit: int = 4) -> tuple[int, ...]:
    """Cantidades del mensaje, en orden y sin repetir: «100, 500 o 1000», «cien y mil».

    No cuentan capacidades («12 oz») ni precios («$5.000»). «2 mil» y «dos mil» son 2.000;
    «1.500» y «1,500» son 1.500. Quien llama decide si un número suelto es una opción.
    """

    folded = plain(text.replace("$", " $ "))
    found: list[tuple[int, int]] = []
    taken: list[tuple[int, int]] = []
    for match in _DIGITS.finditer(folded):
        rest = folded[match.end() :]
        if _NOT_QUANTITY.match(rest) or _PRICE_BEFORE.search(folded[: match.start()]):
            continue
        value = int(re.sub(r"[.,]", "", match.group(1)))
        if match.group(2):
            value *= 1000
        found.append((match.start(), value))
        taken.append((match.start(), match.end()))
    words = folded.split()
    offsets: list[int] = []
    position = 0
    for word in words:
        position = folded.index(word, position)
        offsets.append(position)
        position += len(word)
    for start, end, phrase in _word_numbers(words):
        if phrase in {"un", "una", "uno"}:
            continue  # «una caja», «uno de esos»: artículo, no cantidad
        begin = offsets[start]
        if any(left <= begin < right for left, right in taken):
            continue  # «2 mil»: el «mil» ya va con su cifra
        after = folded[offsets[end - 1] + len(words[end - 1]) :]
        if _NOT_QUANTITY.match(after):
            continue
        value = words_to_number(phrase)
        if value:
            found.append((begin, value))
    ordered: list[int] = []
    for _, value in sorted(found):
        if value > 0 and value not in ordered:
            ordered.append(value)
    return tuple(ordered[:limit])


def resolve_quantity_choice(text: str, quantities: tuple[int, ...]) -> int | None:
    """Cantidad elegida de un comparativo: «con mil», «la de 500», «la segunda», «la mayor»."""

    if "?" in text or not quantities:
        return None
    said = [value for value in parse_quantities(text) if value in quantities]
    if len(said) == 1:
        return said[0]
    folded = plain(text)
    if re.search(r"\b(?:la|el)\s+(?:mas\s+grande|mayor|de\s+mas)\b", folded):
        return max(quantities)
    if re.search(r"\b(?:la|el)\s+(?:mas\s+pequena|menor|de\s+menos)\b", folded):
        return min(quantities)
    found = resolve_choice(text, len(quantities))
    if isinstance(found, Choice) and found.index >= 1:
        return quantities[found.index - 1]
    return None


_ADD: Final = re.compile(
    r"^(?:(?:si|claro|ok|dale|bueno|listo|tambien|ah|y|pues)\s+)*"
    r"(?:agrega\w*|anade\w*|suma\w*|pon\w*|incluye\w*|tambien|otro|otra|mas|quiero|necesito|"
    r"cotiza\w*|y\s+(?:unos?|unas?)|\d+|un|una|unos|unas)\b"
)
_ADD_BARE: Final = frozenset(
    {"si", "claro", "dale", "ok", "obvio", "porfa", "si señor", "sip", "listo", "bueno"}
)
_CLOSE: Final = re.compile(
    r"^(?:(?:no|nop|nada|ya|asi|eso|solo|solamente|listo|gracias|por|ahora|nada|esta|bien|"
    r"es|todo|seria|sería|mas|más|señor|senor|senora|muchas|ninguno|ninguna|ok|y|con|eso)\s*)+$"
)
_CONFIRM_TAIL: Final = re.compile(
    r"\b(?:confirm\w*|registr\w*|genera\w*|emit\w*|pdf|mand\w*\s+(?:el\s+)?pdf|cierra\w*|"
    r"ciérrala|cerrala)\b"
)


def strip_assent(text: str) -> str:
    """«Sí, 500 vasos» → «500 vasos»: el pedido sin el asentimiento que lo abre."""

    return (
        re.sub(
            r"^\s*(?:s[ií]|claro|ok|dale|bueno|listo|también|tambien)\b[\s,.:;!]*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        or text.strip()
    )


def resolve_more(text: str) -> MoreAnswer | None:
    """Respuesta a «¿Algo más?»: agregar, cerrar o cerrar y confirmar de una vez."""

    if "?" in text:
        return None
    folded = plain(text)
    if not folded:
        return None
    words = folded.split()
    if "pdf" in words and len(words) <= 6 and not {"no", "sin"} & set(words[1:]):
        return "close_confirm"  # «PDF», «mándame el PDF», «genéralo en PDF»
    if folded.startswith(("no", "nada", "eso es todo", "ya no", "asi esta", "solo eso")) and (
        _CONFIRM_TAIL.search(folded)
    ):
        return "close_confirm"
    if words[0] in {"confirma", "confirmala", "confirmalo", "registrala", "generala"} or (
        folded in {"genera el pdf", "manda el pdf", "mandame el pdf", "confirmar"}
    ):
        return "close_confirm"
    if _CLOSE.match(folded) and words[0] in {
        "no", "nop", "nada", "ya", "asi", "eso", "solo", "solamente", "ninguno", "ninguna",
    }:  # fmt: skip
        return "close"
    if folded in {"eso es todo", "es todo", "nada mas", "solo eso", "asi esta bien", "ya esta"}:
        return "close"
    if folded in _ADD_BARE or _ADD.match(folded):
        return "add"
    return None


_CANCEL_SWITCH: Final = re.compile(
    r"\b(?:cancel\w*|quit\w*|borr\w*|elimin\w*|olvid\w*|descart\w*|ya\s+no|no\s+los?\s+quiero|"
    r"no\s+las?\s+quiero|cambi\w*|reemplaz\w*|no\s+(?:los|las)\s+(?:necesito|conserves))\b"
)
_KEEP_SWITCH: Final = re.compile(
    r"\b(?:conserv\w*|mant[eé]n\w*|manten\w*|dejal\w*|dej\w*\s+(?:los|las|ambos|las\s+dos|los\s+dos)|"
    r"agreg\w*|ambos|las\s+dos|los\s+dos|tambien|sumal\w*|anad\w*|quedate|no\s+los?\s+quites|"
    r"no\s+las?\s+quites|no\s+cancel\w*)\b"
)


def resolve_switch(text: str) -> SwitchAnswer | None:
    """Respuesta a «¿Cancelo los platos o los conservo?»: por palabra o por número (1/2)."""

    if "?" in text:
        return None
    folded = plain(text)
    keep = bool(_KEEP_SWITCH.search(folded))
    cancel = bool(_CANCEL_SWITCH.search(folded)) and not re.search(r"\bno\s+cancel", folded)
    if keep and not cancel:
        return "keep"
    if cancel and not keep:
        return "cancel"
    found = resolve_choice(text, 2)
    if isinstance(found, Choice) and found.index in {1, 2}:
        return "cancel" if found.index == 1 else "keep"
    return None


_SWITCH: Final = re.compile(
    r"^(?:no\b|nop\b|mejor\b|en\s+vez|en\s+lugar|cambia\w*|cambiemos|olvida\w*|"
    r"ya\s+no\b|mas\s+bien\b|perdon\b|espera\b)"
)


def wants_switch(text: str) -> bool:
    """El mensaje abre con un cambio de rumbo: «no, mejor vasos», «en vez de eso, tapas»."""

    return bool(_SWITCH.match(plain(text)))


_REMOVE: Final = re.compile(r"\b(?:quit\w*|borr\w*|elimin\w*|sac\w*)\b")
_ORDINAL_INDEX: Final = {
    "primera": 1, "primero": 1, "segunda": 2, "segundo": 2, "tercera": 3, "tercero": 3,
    "cuarta": 4, "cuarto": 4, "quinta": 5, "quinto": 5, "ultima": -1, "ultimo": -1,
}  # fmt: skip


def resolve_remove(text: str, labels: tuple[str, ...]) -> int | None:
    """Índice (desde 0) de la línea que se quiere quitar: «quita los vasos», «borra la 2»."""

    folded = plain(text)
    if "?" in text or not labels or not _REMOVE.search(folded):
        return None
    number = re.search(r"\b(?:la|el|linea|producto|numero|#)?\s*(\d{1,2})\b", folded)
    if number:
        index = int(number.group(1)) - 1
        return index if 0 <= index < len(labels) else None
    for word, position in _ORDINAL_INDEX.items():
        if re.search(rf"\b{word}\b", folded):
            index = len(labels) - 1 if position == -1 else position - 1
            return index if 0 <= index < len(labels) else None
    matches = []
    for index, label in enumerate(labels):
        head = [word for word in plain(label).split() if len(word) > 3][:2]
        stems = {word[:-1] if word.endswith("s") else word for word in head}
        if any(re.search(rf"\b{re.escape(stem)}\w*\b", folded) for stem in stems):
            matches.append(index)
    return matches[0] if len(matches) == 1 else None


_CANCEL_ALL: Final = re.compile(
    r"\b(?:cancela\w*|olvida\w*|borra\w*|descarta\w*)\s+(?:toda\s+)?(?:todo|la\s+cotizacion|"
    r"esa\s+cotizacion|toda\s+la\s+cotizacion|el\s+pedido)\b"
    r"|\bempe(?:z|c)\w*\s+de\s+(?:nuevo|cero)\b|\bya\s+no\s+quiero\s+(?:nada|cotizar)\b"
)


def is_cancel_all(text: str) -> bool:
    """«Cancela todo», «olvida la cotización», «empecemos de nuevo»."""

    return bool(_CANCEL_ALL.search(plain(text)))


_LEAVING: Final = re.compile(
    r"\b(?:lo\s+(?:voy\s+a\s+)?(?:pienso|pensar|penso)|(?:voy\s+a\s+|lo\s+)?pensarlo|"
    r"solo\s+(?:estaba\s+)?(?:mirando|viendo|preguntando|averiguando)|"
    r"despues\s+(?:te\s+)?(?:escribo|aviso|miro|vuelvo)|luego\s+(?:te\s+)?(?:escribo|aviso|vuelvo)|"
    r"por\s+ahora\s+no|no\s+por\s+ahora|ahorita\s+no|otro\s+dia|mas\s+adelante|"
    r"no\s+(?:voy\s+a\s+)?compr\w*|no\s+me\s+interesa|ya\s+no\s+(?:me\s+interesa|lo\s+necesito))\b"
)


def is_leaving(text: str) -> bool:
    """La persona se despide sin comprar: «gracias, lo pienso», «solo estaba mirando»."""

    if "?" in text:
        return False
    return bool(_LEAVING.search(plain(text)))


_RESUME: Final = re.compile(
    r"^(?:retom\w*|seguimos|sigamos|continu\w*|volvamos)\b|"
    r"\b(?:retomar|retomemos|seguir\s+con\s+(?:la|mi)\s+cotizacion|mi\s+cotizacion\s+pendiente)\b"
)


def is_resume(text: str) -> bool:
    """«Retomar», «sigamos con la cotización»."""

    return bool(_RESUME.search(plain(text)))


_EXPENSIVE: Final = re.compile(
    r"\b(?:(?:muy|super|demasiado|re|bastante|algo|un\s+poco)\s+)?(?:car[oa]s?|carisim[oa]s?|"
    r"costos[oa]s?)\b|\bque\s+robo\b|\bun\s+robo\b"
)
_INSULT: Final = re.compile(r"\b(?:ladr[oó]n\w*|ladrones|rater[oa]s?|ratas|choros?|estafador\w*)\b")


def is_too_expensive(text: str) -> bool:
    """«Está caro», «muy costoso»; no las preguntas («¿cuál es la menos cara?»)."""

    if "?" in text:
        return False
    folded = plain(text)
    if re.search(r"\b(?:menos|mas\s+barat|economic)\w*", folded):
        return False
    return bool(_EXPENSIVE.search(folded))


def is_price_insult(text: str) -> bool:
    """Rechazo por precio con insulto: «está muy caro, muy ladrones», «caro, ladrones»."""

    folded = plain(text)
    return bool(_EXPENSIVE.search(folded) and _INSULT.search(folded))
