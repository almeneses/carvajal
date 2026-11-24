"""Minimización de lo que viaja a Jev (spec 046, research R8; FR-017 a FR-020).

La pregunta y la respuesta tienen riesgos distintos, por eso hay dos funciones:

- :func:`outgoing_question` (pregunta y línea de contexto del gatekeeper): el filtro de la
  045 (``redact``: documentos, teléfonos, correos, tarjetas, cuentas, claves), el
  enmascarado del nombre y la empresa que la persona declara en el texto y de los valores de
  su memoria (nombre, empresa, notas) con :data:`PERSON_MARK`, y el recorte. La empresa
  declarada solo se enmascara con un marcador fuerte («trabajo en», «mi empresa es», «mi
  negocio se llama») o si tiene forma de nombre propio; las ciudades nunca se enmascaran.
- :func:`outgoing_answer` (respuesta de la compuerta): **no** aplica ``redact`` completo,
  porque borraría los teléfonos del directorio y Jev no podría verificar ni un teléfono
  inventado ni uno reformateado. Solo sustituye los tramos sensibles que la persona escribió
  en su pregunta, los valores de su memoria y los folios de sus cotizaciones.

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
    "context_line",
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


def declared_identity(text: str) -> list[str]:
    """Nombre y empresa que la persona declara en el texto (sin ciudades ni tipo de negocio)."""

    found: list[str] = []
    declared = classify(text)
    if declared.get("nombre"):
        found.append(declared["nombre"])
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

    cleaned = redact(text or "")
    cleaned = _mask(cleaned, [*declared_identity(text or ""), *memory_values(memory)], PERSON_MARK)
    return _cut(cleaned, limit)


def sensitive_spans(text: str) -> list[str]:
    """Textos que el filtro de la 045 encuentra en ``text`` (mismos patrones de ``redact``)."""

    return [text[start:end] for start, end in _spans(text or "")]


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
    limit: int = ANSWER_CHARS,
) -> str:
    """Respuesta tal como viaja a la compuerta (FR-018).

    Conserva los datos que vienen de las consultas (teléfonos y direcciones del
    directorio): solo sustituye lo que la persona dio en su pregunta, su nombre, su empresa y
    sus notas, y los folios de sus cotizaciones.
    """

    text = answer or ""
    for span in sensitive_spans(question or ""):
        text = _span_pattern(span).sub(REDACTED, text)
    text = _mask(text, [*declared_identity(question or ""), *memory_values(memory)], PERSON_MARK)
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
