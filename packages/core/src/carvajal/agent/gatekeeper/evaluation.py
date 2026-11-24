"""Medición del gatekeeper y de la compuerta de afirmaciones (spec 046, research R4, R5, R11).

Lo usan ``carvajal eval-gatekeeper`` y ``carvajal eval-claims``:

- :func:`eval_gatekeeper` clasifica cada pregunta con las reglas, el enrutador LLM o Jev
  (Choice + Score en la misma petición: Score solo aquí) y calcula la exactitud por juez, la
  rejilla de umbrales (exactitud de las decisiones aplicadas, cobertura y preguntas con datos
  enviadas a ``respuesta_directa``), la regla de research R4 para fijar los umbrales y la
  comparación confianza frente a Score. Con los veredictos de Jev arma además la decisión de
  producción con los umbrales vigentes, sin llamadas nuevas.
- :func:`eval_claims` juzga cada respuesta congelada con Noul y calcula detección y falsas
  alarmas por umbral.
- :class:`ReplayJudge` reproduce sin red los veredictos grabados en el libro (FR-032) y
  :func:`replay_agent` corre el agente real (solo DeepSeek) sobre los ítems que terminaron en
  cautela o en respuesta directa.
- :class:`QuotaJudge` pone las cuotas de una corrida (FR-027): al agotarse, el juez responde
  ``JudgeUnavailable`` con motivo ``cuota`` y el turno sigue sin Jev.

Cada llamada real queda en el libro de :class:`~carvajal.adapters.judge.jev.JevClient` (sin
texto); este módulo solo pone las etiquetas ``run`` e ``item``. ``--max-jev-calls`` corta la
corrida en lugar del tope diario.
"""

import json
import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast

from carvajal.agent.gatekeeper.decide import decide_gate
from carvajal.agent.gatekeeper.model import map_route
from carvajal.agent.gatekeeper.privacy import context_line, outgoing_answer, outgoing_question
from carvajal.agent.router import decide_route
from carvajal.agent.state import Turn
from carvajal.application.ports import (
    GATE_CLASSES,
    ClaimsQuery,
    ClaimsVerdict,
    GateClass,
    JevStatus,
    JudgeCall,
    JudgeUnavailable,
    RouteQuery,
    RouteVerdict,
)

__all__ = [
    "CLAIMS_GRID",
    "GRID",
    "JUDGES",
    "MIN_COVERAGE",
    "QUOTA",
    "ClaimsItem",
    "ClaimsReport",
    "ClaimsResult",
    "ClaimsSet",
    "GateItem",
    "GateReport",
    "GateResult",
    "GateSet",
    "QuotaJudge",
    "ReplayJudge",
    "ReplayRow",
    "build_replay_driver",
    "claims_payload",
    "claims_query",
    "eval_claims",
    "eval_gatekeeper",
    "gatekeeper_payload",
    "label_judge",
    "load_claims_items",
    "load_claims_set",
    "load_gate_items",
    "render_claims_report",
    "render_gatekeeper_report",
    "render_replay_report",
    "replay_agent",
    "replay_payload",
    "run_smoke",
]

# Rejilla de research R4 para ``JEV_ROUTER_MIN_CONFIDENCE`` y ``…_DIRECT_MIN_CONFIDENCE``.
GRID: Final = (0.5, 0.6, 0.7, 0.8)
# Umbrales de la compuerta que informa ``eval-claims`` (el vigente es 0,5).
CLAIMS_GRID: Final = (0.3, 0.4, 0.5, 0.6, 0.7)
# Cobertura mínima de las decisiones aplicadas para elegir el umbral (research R4).
MIN_COVERAGE: Final = 0.7
# Motivo de una llamada no hecha porque se agotó la cuota de la corrida (data-model).
QUOTA: Final = "cuota"
JUDGES: Final = ("rules", "llm", "jev")
DIRECT: Final = "respuesta_directa"
_JUDGE_NAMES: Final = {"rules": "reglas", "llm": "LLM", "jev": "Jev"}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(sum(items) / len(items), 4) if items else None


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def _pct(hits: int, total: int) -> str:
    return f"{hits / total:.1%} ({hits}/{total})" if total else "—"


def _fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _cell(text: str, limit: int = 90) -> str:
    return " ".join(str(text).replace("|", "\\|").split())[:limit]


def label_judge(judge: Any, run: str, item: str) -> None:
    """Etiqueta las próximas llamadas (``run`` e ``item`` del libro y del ``ReplayJudge``)."""

    for target in (judge, getattr(judge, "client", None)):
        if target is None:
            continue
        if hasattr(target, "run"):
            target.run = run
        if hasattr(target, "item"):
            target.item = item


def run_smoke(judge: Any) -> dict[str, Any]:
    """Humo (``GET /v1/models``) con el cliente del juez; ``off`` si no hay cliente real."""

    client = getattr(judge, "client", None)
    smoke = getattr(client, "smoke", None)
    if smoke is None:
        return {"status": "off", "latency_ms": 0, "model_listed": False}
    return dict(smoke())


