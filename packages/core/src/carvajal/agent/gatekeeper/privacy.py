"""Minimización de lo que viaja a Jev (spec 046, research R8; FR-017 a FR-020).

La pregunta y la respuesta tienen riesgos distintos, por eso hay dos funciones:

- :func:`outgoing_question` (pregunta y línea de contexto del gatekeeper): el filtro de la
  045 (``redact``: documentos, teléfonos, correos, tarjetas, cuentas, claves), el
  enmascarado del nombre y la empresa que la persona declara en el texto y de los valores de
  su memoria (nombre, empresa, notas) con :data:`PERSON_MARK`, y el recorte. La empresa
  declarada solo se enmascara con un marcador fuerte («trabajo en», «mi empresa es», «mi
  negocio se llama») o si tiene forma de nombre propio; las ciudades nunca se enmascaran.
  También cuentan como declaración las presentaciones habituales («habla Ana Gómez de
  Plásticos Ríos», «le habla el doctor Ramírez», «a nombre de», «de parte de», «cotiza para
  Restaurante El Fogón») y la organización tras un nombre declarado («Soy Carlos, de la
  Panadería Doña Rosa»). Las direcciones de la persona («Calle 5 # 38-25 apto 301») se
  redactan como los demás datos sensibles.
- :func:`outgoing_answer` (respuesta de la compuerta): **no** aplica ``redact`` completo,
  porque borraría los teléfonos del directorio y Jev no podría verificar ni un teléfono
  inventado ni uno reformateado. Solo sustituye los tramos sensibles y la identidad que la
  persona escribió en su pregunta **y en sus preguntas anteriores** (el modelo puede repetir
  un teléfono dado turnos atrás), los valores de su memoria y los folios de sus
  cotizaciones.

Nada de este módulo registra textos: las funciones son puras.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from carvajal.agent.memory.sensitive import REDACTED, redact
from carvajal.agent.memory.sensitive import sensitive_spans as _spans
from carvajal.agent.memory.store import MemoryProfile, classify
from carvajal.agent.router import looks_elliptical
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.evidence import names_city

__all__ = [
    "ANSWER_CHARS",
    "CONTEXT_CHARS",
    "CONTEXT_MAX_WORDS",
    "FOLIO_MARK",
    "PERSON_MARK",
    "QUESTION_CHARS",
    "address_spans",
    "context_line",
    "declared_identity",
    "folios",
    "memory_values",
    "outgoing_answer",
    "outgoing_question",
    "sensitive_spans",
]

PERSON_MARK: Final = "[dato de la persona]"
FOLIO_MARK: Final = "[folio]"
QUESTION_CHARS: Final = 1000
CONTEXT_CHARS: Final = 160
ANSWER_CHARS: Final = 8000
# Pregunta corta: lleva la línea de contexto aunque no sea elíptica (FR-017).
CONTEXT_MAX_WORDS: Final = 6
# Herramientas cuyos ``folio``/``id`` son de cotizaciones de la persona (FR-018).
_FOLIO_TOOLS: Final = frozenset({"consultar_cotizacion", "cotizar"})
_FOLIO_KEYS: Final = frozenset({"folio", "id"})

_STRONG_COMPANY: Final = re.compile(
    r"\b(?:trabajo\s+(?:en|para)|mi\s+empresa\s+(?:es|se\s+llama)"
    r"|mi\s+negocio\s+(?:es|se\s+llama)|nuestra\s+empresa\s+(?:es|se\s+llama))\s+",
    re.IGNORECASE,
)
_PHRASE_CUT: Final = re.compile(r"\s+(?:y|e|pero|que|porque)\s+|[,;.!?¿¡\n]")
_CITY_TAIL: Final = re.compile(r"\s+(?:en|de|del)\s+", re.IGNORECASE)
_CAPITALIZED_RUN: Final = re.compile(
    r"\b[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ'-]*(?:\s+(?:de\s+|del\s+|la\s+|las\s+|los\s+|el\s+)?"
    r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ'-]*)*"
)
_DIGIT: Final = re.compile(r"\d")
# Presentaciones que declaran un nombre (o empresa) seguido de un nombre propio: «habla Ana
# Gómez», «le habla el doctor Ramírez», «a nombre de Inversiones Gómez SAS», «de parte de
# Pedro», «cotiza para Restaurante El Fogón». El nombre propio exige mayúscula inicial.
_TITLE: Final = (
    r"(?:(?:el|la)\s+)?(?:(?:se[ñn]or(?:a|ita)?|sr|sra|srta|don|do[ñn]a|doctor(?:a)?|dr|dra"
    r"|ingenier[oa]|ing|licenciad[oa]|lic)\.?\s+)?"
)
_INTRODUCTION: Final = re.compile(
    # «habla» suelto solo al empezar una cláusula: «¿De qué habla el Informe…?» no presenta.
    r"(?:(?:^|[,.;:!¡\n])\s*habla|\b(?:le|les)\s+habla|\bcon\s+(?:usted|ustedes)\s+habla"
    r"|\ba\s+nombre\s+de"
    r"|\bde\s+parte\s+de|\b(?:cot[ií]za(?:me|nos|r)?|cotizaci[oó]n|factura|pedido)\s+para)\s+"
    + _TITLE,
    re.IGNORECASE,
)
# Organización tras un nombre declarado: «Soy Carlos, de la Panadería Doña Rosa».
_AFTER_NAME: Final = r"\s*,?\s+(?:de|del)\s+(?:(?:la|las|los|el)\s+)?"
# Dirección colombiana: vía, número, «#» o «No.», placa y complemento («apto 301»).
_ADDRESS: Final = re.compile(
    r"\b(?:calle|cll|cl|carrera|cra|kra|kr|cr|avenida|av|transversal|trans|tv|diagonal|diag|dg"
    r"|autopista|circular|circ)\.?\s*\d+\s*[a-z]?(?:\s*bis)?(?:\s*[a-z])?\s*"
    r"(?:#|n[oº°]\.?|n[uú]mero)\s*\d+\s*[a-z]?(?:\s*[-\u2013]\s*\d+[a-z]?)?"
    r"(?:\s*(?:sur|norte|este))?"
    r"(?:\s*,?\s*(?:apto|apartamento|apt|oficina|of|local|casa|torre|interior|int|bloque"
    r"|piso)\.?\s*\w+)*",
    re.IGNORECASE,
)


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _mask(text: str, values: Iterable[str], mark: str) -> str:
    """Sustituye cada valor (sin distinguir mayúsculas, palabra completa) por ``mark``."""

    for value in sorted({v.strip() for v in values if v and v.strip()}, key=len, reverse=True):
        if len(value) < 2:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE)
        text = pattern.sub(mark, text)
    return text


def memory_values(memory: Any) -> list[str]:
    """Nombre, empresa y notas de la memoria del dueño (la ciudad habitual no identifica)."""

    profile = MemoryProfile.from_snapshot(memory)
    values = [profile.values.get(key, "") for key in ("nombre", "empresa")]
    return [value for value in (*values, *profile.notes) if value]


def _without_city_tail(phrase: str) -> str:
    """«Panadería La Espiga en Cali» → «Panadería La Espiga»: la ciudad no se enmascara."""

    for match in _CITY_TAIL.finditer(phrase):
        if names_city(phrase[match.end() :]):
            return phrase[: match.start()]
    return phrase


def _proper_runs(phrase: str) -> list[str]:
    """Tramos con forma de nombre propio que no son ciudades («El Trigal»)."""

    return [run for run in _CAPITALIZED_RUN.findall(phrase) if not names_city(run)]


def _proper_run_at(text: str, start: int) -> str:
    """Nombre propio que empieza en ``start`` (sin la ciudad final); ``""`` si no hay."""

    match = _CAPITALIZED_RUN.match(text, start)
    if match is None:
        return ""
    run = _without_city_tail(match.group()).strip()
    return "" if not run or names_city(run) else run


def _introduced(text: str) -> list[str]:
    """Nombres y empresas tras una presentación («habla Ana Gómez de Plásticos Ríos»)."""

    found = [_proper_run_at(text, match.end()) for match in _INTRODUCTION.finditer(text)]
    return [run for run in found if run]


def _after_names(text: str, names: Iterable[str]) -> list[str]:
    """Organización tras un nombre declarado: «Carlos, de la Panadería Doña Rosa»."""

    found: list[str] = []
    for name in names:
        pattern = re.compile(rf"(?<!\w){re.escape(name)}{_AFTER_NAME}", re.IGNORECASE)
        for match in pattern.finditer(text):
            run = _proper_run_at(text, match.end())
            if run:
                found.append(run)
    return found


def address_spans(text: str) -> list[str]:
    """Direcciones que la persona escribe («Calle 5 # 38-25 apto 301»)."""

    return [match.group().strip() for match in _ADDRESS.finditer(text or "")]


def declared_identity(text: str) -> list[str]:
    """Nombre y empresa que la persona declara en el texto (sin ciudades ni tipo de negocio)."""

    found: list[str] = []
    declared = classify(text)
    if declared.get("nombre"):
        found.append(declared["nombre"])
    found.extend(_introduced(text))
    found.extend(_after_names(text, list(found)))
    for match in _STRONG_COMPANY.finditer(text):
        phrase = _PHRASE_CUT.split(text[match.end() :], maxsplit=1)[0].strip()
        phrase = _without_city_tail(phrase).strip()
        if phrase and not names_city(phrase):
            found.append(phrase)
    company = declared.get("empresa", "")
    if company and not _STRONG_COMPANY.search(text):
        # Marcador débil («tengo una cafetería en Cali»): solo el nombre propio, nunca el
        # tipo de negocio ni la ciudad.
        found.extend(_proper_runs(company))
    return found


def outgoing_question(text: str, memory: Any = None, *, limit: int = QUESTION_CHARS) -> str:
    """Pregunta (o línea de contexto) tal como viaja al gatekeeper (FR-017, FR-019)."""

    cleaned = _ADDRESS.sub(REDACTED, text or "")
    cleaned = redact(cleaned)
    # La identidad se busca también en el texto ya redactado: «a nombre de Inversiones Gómez
    # SAS NIT 900…» deja «Inversiones Gómez SAS» delante del marcador de la 045.
    identity = [*declared_identity(text or ""), *declared_identity(cleaned)]
    cleaned = _mask(cleaned, [*identity, *memory_values(memory)], PERSON_MARK)
    return _cut(cleaned, limit)


def sensitive_spans(text: str) -> list[str]:
    """Textos que el filtro de la 045 encuentra en ``text`` (mismos patrones de ``redact``)."""

    return [text[start:end] for start, end in _spans(text or "")]


def _person_patterns(text: str) -> list[re.Pattern[str]]:
    """Patrones de los tramos sensibles y las direcciones que la persona escribió en ``text``.

    Las direcciones van literales (sin distinguir mayúsculas): sus cifras no son un número
    que el modelo pueda reformatear con otros separadores.
    """

    return [
        *(_span_pattern(span) for span in sensitive_spans(text)),
        *(re.compile(re.escape(span), re.IGNORECASE) for span in address_spans(text)),
    ]


def _span_pattern(span: str) -> re.Pattern[str]:
    """Patrón literal del tramo; si trae cifras, tolera otros separadores entre dígitos."""

    if len(_DIGIT.findall(span)) >= 7:
        digits = _DIGIT.findall(span)
        return re.compile(r"(?<!\d)" + r"[\s().+-]*".join(digits) + r"(?!\d)")
    return re.compile(re.escape(span), re.IGNORECASE)


def folios(payloads: Sequence[tuple[str, Mapping[str, Any]]]) -> list[str]:
    """Folios de las cotizaciones de la persona en los datos del turno (FR-018)."""

    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in _FOLIO_KEYS and isinstance(item, str) and len(item.strip()) >= 4:
                    found.append(item.strip())
                else:
                    walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    for tool, data in payloads:
        if tool in _FOLIO_TOOLS:
            walk(data)
    return list(dict.fromkeys(found))


def outgoing_answer(
    answer: str,
    question: str,
    memory: Any = None,
    payloads: Sequence[tuple[str, Mapping[str, Any]]] = (),
    *,
    prior: Sequence[str] = (),
    limit: int = ANSWER_CHARS,
) -> str:
    """Respuesta tal como viaja a la compuerta (FR-018, FR-020).

    Conserva los datos que vienen de las consultas (teléfonos y direcciones del
    directorio): solo sustituye lo que la persona dio en su pregunta y en ``prior`` (sus
    preguntas anteriores: un teléfono dado turnos atrás que el modelo repite), su nombre, su
    empresa y sus notas, y los folios de sus cotizaciones.
    """

    text = answer or ""
    said = [question or "", *(str(item or "") for item in prior)]
    for pattern in (pattern for item in said for pattern in _person_patterns(item)):
        text = pattern.sub(REDACTED, text)
    identity = [value for item in said for value in declared_identity(item)]
    text = _mask(text, [*identity, *memory_values(memory)], PERSON_MARK)
    for folio in folios(payloads):
        text = text.replace(folio, FOLIO_MARK)
    return text if len(text) <= limit else text[:limit]


def context_line(question: str, turns: Sequence[Turn], memory: Any = None) -> str:
    """Pregunta anterior de la persona, redactada, si la actual la necesita (FR-017).

    Solo si hay turnos previos y la pregunta es elíptica («¿y en Cali?») o tiene seis palabras
    o menos. Nunca la respuesta anterior, el resumen ni la memoria.
    """

    previous = [str(turn.get("question") or "").strip() for turn in turns]
    previous = [text for text in previous if text]
    if not previous:
        return ""
    short = len((question or "").split()) <= CONTEXT_MAX_WORDS
    if not (short or looks_elliptical(question or "", list(turns))):
        return ""
    return outgoing_question(previous[-1], memory, limit=CONTEXT_CHARS)
