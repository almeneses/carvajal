"""Compuerta de afirmaciones con Jev: qué se juzga y qué viaja (spec 046, contrato §3).

Después de la compuerta determinista, el texto del modelo en los caminos ``evidence`` y
``no_query`` se juzga con una pregunta Noul: ¿todas sus afirmaciones de hecho están
respaldadas por las consultas del turno? Este módulo arma la carga y decide si un texto es
juzgable; la llamada y la sustitución viven en el streaming (``apps/api/chat/agent.py``).

Lo que viaja (FR-018): la respuesta pasada por :func:`outgoing_answer` (sin los datos de la
persona, con los del directorio) y los extractos mínimos de evidencia, con un tope total de
``JEV_MAX_EVIDENCE_CHARS``. La pregunta de la persona **no** viaja: solo sus valores
estructurados según la extracción de las reglas, como el extracto ``dicho_por_la_persona``.
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from carvajal.agent.gatekeeper.privacy import (
    ANSWER_CHARS,
    FOLIO_MARK,
    folios,
    outgoing_answer,
)
from carvajal.agent.memory.sensitive import redact
from carvajal.agent.router import decide_route
from carvajal.agent.toolagent.evidence import backing_data
from carvajal.agent.toolagent.tools import CORPUS_TOOLS, DECLARATION_TOOLS, MEMORY_TOOLS
from carvajal.application.ports import ClaimsQuery

__all__ = [
    "ECHO_KEYS",
    "PERSON_EXTRACT",
    "SKIP_ABSTAIN",
    "SKIP_DIRECT",
    "SKIP_LONG",
    "SKIP_MEMORY",
    "SKIP_QUESTIONS",
    "SKIP_REPLACEMENT",
    "SKIP_SHORTCUT",
    "build_claims_query",
    "evidence_extracts",
    "judgeable",
    "memory_only",
    "only_questions",
    "said_by_person",
]

PERSON_EXTRACT: Final = "dicho_por_la_persona"
# Claves de un resultado que no respaldan nada: ``message`` repite argumentos del modelo;
# ``aviso`` y ``busqueda_nombre`` de ``buscar_producto`` son prosa y conteos de la búsqueda.
ECHO_KEYS: Final = frozenset({"message", "aviso", "busqueda_nombre"})
# Motivos de ``claims.status="skipped"`` (data-model).
SKIP_SHORTCUT: Final = "atajo"
SKIP_DIRECT: Final = "directa"
SKIP_ABSTAIN: Final = "abstencion"
SKIP_REPLACEMENT: Final = "reemplazo"
SKIP_MEMORY: Final = "memoria"
SKIP_QUESTIONS: Final = "solo_preguntas"
SKIP_LONG: Final = "demasiado_larga"
_CITATION: Final = re.compile(r"\[(\d+)\]")
_SENTENCE: Final = re.compile(r"(?<=[.!?…])\s+|\n+")
# Tramo de pregunta con apertura: «¿…?».
_QUESTION_SPAN: Final = re.compile(r"¿[^¿?]*\?")
# Separadores de cláusula: una oración sin «¿» que los tenga puede afirmar algo antes de
# preguntar («El vaso cuesta $500, te lo cotizo?»).
_CLAUSE_MARK: Final = re.compile(r"[,;:]")
_WORD: Final = re.compile(r"\w")
_ELLIPSIS: Final = "…"
# Valores estructurados que la persona puede haber dicho (extracción de las reglas).
_PERSON_KEYS: Final = (
    ("quantity", "cantidad"),
    ("family", "familia"),
    ("capacity_oz", "capacidad"),
    ("use", "uso"),
    ("temperature", "temperatura"),
    ("city", "ciudad"),
)


def _is_question(sentence: str) -> bool:
    """La oración es solo pregunta: nada afirmado fuera de sus tramos «¿…?».

    Con «¿», se quitan los tramos «¿…?» y no debe quedar ninguna palabra («El vaso cuesta
    $500, ¿te lo cotizo?» deja una afirmación). Sin «¿», debe terminar en «?» y ser una sola
    cláusula, sin comas, punto y coma ni dos puntos.
    """

    if "¿" in sentence:
        return not _WORD.search(_QUESTION_SPAN.sub(" ", sentence))
    return sentence.endswith("?") and not _CLAUSE_MARK.search(sentence)


def only_questions(text: str) -> bool:
    """El texto solo hace preguntas a la persona: ninguna oración afirma nada.

    Una afirmación seguida de una pregunta en la misma oración («Carvajal fue fundada en
    1904, ¿te ayudo con algo más?») no es solo pregunta y se juzga.
    """

    sentences = [part.strip() for part in _SENTENCE.split(text or "") if part.strip()]
    return bool(sentences) and all(_is_question(sentence) for sentence in sentences)


def memory_only(results: Sequence[Mapping[str, Any]]) -> bool:
    """El turno consultó solo la memoria (y quizá ``sin_consulta``)."""

    tools = [str(r.get("tool") or "") for r in results]
    return any(tool in MEMORY_TOOLS for tool in tools) and all(
        tool in MEMORY_TOOLS or tool in DECLARATION_TOOLS for tool in tools
    )


def judgeable(
    answer: str,
    *,
    shortcut: bool = False,
    source: str = "",
    outcome: str = "model",
    results: Sequence[Mapping[str, Any]] = (),
    max_chars: int = ANSWER_CHARS,
) -> tuple[bool, str]:
    """Si el texto se juzga y, si no, el motivo (contrato §3, *Cuándo se juzga*).

    ``outcome`` es ``model`` (texto del modelo que pasó la compuerta determinista),
    ``abstain`` (abstención) o ``replace`` (reemplazo neutro o respaldo del servicio).
    """

    if shortcut:
        return False, SKIP_DIRECT if source == "jev" else SKIP_SHORTCUT
    if outcome == "abstain":
        return False, SKIP_ABSTAIN
    if outcome == "replace":
        return False, SKIP_REPLACEMENT
    if memory_only(results):
        return False, SKIP_MEMORY
    if only_questions(answer):
        return False, SKIP_QUESTIONS
    if len(answer) > max_chars:
        return False, SKIP_LONG
    return True, ""


def said_by_person(question: str) -> dict[str, str]:
    """Valores estructurados que la persona dio según la extracción de las reglas.

    Cantidad, familia, capacidad, uso, temperatura y ciudad (ruta comercial) e indicador y
    período (ruta ``tool``); nunca texto libre. Cada valor pasa por ``redact``.
    """

    decision = decide_route(question or "")
    found: dict[str, str] = {}
    extra = decision.extra or {}
    for key, label in _PERSON_KEYS:
        value = extra.get(key)
        if value is None or value == "" or isinstance(value, bool):
            continue
        if key == "capacity_oz" and isinstance(value, int | float):
            value = f"{value:g} oz"
        found[label] = redact(str(value))
    if decision.tool_name:
        found["indicador"] = redact(decision.tool_name)
    if decision.tool_period:
        found["periodo"] = redact(decision.tool_period)
    return found


def _replace_folios(text: str, found: Sequence[str]) -> str:
    for folio in found:
        text = text.replace(folio, FOLIO_MARK)
    return text


def _cited(answer: str) -> set[int]:
    return {int(number) for number in _CITATION.findall(answer or "")}


def _corpus_text(tool: str, data: Mapping[str, Any], cited: set[int]) -> str:
    """Fragmentos del corpus como «[n] texto»: solo los citados si la respuesta cita."""

    if tool == "preguntas_frecuentes":
        entries = [
            (entry.get("n"), str(entry.get("fragmento") or ""))
            for entry in data.get("preguntas") or []
            if isinstance(entry, Mapping)
        ]
    else:
        entries = [
            (entry.get("n"), str(entry.get("texto") or ""))
            for entry in data.get("fragmentos") or []
            if isinstance(entry, Mapping)
        ]
    lines = [
        f"[{number}] {text}" if isinstance(number, int) else text
        for number, text in entries
        if text and (not cited or number in cited)
    ]
    return "\n".join(lines)


def evidence_extracts(
    answer: str, payloads: Sequence[tuple[str, Mapping[str, Any]]]
) -> list[tuple[str, str]]:
    """``(consulta, texto)`` de los datos del turno que pueden respaldar texto.

    Mismo criterio que la compuerta determinista (``backing_data`` sin :data:`ECHO_KEYS`);
    sin ``sin_consulta``, memoria ni errores; del corpus, solo los fragmentos citados ``[n]``
    si la respuesta cita alguno; los folios de la persona como ``[folio]``.
    """

    cited = _cited(answer)
    found = folios(payloads)
    extracts: list[tuple[str, str]] = []
    for tool, data in payloads:
        if tool in DECLARATION_TOOLS or tool in MEMORY_TOOLS or data.get("status") == "error":
            continue
        if tool in CORPUS_TOOLS:
            text = _corpus_text(tool, data, cited)
        else:
            backing = {k: v for k, v in backing_data(tool, data).items() if k not in ECHO_KEYS}
            text = json.dumps(backing, ensure_ascii=False, separators=(",", ":"), default=str)
        text = _replace_folios(text, found)
        if text and text not in ("{}", "[]"):
            extracts.append((tool, text))
    return extracts


def _fit(extracts: Sequence[tuple[str, str]], budget: int) -> tuple[tuple[str, str], ...]:
    """Reparte el tope entre los extractos: los cortos enteros, los largos con «…»."""

    if sum(len(text) for _, text in extracts) <= budget:
        return tuple(extracts)
    limits = [0] * len(extracts)
    pending = list(range(len(extracts)))
    remaining = budget
    while pending:
        share = remaining // len(pending)
        small = [i for i in pending if len(extracts[i][1]) <= share]
        if not small:
            for i in pending:
                limits[i] = share
            break
        for i in small:
            limits[i] = len(extracts[i][1])
            remaining -= limits[i]
        pending = [i for i in pending if i not in small]
    fitted = []
    for (name, text), limit in zip(extracts, limits, strict=True):
        if len(text) > limit:
            text = text[: max(0, limit - 1)] + _ELLIPSIS
        fitted.append((name, text))
    return tuple(fitted)


def build_claims_query(
    answer: str,
    question: str,
    payloads: Sequence[tuple[str, Mapping[str, Any]]],
    memory: Any = None,
    rules_extraction: Mapping[str, str] | None = None,
    settings: Any = None,
) -> ClaimsQuery:
    """Carga de la pregunta Noul (FR-018): respuesta minimizada y evidencia mínima.

    ``question`` solo sirve para saber qué sustituir en la respuesta y para derivar
    ``dicho_por_la_persona`` (si no llega ``rules_extraction``); nunca viaja.
    """

    budget = int(getattr(settings, "jev_max_evidence_chars", 12_000))
    said = said_by_person(question) if rules_extraction is None else dict(rules_extraction)
    extracts = evidence_extracts(answer, payloads)
    if said:
        line = "; ".join(f"{label}: {value}" for label, value in said.items())
        extracts.insert(0, (PERSON_EXTRACT, line))
    return ClaimsQuery(
        answer=outgoing_answer(answer, question, memory, payloads),
        evidence=_fit(extracts, budget),
    )