# --- Conjunto del gatekeeper ---------------------------------------------------------------


@dataclass(frozen=True)
class GateItem:
    """Pregunta con su clase esperada (``gatekeeper_046.json`` o ``routing_questions.json``)."""

    id: str
    question: str
    expected: GateClass
    context: str = ""
    turns: tuple[Turn, ...] = ()
    source: str = ""
    rules_route: str = ""


@dataclass(frozen=True)
class GateSet:
    path: Path
    items: tuple[GateItem, ...]
    version: Any = None
    frozen_at: str = ""
    origin: str = ""


def _turns(raw: Any) -> tuple[Turn, ...]:
    return tuple(
        Turn(
            question=str(turn.get("question") or ""),
            answer=str(turn.get("answer") or ""),
            incomplete=bool(turn.get("incomplete")),
        )
        for turn in raw or []
        if isinstance(turn, Mapping)
    )


def load_gate_items(path: Path) -> GateSet:
    """Carga el conjunto; en ``routing_questions.json`` la clase sale de ``map_route``."""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items: list[GateItem] = []
    for entry in raw["items"]:
        expected = entry.get("expected")
        route, intent = str(entry.get("route") or ""), str(entry.get("intent") or "")
        if expected not in GATE_CLASSES:
            if not route:
                raise ValueError(f"El ítem {entry.get('id')} no trae clase esperada ni ruta.")
            expected = map_route(route, intent)
        items.append(
            GateItem(
                id=str(entry["id"]),
                question=str(entry["question"]),
                expected=cast(GateClass, expected),
                context=str(entry.get("context") or ""),
                turns=_turns(entry.get("turns")),
                source=str(entry.get("source") or ""),
                rules_route=str(entry.get("rules_route") or (f"{route}/{intent}" if route else "")),
            )
        )
    return GateSet(
        path=Path(path),
        items=tuple(items),
        version=raw.get("version"),
        frozen_at=str(raw.get("frozen_at") or ""),
        origin=str(raw.get("origin") or ""),
    )


# --- Resultados del gatekeeper -------------------------------------------------------------


@dataclass
class GateResult:
    """Clasificación de una pregunta por un juez; ``status`` es ``ok``, ``cuota`` o el fallo."""

    item: GateItem
    judge: str
    status: str = "ok"
    predicted: GateClass | None = None
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    clarity: float | None = None
    clarity_confidence: float | None = None
    latency_ms: int | None = None
    # Decisión de producción con los umbrales vigentes (solo Jev), sin llamadas nuevas.
    decision: dict[str, Any] | None = None

    @property
    def correct(self) -> bool:
        return self.status == "ok" and self.predicted == self.item.expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item.id,
            "expected": self.item.expected,
            "rules_route": self.item.rules_route,
            "judge": self.judge,
            "status": self.status,
            "predicted": self.predicted,
            "correct": self.correct,
            "confidence": self.confidence,
            "probabilities": dict(self.probabilities),
            "clarity": self.clarity,
            "clarity_confidence": self.clarity_confidence,
            "latency_ms": self.latency_ms,
            "decision": dict(self.decision) if self.decision is not None else None,
        }


