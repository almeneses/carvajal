"""Reglas del gatekeeper del modo ``tools`` (spec 046, FR-002; antes en ``toolagent/graph``).

Las reglas deciden primero y sin servicios externos: saludo o capacidad (``greeting``,
también los saludos coloquiales sin pedido de :func:`is_greeting`), despedida o
agradecimiento sin pedido (``farewell``, :func:`is_closing`) y «olvida todo»
(``memory_forget``) van al atajo sin herramientas; una preferencia declarada
(``memory_write``) va al agente con las 20 consultas; una corrección de cantidad o de entrega
sobre el contexto comercial del hilo (``_commercial_follow_up``) va a las consultas
estructuradas.

:func:`shortcut_intent` e :func:`is_closing` se movieron aquí desde ``toolagent/graph.py``,
que los reexporta para no romper importaciones. :func:`rule_decision` es pura: devuelve
``memory_forget`` y el nodo ``shortcut`` aplica el olvido y lo reescribe a
``memory_unavailable`` si falla (data-model).
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from carvajal.agent.commercial_intent import extract_commercial
from carvajal.agent.graph import _commercial_follow_up
from carvajal.agent.router import decide_route
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.evidence import names_city
from carvajal.application.ports import GateClass, GateScope

__all__ = [
    "COMMERCIAL_FOLLOW_UP",
    "FAREWELL_INTENT",
    "FORGET_INTENT",
    "MEMORY_WRITE",
    "SHORTCUT_INTENTS",
    "UNAVAILABLE_INTENT",
    "RuleDecision",
    "is_closing",
    "is_greeting",
    "rule_decision",
    "rules_route",
    "shortcut_intent",
]

# Intenciones de ``decide_route`` que atiende el atajo (FR-008): saludo, agradecimiento y
# capacidad comparten ``greeting``; ``memory_forget`` es «olvida todo lo que sabes de mí»
# (spec 045, FR-015). ``memory_write`` ya no: la preferencia la guarda el agente con
# ``recordar_preferencia`` (research R15).
FORGET_INTENT: Final = "memory_forget"
# «Olvida todo» sin Store o sin dueño: el atajo dice que la memoria no está disponible.
UNAVAILABLE_INTENT: Final = "memory_unavailable"
SHORTCUT_INTENTS: Final = frozenset({"greeting", FORGET_INTENT})
# Despedida o agradecimiento sin pedido: lo atiende el atajo sin pasar por ``decide_route``,
# que solo reconoce el agradecimiento exacto («gracias») como ``greeting``.
FAREWELL_INTENT: Final = "farewell"
# Regla de preferencia declarada y de seguimiento comercial (spec 046, FR-002).
MEMORY_WRITE: Final = "memory_write"
COMMERCIAL_FOLLOW_UP: Final = "commercial_follow_up"
_THANKS_MARKERS: Final = (
    "muchisimas gracias",
    "muchas gracias",
    "mil gracias",
    "gracias",
    "agradezco",
    "agradecido",
    "agradecida",
    "muy amable",
)
_FAREWELL_MARKERS: Final = (
    "eso era todo",
    "eso es todo",
    "era todo",
    "seria todo",
    "dios le pague",
    "dios te pague",
    "dios le bendiga",
    "dios te bendiga",
    "bendiciones",
    "hasta luego",
    "hasta pronto",
    "nos vemos",
    "chao",
    "chau",
    "adios",
    "feliz dia",
    "feliz tarde",
    "feliz noche",
)
_CLOSING_MARKERS: Final = (*_FAREWELL_MARKERS, *_THANKS_MARKERS)
# Relleno de un cierre: lo que puede acompañar a la marca sin pedir nada.
_CLOSING_FILLER: Final = frozenset(
    {
        "a",
        "amable",
        "atencion",
        "ayuda",
        "bien",
        "buen",
        "buena",
        "bueno",
        "de",
        "el",
        "excelente",
        "genial",
        "info",
        "informacion",
        "la",
        "le",
        "listo",
        "lo",
        "mil",
        "muchas",
        "muchisimas",
        "muy",
        "ok",
        "okay",
        "perfecto",
        "por",
        "super",
        "su",
        "te",
        "ti",
        "todo",
        "tu",
        "usted",
        "vale",
        "x",
    }
)
_SENTENCE_RE: Final = re.compile(r"[.!;:\n]+")
_REQUEST_WORDS: Final = frozenset(
    {
        "ademas",
        "ahora",
        "ayudame",
        "busco",
        "envien",
        "envienme",
        "interesa",
        "ultima",
        "ultimo",
        "busca",
        "buscame",
        "buscar",
        "cambia",
        "cambiar",
        "como",
        "cual",
        "cuales",
        "cuando",
        "cuanto",
        "cuanta",
        "cuantos",
        "cuantas",
        "dame",
        "deme",
        "dime",
        "digame",
        "donde",
        "envia",
        "enviame",
        "hay",
        "manda",
        "mandame",
        "mejor",
        "muestrame",
        "necesito",
        "necesitaria",
        "otra",
        "otras",
        "otro",
        "otros",
        "pero",
        "pido",
        "podria",
        "podrias",
        "precio",
        "precios",
        "puede",
        "puedes",
        "quiero",
        "quisiera",
        "recomienda",
        "recomiendame",
        "sean",
        "serian",
        "tambien",
        "tiene",
        "tienen",
        "valor",
    }
)
_REQUEST_STEMS: Final = ("cotiz",)
_WORDS_RE: Final = re.compile(r"[^\W_]+")
# Saludos y cortesías que, solos o combinados, no piden nada (texto plegado: sin tildes ni
# mayúsculas). «¿Qué más?» suelto no: a mitad de conversación pide más opciones. Los saludos
# regionales («quiubo pues, todo bien?») quedan fuera a propósito: los decide Jev (spec 046,
# casos límite y SC-002; ítem gk-20 del conjunto congelado).
_GREETING_MARKERS: Final = (
    "muy buenos dias",
    "muy buenas tardes",
    "muy buenas noches",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "buen dia",
    "buenas",
    "saludos",
    "saluditos",
    "que tal",
    "como estas",
    "como esta",
    "como estan",
    "como va",
    "como vas",
    "como le va",
    "como te va",
    "como les va",
    "hey",
    "alo",
)
_GREETING_WORD_RE: Final = re.compile(r"hola+|holi+s?|ola+")
# Lo que puede acompañar a un saludo sin pedir nada.
_GREETING_FILLER: Final = frozenset(
    {
        "a",
        "amigo",
        "amiga",
        "amigos",
        "bien",
        "carva",
        "carvajal",
        "como",
        "don",
        "dona",
        "equipo",
        "gente",
        "muy",
        "pues",
        "senor",
        "senora",
        "senorita",
        "todo",
        "todos",
        "usted",
        "ustedes",
        "y",
    }
)


@dataclass(frozen=True)
class RuleDecision:
    """Decisión de una regla: alcance, regla y clase (``None`` para la preferencia)."""

    scope: GateScope
    rule: str
    gate_class: GateClass | None


def shortcut_intent(
    question: str,
    turns: Sequence[Turn] = (),
    commercial_context: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Intención del atajo y preferencia declarada según ``decide_route`` (research R8).

    Devuelve ``(intención, preferencia)``. La intención es ``greeting``, ``memory_forget`` o
    ``farewell`` (:func:`is_closing`) si el turno lo atiende el atajo; vacía si va al agente.
    La preferencia declarada (``memory_write``) siempre va al agente (spec 045, R15): el
    grafo ``tools`` no la guarda por reglas; se devuelve solo para no tratarla como cierre.
    «Olvida todo» con un pedido o una pregunta también sigue al agente (el nodo
    ``shortcut`` ya borró la memoria).

    Con ``commercial_context`` (el del hilo), una corrección de cantidad o de entrega
    sobre ese contexto («Llámame Ana y mejor 2000») también sigue al agente: el grafo
    ``routes`` la atiende como seguimiento comercial (``_commercial_follow_up``).
    """

    decision = decide_route(question, list(turns))
    preference = decision.preference
    context = dict(commercial_context or {})
    follow_up = _commercial_follow_up(question, context) is not None
    if not preference and not follow_up and is_closing(question):
        return FAREWELL_INTENT, ""
    if not preference and not follow_up and is_greeting(question):
        # Saludo coloquial («Buenas», «Hola, ¿cómo estás?»): no busca ni llama a Jev.
        return "greeting", ""
    if decision.route != "direct" or decision.intent not in SHORTCUT_INTENTS:
        return "", preference
    if decision.intent == FORGET_INTENT and _carries_request(question):
        return "", preference
    if _commercial_follow_up(question, context) is not None:
        return "", preference
    return decision.intent, preference


