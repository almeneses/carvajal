"""Enrutadores intercambiables y su evaluación (spec 036, T036-T040)."""

import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from structlog.testing import capture_logs

from carvajal.agent.graph import AgentDeps, build_agent
from carvajal.agent.router import decide_route
from carvajal.agent.routers import build_router, router_name
from carvajal.agent.routers._payload import FALLBACK_PREFIX
from carvajal.agent.routers.decision import (
    DecisionModelUnavailable,
    DecisionRouter,
    load_decision_model,
)
from carvajal.agent.routers.evaluation import evaluate_router, load_items, render_report
from carvajal.agent.routers.llm import LLMRouter
from carvajal.agent.state import RouteDecision, Turn
from carvajal.cli import main
from carvajal.infrastructure.settings import Settings

EVAL_SET = Path(__file__).resolve().parents[1] / "data" / "eval" / "routing_questions.json"

COMERCIAL = """```json
{"route": "commercial", "intent": "commercial",
 "request": {"use": "bebida_caliente", "temperature": "caliente", "capacity_oz": "12",
             "family": "vaso", "quantity": 5000.0, "country": "MX", "city": null,
             "microwave": "sí", "prefers_renewable": "true", "color": "rojo"},
 "reason": "compra de vasos"}
```"""


class FakeLLM:
    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, *, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        yield self.complete(system=system, prompt=prompt)


def _turno(pregunta: str) -> Turn:
    return {"question": pregunta, "answer": "…", "incomplete": False}


def test_el_llm_cumple_el_contrato_y_normaliza_la_solicitud() -> None:
    router = LLMRouter(FakeLLM(COMERCIAL))

    decision = router("Necesito 5.000 vasos de 12 oz para café en México")

    assert (decision.route, decision.intent) == ("commercial", "commercial")
    assert decision.extra["capacity_oz"] == 12.0
    assert decision.extra["quantity"] == 5000
    assert decision.extra["microwave"] is None  # «sí» no es un booleano del contrato
    assert decision.extra["prefers_renewable"] is True  # el texto «true» sí se acepta
    assert "color" not in decision.extra
    assert decision.reason == "llm: compra de vasos"
    assert (router.calls, router.invalid) == (1, 0)


def test_el_llm_resuelve_el_indicador_con_el_registro_real() -> None:
    salida = (
        '{"route": "tool", "intent": "indicator", "tool_name": "ingresos", "tool_period": "2024"}'
    )

    decision = LLMRouter(FakeLLM(salida))("¿Cuáles fueron los ingresos en 2024?")

    assert decision.tool_name == "ingresos_consolidados"
    assert decision.tool_period == "2024"


def test_el_seguimiento_del_llm_recupera_con_el_turno_anterior() -> None:
    salida = '{"route": "rag", "intent": "follow_up"}'
    llm = FakeLLM(salida)

    decision = LLMRouter(llm)("¿y en qué año fue eso?", [_turno("¿Dónde nació Carvajal?")])

    assert decision.query == "¿Dónde nació Carvajal? ¿y en qué año fue eso?"
    assert "¿Dónde nació Carvajal?" in llm.prompts[0]


@pytest.mark.parametrize(
    "salida",
    [
        "no sé",
        '{"route": "tool", "intent": "greeting"}',
        '{"route": "tool", "intent": "indicator", "tool_name": "ingresos", '
        '"tool_period": "2024-13"}',
        ConnectionError("sin red"),
    ],
)
def test_una_salida_invalida_cae_a_las_reglas_y_queda_visible(salida: str | Exception) -> None:
    router = LLMRouter(FakeLLM(salida))

    decision = router("hola")

    assert decision.route == "direct"
    assert decision.intent == "greeting"
    assert decision.reason.startswith(FALLBACK_PREFIX)
    assert router.invalid == 1


def test_sin_modelo_de_decision_el_error_es_controlado() -> None:
    with pytest.raises(DecisionModelUnavailable, match="DECISION_MODEL"):
        load_decision_model("")
    with pytest.raises(DecisionModelUnavailable, match=r"paquete\.modulo:fabrica"):
        load_decision_model("sin_fabrica")
    with pytest.raises(DecisionModelUnavailable, match="No se pudo cargar"):
        load_decision_model("modulo_que_no_existe_036:crear")
    with pytest.raises(DecisionModelUnavailable):
        build_router("decision", Settings(decision_model=""))


