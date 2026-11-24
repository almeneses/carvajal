"""``decide_gate``: reglas → Jev → LLM, umbrales y cautela (spec 046, T020 y T027).

Con ``FakeJudge`` (sin red): cada clase con confianza alta se aplica con su alcance; los
turnos de regla no llaman a Jev; sombra y ``off``; cada fallo de Jev cae al LLM; umbrales,
desacuerdo con las reglas y ``commercial_mixed``.
"""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from tests.fakes.judge import FakeJudge

from carvajal.agent.gatekeeper.decide import decide_gate
from carvajal.agent.gatekeeper.privacy import outgoing_question
from carvajal.agent.toolagent.tools.registry import TOOLS_BY_SCOPE
from carvajal.application.ports import GATE_CLASSES, GateClass, JevStatus
from carvajal.infrastructure.settings import Settings

QUESTION = "¿Qué empaques recomiendan para un evento grande?"
V3 = Path(__file__).resolve().parents[1] / "data" / "eval" / "generalization_v3.json"


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- Parte 1 (US1) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("choice", "scope"),
    [
        ("herramienta_estructurada", "structured"),
        ("rag", "rag"),
        ("mixta", "all"),
        ("respuesta_directa", "direct"),
    ],
)
def test_cada_clase_con_confianza_alta_aplica_su_alcance(choice: str, scope: str) -> None:
    # Arrange
    judge = FakeJudge(choice=cast(GateClass, choice), confidence=0.9)

    # Act
    decision = decide_gate(QUESTION, judge=judge, settings=_settings())

    # Assert
    assert decision.scope == scope
    assert decision.source == "jev"
    assert decision.reason == "aplicada"
    assert decision.gate_class == choice
    assert decision.cautious is False
    assert decision.confidence == 0.9
    assert decision.jev is not None and decision.jev["status"] == "ok"
    assert len(judge.routes) == 1


@pytest.mark.parametrize("question", ["hola", "gracias, eso era todo", "llámame Ana"])
def test_los_turnos_de_regla_no_llaman_a_jev(question: str) -> None:
    judge = FakeJudge()

    decision = decide_gate(question, judge=judge, settings=_settings())

    assert decision.source == "reglas"
    assert judge.routes == []


def test_sombra_consulta_y_no_restringe() -> None:
    judge = FakeJudge(choice="rag", confidence=0.95)

    decision = decide_gate(QUESTION, judge=judge, settings=_settings(jev_router_mode="shadow"))

    assert decision.scope == "all"
    assert decision.reason == "sombra"
    assert decision.source == "jev"
    assert decision.cautious is False
    assert len(judge.routes) == 1


def test_off_no_consulta() -> None:
    judge = FakeJudge()

    decision = decide_gate(QUESTION, judge=judge, settings=_settings(jev_router_mode="off"))

    assert decision.source == "llm"
    assert decision.reason == "sin_jev:off"
    assert judge.routes == []


def test_sin_juez_es_sin_jev_off() -> None:
    decision = decide_gate(QUESTION, judge=None, settings=_settings())

    assert (decision.scope, decision.source, decision.reason) == ("all", "llm", "sin_jev:off")
    assert decision.cautious is False


@pytest.mark.parametrize("status", ["timeout", "error", "auth", "invalid", "cap", "circuit_open"])
def test_cada_fallo_de_jev_cae_al_llm(status: str) -> None:
    judge = FakeJudge(fail=cast(JevStatus, status))

    decision = decide_gate(QUESTION, judge=judge, settings=_settings())

    assert decision.scope == "all"
    assert decision.source == "llm"
    assert decision.reason == f"sin_jev:{status}"
    assert decision.cautious is False
    assert decision.jev_status == status


def test_juez_no_disponible_no_llama() -> None:
    judge = FakeJudge(is_available=False)

    decision = decide_gate(QUESTION, judge=judge, settings=_settings())

    assert decision.reason == "sin_jev:circuit_open"
    assert judge.routes == []


def test_la_consulta_lleva_la_pregunta_redactada() -> None:
    # Arrange
    judge = FakeJudge()
    question = "Soy Ana, mi celular es 3001234567. ¿Qué vasos tienen para café?"

    # Act
    decide_gate(question, judge=judge, settings=_settings())

    # Assert
    assert judge.routes[0].question == outgoing_question(question)
    assert "3001234567" not in judge.routes[0].question
    assert "Ana" not in judge.routes[0].question
    assert judge.routes[0].context == ""


# --- Parte 2 (US2): umbrales, desacuerdo y mixta ------------------------------------------------


def test_confianza_baja_es_cautela() -> None:
    decision = decide_gate(QUESTION, judge=FakeJudge(confidence=0.59), settings=_settings())

    assert (decision.scope, decision.cautious, decision.reason) == ("all", True, "confianza_baja")


def test_respuesta_directa_con_07_nunca_va_al_atajo() -> None:
    judge = FakeJudge(choice="respuesta_directa", confidence=0.7)

    decision = decide_gate("quiubo pues, todo bien?", judge=judge, settings=_settings())

    assert decision.scope == "all"
    assert decision.cautious is True
    assert decision.reason == "confianza_baja_directa"


