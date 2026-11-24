"""Estado del agente con herramientas (spec 044, T011): forma y serialización."""

import json
from typing import Any, get_args, get_type_hints

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from carvajal.agent.state import AgentState
from carvajal.agent.toolagent.state import (
    TOOL_NAMES,
    TOOL_STATUSES,
    ToolAgentState,
    ToolOutcome,
    make_tool_outcome,
    validate_question,
)
from carvajal.agent.toolagent.tools import TOOL_NAMES as REGISTRY_TOOL_NAMES


def _full_state() -> ToolAgentState:
    """Estado completo de un turno con una llamada a herramienta."""
    outcome = make_tool_outcome(
        tool="cotizar",
        args={"family": "vasos", "quantity": 1000, "city": "Cali"},
        status="ok",
        source_ids=[1, 2],
        elapsed_ms=42,
    )
    return {
        "question": "¿Cuánto cuestan 1000 vasos en Cali?",
        "owner_key": "owner-1",
        "thread_id": "thread-1",
        "seed_turns": [{"question": "hola", "answer": "¡Hola!", "incomplete": False}],
        "messages": [
            SystemMessage("Eres el asistente de Carvajal."),
            HumanMessage("¿Cuánto cuestan 1000 vasos en Cali?"),
            AIMessage(
                "",
                tool_calls=[{"name": "cotizar", "args": {"quantity": 1000}, "id": "call-1"}],
            ),
            ToolMessage('{"status": "ok"}', tool_call_id="call-1", name="cotizar"),
            AIMessage("Cuestan 120.000 COP [1]."),
        ],
        "preferences": ["respuestas cortas"],
        "commercial_context": {"product": "vasos", "quantity": 1000, "city": "Cali"},
        "tool_calls_used": 1,
        "seen_calls": ['cotizar{"quantity":1000}'],
        "sources": [{"n": 1, "chunk_id": "c-1", "slug": "vasos", "score": 0.9}],
        "results": [outcome],
        "quotes": [{"quote": {"id": 1, "folio": "abc123"}, "total": 120000}],
        "cards": {"options": [], "quote": None},
        "mode_path": "agent",
        "missing": [],
    }


def test_full_state_round_trips_through_checkpointer_serializer():
    # Arrange
    serializer = JsonPlusSerializer()
    state = _full_state()

    # Act
    restored = serializer.loads_typed(serializer.dumps_typed(state))

    # Assert
    assert restored == state
    assert [type(m) for m in restored["messages"]] == [
        SystemMessage,
        HumanMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
    ]
    assert restored["messages"][2].tool_calls[0]["name"] == "cotizar"
    assert restored["messages"][3].tool_call_id == "call-1"


def test_canales_de_la_045_hacen_ida_y_vuelta_por_el_serializador() -> None:
    """Spec 045 (T012): resumen, memoria y ``turn_count`` son JSON y sobreviven al checkpoint."""

    # Arrange
    serializer = JsonPlusSerializer()
    summary = {
        "v": 1,
        "text": "La persona pidió vasos para tinto en Pereira.",
        "covered_turns": 7,
        "forgotten_through": 0,
        "last_message_id": None,
        "method": "deterministic",
        "user_quotes": ["necesito vasos para tinto en Pereira"],
        "updated_at": "2026-11-24T15:00:00+00:00",
    }
    tools_state: ToolAgentState = {
        **_full_state(),
        "conversation_summary": summary,
        "memory": {"nombre": {"v": 2, "value": "Ana", "origin": "tool"}, "notas": {"items": []}},
        "turn_count": 15,
    }
    routes_state: AgentState = {
        "question": "hola",
        "conversation_summary": summary,
        "turn_count": 15,
    }

    # Act
    restored_tools = serializer.loads_typed(serializer.dumps_typed(tools_state))
    restored_routes = serializer.loads_typed(serializer.dumps_typed(routes_state))

    # Assert
    assert restored_tools == tools_state
    assert restored_routes == routes_state
    assert json.loads(json.dumps(summary)) == summary
    assert {"conversation_summary", "memory", "turn_count"} <= set(get_type_hints(ToolAgentState))
    assert {"conversation_summary", "turn_count"} <= set(get_type_hints(AgentState))


def test_state_without_messages_is_plain_json():
    # Arrange
    state = {k: v for k, v in _full_state().items() if k != "messages"}

    # Act
    restored = json.loads(json.dumps(state))

    # Assert
    assert restored == state


