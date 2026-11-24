"""El gatekeeper en el grafo ``tools`` (spec 046, T021, T028 y T039).

Con ``ScriptedToolChat`` y ``FakeJudge`` (sin red ni LLM): cada clase restringe las
consultas que ve el modelo; la respuesta directa de Jev va al nodo ``direct``; una consulta
fuera del alcance no se ejecuta; los bloques ``<alcance_del_turno>`` y ``<cautela>`` van al
final del sistema; los saludos no llaman a Jev; y sin Jev todo queda como en la 045.
"""

from collections.abc import Sequence
from typing import Any, cast

import pytest
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from tests.fakes.judge import FakeJudge
from tests.fakes.tool_chat import ScriptedToolChat, ScriptStep
from tests.test_tool_agent_stream import FUNDACION, FakeEmbedder, QueueStore

from apps.api.chat.agent import ToolAgentFinal, ToolAgentRunner, ToolAgentToken
from carvajal.agent.gatekeeper.model import GatekeeperDecision
from carvajal.agent.toolagent.evidence import NO_DATA_ANSWER
from carvajal.agent.toolagent.graph import (
    CAUTION_OPEN,
    SCOPE_OPEN,
    build_tool_agent,
    recursion_limit,
)
from carvajal.agent.toolagent.tools import TOOL_NAMES, TOOLS_BY_SCOPE, ToolDeps
from carvajal.application.ports import GateClass, GateScope, JevStatus
from carvajal.infrastructure.settings import Settings
from carvajal.prompts import AGENT_SYSTEM, DIRECT_INSTRUCTIONS, GATEKEEPER_CAUTION

QUESTION = "¿Qué empaques recomiendan para un evento grande?"


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args}


def _graph(
    steps: Sequence[ScriptStep],
    judge: Any = None,
    *,
    store: QueueStore | None = None,
    memory: Any = None,
) -> tuple[Any, ScriptedToolChat]:
    chat = ScriptedToolChat(steps=list(steps))
    deps = ToolDeps(
        vector_store=store or QueueStore(), embedder=FakeEmbedder(), settings=Settings()
    )
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver(), store=memory, judge=judge)
    return graph, chat


def _invoke(graph: Any, question: str = QUESTION) -> dict[str, Any]:
    return graph.invoke(
        {"question": question, "owner_key": "dueno-1", "thread_id": "hilo-1"},
        {"configurable": {"thread_id": "hilo-1"}, "recursion_limit": recursion_limit(Settings())},
    )


def _bound(chat: ScriptedToolChat) -> list[list[str]]:
    """Nombres de las herramientas atadas en cada invocación del modelo con herramientas."""

    return [
        [tool["function"]["name"] for tool in kwargs["tools"]]
        for kwargs in chat.call_kwargs
        if "tools" in kwargs
    ]


def _system(chat: ScriptedToolChat, index: int = 0) -> str:
    message = chat.calls[index][0]
    assert isinstance(message, SystemMessage)
    return str(message.content)


# --- Parte 1 (US1) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("choice", "scope"),
    [("rag", "rag"), ("herramienta_estructurada", "structured"), ("mixta", "all")],
)
def test_cada_clase_ata_solo_las_consultas_de_su_alcance(choice: str, scope: str) -> None:
    # Arrange
    judge = FakeJudge(choice=cast(GateClass, choice), confidence=0.9)
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="Te ayudo con gusto."),
    ]
    graph, chat = _graph(steps, judge)

    # Act
    state = _invoke(graph)

    # Assert
    assert _bound(chat) == [list(TOOLS_BY_SCOPE[cast(GateScope, scope)])] * 2
    assert chat.call_kwargs[0]["tool_choice"] == "required"
    decision = GatekeeperDecision.from_value(state["gatekeeper"])
    assert decision is not None and decision.scope == scope
    assert len(judge.routes) == 1


