"""Compuerta de afirmaciones con Jev: qué se juzga y qué viaja (spec 046, contrato §3).

Después de la compuerta determinista, el texto del modelo en los caminos ``evidence`` y
``no_query`` se juzga con una pregunta Noul: ¿todas sus afirmaciones de hecho están
respaldadas por las consultas del turno? Este módulo arma la carga y decide si un texto es
juzgable; la llamada y la sustitución viven en el streaming (``apps/api/chat/agent.py``).

Lo que viaja (FR-018): la respuesta pasada por :func:`outgoing_answer` (sin los datos de la
persona, con los del directorio) y los extractos mínimos de evidencia, con un tope total de
``JEV_MAX_EVIDENCE_CHARS``. La pregunta de la persona **no** viaja: solo sus valores
estructurados según la extracción de las reglas, como el extracto ``dicho_por_la_persona``.
Desde el 2026-11-26 ese extracto suma los valores de sus mensajes anteriores (los de la
ventana del turno, nunca los anteriores a «olvidar todo») y un extracto
``borrador_de_cotizacion`` con las líneas del borrador de la spec 054 (producto, unidades,
ciudad y subtotal del motor): un pedido del dato que repite lo que la persona ya dio dos
turnos antes ya no queda sin respaldo (evidencia de la 046, §10.3).
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
from carvajal.quotation.draft import line_label, line_subtotal, units_of

__all__ = [
    "DRAFT_EXTRACT",
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
    "draft_extract",
    "evidence_extracts",
    "judgeable",
    "memory_only",
    "only_questions",
    "said_before",
    "said_by_person",
]

PERSON_EXTRACT: Final = "dicho_por_la_persona"
# Líneas del borrador de la cotización (spec 054): productos que la persona eligió, con las
# unidades, la ciudad y el subtotal que calculó el motor.
DRAFT_EXTRACT: Final = "borrador_de_cotizacion"
# Separa, dentro de ``dicho_por_la_persona``, los valores del turno de los anteriores.
_EARLIER_LEAD: Final = "en mensajes anteriores: "
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
# Campos que describen un producto: un valor nuevo solo reemplaza al de la misma familia.
_PRODUCT_LABELS: Final = frozenset({"cantidad", "capacidad", "uso", "temperatura"})


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


def said_before(
    questions: Sequence[str], current: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Valores estructurados vigentes de los mensajes anteriores que el turno no reemplaza.

    Misma extracción que :func:`said_by_person`, mensaje por mensaje y en orden, pero solo
    viaja el **último** valor de cada campo: tras «500 vasos» y «mejor 800 vasos» queda
    ``cantidad: 800``, así que una respuesta que repite el 500 corregido ya no aparece
    respaldada. Cantidad, capacidad, uso y temperatura pertenecen al producto del mensaje
    (su familia o, si no la nombra, la última dicha): «500 vasos» sigue vigente después de
    «300 tapas» y los dos viajan separados por « / ». Las familias distintas se acumulan.
    ``current`` (lo dicho en el turno) reemplaza lo anterior del mismo campo y del mismo
    producto; si el turno no nombra familia, continúa la última.
    """

    current = current or {}
    latest: dict[tuple[str, str], str] = {}
    family = ""
    for question in questions:
        said = said_by_person(question)
        family = said.get("familia", family)
        for label, value in said.items():
            scope = _scope_of(label, value, family)
            latest.pop((label, scope), None)  # el valor nuevo va al final, en su orden
            latest[(label, scope)] = value
    current_family = current.get("familia", family)
    found: dict[str, list[str]] = {}
    for (label, scope), value in latest.items():
        if label == "familia":
            if value == current.get("familia"):
                continue
        elif label in current and (scope == "" or scope == current_family):
            continue  # el turno lo reemplaza
        found.setdefault(label, []).append(value)
    order = [label for label, _ in _PERSON_KEYS] + ["indicador", "periodo"]
    return {
        label: " / ".join(found[label])
        for label in sorted(found, key=lambda item: order.index(item) if item in order else 99)
    }


def _scope_of(label: str, value: str, family: str) -> str:
    """A qué se ata un valor: la familia (los atributos del producto), sí mismo o nada."""

    if label == "familia":
        return value
    if label in _PRODUCT_LABELS:
        return family
    return ""