@dataclass
class GateReport:
    """Agregados de un juez; la rejilla y los umbrales solo tienen sentido con Jev."""

    judge: str
    results: list[GateResult] = field(default_factory=list)
    min_confidence: float = 0.6
    direct_min_confidence: float = 0.8

    @property
    def judged(self) -> list[GateResult]:
        return [r for r in self.results if r.status == "ok"]

    @property
    def total(self) -> int:
        return len(self.judged)

    @property
    def hits(self) -> int:
        return sum(r.correct for r in self.judged)

    @property
    def accuracy(self) -> float | None:
        return _rate(self.hits, self.total)

    @property
    def quota_skipped(self) -> int:
        return sum(r.status == QUOTA for r in self.results)

    @property
    def failed(self) -> int:
        return sum(r.status not in ("ok", QUOTA) for r in self.results)

    @property
    def with_confidence(self) -> bool:
        return any(r.confidence is not None for r in self.judged)

    def grid(self) -> list[dict[str, Any]]:
        """Por umbral: decisiones aplicadas, sus aciertos, cobertura y datos → directa."""

        rows: list[dict[str, Any]] = []
        for threshold in GRID:
            applied = [
                r for r in self.judged if r.confidence is not None and r.confidence >= threshold
            ]
            hits = sum(r.correct for r in applied)
            to_direct = sum(r.item.expected != DIRECT and r.predicted == DIRECT for r in applied)
            rows.append(
                {
                    "threshold": threshold,
                    "applied": len(applied),
                    "applied_hits": hits,
                    "accuracy": _rate(hits, len(applied)),
                    "coverage": _rate(len(applied), self.total),
                    "data_to_direct": to_direct,
                }
            )
        return rows

    def recommend(self) -> dict[str, Any]:
        """Umbrales según la regla de research R4, declarada antes de medir.

        ``min_confidence``: la mayor exactitud de las aplicadas con cobertura ≥ 70 %.
        ``direct_min_confidence``: el menor valor de la rejilla, no menor que el mínimo, sin
        preguntas con datos enviadas a ``respuesta_directa``. La cláusula «si la rejilla no
        separa, se quedan los valores iniciales» vale para los dos (D2): si todos los
        valores empatan, se queda el vigente y la nota dice de cuál umbral se trata.
        """

        rows = self.grid()
        notes: list[str] = []
        eligible = [r for r in rows if r["applied"] and (r["coverage"] or 0) >= MIN_COVERAGE]
        if not eligible:
            minimum = self.min_confidence
            notes.append("umbral mínimo: ningún valor cubre el 70 %, se queda el vigente")
        else:
            best = max(r["accuracy"] for r in eligible)
            tied = [r["threshold"] for r in eligible if r["accuracy"] == best]
            minimum = self.min_confidence if self.min_confidence in tied else min(tied)
            if len(tied) == len(eligible) and len(eligible) > 1:
                notes.append("umbral mínimo: la rejilla no separa, se queda el vigente")
        clean = [r["threshold"] for r in rows if r["data_to_direct"] == 0]
        candidates = [t for t in clean if t >= minimum]
        if len(clean) == len(rows):
            direct = max(self.direct_min_confidence, minimum)
            notes.append("umbral directo: la rejilla no separa, se queda el vigente")
        elif candidates:
            direct = min(candidates)
        else:
            direct = max(self.direct_min_confidence, minimum)
            notes.append(
                "umbral directo: ningún valor evita enviar preguntas con datos a la respuesta "
                "directa, se queda el vigente"
            )
        return {
            "min_confidence": minimum,
            "direct_min_confidence": direct,
            "note": "; ".join(notes),
        }

    def confidence_vs_score(self) -> dict[str, Any]:
        """Confianza de Choice y nivel de Score en aciertos y errores (research R5)."""

        correct = [r for r in self.judged if r.correct]
        wrong = [r for r in self.judged if not r.correct]

        def confidence(results: Sequence[GateResult]) -> float | None:
            return _mean(r.confidence for r in results if r.confidence is not None)

        def clarity(results: Sequence[GateResult]) -> float | None:
            return _mean(float(r.clarity) for r in results if r.clarity is not None)

        # La claridad es real (D1): se agrupa por el nivel más cercano (2,5 → 3).
        by_level: dict[int, list[int]] = {}
        for result in self.judged:
            if result.clarity is None:
                continue
            counts = by_level.setdefault(math.floor(result.clarity + 0.5), [0, 0])
            counts[0] += not result.correct
            counts[1] += 1
        return {
            "correct": len(correct),
            "errors": len(wrong),
            "confidence_correct": confidence(correct),
            "confidence_wrong": confidence(wrong),
            "clarity_correct": clarity(correct),
            "clarity_wrong": clarity(wrong),
            "errors_by_clarity": {
                str(level): {"errors": e, "total": t} for level, (e, t) in sorted(by_level.items())
            },
        }

    def production(self) -> dict[str, Any]:
        """Decisiones de producción con los umbrales vigentes (aplicadas, cautela, directa)."""

        decided = [r for r in self.judged if r.decision is not None]
        applied = [
            r
            for r in decided
            if r.decision is not None
            and r.decision.get("source") == "jev"
            and not r.decision.get("cautious")
        ]
        hits = sum(
            r.decision is not None and r.decision.get("gate_class") == r.item.expected
            for r in applied
        )
        cautious = [r.item.id for r in decided if r.decision and r.decision.get("cautious")]
        direct = [r.item.id for r in decided if r.decision and r.decision.get("scope") == "direct"]
        to_direct = [
            r.item.id
            for r in decided
            if r.decision and r.decision.get("scope") == "direct" and r.item.expected != DIRECT
        ]
        return {
            "decided": len(decided),
            "applied": len(applied),
            "applied_hits": hits,
            "applied_accuracy": _rate(hits, len(applied)),
            "cautious": cautious,
            "direct": direct,
            "data_to_direct": to_direct,
        }

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "total": self.total,
            "hits": self.hits,
            "accuracy": self.accuracy,
            "quota_skipped": self.quota_skipped,
            "failed": self.failed,
            "items": [r.to_dict() for r in self.results],
        }
        if self.with_confidence:
            payload |= {
                "grid": self.grid(),
                "recommend": self.recommend(),
                "confidence_vs_score": self.confidence_vs_score(),
                "production": self.production(),
            }
        return payload


@dataclass(frozen=True)
class _FixedJudge:
    """Devuelve el veredicto ya obtenido: arma la decisión de producción sin otra llamada."""

    verdict: RouteVerdict

    def available(self) -> bool:
        return True

    def classify(self, query: RouteQuery, *, with_score: bool = False) -> RouteVerdict:
        return self.verdict


