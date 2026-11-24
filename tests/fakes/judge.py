"""Dobles del juez de Jev para probar el gatekeeper y la compuerta sin red (spec 046, R13).

``FakeJudge`` implementa ``RouteJudge`` y ``ClaimsJudge``: devuelve la clase y la confianza
fijas (o la probabilidad de respaldo fija), lanza ``JudgeUnavailable`` con el estado dado,
puede tardar y registra cada carga recibida para comprobar lo que viajaría a TypeSafe.
"""

import time
from dataclasses import dataclass, field

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


def probabilities(choice: GateClass, confidence: float) -> dict[GateClass, float]:
    """Probabilidades que suman 1 con ``confidence`` en la clase elegida."""

    rest = round((1 - confidence) / (len(GATE_CLASSES) - 1), 4)
    return {name: confidence if name == choice else rest for name in GATE_CLASSES}


@dataclass
class FakeJudge:
    """Juez falso: clase y confianza fijas, ``p`` fija, o un fallo con su estado."""

    choice: GateClass = "rag"
    confidence: float | None = 0.9
    p: float = 0.9
    fail: JevStatus | None = None
    claims_fail: JevStatus | None = None
    is_available: bool = True
    delay_s: float = 0.0
    routes: list[RouteQuery] = field(default_factory=list)
    claims: list[ClaimsQuery] = field(default_factory=list)

    def available(self) -> bool:
        return self.is_available

    def unavailable_status(self) -> JevStatus | None:
        return None if self.is_available else "circuit_open"

    def _call(self, kind: str, status: JevStatus = "ok") -> JudgeCall:
        return JudgeCall(
            kind="router" if kind == "router" else "claims",
            status=status,
            latency_ms=12,
            model="jev-1.14.0" if status == "ok" else "",
            input_tokens=400 if status == "ok" else 0,
            output_tokens=9 if status == "ok" else 0,
        )

    def classify(self, query: RouteQuery, *, with_score: bool = False) -> RouteVerdict:
        self.routes.append(query)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail is not None:
            raise JudgeUnavailable(self.fail, self._call("router", self.fail))
        confidence = self.confidence if self.confidence is not None else 0.9
        return RouteVerdict(
            choice=self.choice,
            confidence=confidence,
            probabilities=probabilities(self.choice, confidence),
            call=self._call("router"),
        )

    def check(self, query: ClaimsQuery) -> ClaimsVerdict:
        self.claims.append(query)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.claims_fail is not None:
            raise JudgeUnavailable(self.claims_fail, self._call("claims", self.claims_fail))
        return ClaimsVerdict(p_supported=self.p, call=self._call("claims"))