def _line_city(line: Mapping[str, Any]) -> str:
    primary = line.get("primary")
    provider = primary.get("provider") if isinstance(primary, Mapping) else None
    city = line.get("city") or (provider.get("city") if isinstance(provider, Mapping) else "")
    return str(city or "")


def _line_currency(line: Mapping[str, Any]) -> str:
    for key in ("primary", "commercial"):
        block = line.get(key)
        if isinstance(block, Mapping) and block.get("currency"):
            return str(block["currency"])
    return "COP"


def draft_extract(draft: Any) -> str:
    """Líneas del borrador de la cotización como evidencia; ``""`` si no hay ninguna.

    Por línea: el producto, las unidades, la ciudad y el subtotal que calculó el motor (spec
    054). Con una lista de referencias por elegir, sus nombres y la cantidad y la ciudad de
    la solicitud. Nunca el folio, el vendedor ni sus contactos. Un borrador cancelado no
    respalda nada. Cada línea pasa por ``redact``.
    """

    if draft is None or getattr(draft, "state", "") == "cancelada":
        return ""
    rows: list[str] = []
    for index, line in enumerate(getattr(draft, "lines", ()) or (), start=1):
        if not isinstance(line, Mapping):
            continue
        parts = [f"{index}. {line_label(line)}"]
        units = units_of(line)
        if units:
            parts.append(f"{units} und")
        city = _line_city(line)
        if city:
            parts.append(city)
        subtotal = line_subtotal(line)
        if subtotal is not None:
            parts.append(f"subtotal {subtotal.normalize():f} {_line_currency(line)}")
        rows.append(" · ".join(parts))
    choices = [
        str(item.get("name") or "")
        for item in getattr(draft, "choices", ()) or ()
        if isinstance(item, Mapping) and item.get("name")
    ]
    if choices:
        request = getattr(draft, "choice_request", None) or {}
        pending = [f"referencias por elegir: {', '.join(choices)}"]
        for key, label in (("quantity", "cantidad"), ("city", "ciudad")):
            if isinstance(request, Mapping) and request.get(key):
                pending.append(f"{label}: {request[key]}")
        rows.append("; ".join(pending))
    return "\n".join(redact(row) for row in rows)


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
    prior_questions: Sequence[str] = (),
    earlier_questions: Sequence[str] = (),
    draft: Any = None,
) -> ClaimsQuery:
    """Carga de la pregunta Noul (FR-018): respuesta minimizada y evidencia mínima.

    ``question`` solo sirve para saber qué sustituir en la respuesta y para derivar
    ``dicho_por_la_persona`` (si no llega ``rules_extraction``); nunca viaja. Tampoco
    ``prior_questions`` (las preguntas anteriores de la persona, incluidas las de antes de
    «olvidar todo»): solo indican qué datos suyos sustituir si la respuesta los repite
    (FR-020). De ``earlier_questions`` (los mensajes anteriores de la ventana del turno)
    viajan solo sus valores estructurados, tras los del turno (:func:`said_before`). Del
    ``draft`` (``QuoteDraft`` de la spec 054), solo :func:`draft_extract`.
    """

    budget = int(getattr(settings, "jev_max_evidence_chars", 12_000))
    said = said_by_person(question) if rules_extraction is None else dict(rules_extraction)
    earlier = said_before(earlier_questions, said)
    extracts = evidence_extracts(answer, payloads)
    lines = draft_extract(draft)
    if lines:
        extracts.insert(0, (DRAFT_EXTRACT, lines))
    if said or earlier:
        line = "; ".join(f"{label}: {value}" for label, value in said.items())
        if earlier:
            before = "; ".join(f"{label}: {value}" for label, value in earlier.items())
            line = f"{line} | {_EARLIER_LEAD}{before}" if line else f"{_EARLIER_LEAD}{before}"
        extracts.insert(0, (PERSON_EXTRACT, line))
    return ClaimsQuery(
        answer=outgoing_answer(answer, question, memory, payloads, prior=prior_questions),
        evidence=_fit(extracts, budget),
    )
