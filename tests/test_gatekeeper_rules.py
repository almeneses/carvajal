"""Reglas del gatekeeper (spec 046, T019; FR-002, SC-002).

``rule_decision`` es pura: decide sin Jev ni consultas los saludos, las despedidas, «olvida
todo», la preferencia declarada o un dato para la memoria (``declares_memory``) y el
seguimiento comercial; si no decide, devuelve ``None``.
"""

import pytest
from tests.fakes.judge import FakeJudge

from carvajal.agent.gatekeeper.decide import decide_gate
from carvajal.agent.gatekeeper.rules import (
    RuleDecision,
    declares_memory,
    is_greeting,
    rule_decision,
    rules_route,
)
from carvajal.agent.toolagent import graph as graph_module
from carvajal.infrastructure.settings import Settings


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


MEMORY_DECLARATIONS = [
    "Hola, soy Ana Prueba de la Panadería La Espiga y suelo comprar en Cali. "
    "Recuérdalo para la próxima.",
    "Soy Ana, de la Panadería La Espiga.",
    "Gracias, soy Ana",
    "Soy Ana de La Espiga. Chao",
    "Trabajo en la Panadería La Espiga",
    "Suelo comprar en Cali",
    "No olvides que somos de Medellín",
    "Recuerde que somos de Cali",
    "Guarda mi formato: respuestas cortas",
    "Anota que somos una panadería",
    "Tenlo en cuenta para la próxima",
]


@pytest.mark.parametrize("question", MEMORY_DECLARATIONS)
def test_un_dato_para_la_memoria_va_al_agente_sin_jev(question: str) -> None:
    # Arrange: Jev la daría por respuesta directa con confianza sobre el umbral.
    judge = FakeJudge(choice="respuesta_directa", confidence=0.9)

    # Act
    decision = decide_gate(question, (), None, None, judge, Settings(_env_file=None))

    # Assert: la regla de memoria decide; ni atajo ni Jev.
    assert declares_memory(question)
    expected = RuleDecision(scope="all", rule="memory_write", gate_class=None)
    assert rule_decision(question) == expected
    assert (decision.source, decision.scope, decision.rule) == ("reglas", "all", "memory_write")
    assert judge.routes == []


@pytest.mark.parametrize(
    "question",
    [
        "hola",
        "Hola Ana",
        "Olvida todo lo que sabes de mí",
        "¿Recuerdas lo que te dije?",
        "Recordar la historia del edificio de Carvajal",
        "Guardé lo que armamos",
        "Anótame 10 pacas de portacomidas",
        "Recuérdame el precio del vaso de 9 oz",
        "Tengo un restaurante en Neiva, ¿qué empaque me sirve?",
        "¿dónde lo compro en Cali?",
        "Tengo una pregunta",
        # El nombre de un tercero no es una presentación propia.
        "Mi jefa Ana Restrepo quiere saber el precio de los vasos",
        "Mi cliente se llama Ana y quiere 500 vasos",
        "Le escribo de parte de Ana",
    ],
)
def test_lo_que_no_da_datos_para_la_memoria_no_es_declaracion(question: str) -> None:
    assert not declares_memory(question)


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
        "Hola Ana",
        "Hola de nuevo",
        "Buenas, necesito 500 vasos",
        "Saludos desde Cali",
        "¿Qué más?",
        "¿Qué tal los vasos de 9 oz?",
        "Buenas tardes, ¿cómo estás? Quiero precios",
    ],
)
def test_si_ninguna_regla_decide_devuelve_none(question: str) -> None:
    assert rule_decision(question) is None


GREETINGS = [
    "Buenas",
    "Hola, buenas tardes",
    "Hola, ¿cómo estás?",
    "holaaa",
    "Holi",
    "hey",
    "Buen día",
    "hola buen dia",
    "Saludos",
    "Hola, qué tal?",
    "Buenas noches, cómo va?",
    "Hola Carvajal",
    "Muy buenos días a todos",
]


@pytest.mark.parametrize("question", GREETINGS)
def test_los_saludos_con_cortesia_van_al_atajo_sin_jev(question: str) -> None:
    # Arrange
    judge = FakeJudge(choice="respuesta_directa", confidence=0.7)

    # Act
    decision = decide_gate(question, (), None, None, judge, Settings(_env_file=None))

    # Assert: regla de saludo, sin llamar a Jev ni consultar nada.
    assert is_greeting(question)
    assert rule_decision(question) == RuleDecision(
        scope="direct", rule="greeting", gate_class="respuesta_directa"
    )
    assert (decision.source, decision.scope) == ("reglas", "direct")
    assert judge.routes == []


def test_rules_route_y_reexportes_del_grafo() -> None:
    assert rules_route("¿Cuáles fueron los ingresos de Carvajal en 2024?") == "tool/indicator"
    assert graph_module.shortcut_intent("hola") == ("greeting", "")
    assert graph_module.is_closing("gracias, muy amable")
