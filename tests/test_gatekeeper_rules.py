"""Reglas del gatekeeper (spec 046, T019; FR-002, SC-002).

``rule_decision`` es pura: decide sin Jev ni consultas los saludos, las despedidas, «olvida
todo», la preferencia declarada y el seguimiento comercial; si no decide, devuelve ``None``.
"""

import pytest

from carvajal.agent.gatekeeper.rules import RuleDecision, rule_decision, rules_route
from carvajal.agent.toolagent import graph as graph_module


@pytest.mark.parametrize(
    ("question", "rule"),
    [
        pytest.param("hola", "greeting", id="saludo"),
        pytest.param("¿qué puedes hacer?", "greeting", id="capacidad"),
        pytest.param("perfecto, eso era todo, muchas gracias", "farewell", id="despedida"),
        pytest.param("Olvida todo lo que sabes de mí", "memory_forget", id="olvidar-todo"),
    ],
)
def test_las_reglas_de_conversacion_van_al_atajo(question: str, rule: str) -> None:
    # Act
    decision = rule_decision(question)

    # Assert
    assert decision == RuleDecision(scope="direct", rule=rule, gate_class="respuesta_directa")


def test_nunca_devuelve_memory_unavailable() -> None:
    decision = rule_decision("Olvida todo lo que sabes de mí")

    assert decision is not None and decision.rule == "memory_forget"


def test_la_preferencia_declarada_va_al_agente_con_todas() -> None:
    decision = rule_decision("llámame Ana")

    assert decision == RuleDecision(scope="all", rule="memory_write", gate_class=None)


def test_el_seguimiento_comercial_va_a_las_estructuradas() -> None:
    # Arrange
    context = {"family": "vaso", "capacity_oz": 9, "quantity": 500, "city": "Cali"}

    # Act
    decision = rule_decision("mejor 2000", [], context)

    # Assert
    assert decision == RuleDecision(
        scope="structured", rule="commercial_follow_up", gate_class="herramienta_estructurada"
    )


@pytest.mark.parametrize(
    "question",
    [
        "Hola, ¿cuánto vale la caja de vasos de 9 oz?",
        "¿Qué es Propal?",
        "Cotízame 500 vasos en Cali",
        "quiubo pues, todo bien?",
    ],
)
def test_si_ninguna_regla_decide_devuelve_none(question: str) -> None:
    assert rule_decision(question) is None


def test_rules_route_y_reexportes_del_grafo() -> None:
    assert rules_route("¿Cuáles fueron los ingresos de Carvajal en 2024?") == "tool/indicator"
    assert graph_module.shortcut_intent("hola") == ("greeting", "")
    assert graph_module.is_closing("gracias, muy amable")