class FakeDecisionModel:
    def decide(self, question: str, *, turns: Any, schema: str) -> dict[str, Any]:
        assert '"route"' in schema
        return {"route": "rag", "intent": "documental", "reason": "institucional"}


def test_el_modelo_de_decision_se_carga_desde_una_fabrica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modulo = types.ModuleType("decision_falso_036")
    setattr(modulo, "crear", FakeDecisionModel)  # noqa: B010
    monkeypatch.setitem(sys.modules, "decision_falso_036", modulo)

    router = build_router("decision", Settings(decision_model="decision_falso_036:crear"))

    assert isinstance(router, DecisionRouter)
    assert router("¿Qué hace Carvajal?").reason == "decision: institucional"
    assert router_name(router) == "decision"


def test_build_router_por_defecto_son_las_reglas() -> None:
    assert build_router("rules") is decide_route
    assert router_name(decide_route) == "rules"
    with pytest.raises(ValueError, match="desconocido"):
        build_router("otro")


class _SinRecuperacion:
    def embed(self, text: str) -> list[float]:
        raise AssertionError("No se consulta Qdrant.")

    def upsert(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("No se indexa.")

    def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("No se consulta Qdrant.")

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("No se borra.")


def test_el_grafo_usa_el_enrutador_inyectado_y_registra_la_decision() -> None:
    def siempre_saludo(question: str, turns: list[Turn] | None = None) -> RouteDecision:
        return RouteDecision(route="direct", intent="greeting", query=question, reason="fijo")

    falso = _SinRecuperacion()
    grafo = build_agent(
        AgentDeps(vector_store=falso, embedder=falso, settings=Settings()),
        checkpointer=InMemorySaver(),
        router=siempre_saludo,
    )

    with capture_logs() as logs:
        estado = grafo.invoke(
            {"question": "¿Cuál fue el EBITDA en 2024?", "owner_key": "d", "thread_id": "h"},
            config={"configurable": {"thread_id": "h"}},
        )

    assert estado["route"] == "direct"
    [evento] = [log for log in logs if log["event"] == "agent.route_decided"]
    assert evento["router"] == "siempre_saludo"
    assert evento["route"] == "direct"


def test_el_log_incluye_los_datos_extraidos() -> None:
    falso = _SinRecuperacion()
    grafo = build_agent(
        AgentDeps(vector_store=falso, embedder=falso, settings=Settings()),
        checkpointer=InMemorySaver(),
    )

    with capture_logs() as logs:
        grafo.invoke(
            {"question": "busco vasos de 16 oz", "owner_key": "d", "thread_id": "h2"},
            config={"configurable": {"thread_id": "h2"}},
        )

    [evento] = [log for log in logs if log["event"] == "agent.route_decided"]
    assert evento["router"] == "rules"
    assert evento["extracted"] == {"capacity_oz": 16.0, "family": "vaso"}


def test_la_evaluacion_cuenta_aciertos_fallos_y_salidas_invalidas() -> None:
    items = load_items(EVAL_SET)
    malo = LLMRouter(FakeLLM(*["no es JSON"] * 3), fallback=decide_route)

    reglas = evaluate_router(decide_route, items)
    fallido = evaluate_router(malo, items[:3])

    assert reglas.total == len(items)
    assert reglas.route_accuracy == 1.0
    assert reglas.material_violations == 0
    assert (fallido.invalid_outputs, fallido.model_calls) == (3, 3)
    assert all(failure["problems"][0].startswith(FALLBACK_PREFIX) for failure in fallido.failures)
    tabla = render_report([reglas, fallido])
    assert "| rules | 110 | 100.0% |" in tabla
    assert "| llm | 3 |" in tabla


def test_la_orden_eval_router_escribe_la_tabla(tmp_path: Path) -> None:
    salida = tmp_path / "tabla.md"

    assert main(["eval-router", "--output", str(salida)]) == 0
    assert salida.read_text(encoding="utf-8").startswith("| Enrutador |")
    assert main(["eval-router", "--router", "decision"]) == 1
