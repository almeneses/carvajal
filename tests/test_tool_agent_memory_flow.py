"""Flujos de memoria del modo ``tools`` y del respaldo ``routes`` (spec 045, T040).

Con ``ScriptedToolChat``, ``InMemoryStore`` y sin red: «llámame Ana» por la herramienta y
por el atajo del respaldo sin doble escritura, recordado en otra conversación, aislado por
dueño (web frente a WhatsApp), olvidar una categoría y todo en el mismo turno, el resumen
sin lo olvidado dos turnos después y la ciudad habitual solo como sugerencia. SC-003, SC-005.
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from tests.fakes.tool_chat import ScriptedToolChat, ScriptStep

from apps.api.chat.agent import GraphAgentRunner, ToolAgentFinal, ToolAgentRunner, ToolAgentToken
from carvajal.agent.graph import AgentDeps, build_agent
from carvajal.agent.memory.store import load, memory_namespace, remember
from carvajal.agent.memory.summary import SeedTurns
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.graph import build_tool_agent, recursion_limit
from carvajal.agent.toolagent.tools import ToolDeps, content_data
from carvajal.application.ports import ScoredChunk
from carvajal.infrastructure.settings import Settings

WEB = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
WHATSAPP = "wa_0123456789abcdef0123456789abcdef"
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 8


class EmptyIndex:
    def upsert(self, chunks: Any, embeddings: Any) -> None:
        raise AssertionError("no indexa")

    def search(self, embedding: Any, *, limit: int = 8, **kwargs: Any) -> Sequence[ScoredChunk]:
        return []

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("no borra")


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args}


def _graph(
    steps: Sequence[ScriptStep], memory: Any, settings: Settings = SETTINGS
) -> tuple[Any, ScriptedToolChat]:
    chat = ScriptedToolChat(steps=list(steps))
    deps = ToolDeps(vector_store=EmptyIndex(), embedder=FakeEmbedder(), settings=settings)
    return build_tool_agent(deps, chat, checkpointer=InMemorySaver(), store=memory), chat


def _invoke(
    graph: Any,
    question: str,
    *,
    owner: str = WEB,
    thread: str = "hilo-1",
    seed: Sequence[Turn] = (),
    turn_count: int | None = None,
    settings: Settings = SETTINGS,
) -> dict[str, Any]:
    payload = {
        "question": question,
        "owner_key": owner,
        "thread_id": thread,
        "seed_turns": list(seed),
        "turn_count": len(seed) if turn_count is None else turn_count,
    }
    config = {"configurable": {"thread_id": thread}, "recursion_limit": recursion_limit(settings)}
    return graph.invoke(payload, config)


def _system(messages: Sequence[Any]) -> str:
    first = messages[0]
    assert isinstance(first, SystemMessage)
    return str(first.content)


def _payloads(state: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [
        content_data(str(m.content)) or {}
        for m in state["messages"]
        if isinstance(m, ToolMessage) and m.name == name
    ]


def _remember_ana() -> list[ScriptStep]:
    return [
        ScriptStep(tool_calls=[_call("recordar_preferencia", categoria="nombre", valor="Ana")]),
        ScriptStep(text="Listo, Ana."),
    ]


def _no_query(text: str = "Hola.") -> list[ScriptStep]:
    return [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text=text),
    ]


# --- «Llámame Ana» ---------------------------------------------------------------------------


def test_llamame_ana_con_proveedor_lo_guarda_la_herramienta_y_se_recuerda_en_otro_hilo() -> None:
    # Arrange
    memory = InMemoryStore()
    graph, chat = _graph([*_remember_ana(), *_no_query("¡Hola, Ana!")], memory)

    # Act
    first = _invoke(graph, "Llámame Ana", thread="hilo-1")
    second = _invoke(graph, "Hola de nuevo", thread="hilo-2")

    # Assert: sin atajo; el nombre viaja en el sistema de la otra conversación.
    assert first["mode_path"] == "agent"
    assert [r["tool"] for r in first["results"]] == ["recordar_preferencia"]
    assert second["preferences"] == ["nombre: Ana"]
    assert "- nombre: Ana" in _system(chat.calls[2])


def test_con_el_respaldo_el_atajo_lo_guarda_una_sola_vez() -> None:
    # Arrange: el agente ya guardó el nombre y el turno pasa al respaldo ``routes``.
    memory = InMemoryStore()
    graph, _ = _graph(_remember_ana(), memory)
    _invoke(graph, "Llámame Ana")
    routes = GraphAgentRunner(
        build_agent(
            AgentDeps(EmptyIndex(), FakeEmbedder(), SETTINGS),  # type: ignore[arg-type]
            checkpointer=InMemorySaver(),
            store=memory,
        ),
        SETTINGS,
    )

    # Act
    turn = routes.prepare(WEB, "hilo-1", "Llámame Ana", [])
    again = routes.prepare(WEB, "hilo-1", "Recuerda que prefiero opciones compostables", [])
    routes.prepare(WEB, "hilo-1", "Recuerda que prefiero opciones compostables", [])

    # Assert
    assert turn.intent == "memory_write"
    assert again.intent == "memory_write"
    keys = sorted(item.key for item in memory.search(memory_namespace(WEB)))
    assert keys == ["nombre", "notas"]
    profile = load(memory, WEB)
    assert profile.values == {"nombre": "Ana"}
    assert profile.notes == ("prefiero opciones compostables",)


def test_la_memoria_no_cruza_de_dueno_ni_entre_web_y_whatsapp() -> None:
    # Arrange
    memory = InMemoryStore()
    graph, chat = _graph([*_remember_ana(), *_no_query(), *_no_query()], memory)
    _invoke(graph, "Llámame Ana", owner=WEB, thread="web-1")

    # Act
    whatsapp = _invoke(graph, "Cuéntame algo", owner=WHATSAPP, thread="wa-1")
    other = _invoke(graph, "Cuéntame algo", owner="f" * 32, thread="web-2")

    # Assert
    assert whatsapp["preferences"] == [] and other["preferences"] == []
    assert "nombre: Ana" not in _system(chat.calls[2])
    assert "nombre: Ana" not in _system(chat.calls[4])
    assert load(memory, WHATSAPP).empty


# --- Olvidar en el mismo turno ----------------------------------------------------------------


def test_olvidar_una_categoria_vale_desde_el_mismo_turno() -> None:
    # Arrange
    memory = InMemoryStore()
    remember(memory, WEB, "nombre", "Ana")
    remember(memory, WEB, "empresa", "Panadería La Espiga")
    steps = [
        ScriptStep(tool_calls=[_call("olvidar_preferencia", categoria="nombre")]),
        ScriptStep(tool_calls=[_call("listar_memoria")]),
        ScriptStep(text="Listo, Ana, ya lo olvidé."),
    ]
    graph, chat = _graph(steps, memory)

    # Act
    events = list(ToolAgentRunner(graph, SETTINGS).stream(WEB, "hilo-1", "Olvida mi nombre", []))

    # Assert: el sistema del resto del turno ya no trae el nombre y «Ana» no se respalda.
    assert "nombre: Ana" in _system(chat.calls[0])
    assert "nombre: Ana" not in _system(chat.calls[1])
    assert "empresa: Panadería La Espiga" in _system(chat.calls[1])
    text = "".join(e.text for e in events if isinstance(e, ToolAgentToken))
    assert "Ana" not in text
    assert text.startswith("Listo, lo olvidé.")
    final = events[-1]
    assert isinstance(final, ToolAgentFinal)
    assert final.abstained is False
    assert load(memory, WEB).values == {"empresa": "Panadería La Espiga"}


def test_listar_despues_de_recordar_no_sale_de_la_deduplicacion() -> None:
    # Arrange
    memory = InMemoryStore()
    steps = [
        ScriptStep(tool_calls=[_call("listar_memoria")]),
        ScriptStep(tool_calls=[_call("recordar_preferencia", categoria="nombre", valor="Ana")]),
        ScriptStep(tool_calls=[_call("listar_memoria")]),
        ScriptStep(text="Ahora sé que te llamas Ana."),
    ]
    graph, _ = _graph(steps, memory)

    # Act
    state = _invoke(graph, "Llámame Ana y dime qué sabes de mí")

    # Assert
    listed = _payloads(state, "listar_memoria")
    assert [payload["status"] for payload in listed] == ["empty", "ok"]
    assert listed[1]["memoria"]["nombre"] == "Ana"
    assert state["tool_calls_used"] == 3


def test_olvidar_todo_y_dos_turnos_despues_el_resumen_no_trae_lo_olvidado() -> None:
    # Arrange: ventana de 2 turnos para que el resumen aparezca pronto.
    settings = Settings(_env_file=None, agent_history_turns=2)  # type: ignore[call-arg]
    memory = InMemoryStore()
    remember(memory, WEB, "nombre", "Ana")
    turns: list[Turn] = [{"question": "Llámame Ana", "answer": "Listo.", "incomplete": False}]
    turns += [
        {"question": f"Tema {i}, cuéntame", "answer": f"Respuesta {i}.", "incomplete": False}
        for i in range(1, 6)
    ]
    # «Olvidar todo» no llama al modelo: su confirmación es fija (ensayo del 2026-11-26).
    steps = [*_no_query("Hola."), *_no_query("Hola."), *_no_query("Hola.")]
    graph, chat = _graph(steps, memory, settings)

    def seed(count: int) -> SeedTurns:
        return SeedTurns(turns[:count][-4:], turn_count=count)

    before = _invoke(graph, "Sigamos", seed=seed(4), turn_count=4, settings=settings)
    assert "Llámame Ana" in before["conversation_summary"]["text"]

    # Act: «olvidar todo» en el turno 7 (índice 6) y dos turnos más.
    turns.append({"question": "Tema 6, cuéntame", "answer": "Respuesta 6.", "incomplete": False})
    forget = _invoke(
        graph, "Olvida todo lo que sabes de mí", seed=seed(7), turn_count=7, settings=settings
    )
    turns.append(
        {"question": "Olvida todo lo que sabes de mí", "answer": "Listo.", "incomplete": False}
    )
    turns.append({"question": "Tema 8, cuéntame", "answer": "Respuesta 8.", "incomplete": False})
    after = _invoke(graph, "Sigamos", seed=seed(9), turn_count=9, settings=settings)

    # Assert
    assert forget["shortcut_intent"] == "memory_forget"
    assert after["conversation_summary"]["forgotten_through"] == 8
    assert "Ana" not in after["conversation_summary"]["text"]
    system = _system(chat.calls[-2])
    assert "Ana" not in system
    humans = [str(m.content) for m in chat.calls[-2] if m.type == "human"]
    assert humans == ["Tema 8, cuéntame", "Sigamos"]
    assert load(memory, WEB).empty


# --- Ciudad habitual solo como sugerencia ------------------------------------------------------

COFFEE = {"use": "bebida_caliente", "temperature": "caliente", "family": "vaso", "quantity": 500}


def test_la_ciudad_habitual_se_sugiere_y_solo_se_cotiza_tras_confirmarla() -> None:
    # Arrange
    memory = InMemoryStore()
    remember(memory, WEB, "ciudad_habitual", "Cali")
    steps = [
        ScriptStep(tool_calls=[_call("cotizar", **COFFEE, city="Cali")]),
        ScriptStep(text="¿Te cotizo en Cali?"),
        ScriptStep(tool_calls=[_call("cotizar", **COFFEE, city="Cali")]),
        ScriptStep(text="Listo."),
    ]
    graph, _ = _graph(steps, memory)
    question = "Cotiza 500 vasos para café"

    # Act
    first = _invoke(graph, question)
    second = _invoke(
        graph,
        "sí, en Cali",
        seed=[{"question": question, "answer": "¿Te cotizo en Cali?", "incomplete": False}],
    )

    # Assert
    [asked] = _payloads(first, "cotizar")
    assert asked["status"] == "needs_input"
    assert asked["sugerencia_ciudad"] == "Cali"
    assert first["quotes"] == []
    [quoted] = _payloads(second, "cotizar")
    assert quoted["status"] in ("ok", "empty")
    assert "sugerencia_ciudad" not in quoted


def test_buscar_sedes_con_la_ciudad_habitual_pide_confirmacion() -> None:
    # Arrange
    memory = InMemoryStore()
    remember(memory, WEB, "ciudad_habitual", "Cali")
    steps = [
        ScriptStep(tool_calls=[_call("buscar_sedes", ciudad="Cali")]),
        ScriptStep(text="¿Quieres sedes en Cali?"),
    ]
    graph, _ = _graph(steps, memory)

    # Act
    state = _invoke(graph, "¿Dónde puedo comprar vasos?")

    # Assert
    [payload] = _payloads(state, "buscar_sedes")
    assert payload["status"] == "needs_input"
    assert payload["missing"] == ["ciudad"]
    assert payload["sugerencia_ciudad"] == "Cali"
    assert "sedes" not in payload
    assert state["cards"].get("sites") is None