def _decision_value(decision: Any) -> dict[str, Any]:
    value = decision.to_value()
    keys = ("gate_class", "scope", "source", "cautious", "reason", "rule", "confidence")
    return {key: value.get(key) for key in keys}


def _route_query(item: GateItem) -> RouteQuery:
    context = item.context or context_line(item.question, item.turns)
    return RouteQuery(question=outgoing_question(item.question), context=context)


def _class_of(decision: Any) -> GateClass:
    return map_route(str(getattr(decision, "route", "")), str(getattr(decision, "intent", "")))


def eval_gatekeeper(
    items: Sequence[GateItem],
    *,
    judges: Sequence[str],
    jev: Any = None,
    llm_router: Callable[..., Any] | None = None,
    settings: Any = None,
    max_jev_calls: int | None = None,
    run: str = "gatekeeper",
    progress: Callable[[GateResult], None] | None = None,
) -> list[GateReport]:
    """Clasifica cada pregunta con cada juez pedido (una llamada a Jev por pregunta)."""

    minimum = float(getattr(settings, "jev_router_min_confidence", 0.6))
    direct = float(getattr(settings, "jev_router_direct_min_confidence", 0.8))
    reports: list[GateReport] = []
    for name in dict.fromkeys(judges):
        if name not in JUDGES:
            raise ValueError(f"Juez desconocido: {name}")
        if name == "llm" and llm_router is None:
            raise ValueError("El juez llm necesita un enrutador LLM.")
        if name == "jev" and jev is None:
            raise ValueError("El juez jev necesita el juez de Jev.")
        report = GateReport(judge=name, min_confidence=minimum, direct_min_confidence=direct)
        used = 0
        for item in items:
            turns = list(item.turns)
            result = GateResult(item=item, judge=name)
            if name == "rules":
                result.predicted = _class_of(decide_route(item.question, turns))
            elif name == "llm":
                assert llm_router is not None
                result.predicted = _class_of(llm_router(item.question, turns))
            elif max_jev_calls is not None and used >= max_jev_calls:
                result.status = QUOTA
            else:
                used += 1
                label_judge(jev, run, item.id)
                _classify(jev, item, result, settings)
            report.results.append(result)
            if progress is not None:
                progress(result)
        reports.append(report)
    return reports


def _classify(jev: Any, item: GateItem, result: GateResult, settings: Any) -> None:
    try:
        verdict = jev.classify(_route_query(item), with_score=True)
    except JudgeUnavailable as error:
        result.status = error.reason or error.status
        result.latency_ms = error.call.latency_ms if error.call is not None else None
        return
    result.predicted = verdict.choice
    result.confidence = round(verdict.confidence, 4)
    result.probabilities = {k: round(v, 4) for k, v in verdict.probabilities.items()}
    result.clarity = verdict.clarity
    result.clarity_confidence = verdict.clarity_confidence
    result.latency_ms = verdict.call.latency_ms
    decision = decide_gate(item.question, item.turns, judge=_FixedJudge(verdict), settings=settings)
    result.decision = _decision_value(decision)


# --- Informe del gatekeeper ----------------------------------------------------------------


