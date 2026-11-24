"""Decisión del gatekeeper y tipos comunes (spec 046, data-model).

``GateClass``, ``GateScope`` y ``JevStatus`` se definen en ``application/ports.py`` (sin
dependencias) y se reexportan aquí con el resto de los tipos del gatekeeper.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast, get_args

from carvajal.application.ports import GATE_CLASSES, GateClass, GateScope, JevStatus

__all__ = [
    "DECISION_VERSION",
    "GATE_CLASSES",
    "SCOPE_BY_CLASS",
    "ClaimsStatus",
    "GateClass",
    "GateScope",
    "GateSource",
    "GatekeeperDecision",
    "JevStatus",
    "llm_class",
    "map_route",
]

GateSource = Literal["reglas", "jev", "llm"]
ClaimsStatus = Literal["supported", "unsupported", "skipped", "unavailable", "off"]

DECISION_VERSION: Final = 1
# Alcance de cada clase cuando se aplica sin cautela (data-model).
SCOPE_BY_CLASS: Final[Mapping[GateClass, GateScope]] = {
    "herramienta_estructurada": "structured",
    "rag": "rag",
    "mixta": "all",
    "respuesta_directa": "direct",
}
_SCOPES: Final = frozenset(get_args(GateScope))
_SOURCES: Final = frozenset(get_args(GateSource))
# Rutas e intenciones del enrutador de reglas que piden datos estructurados.
_STRUCTURED_INTENTS: Final = frozenset(
    {"commercial", "commercial_custom", "indicator", "commercial_clarify", "missing_period"}
)


@dataclass(frozen=True)
class GatekeeperDecision:
    """Decisión del turno (canal ``gatekeeper`` del estado; data-model).

    ``gate_class`` es ``None`` si decidió el LLM o la regla de preferencia; ``llm_class``
    solo existe con ``source="llm"`` y la añade el streaming al cerrar el turno.
    """

    scope: GateScope
    source: GateSource
    gate_class: GateClass | None = None
    rule: str = ""
    cautious: bool = False
    reason: str = ""
    confidence: float | None = None
    probabilities: Mapping[str, float] = field(default_factory=dict)
    rules_route: str = ""
    jev: Mapping[str, Any] | None = None
    llm_class: GateClass | None = None

    @property
    def jev_status(self) -> str:
        """Estado de la llamada a Jev; ``skipped`` si decidió una regla, ``off`` sin llamada."""

        if self.jev is not None:
            return str(self.jev.get("status") or "")
        if self.source == "reglas":
            return "skipped"
        if self.reason.startswith("sin_jev:"):
            return self.reason.removeprefix("sin_jev:")
        return "off"

    def to_value(self) -> dict[str, Any]:
        """Forma JSON del canal (serializable por el checkpointer)."""

        return {
            "v": DECISION_VERSION,
            "gate_class": self.gate_class,
            "scope": self.scope,
            "source": self.source,
            "rule": self.rule,
            "cautious": self.cautious,
            "reason": self.reason,
            "confidence": self.confidence,
            "probabilities": dict(self.probabilities),
            "rules_route": self.rules_route,
            "jev": dict(self.jev) if self.jev is not None else None,
            "llm_class": self.llm_class,
            "jev_status": self.jev_status,
        }

    @classmethod
    def from_value(cls, value: Any) -> "GatekeeperDecision | None":
        """Decisión desde el canal; ``None`` si falta, es de otra versión o no valida."""

        if not isinstance(value, Mapping) or value.get("v") != DECISION_VERSION:
            return None
        scope, source = value.get("scope"), value.get("source")
        if scope not in _SCOPES or source not in _SOURCES:
            return None
        gate_class = value.get("gate_class")
        llm = value.get("llm_class")
        confidence = value.get("confidence")
        jev = value.get("jev")
        return cls(
            scope=cast(GateScope, scope),
            source=cast(GateSource, source),
            gate_class=cast(GateClass, gate_class) if gate_class in GATE_CLASSES else None,
            rule=str(value.get("rule") or ""),
            cautious=bool(value.get("cautious")),
            reason=str(value.get("reason") or ""),
            confidence=float(confidence) if isinstance(confidence, int | float) else None,
            probabilities={
                str(k): float(v)
                for k, v in (value.get("probabilities") or {}).items()
                if isinstance(v, int | float)
            },
            rules_route=str(value.get("rules_route") or ""),
            jev=dict(jev) if isinstance(jev, Mapping) else None,
            llm_class=cast(GateClass, llm) if llm in GATE_CLASSES else None,
        )


def map_route(route: str, intent: str) -> GateClass:
    """Clase del gatekeeper para una decisión del enrutador de reglas (data-model).

    La usan la traza del modo ``routes`` y la evaluación: ``rag/*`` → ``rag``; las rutas
    comerciales, de indicador y las aclaraciones de datos → ``herramienta_estructurada``;
    ``commercial_mixed`` → ``mixta``; el saludo y la preferencia → ``respuesta_directa``.
    """

    if route == "rag":
        return "rag"
    if intent == "commercial_mixed":
        return "mixta"
    if route in ("commercial", "tool") or intent in _STRUCTURED_INTENTS:
        return "herramienta_estructurada"
    return "respuesta_directa"


def llm_class(groups: Any) -> GateClass:
    """Clase que eligió el LLM según los grupos de las consultas que ejecutó (FR-006).

    Solo ``rag`` → ``rag``; solo ``structured`` → ``herramienta_estructurada``; ambas →
    ``mixta``; solo ``sin_consulta`` o ninguna → ``respuesta_directa``.
    """

    present = {group for group in groups if group in ("rag", "structured")}
    if present == {"rag", "structured"}:
        return "mixta"
    if present == {"rag"}:
        return "rag"
    if present == {"structured"}:
        return "herramienta_estructurada"
    return "respuesta_directa"
