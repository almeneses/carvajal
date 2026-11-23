"""Pruebas del doble ``ScriptedToolChat`` (research R10): tool calling sin red."""

import asyncio
import operator
from typing import Annotated, Any, TypedDict, cast

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from tests.fakes.tool_chat import ScriptedToolChat, ScriptedToolChatError, ScriptStep


@tool
def search_catalog(query: str) -> str:
    """Busca productos en el catálogo."""

    return query


@tool
def search_corpus(query: str) -> str:
    """Busca pasajes en el corpus."""

    return query


TWO_CALLS = ScriptStep(
    tool_calls=[
        {"name": "search_catalog", "args": {"query": "vasos"}, "id": "call_1"},
        {"name": "search_corpus", "args": {"query": "compostable"}, "id": "call_2"},
    ],
    usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
)


def _text_contents(chunks: list[AIMessageChunk]) -> list[str]:
    """Contenido textual no vacío de cada chunk; el doble solo emite texto plano."""

    texts: list[str] = []
    for chunk in chunks:
        if chunk.content:
            assert isinstance(chunk.content, str)
            texts.append(chunk.content)
    return texts


def _aggregate(chunks: list[AIMessageChunk]) -> AIMessageChunk:
    total = chunks[0]
    for chunk in chunks[1:]:
        total = total + chunk
    return total


def test_invoke_returns_two_tool_calls_in_one_step() -> None:
    # Arrange
    model = ScriptedToolChat(steps=[TWO_CALLS])

    # Act
    message = model.invoke([HumanMessage("vasos compostables")])

    # Assert
    assert isinstance(message, AIMessage)
    assert [(c["name"], c["args"], c["id"]) for c in message.tool_calls] == [
        ("search_catalog", {"query": "vasos"}, "call_1"),
        ("search_corpus", {"query": "compostable"}, "call_2"),
    ]
    assert message.usage_metadata == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_stream_emits_tool_call_chunks_that_aggregate_into_tool_calls() -> None:
    # Arrange
    model = ScriptedToolChat(steps=[TWO_CALLS])

    # Act
    chunks = list(model.stream("hola"))

    # Assert
    assert all(isinstance(c, AIMessageChunk) for c in chunks)
    assert any(c.tool_call_chunks for c in chunks)
    total = _aggregate(chunks)
    assert [(c["name"], c["args"], c["id"]) for c in total.tool_calls] == [
        ("search_catalog", {"query": "vasos"}, "call_1"),
        ("search_corpus", {"query": "compostable"}, "call_2"),
    ]
    assert total.invalid_tool_calls == []
    assert total.usage_metadata == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_final_text_step_streams_in_several_chunks() -> None:
    # Arrange
    text = "Tenemos vasos compostables de 12 onzas [1]."
    model = ScriptedToolChat(steps=[ScriptStep(text=text)], chunk_size=5)

    # Act
    chunks = list(model.stream("hola"))

    # Assert
    text_chunks = _text_contents(chunks)
    assert len(text_chunks) > 1
    assert "".join(text_chunks) == text
    total = _aggregate(chunks)
    assert total.content == text
    assert total.tool_calls == []


def test_text_before_tool_call_is_streamed_before_the_call() -> None:
    # Arrange
    step = ScriptStep(
        text="Voy a buscar en el catálogo.",
        tool_calls=[{"name": "search_catalog", "args": {"query": "platos"}, "id": "c1"}],
    )
    model = ScriptedToolChat(steps=[step])

    # Act
    chunks = list(model.stream("platos"))

    # Assert
    first_call = next(i for i, c in enumerate(chunks) if c.tool_call_chunks)
    last_text = max(i for i, c in enumerate(chunks) if c.content)
    assert last_text < first_call
    total = _aggregate(chunks)
    assert total.content == "Voy a buscar en el catálogo."
    assert [c["name"] for c in total.tool_calls] == ["search_catalog"]


def test_bind_tools_records_tools_and_tool_choice() -> None:
    # Arrange
    model = ScriptedToolChat(steps=[TWO_CALLS])

    # Act
    bound = model.bind_tools([search_catalog, search_corpus], tool_choice="auto")
    bound.invoke("hola")

    # Assert
    assert model.bindings == [
        {"tools": ["search_catalog", "search_corpus"], "kwargs": {"tool_choice": "auto"}}
    ]
    assert model.call_kwargs[0]["tool_choice"] == "auto"
    assert [t["function"]["name"] for t in model.call_kwargs[0]["tools"]] == [
        "search_catalog",
        "search_corpus",
    ]