def is_closing(question: str) -> bool:
    """El mensaje es una despedida o un agradecimiento que no pide nada nuevo.

    Sin signo de interrogación, es un cierre si:

    - quitadas las marcas de cierre («gracias», «eso era todo», «chao»…) y el relleno
      («listo», «perfecto», «muy amable», «por la info»), no queda nada: ni cifras, ni
      productos, ni ciudades, ni un «y» o un «no» que respondan o pidan algo; o
    - la última frase es solo una despedida («Dios le pague», «chao») y lo anterior no trae
      palabras de pedido o corrección («cotízame», «necesito», «mejor», «pero»…), ni un «y»
      al inicio de una frase, ni una ciudad de Colombia: la persona cuenta qué hará después.

    Ante la duda no es un cierre: el turno sigue al agente.
    """

    if "?" in question or "¿" in question:
        return False
    sentences = [
        _WORDS_RE.findall(_fold(part)) for part in _SENTENCE_RE.split(question) if part.strip()
    ]
    sentences = [words for words in sentences if words]
    if not sentences:
        return False
    words = [word for sentence in sentences for word in sentence]
    if not _has_marker(words, _CLOSING_MARKERS):
        return False
    if not _closing_residue(words):
        return True
    last = sentences[-1]
    if not _has_marker(last, _FAREWELL_MARKERS) or _closing_residue(last):
        return False
    before = [word for sentence in sentences[:-1] for word in sentence]
    if any(word in _REQUEST_WORDS or word.startswith(_REQUEST_STEMS) for word in before):
        return False
    if any(sentence[0] == "y" for sentence in sentences[:-1]):
        return False
    return not names_city(question)


