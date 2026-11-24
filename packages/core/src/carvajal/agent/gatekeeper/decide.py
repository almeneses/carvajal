"""Decisión del gatekeeper del turno: reglas → Jev → LLM (spec 046, contrato §1).

:func:`decide_gate` corre en el nodo ``shortcut`` del grafo ``tools``:

1. Las reglas deciden primero, sin Jev ni consultas (:func:`rule_decision`).
2. Si no deciden y Jev está disponible (``JEV_ROUTER_MODE`` distinto de ``off``), **una**
   pregunta Choice con la pregunta redactada y, si aplica, una línea de contexto.
3. Sin Jev (no configurado, apagado, tope, circuito, plazo, error o salida inválida), el
   modelo elige entre las 20 consultas como antes (``source="llm"``), sin cautela.

Con Jev, la clase se aplica solo si su confianza llega al umbral (y al umbral propio de la
respuesta directa); si no, o si contradice una señal estructurada fuerte de las reglas, hay
**cautela**: las 20 consultas y el bloque ``<cautela>``. Con ``commercial_mixed`` de las
reglas, todas las consultas. En sombra se consulta y se registra sin restringir.

El log ``gatekeeper.decided`` lleva solo la decisión (sin texto ni probabilidades por clase).
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Final

import structlog

from carvajal.agent.gatekeeper.model import SCOPE_BY_CLASS, GatekeeperDecision
from carvajal.agent.gatekeeper.privacy import context_line, outgoing_question
from carvajal.agent.gatekeeper.rules import rule_decision, rules_route
from carvajal.agent.state import Turn
from carvajal.application.ports import JudgeUnavailable, RouteJudge, RouteQuery

__all__ = [
    "REASON_APPLIED",
    "REASON_DISAGREEMENT",
    "REASON_LOW",
    "REASON_LOW_DIRECT",
    "REASON_MIXED",
    "REASON_SHADOW",
    "decide_gate",
]

logger = structlog.get_logger(__name__)

REASON_APPLIED: Final = "aplicada"
REASON_LOW: Final = "confianza_baja"
REASON_LOW_DIRECT: Final = "confianza_baja_directa"
REASON_DISAGREEMENT: Final = "desacuerdo_reglas"
REASON_MIXED: Final = "mixta_reglas"
REASON_SHADOW: Final = "sombra"
# Señal estructurada fuerte de las reglas (research R4): si Jev la contradice con ``rag`` o
# ``respuesta_directa``, cautela.
_STRONG_ROUTES: Final = frozenset({"commercial", "tool"})
_STRONG_INTENTS: Final = frozenset({"commercial_clarify", "missing_period"})
_WEAK_CLASSES: Final = frozenset({"rag", "respuesta_directa"})
_MIXED_INTENT: Final = "commercial_mixed"


def _without_jev(status: str, rules: str) -> GatekeeperDecision:
    return GatekeeperDecision(
        scope="all", source="llm", reason=f"sin_jev:{status}", rules_route=rules
    )


def _strong_signal(rules: str) -> bool:
    route, _, intent = rules.partition("/")
    return (route in _STRONG_ROUTES and intent != _MIXED_INTENT) or intent in _STRONG_INTENTS


def decide_gate(
    question: str,
    turns: Sequence[Turn] = (),
    commercial_context: Mapping[str, Any] | None = None,
    memory: Any = None,
    judge: RouteJudge | None = None,
    settings: Any = None,
    *,
    shortcut: tuple[str, str] | None = None,
) -> GatekeeperDecision:
    """Decisión del gatekeeper del turno (data-model *GatekeeperDecision*).

    ``memory`` es la instantánea de la memoria del dueño: solo sirve para enmascarar sus
    valores en lo que viaja a Jev (nunca viaja). ``shortcut`` es el resultado ya calculado
    de ``shortcut_intent``. ``settings`` aporta ``jev_router_mode`` y los umbrales.
    """

    decision = _decide(question, turns, commercial_context, memory, judge, settings, shortcut)
    logger.info(
        "gatekeeper.decided",
        gate_class=decision.gate_class,
        scope=decision.scope,
        source=decision.source,
        rule=decision.rule or None,
        cautious=decision.cautious,
        reason=decision.reason or None,
        confidence=decision.confidence,
        jev_status=decision.jev_status,
    )
    return decision


def _decide(
    question: str,
    turns: Sequence[Turn],
    commercial_context: Mapping[str, Any] | None,
    memory: Any,
    judge: RouteJudge | None,
    settings: Any,
    shortcut: tuple[str, str] | None,
) -> GatekeeperDecision:
    rules = rules_route(question, turns)
    ruled = rule_decision(question, turns, commercial_context, shortcut=shortcut)
    if ruled is not None:
        return GatekeeperDecision(
            scope=ruled.scope,
            source="reglas",
            gate_class=ruled.gate_class,
            rule=ruled.rule,
            rules_route=rules,
        )
    mode = str(getattr(settings, "jev_router_mode", "on") or "on")
    if judge is None or mode == "off":
        return _without_jev("off", rules)
    if not judge.available():
        status = getattr(judge, "unavailable_status", lambda: None)() or "off"
        return _without_jev(str(status), rules)
    query = RouteQuery(
        question=outgoing_question(question, memory),
        context=context_line(question, turns, memory),
    )
    try:
        verdict = judge.classify(query)
    except JudgeUnavailable as error:
        # ``reason`` distingue la cuota de una corrida de evaluación (``cuota``) del estado.
        decision = _without_jev(error.reason or error.status, rules)
        if error.call is not None:
            return replace(decision, jev=error.call.to_value())
        return decision
    common: dict[str, Any] = {
        "source": "jev",
        "gate_class": verdict.choice,
        "confidence": round(verdict.confidence, 2),
        "probabilities": {k: round(v, 4) for k, v in verdict.probabilities.items()},
        "rules_route": rules,
        "jev": {
            **verdict.call.to_value(),
            "choice": verdict.choice,
            "confidence": round(verdict.confidence, 4),
            "probabilities": dict(verdict.probabilities),
        },
    }
    if mode == "shadow":
        return GatekeeperDecision(scope="all", reason=REASON_SHADOW, **common)
    if rules.endswith(f"/{_MIXED_INTENT}"):
        return GatekeeperDecision(scope="all", reason=REASON_MIXED, **common)
    if _strong_signal(rules) and verdict.choice in _WEAK_CLASSES:
        return GatekeeperDecision(scope="all", cautious=True, reason=REASON_DISAGREEMENT, **common)
    minimum = float(getattr(settings, "jev_router_min_confidence", 0.6))
    direct_minimum = float(getattr(settings, "jev_router_direct_min_confidence", 0.8))
    if verdict.confidence < minimum:
        return GatekeeperDecision(scope="all", cautious=True, reason=REASON_LOW, **common)
    if verdict.choice == "respuesta_directa" and verdict.confidence < direct_minimum:
        return GatekeeperDecision(scope="all", cautious=True, reason=REASON_LOW_DIRECT, **common)
    return GatekeeperDecision(scope=SCOPE_BY_CLASS[verdict.choice], reason=REASON_APPLIED, **common)
