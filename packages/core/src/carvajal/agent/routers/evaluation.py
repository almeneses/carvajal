"""Evaluación comparativa de enrutadores sobre ``routing_questions.json`` (spec 036, T039).

Métricas por enrutador:

- precisión de ruta y de intención sobre todas las preguntas;
- precisión por dato: cada clave de ``expected`` cuenta como un acierto o un fallo;
- violaciones de material: opciones recomendadas con un material de
  ``constraints.forbidden_materials`` (se ejecuta la secuencia comercial real);
- salidas inválidas: veces que el enrutador cayó al respaldo de reglas;
- latencia media y p95 de la decisión, y llamadas al modelo como base del costo.
"""

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from carvajal.agent.commercial import CAPACITY_TOLERANCE_OZ
from carvajal.agent.commercial_flow import run_commercial
from carvajal.agent.routers import Router, router_name
from carvajal.agent.routers._payload import FALLBACK_PREFIX
from carvajal.catalog.loader import Catalog

__all__ = ["RouterReport", "evaluate_router", "load_items", "render_report"]


@dataclass
class RouterReport:
    """Resultado de un enrutador sobre el conjunto de evaluación."""

    router: str
    total: int = 0
    route_hits: int = 0
    intent_hits: int = 0
    field_total: int = 0
    field_hits: int = 0
    material_violations: int = 0
    invalid_outputs: int = 0
    model_calls: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def _rate(hits: int, total: int) -> float:
        return hits / total if total else 0.0

    @property
    def route_accuracy(self) -> float:
        return self._rate(self.route_hits, self.total)

    @property
    def intent_accuracy(self) -> float:
        return self._rate(self.intent_hits, self.total)

    @property
    def field_accuracy(self) -> float:
        return self._rate(self.field_hits, self.field_total)

    @property
    def latency_mean_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def latency_p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def _matches(key: str, got: Any, expected: Any) -> bool:
    """La capacidad se compara con la tolerancia de la búsqueda; lo demás, exacto."""

    if key == "capacity_oz" and isinstance(got, float) and isinstance(expected, float):
        return abs(got - expected) <= CAPACITY_TOLERANCE_OZ
    return got == expected


def load_items(path: Path) -> list[dict[str, Any]]:
    """Preguntas del conjunto, en el orden del archivo."""

    return list(json.loads(path.read_text(encoding="utf-8"))["items"])


def evaluate_router(
    router: Router, items: list[dict[str, Any]], *, catalog: Catalog | None = None
) -> RouterReport:
    """Ejecuta el enrutador sobre cada pregunta y acumula las métricas."""

    report = RouterReport(router=router_name(router))
    for item in items:
        started = time.perf_counter()
        decision = router(item["question"], item.get("turns"))
        report.latencies_ms.append((time.perf_counter() - started) * 1000)
        report.total += 1
        problems: list[str] = []
        if decision.reason.startswith(FALLBACK_PREFIX):
            report.invalid_outputs += 1
            # El respaldo a reglas puede acertar: el fallo del modelo se lista igual.
            problems.append(decision.reason.split("; reglas:")[0])
        if decision.route == item["route"]:
            report.route_hits += 1
        else:
            problems.append(f"ruta {decision.route} (esperada {item['route']})")
        if decision.intent == item["intent"]:
            report.intent_hits += 1
        else:
            problems.append(f"intención {decision.intent} (esperada {item['intent']})")
        for key, expected in item.get("expected", {}).items():
            report.field_total += 1
            got = decision.extra.get(key)
            if _matches(key, got, expected):
                report.field_hits += 1
            else:
                problems.append(f"{key}={got!r} (esperado {expected!r})")
        forbidden = set(item.get("constraints", {}).get("forbidden_materials", []))
        if forbidden and decision.route == "commercial" and decision.intent != "commercial_custom":
            try:
                result = run_commercial(decision.extra, catalog=catalog)
            except Exception as error:  # una pregunta no debe abortar la corrida
                problems.append(f"secuencia comercial falló: {type(error).__name__}: {error}")
            else:
                violations = [
                    opt["sku"] for opt in result["options"] if opt["material"] in forbidden
                ]
                report.material_violations += len(violations)
                if violations:
                    problems.append(f"material prohibido en {', '.join(violations)}")
        if problems:
            report.failures.append(
                {"id": item["id"], "question": item["question"], "problems": problems}
            )
    report.model_calls = int(getattr(router, "calls", 0))
    return report


def render_report(reports: list[RouterReport]) -> str:
    """Tabla comparativa en Markdown y fallos por enrutador."""

    lines = [
        "| Enrutador | Preguntas | Ruta | Intención | Datos | Violaciones de material | "
        "Salidas inválidas | Latencia media (ms) | Latencia p95 (ms) | Llamadas al modelo |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        lines.append(
            f"| {report.router} | {report.total} | {report.route_accuracy:.1%} | "
            f"{report.intent_accuracy:.1%} | {report.field_accuracy:.1%} | "
            f"{report.material_violations} | {report.invalid_outputs} | "
            f"{report.latency_mean_ms:.1f} | {report.latency_p95_ms:.1f} | {report.model_calls} |"
        )
    for report in reports:
        if not report.failures:
            continue
        lines += ["", f"Fallos de `{report.router}` ({len(report.failures)}):", ""]
        for failure in report.failures:
            lines.append(
                f"- `{failure['id']}` «{failure['question']}»: {'; '.join(failure['problems'])}"
            )
    return "\n".join(lines)
