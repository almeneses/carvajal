"""Caracterización de las tres capas del estado del agente (spec 046).

Pruebas de integración con PostgreSQL real (testcontainers): las conversaciones y
los mensajes viven en una base, y el checkpointer y el Store en otra, como en
producción. El agente es el grafo real (``GraphAgentRunner``) y solo el LLM, el
embedder y el índice vectorial son dobles deterministas.

Las tres capas que se fijan aquí:

- **Historial visible**: PostgreSQL (``conversations`` y ``messages``).
- **Preferencias del visitante**: Store del agente, aislado por dueño.
- **Estado técnico del grafo**: checkpointer; no es autoridad de nada visible.
"""

from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy import text as sql_text

from apps.api.agent_runtime import _plain_postgres_url
from apps.api.chat.agent import GraphAgentRunner
from apps.api.chat.service import ChatService, _seed_turns
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.chat import SqlChatRepository
from carvajal.adapters.storage.database import (
    build_session_factory,
    normalize_database_url,
)
from carvajal.agent.graph import AgentDeps, build_agent, memory_namespace
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings

pytestmark = pytest.mark.integration

CHUNK = Chunk(
    id="historia:1",
    document_id="historia",
    text="Carvajal fue fundada en Cali en 1904.",
    position=1,
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
HISTORIA = "¿En qué ciudad fue fundada Carvajal?"
SEGUIMIENTO = "¿Y en qué año ocurrió eso?"


class FakeEmbedder:
    """Embedder mínimo: el índice vectorial es un doble."""

    def embed(self, text: str) -> list[float]:
        return [0.1] * 768


class FakeVectorStore:
    """Índice que siempre devuelve el mismo fragmento."""

    def __init__(self, hits: Sequence[ScoredChunk]) -> None:
        self.hits = list(hits)

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise AssertionError("El chat no indexa.")

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        return self.hits[:limit]

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("El chat no borra documentos.")


class RecordingLLM:
    """LLM que registra cada prompt y siempre responde lo mismo."""

    def __init__(self, answer: str = "Carvajal fue fundada en Cali en 1904 [1].") -> None:
        self.answer = answer
        self.prompts: list[str] = []
        self.stream_calls = 0
        self.complete_calls = 0

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.stream_calls += 1
        self.prompts.append(prompt)
        yield self.answer

    def complete(self, *, system: str, prompt: str) -> str:
        self.complete_calls += 1
        self.prompts.append(prompt)
        return self.answer


class BrokenAfterTokenLLM(RecordingLLM):
    """Entrega un fragmento y corta, como un stream interrumpido."""

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.stream_calls += 1
        self.prompts.append(prompt)
        yield "Carvajal "
        raise RuntimeError("corte a mitad del stream")


class SwitchableLLM(RecordingLLM):
    """Corta el primer ``stream`` y responde completo en los siguientes."""

    def __init__(self) -> None:
        super().__init__()
        self.broken = True

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.stream_calls += 1
        self.prompts.append(prompt)
        if self.broken:
            yield "Carvajal "
            raise RuntimeError("corte a mitad del stream")
        yield self.answer


@dataclass
class ChatStack:
    """Servicio real con grafo, checkpointer y Store reales."""

    service: ChatService
    graph: Any
    store: Any
    engine: Engine


@pytest.fixture()
def crear_base(postgres_container: Any) -> Iterator[Callable[[], str]]:
    """Crea bases desechables y las borra al terminar la prueba."""

    admin_url = normalize_database_url(postgres_container.get_connection_url())
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    creadas: list[str] = []

    def crear() -> str:
        name = f"estado046_{uuid4().hex[:12]}"
        with admin_engine.connect() as connection:
            connection.execute(sql_text(f'CREATE DATABASE "{name}"'))
        creadas.append(name)
        return make_url(admin_url).set(database=name).render_as_string(hide_password=False)

    try:
        yield crear
    finally:
        for name in creadas:
            with admin_engine.connect() as connection:
                connection.execute(sql_text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin_engine.dispose()


@contextmanager
def chat_stack(chat_url: str, checkpoint_url: str, llm: Any) -> Generator[ChatStack]:
    """Abre una unidad completa (base de chat + checkpointer + Store + grafo)."""

    plain_url = _plain_postgres_url(checkpoint_url)
    with ExitStack() as stack:
        engine = create_engine(chat_url, pool_pre_ping=True)
        tables.metadata.create_all(engine)
        stack.callback(engine.dispose)
        saver = stack.enter_context(PostgresSaver.from_conn_string(plain_url))
        saver.setup()
        store = stack.enter_context(PostgresStore.from_conn_string(plain_url))
        store.setup()
        settings = Settings()
        graph = build_agent(
            AgentDeps(FakeVectorStore([HIT]), FakeEmbedder(), settings),
            checkpointer=saver,
            store=store,
        )
        service = ChatService(
            build_session_factory(engine),
            FakeEmbedder(),
            llm,
            agent=GraphAgentRunner(graph, settings),
            settings=settings,
        )
        yield ChatStack(service=service, graph=graph, store=store, engine=engine)


def _events(events: Sequence[Any], name: str) -> list[Any]:
    """Filtra los eventos SSE por nombre."""

    return [event for event in events if event.name == name]


def _trace(events: Sequence[Any]) -> dict[str, Any]:
    """Datos del evento ``trace`` (ruta e intención del turno)."""

    trazas = _events(events, "trace")
    assert trazas, "el turno no emitió trace"
    return dict(trazas[0].data)


def test_hilo_nuevo_empieza_sin_memoria(crear_base: Callable[[], str]) -> None:
    """C1: un visitante sin hilo crea uno y no hereda nada."""

    # Arrange
    chat_url, checkpoint_url = crear_base(), crear_base()
    llm = RecordingLLM()

    with chat_stack(chat_url, checkpoint_url, llm) as stack:
        # Act
        events = list(stack.service.stream("vis-nuevo", "Hola, ¿qué puedes hacer?"))
        historial = list(stack.service.history("vis-nuevo"))

    # Assert
    assert [event.name for event in events][:3] == ["open", "trace", "meta"]
    assert events[-1].name == "done"
    assert _trace(events)["route"] == "direct"
    assert [message.role for message in historial] == ["user", "assistant"]
    assert historial[1].status == "complete"
    assert "Historial reciente del hilo" not in llm.prompts[0]
    assert "Preferencias declaradas" not in llm.prompts[0]


def test_seguimiento_eliptico_usa_el_turno_anterior(crear_base: Callable[[], str]) -> None:
    """C2: la pregunta elíptica se reescribe con el turno anterior de PostgreSQL."""

    # Arrange
    chat_url, checkpoint_url = crear_base(), crear_base()
    llm = RecordingLLM()

    with chat_stack(chat_url, checkpoint_url, llm) as stack:
        # Act
        primero = list(stack.service.stream("vis-1", HISTORIA))
        segundo = list(stack.service.stream("vis-1", SEGUIMIENTO))

    # Assert
    assert _trace(primero)["intent"] == "documental"
    assert _trace(segundo)["route"] == "rag"
    assert _trace(segundo)["intent"] == "follow_up"
    assert "ciudad fue fundada" in llm.prompts[-1]
    assert "Pregunta anterior del hilo" in llm.prompts[-1]


def test_preferencia_sobrevive_al_hilo_nuevo(crear_base: Callable[[], str]) -> None:
    """C3: la preferencia se guarda en el Store y se aplica en otro hilo del dueño."""

    # Arrange
    chat_url, checkpoint_url = crear_base(), crear_base()
    llm = RecordingLLM()

    with chat_stack(chat_url, checkpoint_url, llm) as stack:
        # Act: el dueño declara una preferencia en el hilo activo.
        primero = list(stack.service.stream("vis-1", "Recuerda que prefiero respuestas breves"))
        item = stack.store.get(memory_namespace("vis-1"), "profile")
        hilo = stack.service.new_conversation("vis-1")
        segundo = list(stack.service.stream("vis-1", "Hola", thread_id=hilo.id))
        historial_nuevo = list(stack.service.history("vis-1", hilo.id))

    # Assert
    assert _trace(primero)["route"] == "direct"
    assert _trace(primero)["intent"] == "memory_write"
    assert item is not None
    assert "respuestas breves" in item.value["items"][0]
    assert _trace(segundo)["memory"] == 1
    assert "Preferencias declaradas" in llm.prompts[-1]
    assert "respuestas breves" in llm.prompts[-1]
    assert len(historial_nuevo) == 2


def test_visitantes_aislados_en_preferencias_e_hilos(crear_base: Callable[[], str]) -> None:
    """C4: ni las preferencias ni los hilos cruzan de un visitante a otro."""

    # Arrange
    chat_url, checkpoint_url = crear_base(), crear_base()
    llm = RecordingLLM()

    with chat_stack(chat_url, checkpoint_url, llm) as stack:
        list(stack.service.stream("vis-a", "Recuerda que prefiero respuestas breves"))
        hilo_a = stack.service.conversations("vis-a")[0].id
        # Act: el visitante B intenta escribir en el hilo de A.
        intrusion = list(stack.service.stream("vis-b", "¿Dato?", thread_id=hilo_a))
        historial_b = list(stack.service.history("vis-b"))
        saludo = list(stack.service.stream("vis-b", "Hola"))
        hilos_b = stack.service.conversations("vis-b")

    # Assert
    assert intrusion[-1].name == "error"
    assert intrusion[-1].data["incomplete"] is False
    assert historial_b == []
    assert _trace(saludo)["memory"] == 0
    assert "respuestas breves" not in llm.prompts[-1]
    assert [hilo.id for hilo in hilos_b] != [hilo_a]
    assert len(hilos_b) == 1


def test_reinicio_recupera_historial_y_seguimiento(crear_base: Callable[[], str]) -> None:
    """C5: un runtime nuevo (grafo y checkpointer nuevos) sigue leyendo PostgreSQL."""

    # Arrange: primer proceso, con su propio grafo y checkpointer.
    chat_url, checkpoint_url = crear_base(), crear_base()
    with chat_stack(chat_url, checkpoint_url, RecordingLLM()) as primero:
        list(primero.service.stream("vis-1", HISTORIA))
        hilo_id = primero.service.conversations("vis-1")[0].id

    # Act: el proceso se reinicia y vuelve a abrir el mismo almacén.
    llm = RecordingLLM()
    with chat_stack(chat_url, checkpoint_url, llm) as reiniciado:
        historial = list(reiniciado.service.history("vis-1", hilo_id))
        events = list(reiniciado.service.stream("vis-1", SEGUIMIENTO, thread_id=hilo_id))

    # Assert
    assert [message.content for message in historial] == [
        HISTORIA,
        "Carvajal fue fundada en Cali en 1904 [1].",
    ]
    assert _trace(events)["intent"] == "follow_up"
    assert "ciudad fue fundada" in llm.prompts[-1]


def test_reintento_idempotente_no_duplica_ni_llama_al_llm(
    crear_base: Callable[[], str],
) -> None:
    """C6: el mismo ``client_turn_id`` reemite el turno sin volver a generar."""

    # Arrange
    chat_url, checkpoint_url = crear_base(), crear_base()
    llm = RecordingLLM()

    with chat_stack(chat_url, checkpoint_url, llm) as stack:
        # Act
        primero = list(stack.service.stream("vis-1", HISTORIA, client_turn_id="turno-1"))
        segundo = list(stack.service.stream("vis-1", HISTORIA, client_turn_id="turno-1"))
        historial = list(stack.service.history("vis-1"))

    # Assert
    assert primero[-1].name == "done"
    assert _trace(segundo)["route"] == "replay"
    assert segundo[-1].name == "done"
    assert (
        _events(segundo, "done")[0].data["message_id"]
        == (_events(primero, "done")[0].data["message_id"])
    )
    assert len(historial) == 2
    assert llm.stream_calls == 1


def test_turno_incompleto_se_persiste_y_el_replay_lo_declara(
    crear_base: Callable[[], str],
) -> None:
    """C7: un corte tras el primer token queda ``incomplete`` y el reintento lo dice."""

    # Arrange
    chat_url, checkpoint_url = crear_base(), crear_base()
    llm = BrokenAfterTokenLLM()

    with chat_stack(chat_url, checkpoint_url, llm) as stack:
        # Act
        primero = list(stack.service.stream("vis-1", HISTORIA, client_turn_id="turno-x"))
        historial = list(stack.service.history("vis-1"))
        replay = list(stack.service.stream("vis-1", HISTORIA, client_turn_id="turno-x"))

    # Assert
    assert primero[-1].name == "error"
    assert primero[-1].data["incomplete"] is True
    assert [message.role for message in historial] == ["user", "assistant"]
    assert historial[1].status == "incomplete"
    assert historial[1].content == "Carvajal "
    assert _trace(replay)["route"] == "replay"
    assert replay[-1].name == "error"
    assert replay[-1].data["incomplete"] is True
    assert [event.data["text"] for event in _events(replay, "token")] == ["Carvajal "]


def test_el_turno_incompleto_no_se_confunde_con_respuesta(
    crear_base: Callable[[], str],
) -> None:
    """C8: el turno incompleto es contexto histórico marcado, no respuesta buena."""

    # Arrange
    chat_url, checkpoint_url = crear_base(), crear_base()
    llm = SwitchableLLM()

    with chat_stack(chat_url, checkpoint_url, llm) as stack:
        # Act: primer turno cortado y segundo turno normal en el mismo hilo.
        list(stack.service.stream("vis-1", HISTORIA))
        hilo_id = stack.service.conversations("vis-1")[0].id
        with build_session_factory(stack.engine)() as session:
            semilla = _seed_turns(SqlChatRepository(session), hilo_id)
        llm.broken = False
        segundo = list(stack.service.stream("vis-1", SEGUIMIENTO, thread_id=hilo_id))

    # Assert
    assert len(semilla) == 1
    assert semilla[0]["question"] == HISTORIA
    assert semilla[0]["answer"] == "Carvajal "
    assert semilla[0]["incomplete"] is True
    assert _trace(segundo)["intent"] == "follow_up"
    assert "ciudad fue fundada" in llm.prompts[-1]


def test_el_historial_de_postgres_basta_con_checkpoint_vacio(
    crear_base: Callable[[], str],
) -> None:
    """C9: el seguimiento sale de PostgreSQL aunque el checkpointer sea otro y esté vacío."""

    # Arrange: primer proceso con su checkpointer.
    chat_url, checkpoint_viejo = crear_base(), crear_base()
    with chat_stack(chat_url, checkpoint_viejo, RecordingLLM()) as primero:
        list(primero.service.stream("vis-1", HISTORIA))
        hilo_id = primero.service.conversations("vis-1")[0].id

    # Act: el runtime nuevo usa una base de checkpoints distinta y vacía.
    checkpoint_nuevo = crear_base()
    llm = RecordingLLM()
    with chat_stack(chat_url, checkpoint_nuevo, llm) as reiniciado:
        events = list(reiniciado.service.stream("vis-1", SEGUIMIENTO, thread_id=hilo_id))

    # Assert
    assert _trace(events)["intent"] == "follow_up"
    assert "ciudad fue fundada" in llm.prompts[-1]


def test_el_estado_tecnico_no_guarda_la_respuesta(crear_base: Callable[[], str]) -> None:
    """C11: el checkpoint es estado técnico, no una copia acumulada del turno."""

    # Arrange
    chat_url, checkpoint_url = crear_base(), crear_base()

    with chat_stack(chat_url, checkpoint_url, RecordingLLM()) as stack:
        # Act
        list(stack.service.stream("vis-1", HISTORIA))
        hilo_id = stack.service.conversations("vis-1")[0].id
        estado = stack.graph.get_state({"configurable": {"thread_id": hilo_id}}).values

    # Assert
    assert "answer" not in estado
    assert "incomplete" not in estado
    assert estado.get("turns", []) == []