def test_tool_outcome_is_a_plain_dict():
    # Arrange / Act
    outcome = make_tool_outcome(
        tool="buscar_corpus", args={"query": "sedes"}, status="empty", elapsed_ms=5
    )

    # Assert
    assert type(outcome) is dict
    assert outcome == {
        "tool": "buscar_corpus",
        "args": {"query": "sedes"},
        "status": "empty",
        "source_ids": [],
        "elapsed_ms": 5,
    }


def test_tool_names_match_contract():
    # Arrange: desde la spec 045 los nombres salen del registro de herramientas.
    expected = set(REGISTRY_TOOL_NAMES)

    # Act / Assert
    assert set(TOOL_NAMES) == expected
    assert set(TOOL_STATUSES) == {"ok", "empty", "needs_input", "error"}
    assert set(get_args(get_type_hints(ToolOutcome)["tool"])) == expected


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"tool": "borrar_todo"}, "herramienta"),
        ({"status": "pending"}, "estado"),
        ({"source_ids": ["1"]}, "source_ids"),
        ({"source_ids": [True]}, "source_ids"),
        ({"elapsed_ms": -1}, "elapsed_ms"),
        ({"elapsed_ms": 1.5}, "elapsed_ms"),
        ({"args": ["no", "dict"]}, "args"),
    ],
)
def test_invalid_tool_outcome_is_rejected(kwargs, match):
    # Arrange
    valid: dict[str, Any] = {
        "tool": "cotizar",
        "args": {},
        "status": "ok",
        "source_ids": [1],
        "elapsed_ms": 1,
    }

    # Act / Assert
    with pytest.raises(ValueError, match=match):
        make_tool_outcome(**{**valid, **kwargs})


def test_tool_outcome_copies_mutable_inputs():
    # Arrange
    args = {"quantity": 10}
    source_ids = [3]

    # Act
    outcome = make_tool_outcome(tool="cotizar", args=args, status="ok", source_ids=source_ids)
    args["quantity"] = 99
    source_ids.append(4)

    # Assert
    assert outcome["args"] == {"quantity": 10}
    assert outcome["source_ids"] == [3]


def test_validate_question_strips_and_rejects_blank():
    # Arrange / Act / Assert
    assert validate_question("  ¿Qué sedes hay?  ") == "¿Qué sedes hay?"
    with pytest.raises(ValueError, match="vacía"):
        validate_question("   ")


def test_messages_channel_uses_add_messages_reducer():
    # Arrange
    from langgraph.graph.message import add_messages

    # Act
    hint = get_type_hints(ToolAgentState, include_extras=True)["messages"]

    # Assert
    assert add_messages in hint.__metadata__


def test_tool_outcome_copia_en_profundidad_y_exige_json() -> None:
    """``args`` anidados no comparten referencias y lo no serializable se rechaza."""

    anidado = {"filtro": {"ciudad": "Cali"}}
    outcome = make_tool_outcome(tool="cotizar", args=anidado, status="ok", source_ids=[1])
    anidado["filtro"]["ciudad"] = "Bogotá"

    assert outcome["args"]["filtro"]["ciudad"] == "Cali"
    with pytest.raises(ValueError):
        make_tool_outcome(tool="cotizar", args={"x": {1, 2}}, status="ok")
    with pytest.raises(ValueError):
        make_tool_outcome(tool="cotizar", args={}, status="ok", source_ids=[0])
    with pytest.raises(ValueError):
        # Tipo inválido a propósito: se prueba la validación en tiempo de ejecución.
        make_tool_outcome(tool="cotizar", args={}, status="ok", source_ids=5)  # ty: ignore[invalid-argument-type]


def test_el_canal_gatekeeper_hace_ida_y_vuelta_por_el_serializador() -> None:
    """Spec 046 (T016): la decisión del gatekeeper es JSON y sobrevive al checkpointer."""

    from carvajal.agent.gatekeeper.model import GatekeeperDecision

    # Arrange
    serializer = JsonPlusSerializer()
    decision = GatekeeperDecision(
        scope="structured",
        source="jev",
        gate_class="herramienta_estructurada",
        reason="aplicada",
        confidence=0.91,
        probabilities={"herramienta_estructurada": 0.91, "rag": 0.09},
        jev={"kind": "router", "status": "ok", "latency_ms": 250, "model": "jev-1.14.0"},
    )
    state = {"question": "hola", "gatekeeper": decision.to_value()}

    # Act
    restored = serializer.loads_typed(serializer.dumps_typed(state))

    # Assert
    assert restored == state
    assert GatekeeperDecision.from_value(restored["gatekeeper"]) == decision
    assert "gatekeeper" in get_type_hints(ToolAgentState)