def test_la_respuesta_directa_de_jev_va_al_nodo_directo_sin_herramientas() -> None:
    # Arrange
    judge = FakeJudge(choice="respuesta_directa", confidence=0.95)
    graph, chat = _graph([ScriptStep(text="Soy un asistente virtual de Carvajal.")], judge)

    # Act
    state = _invoke(graph, "¿eres una persona o un bot?")

    # Assert
    assert _bound(chat) == []
    assert state["mode_path"] == "shortcut"
    assert state["shortcut_intent"] == "gatekeeper_direct"
    assert DIRECT_INSTRUCTIONS["gatekeeper_direct"] in str(chat.calls[0][1].content)
    assert state["gatekeeper"]["source"] == "jev"


def test_una_consulta_fuera_del_alcance_no_se_ejecuta_y_gasta_cupo() -> None:
    # Arrange
    judge = FakeJudge(choice="rag", confidence=0.9)
    steps = [
        ScriptStep(tool_calls=[_call("buscar_producto", familia="vaso")]),
        ScriptStep(text="No pude consultar eso en este turno."),
    ]
    graph, _ = _graph(steps, judge)

    # Act
    state = _invoke(graph)

    # Assert
    tool_message = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert tool_message.status == "error"
    assert "fuera_de_alcance" in str(tool_message.content)
    assert "Esa consulta no está disponible en este turno." in str(tool_message.content)
    assert state["tool_calls_used"] == 1
    assert state["results"] == []


def test_el_bloque_de_alcance_va_al_final_del_sistema() -> None:
    # Arrange
    judge = FakeJudge(choice="rag", confidence=0.9)
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="Listo."),
    ]
    graph, chat = _graph(steps, judge)

    # Act
    state = _invoke(graph)

    # Assert
    system = _system(chat)
    assert system.startswith(AGENT_SYSTEM)
    assert system.rstrip().endswith("</alcance_del_turno>")
    assert ", ".join(TOOLS_BY_SCOPE["rag"]) in system
    assert CAUTION_OPEN not in system
    # El bloque no se guarda en el estado: solo viaja al modelo.
    stored = next(m for m in state["messages"] if isinstance(m, SystemMessage))
    assert SCOPE_OPEN not in str(stored.content)


def test_un_saludo_no_llama_a_jev_ni_consulta() -> None:
    judge = FakeJudge()
    graph, _ = _graph([ScriptStep(text="¡Hola! Soy Carva.")], judge)

    state = _invoke(graph, "Hola")

    assert judge.routes == []
    assert [m for m in state["messages"] if isinstance(m, ToolMessage)] == []
    assert state["gatekeeper"]["source"] == "reglas"
    assert state["gatekeeper"]["rule"] == "greeting"


def test_olvida_todo_sin_store_reescribe_la_regla_a_memoria_no_disponible() -> None:
    # Arrange: sin Store el olvido falla (``store_no_disponible``).
    judge = FakeJudge()
    graph, _ = _graph([ScriptStep(text="La memoria no está disponible.")], judge)

    # Act
    state = _invoke(graph, "Olvida todo lo que sabes de mí")

    # Assert
    assert state["shortcut_intent"] == "memory_unavailable"
    assert state["gatekeeper"]["rule"] == "memory_unavailable"
    assert judge.routes == []


# --- Parte 2 (US2): cautela -------------------------------------------------------------------


def test_con_cautela_el_modelo_ve_las_20_y_el_bloque_de_cautela() -> None:
    # Arrange
    judge = FakeJudge(choice="rag", confidence=0.4)
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="pedir_dato", dato="ciudad")]),
        ScriptStep(text="¿En qué ciudad lo necesitas?"),
    ]
    graph, chat = _graph(steps, judge)

    # Act
    state = _invoke(graph)

    # Assert
    assert _bound(chat)[0] == list(TOOL_NAMES)
    system = _system(chat)
    assert system.index(SCOPE_OPEN) < system.index(CAUTION_OPEN)
    assert GATEKEEPER_CAUTION in system
    assert state["gatekeeper"]["cautious"] is True


def _stream(judge: Any, steps: Sequence[ScriptStep], question: str) -> list[Any]:
    chat = ScriptedToolChat(steps=list(steps))
    settings = Settings()
    deps = ToolDeps(vector_store=QueueStore(), embedder=FakeEmbedder(), settings=settings)
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver(), judge=judge)
    return list(ToolAgentRunner(graph, settings).stream("dueno-1", "hilo-1", question, []))


