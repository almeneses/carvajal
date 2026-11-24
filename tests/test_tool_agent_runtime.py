"""Runtime del modo ``tools`` (spec 044, revisión de US3+US4).

- El respaldo determinista (FR-009) corre el grafo ``routes`` con el enrutador de reglas:
  con ``AGENT_ROUTER=llm`` no debe llamar a ningún LLM.
- El chat del modo ``tools`` queda acotado al plazo del turno (FR-010, R9): sin reintentos
  del cliente y con un tiempo de espera que no supera ``agent_turn_timeout_s``.
- El modo ``tools`` (valor por defecto) solo arranca con ``LLM_PROVIDER=deepseek``: con
  otro proveedor el chat no envía turnos a DeepSeek aunque haya clave. Una configuración
  esperada sin ``tools`` deja un aviso sin traza; un fallo inesperado, un error con traza.

Sin red: el ``ChatOpenAI`` real se construye con una clave falsa y nunca se invoca.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from structlog.testing import capture_logs
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
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        agent_mode="tools",
        agent_router="llm",
        llm_provider="deepseek",
        deepseek_api_key="sk-prueba",
    )
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
    settings = Settings(_env_file=None, agent_mode="routes", agent_router="llm")  # type: ignore[call-arg]
    router = RecordingRouter(forbidden=False)
    graph, tool_graph = _build_graphs(settings, router)

    # Act
    GraphAgentRunner(graph, settings).prepare("dueno-1", "hilo-1", "hola", [])

    # Assert
    assert tool_graph is None
    assert router.calls == ["hola"]


def test_sin_grafo_tools_el_grafo_routes_conserva_el_enrutador_configurado() -> None:
    # Arrange: sin clave de DeepSeek los turnos siguen por ``routes`` con su enrutador.
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        agent_mode="tools",
        agent_router="llm",
        llm_provider="deepseek",
        deepseek_api_key="",
    )
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


def _tools_unavailable(logs: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return next(item for item in logs if item["event"] == "agent.tools_mode_unavailable")


def test_por_defecto_con_deepseek_configurado_arranca_el_grafo_tools() -> None:
    # Arrange: valor por defecto de ``AGENT_MODE`` (``tools``), sin red: el ``ChatOpenAI``
    # real se construye con una clave falsa y nunca se invoca.
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="deepseek",
        deepseek_api_key="sk-prueba",
    )

    # Act
    with capture_logs() as logs:
        _, tool_graph = _build_graphs(settings, RecordingRouter(forbidden=True))

    # Assert
    assert settings.agent_mode == "tools"
    assert tool_graph is not None
    assert [item for item in logs if item["event"] == "agent.tools_mode_unavailable"] == []


@pytest.mark.parametrize("provider", ["ollama", "claude", " Ollama "])
def test_con_otro_proveedor_y_clave_de_deepseek_no_arranca_tools(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    # Arrange: la clave existe para el enrutador o ``eval-agent``; el chat no debe
    # enviar turnos a DeepSeek si ``LLM_PROVIDER`` pide un proveedor local u otro.
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        agent_mode="tools",
        llm_provider=provider,
        deepseek_api_key="sk-prueba",
    )

    def no_deepseek(*_: Any, **__: Any) -> Any:
        raise AssertionError("Con otro proveedor no se construye el chat de DeepSeek.")

    monkeypatch.setattr(agent_runtime_module, "build_deepseek_chat", no_deepseek)
    router = RecordingRouter(forbidden=False)

    # Act
    with capture_logs() as logs:
        graph, tool_graph = _build_graphs(settings, router)
    GraphAgentRunner(graph, settings).prepare("dueno-1", "hilo-1", "hola", [])

    # Assert
    evento = _tools_unavailable(logs)
    assert tool_graph is None
    assert router.calls == ["hola"]
    assert evento["log_level"] == "warning"
    assert evento["reason"] == "llm_provider"
    assert evento["llm_provider"] == provider
    assert "exc_info" not in evento


@pytest.mark.parametrize(
    ("clave", "modelo", "url", "motivo"),
    [
        ("", "deepseek-chat", "https://api.deepseek.com/v1", "missing_deepseek_api_key"),
        ("   ", "deepseek-chat", "https://api.deepseek.com/v1", "missing_deepseek_api_key"),
        ("sk-prueba", " ", "https://api.deepseek.com/v1", "missing_deepseek_model"),
        ("sk-prueba", "deepseek-chat", "", "missing_deepseek_base_url"),
    ],
)
def test_deepseek_incompleto_deja_un_aviso_sin_traza(
    clave: str, modelo: str, url: str, motivo: str
) -> None:
    # Arrange: CI o desarrollo sin clave; es una configuración esperada, no un error.
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        agent_mode="tools",
        llm_provider="deepseek",
        deepseek_api_key=clave,
        deepseek_model=modelo,
        deepseek_base_url=url,
    )

    # Act
    with capture_logs() as logs:
        _, tool_graph = _build_graphs(settings, RecordingRouter(forbidden=False))

    # Assert
    evento = _tools_unavailable(logs)
    assert tool_graph is None
    assert evento["log_level"] == "warning"
    assert evento["reason"] == motivo
    assert "exc_info" not in evento


def test_un_fallo_inesperado_al_construir_tools_se_registra_como_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        agent_mode="tools",
        llm_provider="deepseek",
        deepseek_api_key="sk-prueba",
    )

    def roto(*_: Any, **__: Any) -> Any:
        raise RuntimeError("esquema de herramientas inválido")

    monkeypatch.setattr(agent_runtime_module, "build_tool_agent", roto)

    # Act
    with capture_logs() as logs:
        _, tool_graph = _build_graphs(settings, RecordingRouter(forbidden=False))

    # Assert
    evento = _tools_unavailable(logs)
    assert tool_graph is None
    assert evento["log_level"] == "error"
    assert evento["reason"] == "build_failed"
    assert evento["exc_info"] is True
    assert "esquema" in evento["error"]
