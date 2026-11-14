"""Pruebas del grafo: rutas, citas, abstención y memoria entre hilos."""

from collections.abc import Sequence
from unittest.mock import Mock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.memory import InMemoryStore

from apps.api.agent_runtime import _plain_postgres_url
from apps.api.chat.agent import GraphAgentRunner
from carvajal.adapters.vector.qdrant_store import point_id
from carvajal.agent.graph import AgentDeps, build_agent, memory_namespace
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

    # Assert
    assert primero.route == "direct"
    assert "respuestas breves" in (segundo.prompt or "")
    assert segundo.preferences == ("Recuerda que prefiero respuestas breves",)
    item = store.get(memory_namespace("dueno-1"), "profile")
    assert item is not None
    assert "respuestas breves" in item.value["items"][0]


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
    assert "respuestas breves" not in (otro.prompt or "")


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
