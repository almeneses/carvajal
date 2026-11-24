"""Runtime del modo ``tools`` (spec 044, revisión de US3+US4).

- El respaldo determinista (FR-009) corre el grafo ``routes`` con el enrutador de reglas:
  con ``AGENT_ROUTER=llm`` no debe llamar a ningún LLM.
- El chat del modo ``tools`` queda acotado al plazo del turno (FR-010, R9): sin reintentos
  del cliente y con un tiempo de espera que no supera ``agent_turn_timeout_s``.

Sin red: el ``ChatOpenAI`` real se construye con una clave falsa y nunca se invoca.
"""

from collections.abc import Sequence
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from tests.fakes.tool_chat import ScriptedToolChat

from apps.api import agent_runtime as agent_runtime_module
from apps.api.agent_runtime import _graphs, _tool_chat
from apps.api.chat.agent import GraphAgentRunner
from carvajal.agent.state import RouteDecision
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 8


class EmptyStore:
    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise AssertionError("El runtime no indexa.")

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        return []

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("El runtime no borra.")


class RecordingRouter:
    """Enrutador configurado (p. ej. ``LLMRouter``); registra o rechaza cada llamada."""

    def __init__(self, *, forbidden: bool) -> None:
        self.forbidden = forbidden
        self.calls: list[str] = []

    def __call__(self, question: str, turns: Any = None) -> RouteDecision:
        self.calls.append(question)
        if self.forbidden:
            raise AssertionError("El respaldo no debe llamar al enrutador LLM.")
        return RouteDecision(route="direct", intent="greeting", query=question)


def _build_graphs(settings: Settings, router: RecordingRouter) -> tuple[Any, Any]:
    return _graphs(
        settings,
        vector_store=EmptyStore(),
        embedder=FakeEmbedder(),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        router=router,
    )


def test_en_modo_tools_el_grafo_del_respaldo_usa_el_enrutador_de_reglas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: ``AGENT_ROUTER=llm`` construiría un ``LLMRouter``; el respaldo no lo usa.
    settings = Settings(agent_mode="tools", agent_router="llm")
    monkeypatch.setattr(
        agent_runtime_module, "build_deepseek_chat", lambda s, **_: ScriptedToolChat(steps=[])
    )
    router = RecordingRouter(forbidden=True)
    graph, tool_graph = _build_graphs(settings, router)

    # Act
    turn = GraphAgentRunner(graph, settings).prepare("dueno-1", "hilo-1", "hola", [])

    # Assert
    assert tool_graph is not None
    assert router.calls == []
    assert (turn.route, turn.intent) == ("direct", "greeting")


def test_en_modo_routes_el_grafo_usa_el_enrutador_configurado() -> None:
    # Arrange
    settings = Settings(agent_mode="routes", agent_router="llm")
    router = RecordingRouter(forbidden=False)
    graph, tool_graph = _build_graphs(settings, router)

    # Act
    GraphAgentRunner(graph, settings).prepare("dueno-1", "hilo-1", "hola", [])

    # Assert
    assert tool_graph is None
    assert router.calls == ["hola"]


def test_sin_grafo_tools_el_grafo_routes_conserva_el_enrutador_configurado() -> None:
    # Arrange: sin clave de DeepSeek los turnos siguen por ``routes`` con su enrutador.
    settings = Settings(agent_mode="tools", agent_router="llm", deepseek_api_key="")
    router = RecordingRouter(forbidden=False)
    graph, tool_graph = _build_graphs(settings, router)

    # Act
    GraphAgentRunner(graph, settings).prepare("dueno-1", "hilo-1", "hola", [])

    # Assert
    assert tool_graph is None
    assert router.calls == ["hola"]


def test_el_chat_del_modo_tools_no_reintenta_ni_espera_mas_que_el_plazo() -> None:
    # Arrange: el cliente de ``openai`` reintenta 2 veces por defecto y espera 60 s.
    settings = Settings(
        deepseek_api_key="sk-prueba", llm_timeout_seconds=60.0, agent_turn_timeout_s=45.0
    )

    # Act
    chat = _tool_chat(settings)

    # Assert
    root = chat.root_client
    assert root.max_retries == 0
    assert float(root.timeout) <= settings.agent_turn_timeout_s
    assert chat.client._client is root
    assert chat.request_timeout is not None
    assert float(chat.request_timeout) <= settings.agent_turn_timeout_s


def test_con_un_plazo_largo_se_respeta_el_tiempo_de_espera_del_proveedor() -> None:
    # Arrange
    settings = Settings(
        deepseek_api_key="sk-prueba", llm_timeout_seconds=30.0, agent_turn_timeout_s=45.0
    )

    # Act
    chat = _tool_chat(settings)

    # Assert
    assert float(chat.root_client.timeout) == 30.0
    assert chat.root_client.max_retries == 0