def _thresholds(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def render_gatekeeper_report(
    reports: Sequence[GateReport],
    *,
    questions: Path,
    settings: Any = None,
    smoke: Mapping[str, Any] | None = None,
    gate_set: GateSet | None = None,
) -> str:
    """Informe Markdown de ``eval-gatekeeper`` (exactitud, rejilla, umbrales, Score)."""

    minimum = float(getattr(settings, "jev_router_min_confidence", 0.6))
    direct = float(getattr(settings, "jev_router_direct_min_confidence", 0.8))
    lines = ["# Evaluación del gatekeeper (spec 046)", ""]
    described = f"Conjunto: `{questions}`"
    if gate_set is not None and gate_set.frozen_at:
        described += f" (congelado el {gate_set.frozen_at})"
    lines += [
        described + f"; umbrales vigentes {_thresholds(minimum)} y {_thresholds(direct)}.",
        "",
    ]
    if smoke is not None:
        lines += [
            f"Humo (`GET /v1/models`): {smoke.get('status')}, {smoke.get('latency_ms')} ms, "
            f"modelo en la lista: {'sí' if smoke.get('model_listed') else 'no'}.",
            "",
        ]
    lines += [
        "| Juez | Exactitud | Sin llamada | Fallos |",
        "|---|---|---|---|",
    ]
    for report in reports:
        lines.append(
            f"| {report.judge} | {_pct(report.hits, report.total)} | "
            f"sin llamada por cuota: {report.quota_skipped} | {report.failed} |"
        )
    for report in reports:
        if not report.with_confidence:
            continue
        name = _JUDGE_NAMES.get(report.judge, report.judge)
        lines += [
            "",
            f"## Rejilla de umbrales ({name})",
            "",
            "| Umbral | Aplicadas | Exactitud de las aplicadas | Cobertura | Datos → directa |",
            "|---|---|---|---|---|",
        ]
        for row in report.grid():
            lines.append(
                f"| {_thresholds(row['threshold'])} | {row['applied']} | "
                f"{_pct(row['applied_hits'], row['applied'])} | "
                f"{_fmt(row['coverage'])} | {row['data_to_direct']} |"
            )
        choice = report.recommend()
        lines += [
            "",
            "Regla de research R4: `JEV_ROUTER_MIN_CONFIDENCE` = "
            f"{_thresholds(choice['min_confidence'])}, `JEV_ROUTER_DIRECT_MIN_CONFIDENCE` = "
            f"{_thresholds(choice['direct_min_confidence'])}"
            + (f" ({choice['note']})" if choice["note"] else "")
            + ". Con n = 20 el riesgo de sobreajuste es alto.",
        ]
        production = report.production()
        lines += [
            "",
            "Con los umbrales vigentes: "
            f"aplicadas {_pct(production['applied_hits'], production['applied'])}; "
            f"cautela en {', '.join(production['cautious']) or 'ninguna'}; "
            f"respuesta directa en {', '.join(production['direct']) or 'ninguna'}; "
            "preguntas con datos a la respuesta directa: "
            f"{', '.join(production['data_to_direct']) or 'ninguna'}.",
        ]
        versus = report.confidence_vs_score()
        lines += [
            "",
            f"## Confianza frente a Score ({name})",
            "",
            "| | Aciertos | Errores |",
            "|---|---|---|",
            f"| Ítems | {versus['correct']} | {versus['errors']} |",
            f"| Confianza media de Choice | {_fmt(versus['confidence_correct'])} | "
            f"{_fmt(versus['confidence_wrong'])} |",
            f"| Claridad media de Score (de 0 a 4) | {_fmt(versus['clarity_correct'])} | "
            f"{_fmt(versus['clarity_wrong'])} |",
        ]
    lines += ["", "## Por pregunta", ""]
    header = ["Ítem", "Esperada", *[r.judge for r in reports]]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    by_id = {r.judge: {res.item.id: res for res in r.results} for r in reports}
    items = reports[0].results if reports else []
    for base in items:
        cells = [f"`{base.item.id}`", base.item.expected]
        for report in reports:
            result = by_id[report.judge][base.item.id]
            if result.status != "ok":
                cells.append(result.status)
                continue
            mark = "✔" if result.correct else "✘"
            cell = f"{mark} {result.predicted}"
            if result.confidence is not None:
                cell += f" {_fmt(result.confidence)}"
            if result.clarity is not None:
                cell += f" · claridad {_fmt(result.clarity)}"
            if result.decision is not None:
                state = "cautela" if result.decision.get("cautious") else result.decision["scope"]
                cell += f" → {state}"
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    if gate_set is not None and gate_set.origin:
        lines += ["", "## Origen del conjunto", "", gate_set.origin]
    return "\n".join(lines)


def gatekeeper_payload(
    reports: Sequence[GateReport],
    *,
    questions: Path,
    settings: Any = None,
    smoke: Mapping[str, Any] | None = None,
    gate_set: GateSet | None = None,
) -> dict[str, Any]:
    return {
        "questions": str(questions),
        "frozen_at": gate_set.frozen_at if gate_set is not None else "",
        "thresholds": {
            "min_confidence": float(getattr(settings, "jev_router_min_confidence", 0.6)),
            "direct_min_confidence": float(
                getattr(settings, "jev_router_direct_min_confidence", 0.8)
            ),
        },
        "smoke": dict(smoke) if smoke is not None else None,
        "judges": {report.judge: report.to_dict() for report in reports},
    }


# --- Compuerta de afirmaciones -------------------------------------------------------------


@dataclass(frozen=True)
class ClaimsItem:
    """Respuesta congelada con su evidencia (tal como viajaría) y su etiqueta."""

    id: str
    v3: str
    variant: str
    question: str
    answer: str
    evidence: tuple[tuple[str, str], ...]
    label: str
    note: str = ""


@dataclass(frozen=True)
class ClaimsSet:
    path: Path
    items: tuple[ClaimsItem, ...]
    version: Any = None
    frozen_at: str = ""
    origin: str = ""


def load_claims_set(path: Path) -> ClaimsSet:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = tuple(
        ClaimsItem(
            id=str(entry["id"]),
            v3=str(entry.get("v3") or ""),
            variant=str(entry.get("variant") or ""),
            question=str(entry.get("question") or ""),
            answer=str(entry["answer"]),
            evidence=tuple(
                (str(extract["consulta"]), str(extract["texto"]))
                for extract in entry.get("evidence") or []
            ),
            label=str(entry["label"]),
            note=str(entry.get("note") or ""),
        )
        for entry in raw["items"]
    )
    for item in items:
        if item.label not in ("supported", "unsupported"):
            raise ValueError(f"Etiqueta desconocida en {item.id}: {item.label}")
    return ClaimsSet(
        path=Path(path),
        items=items,
        version=raw.get("version"),
        frozen_at=str(raw.get("frozen_at") or ""),
        origin=str(raw.get("origin") or ""),
    )


def load_claims_items(path: Path) -> tuple[ClaimsItem, ...]:
    return load_claims_set(path).items


def claims_query(item: ClaimsItem) -> ClaimsQuery:
    """Carga como en producción: la respuesta minimizada y la evidencia congelada.

    La evidencia ya es la que arma ``build_claims_query`` (con ``dicho_por_la_persona``); la
    pregunta solo sirve para minimizar la respuesta y no viaja.
    """

    return ClaimsQuery(answer=outgoing_answer(item.answer, item.question), evidence=item.evidence)


@dataclass
class ClaimsResult:
    item: ClaimsItem
    status: str = "ok"
    p: float | None = None
    latency_ms: int | None = None

    def to_dict(self, threshold: float) -> dict[str, Any]:
        return {
            "id": self.item.id,
            "v3": self.item.v3,
            "variant": self.item.variant,
            "label": self.item.label,
            "status": self.status,
            "p": self.p,
            "flagged": self.p is not None and self.p < threshold,
            "latency_ms": self.latency_ms,
            "note": self.item.note,
        }


@dataclass
class ClaimsReport:
    threshold: float = 0.5
    results: list[ClaimsResult] = field(default_factory=list)

    @property
    def judged(self) -> list[ClaimsResult]:
        return [r for r in self.results if r.status == "ok" and r.p is not None]

    @property
    def quota_skipped(self) -> int:
        return sum(r.status == QUOTA for r in self.results)

    @property
    def failed(self) -> int:
        return sum(r.status not in ("ok", QUOTA) for r in self.results)

    def grid(self) -> list[dict[str, Any]]:
        """Por umbral: no respaldadas detectadas y buenas marcadas (p < umbral)."""

        unsupported = [r for r in self.judged if r.item.label == "unsupported"]
        supported = [r for r in self.judged if r.item.label == "supported"]
        thresholds = sorted({*CLAIMS_GRID, self.threshold})
        return [
            {
                "threshold": t,
                "detected": sum(r.p is not None and r.p < t for r in unsupported),
                "unsupported": len(unsupported),
                "false_alarms": sum(r.p is not None and r.p < t for r in supported),
                "supported": len(supported),
            }
            for t in thresholds
        ]

    def latencies(self) -> list[int]:
        return [r.latency_ms for r in self.judged if r.latency_ms is not None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "judged": len(self.judged),
            "quota_skipped": self.quota_skipped,
            "failed": self.failed,
            "grid": self.grid(),
            "items": [r.to_dict(self.threshold) for r in self.results],
        }


def eval_claims(
    items: Sequence[ClaimsItem],
    judge: Any,
    *,
    settings: Any = None,
    max_jev_calls: int | None = None,
    run: str = "claims",
    progress: Callable[[ClaimsResult], None] | None = None,
) -> ClaimsReport:
    """Una pregunta Noul por respuesta, hasta ``max_jev_calls``."""

    report = ClaimsReport(threshold=float(getattr(settings, "jev_claims_threshold", 0.5)))
    used = 0
    for item in items:
        result = ClaimsResult(item=item)
        if max_jev_calls is not None and used >= max_jev_calls:
            result.status = QUOTA
        else:
            used += 1
            label_judge(judge, run, item.id)
            try:
                verdict: ClaimsVerdict = judge.check(claims_query(item))
            except JudgeUnavailable as error:
                result.status = error.reason or error.status
                result.latency_ms = error.call.latency_ms if error.call is not None else None
            else:
                result.p = round(verdict.p_supported, 4)
                result.latency_ms = verdict.call.latency_ms
        report.results.append(result)
        if progress is not None:
            progress(result)
    return report


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))]