def test_records_received_messages_per_call() -> None:
    # Arrange
    model = ScriptedToolChat(steps=[ScriptStep(text="uno"), ScriptStep(text="dos")])

    # Act
    model.invoke([HumanMessage("primera")])
    list(model.stream([HumanMessage("segunda"), AIMessage("previa")]))

    # Assert
    assert [[m.content for m in call] for call in model.calls] == [
        ["primera"],
        ["segunda", "previa"],
    ]
    assert model.remaining_steps == 0


def test_step_with_error_raises_it_in_invoke_and_stream() -> None:
    # Arrange
    model = ScriptedToolChat(
        steps=[
            ScriptStep(error=TimeoutError("sin respuesta")),
            ScriptStep(error=TimeoutError("sin respuesta")),
        ]
    )

    # Act / Assert
    with pytest.raises(TimeoutError, match="sin respuesta"):
        model.invoke("hola")
    with pytest.raises(TimeoutError, match="sin respuesta"):
        list(model.stream("hola"))
    assert len(model.calls) == 2


def test_exhausted_script_raises_clear_error() -> None:
    # Arrange
    model = ScriptedToolChat(steps=[ScriptStep(text="única")])
    model.invoke("hola")

    # Act / Assert
    with pytest.raises(ScriptedToolChatError, match="guion agotado"):
        model.invoke("otra")


def test_astream_aggregates_like_stream() -> None:
    # Arrange
    model = ScriptedToolChat(steps=[TWO_CALLS])

    # Act
    async def collect() -> list[AIMessageChunk]:
        return [chunk async for chunk in model.astream("hola")]

    chunks = asyncio.run(collect())

    # Assert
    assert [c["id"] for c in _aggregate(chunks).tool_calls] == ["call_1", "call_2"]


class _State(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]


def test_chunks_reach_graph_stream_in_messages_mode() -> None:
    # Arrange
    model = ScriptedToolChat(
        steps=[
            ScriptStep(
                text="pienso",
                tool_calls=[{"name": "search_catalog", "args": {"query": "x"}, "id": "c1"}],
            ),
            ScriptStep(text="Respuesta final con varias palabras."),
        ],
        chunk_size=4,
    )
    bound = model.bind_tools([search_catalog], tool_choice="auto")

    def decide(state: _State) -> dict:
        return {"messages": [bound.invoke(state["messages"])]}

    def answer(state: _State) -> dict:
        return {"messages": [bound.invoke(state["messages"])]}

    # ty no reconoce un TypedDict como ``TypedDictLikeV1/V2`` (igual que en agent/graph.py).
    builder = StateGraph(cast(Any, _State))
    builder.add_node("decide", decide)
    builder.add_node("answer", answer)
    builder.add_edge(START, "decide")
    builder.add_edge("decide", "answer")
    builder.add_edge("answer", END)
    graph = builder.compile()

    # Act
    # Con ``stream_mode`` en lista cada evento es ``(modo, (chunk, metadata))``; el tipo
    # declarado de ``stream`` no lo refleja.
    events = cast(
        list[tuple[str, tuple[AIMessageChunk, dict[str, Any]]]],
        list(graph.stream({"messages": [HumanMessage("hola")]}, stream_mode=["messages"])),
    )

    # Assert
    assert all(mode == "messages" for mode, _ in events)
    by_node: dict[str, list[AIMessageChunk]] = {}
    for _, (chunk, metadata) in events:
        by_node.setdefault(metadata["langgraph_node"], []).append(chunk)
    assert set(by_node) == {"decide", "answer"}
    assert [c["name"] for c in _aggregate(by_node["decide"]).tool_calls] == ["search_catalog"]
    answer_chunks = _text_contents(by_node["answer"])
    assert len(answer_chunks) > 1
    assert "".join(answer_chunks) == "Respuesta final con varias palabras."
    assert model.calls[1][0].content == "hola"
    previa = model.calls[1][1]
    assert isinstance(previa, AIMessage)
    assert previa.tool_calls[0]["id"] == "c1"
