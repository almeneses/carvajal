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
  :func:`resolve_remove`, :func:`wants_switch`, :func:`wants_change` (editar la cotización
  sin decir qué), :func:`wants_edit` (pedir un cambio concreto con «quiero cambiar…»),
  :func:`names_amount` (el mensaje lleva una cantidad y no el número de una línea),
  :func:`removes_whole_line` (el pedido quita una línea entera, no algo de ella) y los
  detectores de salida, retomar, cancelar todo, «está caro» y el rechazo con insulto
  (:func:`is_price_insult`).
- :func:`resolve_reference` elige entre las referencias de un nombre ambiguo («la 2», el
  SKU, «el Gold»); «sí» o «esa» no eligen ninguna y se vuelve a preguntar.
  :func:`reference_quantity` saca la cantidad (y su unidad) dicha junto a esa elección.

Todas trabajan sobre texto: una nota de voz llega ya transcrita. Ante la duda devuelven
``None`` y el turno sigue el camino normal (o pasa por Jev, ver ``carvajal.quotation.flow``).
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Final, Literal

from carvajal.numbers import word_numbers as _word_numbers
from carvajal.numbers import words_to_number

__all__ = [
    "Choice",
    "ChoiceOutOfRange",
    "MoreAnswer",
    "ReferenceAnswer",
    "ReferencePick",
    "SwitchAnswer",
    "is_cancel_all",
    "is_confirmation",
    "is_leaving",
    "is_price_insult",
    "is_resume",
    "is_too_expensive",
    "names_amount",
    "parse_quantities",
    "plain",
    "reference_quantity",
    "removes_whole_line",
    "resolve_choice",
    "resolve_more",
    "resolve_quantity_change",
    "resolve_quantity_choice",
    "resolve_reference",
    "resolve_remove",
    "resolve_switch",
    "strip_assent",
    "wants_cart",
    "wants_change",
    "wants_edit",
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
    if match is not None and not _CHANGE.search(match["rest"]) and not _CLOSING.search(plain):
        return Choice(0)
    return None


# «Eso es todo», «eso sería todo», «eso nada más»: cierran, no eligen «eso» (la recomendada).
# Solo con «eso»: «esa nada más» (solo esa) y «eso es lo que quiero» sí eligen.
_CLOSING: Final = re.compile(
    r"^(?:(?:no|ya|bueno|listo|ok|y)\s+)*eso\s+(?:(?:es|seria|era|fue|sera)\s+todo|(?:nada|no)\s+mas)\b"
)


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

    # El separador de miles va antes de :func:`plain`, que borra los puntos: «1.000» era 1.
    grouped = re.sub(r"(?<=\d)[.,](?=\d{3}(?!\d))", "", text)
    folded = plain(grouped.replace("$", " $ "))
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


# «Sacar una cotización» es pedirla, no quitar nada: tras ``sac…`` no puede seguir «cotiz…».
_NOT_QUOTE_AHEAD: Final = r"(?!\s+(?:(?:una|un|la|el|otra|otro|mi|nueva|esa|esta)\s+)*cotiz)"
# «Quiero cambiar algo» o «necesito quitar la 2» no agregan: el verbo que sigue manda.
# «Sacar» no está: «quiero sacar el pedido / el PDF / los vasos en otro color» es pedir algo,
# no quitarlo, y sigue siendo agregar (o cerrar con PDF) como antes.
_EDIT_VERB: Final = (
    r"(?:cambi\w*|modific\w*|edit\w*|corregi\w*|corrig\w*|ajust\w*|quit\w*|borr\w*|"
    r"elimin\w*|cancel\w*)"
)
# Tras «pero» sí cuenta «saca(r)…» («listo, pero saca los platos»), salvo que pida la
# cotización o el PDF («eso es todo, pero sácala en PDF» cierra).
_SACAR_LINE: Final = r"sac\w*(?!.*\b(?:cotiz\w*|pdf)\b)"
_ADD: Final = re.compile(
    r"^(?:(?:si|claro|ok|dale|bueno|listo|tambien|ah|y|pues)\s+)*"
    r"(?:agrega\w*|anade\w*|suma\w*|pon\w*|incluye\w*|tambien|otro|otra|mas|"
    rf"(?:quiero|necesito)(?!\s+{_EDIT_VERB}\b)|"
    r"cotiza\w*|sac\w*\s+(?:(?:una|un|la|otra|otro|nueva)\s+)*cotiz\w*\s+(?:de|para)\s+\w+|"
    r"y\s+(?:unos?|unas?)|\d+|un|una|unos|unas)\b"
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


_CHANGE_REQUEST: Final = re.compile(
    r"^(?:(?:si|no|bueno|pues|ok|oye|ah|eh|espera|mejor|un\s+momento|perdon)\s+)*"
    r"(?:(?:yo\s+)?(?:quiero|quisiera|necesito|deseo|me\s+gustaria|voy\s+a|vamos\s+a|hay\s+que|"
    r"puedo|podemos|podria|se\s+puede|dejame|tengo\s+que)\s+)?"
    r"(?:cambiar|modificar|editar|corregir|ajustar|cambia|modifica|edita|corrige|ajusta|"
    r"cambiemos|modifiquemos|corrijamos|ajustemos)"
    r"(?:\s+(?:algo|algo\s+mas|una\s+cosa|unas?\s+cosas?|algunas?\s+cosas?|cosas|eso|esto|"
    r"(?:la|mi|esta|esa)\s+cotizacion|el\s+pedido|el\s+carrito|(?:los|unos)\s+productos|"
    r"un\s+producto|algo\s+de\s+(?:la|mi)\s+cotizacion))?"
    r"(?:\s+(?:por\s+favor|porfa|porfavor|antes|primero))*$"
)


# «No quiero cambiar» niega el cambio; «No, quiero cambiar algo» (con la coma) lo pide. Se
# mira antes de :func:`plain`, que borra la coma. «Tampoco quiero cambiar nada» también niega.
_NEGATED_INTENT: Final = re.compile(
    r"^\s*[¿¡]?\s*(?:(?:ok|bueno|pues|no|si|yo)[\s,.;:!]+)*(?:no|tampoco)\s+"
    r"(?:quiero|quisiera|necesito|deseo|me\s+gustaria|voy\s+a|vamos\s+a|tengo\s+que)\b"
)
_EDIT_INTENT: Final = (
    rf"(?:yo\s+)?(?:quiero|quisiera|necesito|deseo|me\s+gustaria)\s+{_EDIT_VERB}\b"
)
# Lo que el «quiero/necesito» de ``_ADD`` deja de leer como agregar: pedir un cambio concreto.
_EDIT_REQUEST: Final = re.compile(
    r"^(?:(?:si|no|claro|ok|dale|bueno|listo|tambien|ah|y|pues|oye|espera|mejor|perdon)\s+)*"
    rf"{_EDIT_INTENT}"
)
# El cambio dicho a mitad de frase: «eso es todo, pero quiero cambiar la 2», «listo, pero
# quita los vasos». Tras «pero» también vale el imperativo; sin «pero», solo «quiero…».
_EDIT_INSIDE: Final = re.compile(
    rf"(?<!\bno\s)(?<!\bsin\s)(?<!\btampoco\s)\b{_EDIT_INTENT}"
    r"|\b(?:pero|aunque|solo\s+que)\s+(?:(?:si|primero|antes|tambien|eso\s+si)\s+)*"
    r"(?:(?:yo\s+)?(?:quiero|quisiera|necesito|deseo|me\s+gustaria)\s+)?"
    rf"(?:{_EDIT_VERB}\b|{_SACAR_LINE})"
)
# «Quítale la tapa a los vasos», «sácale el logo a los platos»: el pronombre -le/-les y
# «a los/las…» dicen que se quita algo de la línea, no la línea entera.
_FROM_LINE: Final = re.compile(
    r"\b(?:quit|sac|borr|elimin)\w*?les?\b(?:\s+\w+)*?\s+a\s+(?:los|las|la|el)\s+\w"
)
# El número de una línea («quita la 2», «el producto 3»), no una cantidad.
_LINE_NUMBER: Final = re.compile(r"\b(?:la|el|linea|producto|numero|#)\s*(\d{1,2})\b")


def _negated(text: str) -> bool:
    folded = unicodedata.normalize("NFKD", text.casefold())
    without = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return bool(_NEGATED_INTENT.match(without))


_CART_PLACE: Final = r"(?:en|de)\s+(?:la|mi|el)\s+(?:cotizacion|carrito|pedido)"
_CART_QUESTION: Final = re.compile(
    r"^(?:(?:y|oye|bueno|ok|a\s+ver|entonces)\s+)*(?:"
    rf"(?:que|cuanto|cuantos|cuantas)\s+(?:llevo|llevamos)(?:\s+{_CART_PLACE})?"
    rf"|(?:que|cuanto|cuantos|cuantas)\s+(?:tengo|lleva|va|van)\s+{_CART_PLACE}"
    rf"|(?:que|cuanto)\s+hay\s+{_CART_PLACE}"
    r"|(?:muestrame|ensename|dejame\s+ver|quiero\s+ver|ver|mostrar|mira|revisa)\s+"
    r"(?:la|mi|el)\s+(?:cotizacion|carrito|pedido)"
    r"|(?:como|que)\s+va\s+(?:la|mi)\s+(?:cotizacion|pedido)"
    r")$"
)


def wants_cart(text: str) -> bool:
    """«¿Qué tengo en la cotización?», «¿cuánto llevo?», «muéstrame el carrito»: ver el borrador.

    Solo la pregunta por el carrito, sin pedir otra cosa: «¿qué tengo que hacer?» no cuenta.
    """

    return bool(_CART_QUESTION.match(plain(text).replace("?", " ").strip()))


def wants_change(text: str) -> bool:
    """«Quiero cambiar algo», «modificar», «¿puedo cambiar algo?»: editar sin decir qué.

    Solo el pedido genérico; «cambia la 2 a 800» o «quiero cambiar los vasos por platos»
    ya dicen el cambio y siguen su camino. «No quiero cambiar» lo niega y no cuenta.
    """

    if _negated(text):
        return False
    return bool(_CHANGE_REQUEST.match(plain(text).replace("?", " ").strip()))


def wants_edit(text: str) -> bool:
    """«Quiero cambiar los vasos por platos», «necesito cambiar la 2 a 800»: un cambio dicho.

    Cubre todo lo que ``resolve_more`` ya no cuenta como agregar por llevar un verbo de
    edición tras «quiero/necesito» (también el pedido genérico de :func:`wants_change`), y el
    cambio que sigue a un cierre: «eso es todo, pero quiero cambiar la 2». «No quiero
    cambiar…» lo niega y no cuenta; «quiero sacar una cotización / el PDF» pide algo y tampoco.
    """

    if _negated(text):
        return False
    folded = plain(text)
    return bool(_EDIT_REQUEST.match(folded) or _EDIT_INSIDE.search(folded))


def names_amount(text: str) -> bool:
    """«Quiero sacarle 100 a los vasos»: el mensaje dice una cantidad, no el número de una línea.

    Con una cantidad el pedido cambia la línea en vez de quitarla; «quita la 2» no la lleva.
    """

    folded = plain(text)
    lines = {int(found.group(1)) for found in _LINE_NUMBER.finditer(folded)}
    return any(value not in lines for value in parse_quantities(text))


def removes_whole_line(text: str) -> bool:
    """«Quiero quitar la 2», «necesito quitar los vasos»: el pedido quita una línea entera.

    No cuando lleva una cantidad (:func:`names_amount`), cuando quita algo de la línea
    («quiero quitarle la tapa a los vasos») o cuando nombra varias líneas («quiero quitar la 1
    y la 2»): la regla quita una sola, y esos cambios los hace el agente.
    """

    folded = plain(text)
    several = len({found.group(1) for found in _LINE_NUMBER.finditer(folded)}) > 1
    return not (names_amount(text) or _FROM_LINE.search(folded) or several)


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


_REMOVE: Final = re.compile(rf"\b(?:quit\w*|borr\w*|elimin\w*|sac\w*{_NOT_QUOTE_AHEAD})\b")
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


# Palabras que dicen «otra cantidad para esa línea» («mejor que sean 800», «cambia la 1 a
# 1000», «ponle 2 cajas», «déjalo en 500»).
_QUANTITY_CHANGE: Final = re.compile(
    r"\b(?:cambi\w*|pon\w*|dej\w*|sub\w*|baj\w*|ajust\w*|modific\w*|actualiz\w*|corrig\w*|"
    r"mejor|sean?|serian?|seran?)\b"
)
# Lo único que puede acompañar el cambio de cantidad, además de la cantidad, la línea y las
# palabras del producto de esa línea. Otra palabra («cotízame», «hondos», «Bogotá») es otro
# pedido y lo atiende el agente.
_QUANTITY_CHANGE_FILLER: Final = frozenset(
    {
        "mejor", "que", "sean", "sea", "son", "serian", "seria", "seran", "sera", "a", "en",
        "de", "la", "el", "los", "las", "lo", "le", "les", "linea", "producto", "numero", "#",
        "cantidad", "unidades", "unidad", "und", "unds", "entonces", "por", "favor", "porfa",
        "si", "no", "ok", "bueno", "mas", "bien", "pero", "y", "quiero", "quisiera",
        "necesito", "total", "mil", "solo", "pues", "eso", "esa", "ese", "esos",
        "esas", "ahora", "al", "final", "finalmente", "cajas", "caja", "paquetes",
        "paquete", "pacas", "paca", "x",
    }
)  # fmt: skip
_QUANTITY_UNIT_AFTER: Final = {
    "caja": "caja", "cajas": "caja", "paquete": "paquete", "paquetes": "paquete",
    "paca": "paca", "pacas": "paca",
}  # fmt: skip


def resolve_quantity_change(
    text: str, labels: tuple[str, ...], *, default_last: bool = False
) -> tuple[int, int, str | None] | None:
    """Otra cantidad para una línea del carrito: ``(índice desde 0, cantidad, unidad)``.

    «Mejor que sean 800», «cambia la 1 a 1000», «ponle 2 cajas a los vasos», «los platos que
    sean 300». La línea es la que se nombra por su número («la 1») o por su producto («los
    vasos»); sin nombrarla, la única del carrito o, con ``default_last``, la última (la que
    se acaba de agregar). La unidad es ``caja``, ``paquete`` o ``paca`` si se dice; si no,
    ``None`` (unidades). Devuelve ``None`` si no es un cambio de cantidad, si dice varias
    cantidades o si trae otras palabras: «no, mejor cotízame 500 platos hondos» es otro
    pedido.
    """

    if "?" in text or not labels:
        return None
    folded = plain(text)
    if not _QUANTITY_CHANGE.search(folded) or _REMOVE.search(folded):
        return None
    lines = {int(found.group(1)) for found in _LINE_NUMBER.finditer(folded)}
    if len(lines) > 1:
        return None
    named_line = next(iter(lines), None)
    quantities = [value for value in parse_quantities(text) if value != named_line]
    if len(quantities) != 1:
        return None
    quantity = quantities[0]
    label_words = [{word for word in plain(label).split() if len(word) > 2} for label in labels]
    known = set().union(*label_words)
    named_by_product: set[int] = set()
    for word in folded.split():
        if word.isdigit() or word in _QUANTITY_CHANGE_FILLER or words_to_number(word):
            continue
        if _QUANTITY_CHANGE.fullmatch(word):
            continue
        stem = _stem(word, known)
        hits = {index for index, words in enumerate(label_words) if stem in words}
        if not hits:
            return None  # otra palabra: otro pedido, no un cambio de cantidad
        named_by_product |= hits
    if named_line is not None:
        index = named_line - 1
        if not 0 <= index < len(labels):
            return None
    elif len(named_by_product) == 1:
        index = next(iter(named_by_product))
    elif named_by_product:
        return None  # nombra palabras de varias líneas: lo decide el agente
    elif len(labels) == 1 or default_last:
        index = len(labels) - 1
    else:
        return None
    words = folded.split()
    unit = next(
        (
            _QUANTITY_UNIT_AFTER[after]
            for word, after in pairwise(words)
            if after in _QUANTITY_UNIT_AFTER and (word.isdigit() or words_to_number(word))
        ),
        None,
    )
    return index, quantity, unit


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


# --- Elegir entre las referencias de un nombre ambiguo ------------------------------------------


@dataclass(frozen=True)
class ReferencePick:
    """Candidata elegida: su posición (desde 0) en la lista que se mostró."""

    index: int


ReferenceAnswer = ReferencePick | ChoiceOutOfRange | Literal["ask"]

# Palabras que acompañan una elección sin decir cuál: no cuentan a favor ni en contra.
_PICK_FILLER: Final = frozenset(
    {
        "el", "la", "lo", "los", "las", "de", "del", "que", "me", "mi", "y", "en", "para",
        "con", "por", "a", "un", "una", "uno", "unos", "unas", "ese", "esa", "eso", "este",
        "esta", "si", "ok", "okay", "bueno", "listo", "dale", "pues", "entonces", "mejor",
        "porfa", "favor", "gracias", "quiero", "quisiera", "necesito", "dame", "deme",
        "cotiza", "cotizame", "cotizala", "cotizalo", "prefiero", "elijo", "escojo",
        "selecciono", "tomo", "vamos", "voy", "quedo", "llevo", "seria", "sera", "sirve",
        "gusta", "interesa", "referencia", "producto", "opcion", "numero", "son", "serian",
        "mil", "unidades", "und", "unidad", "cajas", "caja", "paquetes", "paquete", "pacas",
        "paca",
    }
)  # fmt: skip
# Respuestas que hablan de la lista sin escoger: se vuelve a preguntar, no se elige.
_PICK_VAGUE: Final = re.compile(
    r"^(?:(?:si|ok|bueno|listo|dale|pues)\s+)*(?:cualquier\w*|(?:la|el)\s+que\s+sea|"
    r"(?:la|el)\s+que\s+(?:tu\s+)?(?:digas|quieras|elijas|recomiendes)|"
    r"(?:elige|escoge)(?:la)?\s+tu|tu\s+(?:elige|escoge|decide)\w*|no\s+se|me\s+da\s+igual)$"
)
# Palabras que el material o la familia de una candidata vuelven distintivas («la de icopor»).
_MATERIAL_WORDS: Final = {
    "eps": ("eps", "icopor", "espumado", "foam"),
    "pulpa": ("pulpa", "cana", "bagazo"),
    "pp": ("pp", "polipropileno", "plastico"),
    "pet": ("pet", "rpet", "plastico"),
    "ps": ("ps", "poliestireno", "plastico"),
    "papel": ("papel", "carton"),
    "carton": ("carton", "papel"),
}
_FAMILY_WORDS: Final = {
    "contenedor": ("contenedor", "portacomidas"),
    "charola": ("charola", "bandeja"),
}
_NAME_STOP: Final = frozenset({"de", "del", "la", "el", "para", "con", "x", "y", "en"})
_UNIT_ALIASES: Final = {"onzas": "oz", "onza": "oz"}
# Una cifra pegada o seguida de una medida nombra la referencia («14 oz», «90mm»).
_MEASURES: Final = frozenset(
    {
        "oz", "onza", "onzas", "ml", "cc", "mm", "cm", "cms", "g", "gr", "grs", "gramos",
        "kg", "l", "lt", "lts", "litro", "litros", "pulgadas",
    }
)  # fmt: skip
_GLUED_MEASURE: Final = re.compile(
    r"^(\d+)(oz|onzas?|ml|cc|mm|cms?|gr?s?|gramos|kg|lts?|l|litros?|pulgadas)$"
)
# Una cifra seguida de una unidad de compra es una cantidad («2 cajas», «300 unidades»).
_COUNT_UNITS: Final = {
    "unidad": "unidad", "unidades": "unidad", "und": "unidad", "unds": "unidad",
    "uds": "unidad", "caja": "caja", "cajas": "caja", "paquete": "paquete",
    "paquetes": "paquete", "paca": "paca", "pacas": "paca",
}  # fmt: skip
# Tras «de», «el» o «la» una cifra nombra la referencia («el de 9», «la 2»), no una cantidad.
_NAME_NUMBER_BEFORE: Final = frozenset({"de", "el", "la"})
# Lo que puede ir antes de una posición cuando se nombran varias («la 1 y la 2», «1 o 2»).
_POSITION_BEFORE: Final = frozenset({"la", "el", "y", "o", "u", "e", "opcion", "numero", "#"})
# Una negación ante la lista no elige: «la 1 no me sirve», «la primera no, la segunda».
_REFERENCE_NO: Final = re.compile(r"\b(?:no|nop|ninguna|ninguno|tampoco)\b")


def _words(text: str) -> list[str]:
    """Palabras de ``text`` sin tildes, con la medida pegada aparte: «90mm» → «90», «mm»."""

    words: list[str] = []
    for word in plain(text).split():
        glued = _GLUED_MEASURE.match(word)
        words.extend(glued.groups() if glued else (word,))
    return words


def _is_amount(word: str) -> bool:
    return word.isdigit() or word == "mil" or bool(words_to_number(word))


def _name_numbers(words: Sequence[str]) -> set[int]:
    """Posiciones de las cifras que nombran una medida o una posición, no una cantidad.

    «de 14 oz», «90mm», «el de 9» y «la 2» nombran; «300», «son 500» o «2 cajas» son
    cantidades.
    """

    found: set[int] = set()
    for index, word in enumerate(words):
        if not word.isdigit():
            continue
        after = words[index + 1] if index + 1 < len(words) else ""
        before = words[index - 1] if index else ""
        if after in _COUNT_UNITS or after == "mil":
            continue
        if after in _MEASURES or before in _NAME_NUMBER_BEFORE:
            found.add(index)
    return found


def _several_positions(words: Sequence[str]) -> bool:
    """El mensaje nombra más de una posición («la 1 y la 2», «la primera o la segunda»)."""

    seen: set[int] = set()
    for index, word in enumerate(words):
        if word not in _NUMBERS:
            continue
        before = words[index - 1] if index else ""
        after = words[index + 1] if index + 1 < len(words) else ""
        if index and before not in _POSITION_BEFORE:
            continue
        if after in _COUNT_UNITS or after in _MEASURES or after == "mil":
            continue
        seen.add(_NUMBERS[word])
    return len(seen) > 1


def _candidate_words(candidate: Mapping[str, Any]) -> set[str]:
    words = set(_words(str(candidate.get("name") or "")))
    material = plain(str(candidate.get("material") or ""))
    words.update(_MATERIAL_WORDS.get(material, (material,) if material else ()))
    family = plain(str(candidate.get("family") or ""))
    words.update(_FAMILY_WORDS.get(family, (family,) if family else ()))
    return words - _NAME_STOP


def _stem(word: str, known: set[str]) -> str:
    """«vasos» → «vaso» si así aparece en los nombres; si no, la palabra tal cual."""

    if word not in known and len(word) > 3 and word.endswith("s") and word[:-1] in known:
        return word[:-1]
    return word


def _by_name(text: str, candidates: Sequence[Mapping[str, Any]]) -> ReferenceAnswer | None:
    """Candidata que nombra un fragmento distintivo de su nombre («el Gold», «la de 14 oz»).

    Distintiva es una palabra que tienen unas candidatas y otras no. Cada palabra del mensaje
    debe ser de algún nombre (o de relleno, o una cantidad): «mejor platos» no habla de la
    lista y devuelve ``None``, como una medida que ninguna tiene («el de 12 oz» entre vasos
    de 4 a 10 oz: el motor listó solo unas). Una cantidad («800», «2 pacas») no nombra
    aunque la cifra o la unidad estén en un nombre («Caja con 800 pzs», «paca x 200»). Si
    gana una sola candidata, esa; si empatan o el mensaje solo usa palabras que comparten
    todas («el C1»), ``"ask"``.
    """

    sets = [_candidate_words(item) for item in candidates]
    known = set().union(*sets)
    words = [_stem(_UNIT_ALIASES.get(word, word), known) for word in _words(text)]
    names_at = _name_numbers(words)
    named: list[str] = []
    for index, word in enumerate(words):
        if word.isdigit():
            if index not in names_at:
                continue  # una cantidad («300», «son 500») no elige
            if word not in known:
                return None  # «el de 12 oz» y ninguna listada lo es: otra búsqueda
            named.append(word)
            continue
        previous = words[index - 1] if index else ""
        if word in _COUNT_UNITS and _is_amount(previous) and index - 1 not in names_at:
            continue  # «2 pacas»: la unidad de la cantidad, no la «paca x 200» del nombre
        if word in known:
            named.append(word)
            continue
        if word in _PICK_FILLER:
            continue
        return None  # otra palabra: no es una elección entre las candidatas
    if not named:
        return None
    distinctive = {word for word in named if 0 < sum(word in item for item in sets) < len(sets)}
    scores = [len(distinctive & item) for item in sets]
    best = max(scores)
    if best == 0 or scores.count(best) > 1:
        return "ask"
    return ReferencePick(scores.index(best))


def resolve_reference(text: str, candidates: Sequence[Mapping[str, Any]]) -> ReferenceAnswer | None:
    """Respuesta a «¿Cuál quieres?» con las referencias de un nombre ambiguo, en orden.

    Elige una candidata (:class:`ReferencePick`) por su SKU, su posición («la primera», «la
    2», «3») o un fragmento distintivo de su nombre («el Gold», «la de icopor»). Devuelve
    ``"ask"`` si la respuesta habla de la lista sin escoger una («sí», «ok», «esa»,
    «cualquiera», un fragmento que comparten varias), :class:`ChoiceOutOfRange` si nombra
    una posición que no existe y ``None`` si no es una elección (una pregunta, otro producto,
    solo una cantidad, una negación o varias posiciones a la vez), que sigue al agente.
    """

    if "?" in text or not candidates:
        return None
    folded = plain(text)
    if not folded:
        return None
    if _PICK_VAGUE.match(folded):
        return "ask"  # «no sé», «cualquiera»: no dice cuál
    if _REFERENCE_NO.search(folded) or _several_positions(_words(text)):
        return None  # «la 1 no me sirve», «la 1 y la 2»: lo atiende el agente
    tokens = set(folded.split())
    by_sku = [
        index
        for index, item in enumerate(candidates)
        if plain(str(item.get("sku") or "")) in tokens
    ]
    if len(by_sku) == 1:
        return ReferencePick(by_sku[0])
    position = resolve_choice(text, len(candidates))
    if isinstance(position, Choice) and position.index >= 1:
        return ReferencePick(position.index - 1)
    named = _by_name(text, candidates)
    if isinstance(position, ChoiceOutOfRange):
        # «el 9» entre vasos de 9, 12 y 14 oz es el de 9 oz, no la opción 9.
        return named if isinstance(named, ReferencePick) else position
    if isinstance(named, ReferencePick):
        return named
    if position == Choice(0) or is_confirmation(text) or _PICK_VAGUE.match(folded):
        return "ask"  # «esa», «sí», «cualquiera»: no dice cuál
    return named


def reference_quantity(
    text: str, candidate: Mapping[str, Any], position: int
) -> tuple[int | None, str | None]:
    """Cantidad y unidad de compra dichas junto a la elección de una referencia.

    «La 2, son 500» → ``(500, None)``; «la 3, 2 cajas» → ``(2, "caja")``. No cuentan la
    posición (``position``, desde 1), el SKU de ``candidate`` ni las cifras que nombran una
    medida («el de 14 oz», «el de 90mm»). Sin cantidad devuelve ``(None, None)``: se cotiza
    con la de la solicitud.
    """

    sku = plain(str(candidate.get("sku") or ""))
    words = _words(text)
    names_at = _name_numbers(words)
    kept: list[str] = []
    dropped_position = False
    for index, word in enumerate(words):
        if index in names_at or (sku and word == sku):
            continue
        after = words[index + 1] if index + 1 < len(words) else ""
        if (
            not dropped_position
            and _NUMBERS.get(word) == position
            and after not in _COUNT_UNITS
            and after != "mil"
        ):
            dropped_position = True  # «2, son 300»: el 2 es la posición
            continue
        kept.append(word)
    quantities = parse_quantities(" ".join(kept))
    if not quantities:
        return None, None
    unit = next(
        (
            _COUNT_UNITS[word]
            for index, word in enumerate(kept)
            if word in _COUNT_UNITS and index and _is_amount(kept[index - 1])
        ),
        None,
    )
    return quantities[0], unit