def render_claims_report(
    report: ClaimsReport, *, questions: Path, claims_set: ClaimsSet | None = None
) -> str:
    """Informe Markdown de ``eval-claims``: detección y falsas alarmas por umbral.

    Las respuestas no se copian al informe: solo los identificadores y la nota del conjunto.
    """

    described = f"Conjunto: `{questions}`"
    if claims_set is not None and claims_set.frozen_at:
        described += f" (congelado el {claims_set.frozen_at})"
    latencies = report.latencies()
    lines = [
        "# Evaluación de la compuerta de afirmaciones (spec 046)",
        "",
        described
        + f"; umbral vigente {_thresholds(report.threshold)} (no respaldada si p < umbral).",
        "",
        f"Juzgadas: {len(report.judged)}; sin llamada por cuota: {report.quota_skipped}; "
        f"fallos: {report.failed}. Latencia p50 / p95: "
        f"{_percentile(latencies, 0.5) or '—'} / {_percentile(latencies, 0.95) or '—'} ms.",
        "",
        "| Umbral | Detección (no respaldadas) | Falsas alarmas (buenas) |",
        "|---|---|---|",
    ]
    for row in report.grid():
        mark = " (vigente)" if row["threshold"] == report.threshold else ""
        lines.append(
            f"| {_thresholds(row['threshold'])}{mark} | "
            f"{_pct(row['detected'], row['unsupported'])} | "
            f"{_pct(row['false_alarms'], row['supported'])} |"
        )
    lines += [
        "",
        "## Por respuesta",
        "",
        "| Ítem | v3 | Variante | Etiqueta | p | Veredicto | Nota |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in report.results:
        if result.p is None:
            verdict = result.status
        else:
            verdict = "no respaldada" if result.p < report.threshold else "respaldada"
        lines.append(
            f"| `{result.item.id}` | {result.item.v3} | {result.item.variant} | "
            f"{result.item.label} | {_fmt(result.p)} | {verdict} | {_cell(result.item.note)} |"
        )
    if claims_set is not None and claims_set.origin:
        lines += ["", "## Origen del conjunto", "", claims_set.origin]
    return "\n".join(lines)


def claims_payload(
    report: ClaimsReport, *, questions: Path, claims_set: ClaimsSet | None = None
) -> dict[str, Any]:
    return {
        "questions": str(questions),
        "frozen_at": claims_set.frozen_at if claims_set is not None else "",
        **report.to_dict(),
    }


# --- Reproducción y cuotas -----------------------------------------------------------------


def _call_from(line: Mapping[str, Any]) -> JudgeCall:
    return JudgeCall(
        kind="router",
        status=cast(JevStatus, str(line.get("status") or "ok")),
        latency_ms=int(line.get("latency_ms") or 0),
        model=str(line.get("model") or ""),
        input_tokens=int(line.get("input_tokens") or 0),
        output_tokens=int(line.get("output_tokens") or 0),
    )


class ReplayJudge:
    """Reproduce sin red los veredictos del gatekeeper grabados en el libro (FR-032).

    ``item`` elige el veredicto (lo fija quien corre la evaluación). Sin veredicto para el
    ítem, o en la compuerta, responde ``JudgeUnavailable``: el turno sigue sin Jev.
    """

    def __init__(self, verdicts: Mapping[str, RouteVerdict]) -> None:
        self._verdicts = dict(verdicts)
        self.run = "replay"
        self.item = ""
        self.calls = 0

    @classmethod
    def from_ledger(cls, path: Path, *, run: str = "gatekeeper") -> "ReplayJudge":
        verdicts: dict[str, RouteVerdict] = {}
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            line = json.loads(raw)
            if line.get("run") != run or line.get("kind") != "router":
                continue
            choice = line.get("choice")
            confidence = _number(line.get("confidence"))
            if line.get("status") != "ok" or choice not in GATE_CLASSES or confidence is None:
                continue
            probabilities = {
                str(k): float(v)
                for k, v in (line.get("probabilities") or {}).items()
                if _number(v) is not None
            }
            verdicts[str(line.get("item") or "")] = RouteVerdict(
                choice=cast(GateClass, choice),
                confidence=confidence,
                probabilities=cast(Mapping[GateClass, float], probabilities),
                call=_call_from(line),
                clarity=_number(line.get("clarity")),
                clarity_confidence=_number(line.get("clarity_confidence")),
            )
        return cls(verdicts)

    def items(self) -> set[str]:
        return set(self._verdicts)

    def available(self) -> bool:
        return True

    def unavailable_status(self) -> JevStatus | None:
        return None

    def classify(self, query: RouteQuery, *, with_score: bool = False) -> RouteVerdict:
        verdict = self._verdicts.get(self.item)
        if verdict is None:
            raise JudgeUnavailable("off", reason="sin_veredicto")
        self.calls += 1
        return verdict

    def check(self, query: ClaimsQuery) -> ClaimsVerdict:
        raise JudgeUnavailable("off", reason="reproduccion")


class QuotaJudge:
    """Cuotas de una corrida de ``eval-agent`` sobre el juez real (FR-027, research R11).

    Cada cuota (gatekeeper y compuerta) cuenta los intentos; al agotarse, la llamada no se
    hace y el juez lanza ``JudgeUnavailable("cap", reason="cuota")``: el gatekeeper decide
    sin Jev y la compuerta queda ``unavailable`` con motivo ``cuota``. ``available()`` no
    cambia, para que una cuota agotada no apague la otra.
    """

    def __init__(
        self, judge: Any, *, router_calls: int | None = None, claims_calls: int | None = None
    ) -> None:
        self.judge = judge
        self.router_calls = router_calls
        self.claims_calls = claims_calls
        self.router_used = 0
        self.claims_used = 0
        self.router_skipped = 0
        self.claims_skipped = 0

    @property
    def client(self) -> Any:
        return getattr(self.judge, "client", None)

    def available(self) -> bool:
        return bool(self.judge.available())

    def unavailable_status(self) -> JevStatus | None:
        status = getattr(self.judge, "unavailable_status", None)
        return status() if status is not None else None

    def classify(self, query: RouteQuery, *, with_score: bool = False) -> RouteVerdict:
        if self.router_calls is not None and self.router_used >= self.router_calls:
            self.router_skipped += 1
            raise JudgeUnavailable("cap", reason=QUOTA)
        self.router_used += 1
        return self.judge.classify(query, with_score=with_score)

    def check(self, query: ClaimsQuery) -> ClaimsVerdict:
        if self.claims_calls is not None and self.claims_used >= self.claims_calls:
            self.claims_skipped += 1
            raise JudgeUnavailable("cap", reason=QUOTA)
        self.claims_used += 1
        return self.judge.check(query)

    def close(self) -> None:
        close = getattr(self.judge, "close", None)
        if close is not None:
            close()


# --- Agente real con los veredictos reproducidos -------------------------------------------


@dataclass
class ReplayRow:
    """Un ítem en cautela o en respuesta directa, corrido con el agente real."""

    item: GateItem
    category: str
    decision: dict[str, Any]
    path: str = ""
    tools: tuple[str, ...] = ()
    consulted_data: bool = False
    answer: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item.id,
            "expected": self.item.expected,
            "category": self.category,
            "decision": dict(self.decision),
            "path": self.path,
            "tools": list(self.tools),
            "consulted_data": self.consulted_data,
            "answer": self.answer,
            "error": self.error,
        }