def test_con_cautela_pedir_dato_termina_en_la_peticion_neutra() -> None:
    # Arrange
    judge = FakeJudge(choice="rag", confidence=0.4)
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="pedir_dato", dato="ciudad")]),
        ScriptStep(text="Te cuento que en Barranquilla hay 14 sedes. ¿Dónde estás?"),
    ]

    # Act
    events = _stream(judge, steps, QUESTION)

    # Assert
    final = events[-1]
    assert isinstance(final, ToolAgentFinal)
    assert final.abstained is False
    assert "ciudad" in final.answer.lower()
    assert "14 sedes" not in final.answer
    assert final.gatekeeper["cautious"] is True


def test_saludo_coloquial_con_cautela_no_consulta_datos() -> None:
    # Arrange: las reglas no lo detectan y Jev duda (respuesta directa con 0,7).
    judge = FakeJudge(choice="respuesta_directa", confidence=0.7)
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="¡Todo bien! ¿En qué te ayudo hoy?"),
    ]

    # Act
    chat_events = _stream(judge, steps, "quiubo pues, todo bien?")

    # Assert
    final = chat_events[-1]
    assert isinstance(final, ToolAgentFinal)
    assert final.gatekeeper["reason"] == "confianza_baja_directa"
    assert [r["tool"] for r in final.results] == ["sin_consulta"]
    assert "".join(e.text for e in chat_events if isinstance(e, ToolAgentToken))


def test_la_cautela_lleva_la_regla_de_conversacion() -> None:
    judge = FakeJudge(choice="respuesta_directa", confidence=0.7)
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="¡Todo bien!"),
    ]
    graph, chat = _graph(steps, judge)

    _invoke(graph, "quiubo pues, todo bien?")

    assert "sin_consulta (motivo conversacion)" in _system(chat)


# --- Sin Jev (US4, T039): como en la 045 ------------------------------------------------------


def _without_judge_answer(judge: Any) -> tuple[list[list[str]], str, str]:
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación")]),
        ScriptStep(text="Carvajal fue fundada en Cali en 1904 [1]. Hoy tiene varias empresas."),
    ]
    chat = ScriptedToolChat(steps=steps)
    settings = Settings()
    deps = ToolDeps(
        vector_store=QueueStore([FUNDACION]), embedder=FakeEmbedder(), settings=settings
    )
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver(), judge=judge)
    events = list(ToolAgentRunner(graph, settings).stream("d", "h", "¿Cuándo se fundó?", []))
    answer = "".join(e.text for e in events if isinstance(e, ToolAgentToken))
    return _bound(chat), _system(chat), answer


@pytest.mark.parametrize(
    "judge",
    [
        pytest.param(None, id="sin-juez"),
        pytest.param(FakeJudge(is_available=False), id="no-disponible"),
        *[
            pytest.param(FakeJudge(fail=cast(JevStatus, status)), id=status)
            for status in ("off", "cap", "circuit_open", "timeout", "error", "auth", "invalid")
        ],
    ],
)
def test_sin_jev_el_turno_es_el_de_la_045(judge: Any) -> None:
    # Arrange: la referencia es el mismo guion sin juez.
    reference_bound, reference_system, reference_answer = _without_judge_answer(None)

    # Act
    bound, system, answer = _without_judge_answer(judge)

    # Assert
    assert bound == reference_bound == [list(TOOL_NAMES)] * 2
    assert system == reference_system
    assert SCOPE_OPEN not in system and CAUTION_OPEN not in system
    assert answer == reference_answer
    assert answer.startswith("Carvajal fue fundada")


def test_la_respuesta_directa_de_jev_no_es_un_hecho_sin_fuente() -> None:
    # El filtro determinista de ``no_query`` sigue aplicando a la respuesta directa.
    judge = FakeJudge(choice="respuesta_directa", confidence=0.95)

    events = _stream(
        judge,
        [ScriptStep(text="Carvajal factura 3 billones al año.")],
        "¿eres una persona o un bot?",
    )

    final = events[-1]
    assert isinstance(final, ToolAgentFinal)
    assert final.mode_path == "shortcut"
    assert final.answer == NO_DATA_ANSWER
