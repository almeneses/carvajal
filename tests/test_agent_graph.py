"""Pruebas del grafo: rutas, citas, abstención y memoria entre hilos."""

import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import Mock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.memory import InMemoryStore
from structlog.testing import capture_logs
from tests.fakes.tool_chat import ScriptedToolChat

from apps.api import agent_runtime as agent_runtime_module
from apps.api.agent_runtime import AgentRuntime, _graphs, _plain_postgres_url, _router
from apps.api.chat.agent import GraphAgentRunner
from carvajal.adapters.vector.qdrant_store import point_id
from carvajal.agent.graph import AgentDeps, build_agent, memory_namespace
from carvajal.agent.router import decide_route
from carvajal.agent.state import Turn
from carvajal.agent.tools import IndicatorResult, indicator_lookup
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings

CHUNK = Chunk(
    id="historia:3",
    document_id="historia",
    text="Carvajal fue fundada en Cali en 1904.",
    position=3,
    section="historia",
    layer="core",
)
HIT = ScoredChunk(
    chunk=CHUNK,
    score=0.91,
    metadata={
        "url": "https://www.carvajal.com/historia/",
        "title": "Historia",
        "source_type": "oficial",
        "content_hash": "abc",
    },
)


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 768


class FakeVectorStore:
    def __init__(self, hits: Sequence[ScoredChunk]) -> None:
        self.hits = list(hits)
        self.calls = 0

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise AssertionError("El grafo no indexa.")

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        self.calls += 1
        return self.hits[:limit]

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("El grafo no borra.")


def runner(store: FakeVectorStore | None = None) -> GraphAgentRunner:
    settings = Settings()
    graph = build_agent(
        AgentDeps(store or FakeVectorStore([]), FakeEmbedder(), settings),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )
    return GraphAgentRunner(graph, settings)


def test_directo_no_consulta_qdrant() -> None:
    # Arrange
    store = FakeVectorStore([HIT])
    agent = runner(store)

    # Act
    turn = agent.prepare("dueno-1", "hilo-1", "Hola, ¿qué puedes hacer?", [])

    # Assert
    assert turn.route == "direct"
    assert turn.prompt is not None
    assert store.calls == 0
    assert turn.sources == ()
    assert "no consulta el corpus" in turn.system


def test_herramienta_ejecuta_el_registro_real_y_cita_la_fuente() -> None:
    # Arrange
    agent = runner()

    # Act
    with patch("carvajal.agent.graph.indicator_lookup", wraps=indicator_lookup) as tool:
        turn = agent.prepare(
            "dueno-1",
            "hilo-1",
            "¿Cuáles fueron los ingresos consolidados de Carvajal en 2024?",
            [],
        )

    # Assert
    assert tool.invoke.call_count == 1
    assert tool.invoke.call_args.args == ({"name": "ingresos_consolidados", "period": "2024"},)
    assert turn.route == "tool"
    assert turn.tool == "ingresos_consolidados"
    assert "$3,96 billones" in (turn.prompt or "")
    assert len(turn.sources) == 1
    assert turn.sources[0].url.startswith("https://caliescribe.com/")


def test_herramienta_acepta_una_salida_pydantic() -> None:
    # Arrange
    agent = runner()
    output = IndicatorResult(
        found=True,
        name="ebitda",
        period="2025-09",
        value=1.0,
        unit="COP",
        display="$1,0",
        source_document="informe",
        source_url="https://example.com/informe",
        quote="EBITDA de $1,0",
    )
    tool = Mock()
    tool.invoke.return_value = output

    # Act
    with patch("carvajal.agent.graph.indicator_lookup", tool):
        turn = agent.prepare(
            "dueno-1",
            "hilo-1",
            "¿Cuál fue el EBITDA de Carvajal en septiembre de 2025?",
            [],
        )

    # Assert
    assert turn.route == "tool"
    assert "$1,0" in (turn.prompt or "")
    assert turn.sources[0].url == "https://example.com/informe"


def test_herramienta_con_salida_no_estructurada_declara_ausencia() -> None:
    # Arrange
    agent = runner()
    tool = Mock()
    tool.invoke.return_value = "respuesta sin estructura"

    # Act
    with patch("carvajal.agent.graph.indicator_lookup", tool):
        turn = agent.prepare(
            "dueno-1",
            "hilo-1",
            "¿Cuál fue el EBITDA de Carvajal en septiembre de 2025?",
            [],
        )

    # Assert
    assert turn.route == "tool"
    assert turn.sources == ()
    assert "respuesta sin estructura" in (turn.prompt or "")