@pytest.mark.parametrize(
    "question",
    [
        "Cotízame 500 vasos de 9 oz para café en Cali",  # commercial/commercial
        "¿Cuáles fueron los ingresos consolidados de Carvajal en 2024?",  # tool/indicator
        "busco vasos de 16 oz",  # direct/commercial_clarify
        "¿Cuál es el EBITDA de Carvajal?",  # direct/missing_period
    ],
)
@pytest.mark.parametrize("choice", ["rag", "respuesta_directa"])
def test_desacuerdo_fuerte_con_las_reglas_es_cautela(question: str, choice: str) -> None:
    judge = FakeJudge(choice=cast(GateClass, choice), confidence=0.95)

    decision = decide_gate(question, judge=judge, settings=_settings())

    assert decision.scope == "all"
    assert decision.cautious is True
    assert decision.reason == "desacuerdo_reglas"


@pytest.mark.parametrize("choice", list(GATE_CLASSES))
def test_commercial_mixed_de_las_reglas_usa_todas(choice: str) -> None:
    judge = FakeJudge(choice=cast(GateClass, choice), confidence=0.95)

    decision = decide_gate(
        "Necesito vasos para café de 12 oz. ¿Quién fundó Carvajal?",
        judge=judge,
        settings=_settings(),
    )

    assert (decision.scope, decision.cautious, decision.reason) == ("all", False, "mixta_reglas")


def _v3_question(item_id: str) -> str:
    data = json.loads(V3.read_text(encoding="utf-8"))
    items = data["items"] if isinstance(data, dict) else data
    return next(item["turns"][-1] for item in items if item["id"] == item_id)


def test_rag_con_un_producto_del_catalogo_conserva_las_consultas_estructuradas() -> None:
    # Arrange: v3-003 (R1 de la evidencia). Las reglas ven ``rag/documental`` y Jev elige
    # ``rag`` con confianza alta; el alcance ``rag`` quitaba ``buscar_producto``.
    question = _v3_question("v3-003")
    judge = FakeJudge(choice="rag", confidence=0.95)

    # Act
    decision = decide_gate(question, judge=judge, settings=_settings())

    # Assert: se trata como mixta (todas las consultas), sin cautela.
    assert decision.rules_route == "rag/documental"
    assert (decision.scope, decision.cautious, decision.reason) == ("all", False, "producto_reglas")
    assert decision.gate_class == "rag" and decision.source == "jev"
    assert "buscar_producto" in TOOLS_BY_SCOPE[decision.scope]
    assert "buscar_corpus" in TOOLS_BY_SCOPE[decision.scope]


@pytest.mark.parametrize(
    "question",
    [
        "¿De qué material son los portacomidas Bioform y quién los fabrica?",
        "¿Cuánto cuestan los productos de Carvajal Empaques?",
        "¿Carvajal vende 5000 unidades al por mayor?",
        "¿Dónde se consiguen los productos de Carvajal Empaques?",
    ],
)
def test_rag_con_senal_comercial_de_las_reglas_usa_todas(question: str) -> None:
    # Las reglas ven ``rag/documental``, pero la pregunta nombra un producto, un precio, una
    # cantidad o dónde comprar.
    judge = FakeJudge(choice="rag", confidence=0.9)

    decision = decide_gate(question, judge=judge, settings=_settings())

    assert decision.rules_route == "rag/documental"
    assert (decision.scope, decision.cautious, decision.reason) == ("all", False, "producto_reglas")


@pytest.mark.parametrize(
    "question",
    [
        "¿Quién fundó Carvajal?",
        "¿Qué empresas conforman el sector de Papel y Empaques?",
        "¿Cuál es la política de sostenibilidad del grupo?",
        QUESTION,
    ],
)
def test_rag_sin_senal_comercial_se_aplica_con_su_alcance(question: str) -> None:
    judge = FakeJudge(choice="rag", confidence=0.9)

    decision = decide_gate(question, judge=judge, settings=_settings())

    assert (decision.scope, decision.reason) == ("rag", "aplicada")


def test_rag_con_producto_y_confianza_baja_sigue_en_cautela() -> None:
    judge = FakeJudge(choice="rag", confidence=0.5)

    decision = decide_gate(_v3_question("v3-003"), judge=judge, settings=_settings())

    assert (decision.scope, decision.cautious, decision.reason) == ("all", True, "confianza_baja")


def test_sin_confidence_usa_la_mayor_probabilidad() -> None:
    # El adaptador ya completa ``confidence`` con la mayor probabilidad (contrato §2).
    judge = FakeJudge(choice="rag", confidence=None)

    decision = decide_gate(QUESTION, judge=judge, settings=_settings())

    assert decision.confidence == 0.9


def test_los_umbrales_se_leen_de_los_ajustes() -> None:
    judge = FakeJudge(choice="respuesta_directa", confidence=0.75)
    lenient = _settings(jev_router_min_confidence=0.5, jev_router_direct_min_confidence=0.7)

    decision = decide_gate("¿eres una persona o un bot?", judge=judge, settings=lenient)

    assert decision.scope == "direct"
    assert decision.reason == "aplicada"