def build_replay_driver(settings: Any, replay: ReplayJudge) -> tuple[Any, Any]:
    """Driver ``tools`` del ejecutor de evaluación con el ``ReplayJudge`` como gatekeeper.

    La compuerta de afirmaciones no tiene veredictos grabados: corre sin Jev (solo la
    determinista), y así se declara en el informe.
    """

    from carvajal.agent.toolagent.evaluation import build_runtime

    runtime = build_runtime(settings, ["tools"], judge=replay, claims_judge=None)
    return runtime.drivers["tools"], runtime.close


def _category(decision: Any) -> str:
    if decision.cautious:
        return "cautious"
    if decision.scope == "direct" and decision.source == "jev":
        return "direct"
    return ""


def replay_agent(
    items: Sequence[GateItem],
    replay: ReplayJudge,
    driver: Any,
    *,
    settings: Any = None,
    only: Iterable[str] = ("cautious", "direct"),
    progress: Callable[[ReplayRow], None] | None = None,
) -> list[ReplayRow]:
    """Corre el agente sobre los ítems cuya decisión reproducida cae en ``only``."""

    from carvajal.agent.toolagent.tools import DECLARATION_TOOLS

    wanted = set(only)
    rows: list[ReplayRow] = []
    for item in items:
        if item.id not in replay.items():
            continue
        label_judge(replay, "replay", item.id)
        decision = decide_gate(item.question, item.turns, judge=replay, settings=settings)
        category = _category(decision)
        if category not in wanted:
            continue
        row = ReplayRow(item=item, category=category, decision=_decision_value(decision))
        try:
            observation = driver.run_turn(
                owner_key=f"replay-{item.id}",
                thread_id=f"replay-{item.id}",
                question=item.question,
                seed_turns=list(item.turns),
            )
        except Exception as error:  # el ítem queda como error, la corrida sigue
            row.error = f"{type(error).__name__}: {error}"[:300]
        else:
            row.path = str(getattr(observation, "path", ""))
            row.tools = tuple(observation.tool_names)
            row.consulted_data = any(name not in DECLARATION_TOOLS for name in row.tools)
            row.answer = str(observation.answer)
        rows.append(row)
        if progress is not None:
            progress(row)
    return rows