def test_rag_usa_qdrant_y_expone_la_url_del_fragmento() -> None:
    # Arrange
    store = FakeVectorStore([HIT])
    agent = runner(store)

    # Act
    turn = agent.prepare("dueno-1", "hilo-1", "¿Dónde fue fundada Carvajal?", [])

    # Assert
    assert turn.route == "rag"
    assert store.calls == 1
    assert "Cali en 1904" in (turn.prompt or "")
    assert turn.sources[0].url == "https://www.carvajal.com/historia/"
    assert turn.sources[0].chunk_id == "historia:3"


def test_sin_evidencia_el_prompt_es_none_y_no_se_genera() -> None:
    # Arrange
    agent = runner(FakeVectorStore([]))

    # Act
    turn = agent.prepare("dueno-1", "hilo-1", "¿Cuál es el teléfono de Tokio?", [])

    # Assert
    assert turn.route == "rag"
    assert turn.prompt is None
    assert turn.sources == ()


def test_un_nombre_propio_ausente_fuerza_abstencion_aunque_el_score_sea_alto() -> None:
    # Arrange: el score es alto, pero «Tokio» no aparece en el contexto.
    agent = runner(FakeVectorStore([HIT]))

    # Act
    turn = agent.prepare("dueno-1", "hilo-1", "¿Cuál es el teléfono de Tokio?", [])

    # Assert
    assert turn.prompt is None
    assert turn.sources == ()


def test_evidencia_bajo_el_umbral_se_descarta() -> None:
    # Arrange: el score 0.1 queda por debajo de RAG_MIN_SCORE.
    bajo = ScoredChunk(chunk=CHUNK, score=0.1, metadata=HIT.metadata)
    agent = runner(FakeVectorStore([bajo]))

    # Act
    turn = agent.prepare("dueno-1", "hilo-1", "¿Dónde fue fundada Carvajal?", [])

    # Assert
    assert turn.prompt is None
    assert turn.sources == ()


def test_la_preferencia_se_recupera_en_otro_hilo_del_mismo_dueno() -> None:
    # Arrange
    settings = Settings()
    store = InMemoryStore()
    graph = build_agent(
        AgentDeps(FakeVectorStore([]), FakeEmbedder(), settings),
        checkpointer=InMemorySaver(),
        store=store,
    )
    agent = GraphAgentRunner(graph, settings)

    # Act: el dueño declara una preferencia en el hilo A.
    primero = agent.prepare("dueno-1", "hilo-a", "Recuerda que prefiero respuestas breves", [])
    # Y abre el hilo B del mismo dueño.
    segundo = agent.prepare("dueno-1", "hilo-b", "Hola", [])

    # Assert: el atajo del respaldo la guarda por categoría (spec 045, FR-015).
    assert primero.route == "direct"
    assert primero.intent == "memory_write"
    assert "formato preferido: corto" in (segundo.prompt or "")
    assert segundo.preferences == ("formato preferido: corto",)
    item = store.get(memory_namespace("dueno-1"), "formato_preferido")
    assert item is not None
    assert item.value["value"] == "corto"
    assert item.value["origin"] == "shortcut"


def test_otro_dueno_no_hereda_la_preferencia() -> None:
    # Arrange
    settings = Settings()
    agent = GraphAgentRunner(
        build_agent(
            AgentDeps(FakeVectorStore([]), FakeEmbedder(), settings),
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
        ),
        settings,
    )

    # Act
    agent.prepare("dueno-1", "hilo-a", "Recuerda que prefiero respuestas breves", [])
    otro = agent.prepare("dueno-2", "hilo-b", "Hola", [])

    # Assert
    assert otro.preferences == ()
    assert "formato preferido" not in (otro.prompt or "")


def test_el_checkpointer_recupera_el_turno_para_un_seguimiento() -> None:
    # Arrange
    settings = Settings()
    agent = GraphAgentRunner(
        build_agent(
            AgentDeps(FakeVectorStore([HIT]), FakeEmbedder(), settings),
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
        ),
        settings,
    )

    # Act: primer turno y registro manual (el servicio lo hace al cerrar).
    primero = agent.prepare("dueno-1", "hilo-a", "¿En qué ciudad fue fundada Carvajal?", [])
    agent.record(
        "hilo-a",
        question="¿En qué ciudad fue fundada Carvajal?",
        answer="Fue fundada en Cali [1].",
        incomplete=False,
        turns=primero.turns,
    )
    segundo = agent.prepare("dueno-1", "hilo-a", "¿Y en qué año ocurrió eso?", [])

    # Assert
    assert len(segundo.turns) == 1
    assert segundo.intent == "follow_up"
    assert "ciudad fue fundada" in segundo.query


