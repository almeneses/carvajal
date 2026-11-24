"""Decisión del gatekeeper como JSON y mapas de clases (spec 046, T014; data-model)."""

import json

import pytest

from carvajal.agent.gatekeeper.model import (
    SCOPE_BY_CLASS,
    GatekeeperDecision,
    llm_class,
    map_route,
)
from carvajal.agent.routers._payload import INTENTS
from carvajal.application import ports


def test_la_decision_va_y_vuelve_como_json() -> None:
    # Arrange
    decision = GatekeeperDecision(
        scope="rag",
        source="jev",
        gate_class="rag",
        reason="aplicada",
        confidence=0.83,
        probabilities={"rag": 0.83, "mixta": 0.17},
        rules_route="rag/documental",
        jev={"kind": "router", "status": "ok", "latency_ms": 271},
    )

    # Act
    value = json.loads(json.dumps(decision.to_value()))
    restored = GatekeeperDecision.from_value(value)

    # Assert
    assert restored == decision
    assert value["v"] == 1
    assert value["jev_status"] == "ok"


@pytest.mark.parametrize(
    "value",
    [None, {}, {"v": 2, "scope": "all", "source": "llm"}, {"v": 1, "scope": "x", "source": "llm"}],
)
def test_una_version_desconocida_o_invalida_no_se_lee(value: object) -> None:
    assert GatekeeperDecision.from_value(value) is None


def test_estado_de_jev_segun_la_fuente() -> None:
    assert GatekeeperDecision(scope="direct", source="reglas").jev_status == "skipped"
    assert GatekeeperDecision(scope="all", source="llm", reason="sin_jev:cap").jev_status == "cap"


def test_mapa_clase_alcance() -> None:
    assert SCOPE_BY_CLASS == {
        "herramienta_estructurada": "structured",
        "rag": "rag",
        "mixta": "all",
        "respuesta_directa": "direct",
    }
    assert set(SCOPE_BY_CLASS) == set(ports.GATE_CLASSES)


EXPECTED = {
    ("direct", "greeting"): "respuesta_directa",
    ("direct", "memory_write"): "respuesta_directa",
    ("direct", "missing_period"): "herramienta_estructurada",
    ("direct", "commercial_clarify"): "herramienta_estructurada",
    ("rag", "documental"): "rag",
    ("rag", "follow_up"): "rag",
    ("tool", "indicator"): "herramienta_estructurada",
    ("commercial", "commercial"): "herramienta_estructurada",
    ("commercial", "commercial_custom"): "herramienta_estructurada",
    ("commercial", "commercial_mixed"): "mixta",
}


def test_map_route_cubre_todas_las_rutas_e_intenciones() -> None:
    pairs = {(route, intent) for route, intents in INTENTS.items() for intent in intents}

    assert pairs == set(EXPECTED)
    for (route, intent), expected in EXPECTED.items():
        assert map_route(route, intent) == expected


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        (["rag"], "rag"),
        (["structured", "any"], "herramienta_estructurada"),
        (["rag", "structured"], "mixta"),
        (["any"], "respuesta_directa"),
        ([], "respuesta_directa"),
    ],
)
def test_llm_class(groups: list[str], expected: str) -> None:
    assert llm_class(groups) == expected