_CATEGORY_LABELS: Final = {"cautious": "cautela", "direct": "respuesta directa"}


def render_replay_report(rows: Sequence[ReplayRow], *, questions: Path, ledger: Path) -> str:
    """Informe de la reproducción: decisión, camino, consultas y respuesta de cada ítem."""

    lines = [
        "# Cautela y respuesta directa con veredictos reproducidos (spec 046, FR-032)",
        "",
        f"Conjunto: `{questions}`; veredictos de `{ledger}` (0 llamadas a Jev). La compuerta "
        "de afirmaciones corre sin Jev (solo la determinista).",
        "",
        "| Ítem | Esperada | Decisión | Motivo | Confianza | Camino | Consultas | "
        "¿Consultó datos? | Respuesta |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    if not rows:
        lines.append("| — | — | — | — | — | — | — | — | Ningún ítem en cautela ni directo. |")
    for row in rows:
        confidence = _number(row.decision.get("confidence"))
        lines.append(
            f"| `{row.item.id}` | {row.item.expected} | "
            f"{_CATEGORY_LABELS.get(row.category, row.category)} | "
            f"{row.decision.get('reason') or '—'} | {_fmt(confidence)} | {row.path or '—'} | "
            f"{', '.join(row.tools) or '—'} | {'sí' if row.consulted_data else 'no'} | "
            f"{_cell(row.error or row.answer, 200)} |"
        )
    return "\n".join(lines)


def replay_payload(rows: Sequence[ReplayRow], *, questions: Path, ledger: Path) -> dict[str, Any]:
    return {
        "questions": str(questions),
        "ledger": str(ledger),
        "items": [row.to_dict() for row in rows],
        "categories": dict(Counter(row.category for row in rows)),
    }