@pytest.mark.integration
def test_el_checkpointer_postgres_recupera_el_hilo_tras_reconectar(
    migration_database_url: str,
) -> None:
    settings = Settings()
    deps = AgentDeps(FakeVectorStore([HIT]), FakeEmbedder(), settings)
    url = _plain_postgres_url(migration_database_url)
    question = "¿En qué ciudad fue fundada Carvajal?"

    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        agent = GraphAgentRunner(build_agent(deps, checkpointer=saver), settings)
        first = agent.prepare("dueno-1", "hilo-a", question, [])
        agent.record(
            "hilo-a",
            question=question,
            answer="Fue fundada en Cali [1].",
            incomplete=False,
            turns=first.turns,
        )

    with PostgresSaver.from_conn_string(url) as saver:
        restarted = GraphAgentRunner(build_agent(deps, checkpointer=saver), settings)
        follow_up = restarted.prepare("dueno-1", "hilo-a", "¿Y en qué año ocurrió eso?", [])
        other_thread = restarted.prepare("dueno-1", "hilo-b", "¿Y en qué año ocurrió eso?", [])

    assert follow_up.intent == "follow_up"
    assert question in follow_up.query
    assert follow_up.turns[0]["answer"] == "Fue fundada en Cali [1]."
    assert other_thread.turns == ()


def test_point_id_es_estable_para_el_mismo_chunk() -> None:
    # Act / Assert
    assert point_id(CHUNK) == point_id(CHUNK)


def test_el_runtime_cae_a_las_reglas_si_el_enrutador_no_arranca() -> None:
    # Arrange: nombre inexistente y «decision» sin DECISION_MODEL.
    settings = Settings(agent_router="otro")
    sin_modelo = Settings(agent_router="decision", decision_model="")

    # Act
    router = _router(settings)
    router_sin_modelo = _router(sin_modelo)

    # Assert
    assert router is decide_route
    assert router_sin_modelo is decide_route