def is_greeting(question: str) -> bool:
    """El mensaje es solo un saludo o una cortesía, sin nada que pedir.

    Quitadas las marcas de saludo («hola», «buenas», «buen día», «saludos», «¿cómo estás?»,
    «qué tal», «quiubo») y el relleno («pues», «Carvajal», «a todos»), no queda nada: ni
    productos, ni cifras, ni ciudades, ni otra pregunta («Hola, ¿tienen vasos?» no es un
    saludo). Puede llevar «?» («Hola, ¿cómo estás?»). Ante la duda no es saludo.
    """

    words = _WORDS_RE.findall(_fold(question))
    if not words:
        return False
    text = " ".join("hola" if _GREETING_WORD_RE.fullmatch(word) else word for word in words)
    if "hola" not in text.split() and not _has_marker(text.split(), _GREETING_MARKERS):
        return False
    text = re.sub(r"(?<!\S)hola(?!\S)", " ", text)
    for marker in _GREETING_MARKERS:
        text = re.sub(rf"(?<!\S){marker}(?!\S)", " ", text)
    return not [word for word in text.split() if word not in _GREETING_FILLER]


def _has_marker(words: Sequence[str], markers: Sequence[str]) -> bool:
    text = f" {' '.join(words)} "
    return any(f" {marker} " in text for marker in markers)


def _closing_residue(words: Sequence[str]) -> list[str]:
    """Palabras que quedan al quitar las marcas de cierre y el relleno."""

    text = " ".join(words)
    for marker in _CLOSING_MARKERS:
        text = re.sub(rf"(?<!\S){marker}(?!\S)", " ", text)
    return [word for word in text.split() if word not in _CLOSING_FILLER]


def _carries_request(question: str) -> bool:
    """La preferencia viene con una pregunta o un pedido comercial que hay que atender."""

    return "?" in question or "¿" in question or extract_commercial(question) is not None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def rules_route(question: str, turns: Sequence[Turn] = ()) -> str:
    """Ruta e intención de ``decide_route`` («commercial/commercial»), sin costo ni red."""

    decision = decide_route(question, list(turns))
    return f"{decision.route}/{decision.intent}"


def rule_decision(
    question: str,
    turns: Sequence[Turn] = (),
    commercial_context: Mapping[str, Any] | None = None,
    *,
    shortcut: tuple[str, str] | None = None,
) -> RuleDecision | None:
    """Decisión de las reglas, o ``None`` si ninguna decide (contrato §1, pasos 1 a 3).

    1. Conversación (``greeting``, ``farewell``, ``memory_forget``) → ``direct``.
    2. Preferencia declarada (``memory_write``) → ``all`` (el agente la guarda).
    3. Seguimiento comercial del hilo → ``structured``.

    ``shortcut`` es el resultado ya calculado de :func:`shortcut_intent` (el nodo
    ``shortcut`` lo calcula una vez). Nunca devuelve ``memory_unavailable``: depende del
    Store y lo decide el nodo.
    """

    intent, preference = shortcut or shortcut_intent(question, turns, commercial_context)
    if intent:
        return RuleDecision(scope="direct", rule=intent, gate_class="respuesta_directa")
    if preference or decide_route(question, list(turns)).intent == MEMORY_WRITE:
        return RuleDecision(scope="all", rule=MEMORY_WRITE, gate_class=None)
    if _commercial_follow_up(question, dict(commercial_context or {})) is not None:
        return RuleDecision(
            scope="structured", rule=COMMERCIAL_FOLLOW_UP, gate_class="herramienta_estructurada"
        )
    return None