def test_agent_runtime_se_inicializa_una_sola_vez_bajo_concurrencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dos primeras preguntas simultáneas no construyen dos runtimes."""

    # Arrange
    llamadas: list[int] = []

    class DummyRuntime:
        def close(self) -> None:
            pass

    def fake_build(settings: Settings) -> DummyRuntime:
        llamadas.append(1)
        time.sleep(0.02)
        return DummyRuntime()

    monkeypatch.setattr(agent_runtime_module, "_build", fake_build)
    monkeypatch.setattr(agent_runtime_module, "_runtime", None)

    # Act
    with ThreadPoolExecutor(max_workers=10) as pool:
        carreras = list(pool.map(lambda _: agent_runtime_module.agent_runtime(), range(10)))

    # Assert
    assert len(llamadas) == 1
    assert all(item is carreras[0] for item in carreras)
    agent_runtime_module.close_agent_runtime()


def _runtime_graphs(settings: Settings) -> tuple[Any, Any]:
    return _graphs(
        settings,
        vector_store=FakeVectorStore([]),
        embedder=FakeEmbedder(),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        router=decide_route,
    )


def test_el_runtime_en_modo_routes_no_construye_el_grafo_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    settings = Settings(agent_mode="routes")
    chats: list[Settings] = []
    monkeypatch.setattr(agent_runtime_module, "build_deepseek_chat", lambda s, **_: chats.append(s))

    # Act
    graph, tool_graph = _runtime_graphs(settings)

    # Assert
    assert graph is not None
    assert tool_graph is None
    assert chats == []


def test_el_runtime_en_modo_tools_construye_ambos_grafos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: el modo ``tools`` exige ``LLM_PROVIDER=deepseek`` y DeepSeek completo.
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        agent_mode="tools",
        llm_provider="deepseek",
        deepseek_api_key="sk-prueba",
    )
    opciones: list[dict[str, object]] = []

    def fake_chat(received: Settings, **kwargs: object) -> ScriptedToolChat:
        opciones.append(kwargs)
        return ScriptedToolChat(steps=[])

    monkeypatch.setattr(agent_runtime_module, "build_deepseek_chat", fake_chat)

    # Act
    graph, tool_graph = _runtime_graphs(settings)

    # Assert: el grafo routes sigue para el atajo y el respaldo.
    assert graph is not None
    assert tool_graph is not None
    assert set(graph.nodes) >= {"route", "direct", "rag"}
    assert set(tool_graph.nodes) >= {"recall", "agent", "tools"}
    assert opciones == [
        {"stream_usage": True, "for_tools": True, "timeout_s": 45.0, "max_retries": 0}
    ]


def test_el_runtime_en_modo_tools_sin_deepseek_sigue_con_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        agent_mode="tools",
        llm_provider="deepseek",
        deepseek_api_key="",
    )

    # Act
    with capture_logs() as logs:
        graph, tool_graph = _runtime_graphs(settings)

    # Assert
    assert graph is not None
    assert tool_graph is None
    evento = next(item for item in logs if item["event"] == "agent.tools_mode_unavailable")
    assert evento["reason"] == "missing_deepseek_api_key"
    assert evento["log_level"] == "warning"


@pytest.mark.parametrize(("tool_graph", "mode"), [(None, "routes"), (object(), "tools")])
def test_agent_runtime_informa_el_modo_efectivo(tool_graph: object, mode: str) -> None:
    # Arrange
    runtime = AgentRuntime(
        graph=object(),
        qdrant=Mock(),
        checkpointer=None,
        store=None,
        _stack=Mock(),
        tool_graph=tool_graph,
    )

    # Act / Assert
    assert runtime.mode == mode


def test_sin_evidencia_registra_el_tema_en_el_log() -> None:
    # Arrange
    agent = runner(FakeVectorStore([HIT]))

    # Act
    with capture_logs() as logs:
        turn = agent.prepare("dueno-1", "hilo-1", "¿Dónde queda Tokio Factoring?", [])

    # Assert
    assert turn.prompt is None
    evento = next(item for item in logs if item["event"] == "rag.no_context")
    assert evento["reason"] == "entidad_sin_respaldo"
    assert "tokio" in evento["topic"]
    assert "factoring" in evento["topic"]


def test_sin_resultados_registra_el_hueco_con_motivo() -> None:
    # Arrange
    agent = runner(FakeVectorStore([]))

    # Act
    with capture_logs() as logs:
        turn = agent.prepare("dueno-1", "hilo-1", "¿Cuál es la misión de Carvajal?", [])

    # Assert
    assert turn.prompt is None
    evento = next(item for item in logs if item["event"] == "rag.no_context")
    assert evento["reason"] == "sin_resultados"
    assert evento["topic"] == "mision carvajal"


# --- Resumen y memoria en el respaldo ``routes`` (spec 045, T028 y T043) ------------------------


def _seed(count: int, start: int = 0) -> list[Turn]:
    return [
        {"question": f"pregunta número {i}", "answer": f"respuesta {i}", "incomplete": False}
        for i in range(start, start + count)
    ]


def test_el_atajo_de_routes_recibe_el_resumen_y_la_ventana_configurada() -> None:
    # Arrange: 10 turnos persistidos; la semilla los trae (ventana 8 + holgura 2).
    from carvajal.agent.memory.summary import SeedTurns

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    agent = GraphAgentRunner(
        build_agent(
            AgentDeps(FakeVectorStore([]), FakeEmbedder(), settings),
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
        ),
        settings,
    )
    seed = SeedTurns(_seed(10), turn_count=10)

    # Act
    turn = agent.prepare("dueno-1", "hilo-r", "Hola", seed)

    # Assert: ocho turnos completos (no cuatro) y el resumen de los que salieron.
    prompt = turn.prompt or ""
    assert turn.route == "direct"
    assert len(turn.turns) == 8
    assert "Usuario: pregunta número 2" in prompt
    assert "Usuario: pregunta número 9" in prompt
    assert "<resumen_conversacion>" in prompt
    assert "Turno 1 — la persona: «pregunta número 0»" in prompt
    assert "Turno 2 — la persona: «pregunta número 1»" in prompt
    assert "Usuario: pregunta número 1\n" not in prompt


def test_un_turno_de_respaldo_conserva_el_canal_del_resumen() -> None:
    # Arrange
    from carvajal.agent.memory.summary import ConversationSummary, SeedTurns

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    graph = build_agent(
        AgentDeps(FakeVectorStore([]), FakeEmbedder(), settings),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )
    config = {"configurable": {"thread_id": "hilo-r"}}
    previous = ConversationSummary(
        text="La persona tiene una cafetería.", covered_turns=4, method="llm"
    ).to_value()
    graph.update_state(config, {"conversation_summary": {**previous, "updated_at": "2026-11-24"}})

    # Act
    GraphAgentRunner(graph, settings).prepare(
        "dueno-1", "hilo-r", "Hola", SeedTurns(_seed(8, start=4), turn_count=12)
    )

    # Assert
    channel = graph.get_state(config).values["conversation_summary"]
    assert channel["text"] == "La persona tiene una cafetería."
    assert channel["covered_turns"] == 4


def test_el_nodo_rag_no_recibe_el_bloque_del_resumen() -> None:
    # Arrange: excepción declarada en FR-002.
    from carvajal.agent.memory.summary import SeedTurns

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    agent = GraphAgentRunner(
        build_agent(
            AgentDeps(FakeVectorStore([HIT]), FakeEmbedder(), settings),
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
        ),
        settings,
    )

    # Act
    turn = agent.prepare(
        "dueno-1",
        "hilo-r",
        "¿En qué ciudad fue fundada Carvajal?",
        SeedTurns(_seed(10, start=2), turn_count=12),
    )

    # Assert
    assert turn.route == "rag"
    assert "<resumen_conversacion>" not in (turn.prompt or "")


def test_routes_clasifica_la_preferencia_por_categoria() -> None:
    # Arrange
    from carvajal.agent.memory.store import load

    store = InMemoryStore()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    agent = GraphAgentRunner(
        build_agent(
            AgentDeps(FakeVectorStore([]), FakeEmbedder(), settings),
            checkpointer=InMemorySaver(),
            store=store,
        ),
        settings,
    )

    # Act
    turn = agent.prepare(
        "dueno-1",
        "hilo-a",
        "Llámame Ana, trabajo en Panadería La Espiga y suelo comprar en Cali",
        [],
    )

    # Assert
    assert turn.intent == "memory_write"
    assert load(store, "dueno-1").values == {
        "nombre": "Ana",
        "empresa": "Panadería La Espiga",
        "ciudad_habitual": "Cali",
    }


def test_routes_no_guarda_un_dato_sensible_y_lo_explica() -> None:
    # Arrange
    from carvajal.prompts import DIRECT_INSTRUCTIONS

    store = InMemoryStore()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    agent = GraphAgentRunner(
        build_agent(
            AgentDeps(FakeVectorStore([]), FakeEmbedder(), settings),
            checkpointer=InMemorySaver(),
            store=store,
        ),
        settings,
    )

    # Act
    turn = agent.prepare("dueno-1", "hilo-a", "Recuerda que mi cédula es 1.234.567.890", [])

    # Assert
    assert turn.intent == "memory_rejected"
    assert DIRECT_INSTRUCTIONS["memory_rejected"] in (turn.prompt or "")
    assert store.search(memory_namespace("dueno-1")) == []
    assert turn.preferences == ()


def test_routes_atiende_olvidar_todo_sin_enrutador() -> None:
    # Arrange: un enrutador que falla si se consulta.
    from carvajal.agent.memory.store import load, remember
    from carvajal.prompts import DIRECT_INSTRUCTIONS

    def router(question: str, turns: Any = None) -> Any:
        raise AssertionError("«olvidar todo» no pasa por el enrutador")

    store = InMemoryStore()
    remember(store, "dueno-1", "nombre", "Ana")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    graph = build_agent(
        AgentDeps(FakeVectorStore([]), FakeEmbedder(), settings),
        checkpointer=InMemorySaver(),
        store=store,
        router=router,
    )

    # Act
    turn = GraphAgentRunner(graph, settings).prepare(
        "dueno-1", "hilo-a", "Olvida todo lo que sabes de mí", _seed(2)
    )

    # Assert
    assert turn.intent == "memory_forget"
    assert DIRECT_INSTRUCTIONS["memory_forget"] in (turn.prompt or "")
    assert "pregunta número" not in (turn.prompt or "")
    assert turn.preferences == ()
    assert load(store, "dueno-1").values == {}
    channel = graph.get_state({"configurable": {"thread_id": "hilo-a"}}).values
    assert channel["conversation_summary"]["method"] == "forgotten"
    assert channel["conversation_summary"]["forgotten_through"] == 3
