"""Runtime del agente: checkpointer, Store, Qdrant y grafos compilados.

Se construye en la primera pregunta, nunca al importar ni al comprobar la
vitalidad. ``PostgresSaver`` y ``PostgresStore`` usan el Postgres existente;
``agent_checkpointer=sqlite`` deja una demo local con archivo persistente.

``AGENT_MODE`` (spec 044, FR-015) elige qué grafo atiende los turnos. El grafo
``routes`` se construye siempre: con ``tools`` activo atiende el respaldo determinista
cuando DeepSeek falla o vence el plazo (FR-009), y en ese caso se compila con el
enrutador de reglas (``decide_route``) aunque ``AGENT_ROUTER`` pida otro: el respaldo no
llama a ningún LLM. Sin grafo ``tools``, ``routes`` atiende los turnos con el enrutador
configurado: el modo ``tools`` solo arranca con ``LLM_PROVIDER=deepseek`` y DeepSeek
completo (``carvajal.infrastructure.agent_mode``), así que con ``ollama`` o ``claude`` el
chat no envía los turnos a DeepSeek aunque haya una clave.
"""

import atexit
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from langgraph.store.base import BaseStore
from sqlalchemy.engine import make_url

from carvajal.adapters.embeddings import OllamaEmbedder
from carvajal.adapters.llm.deepseek import build_deepseek_chat
from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.agent.graph import AgentDeps, build_agent
from carvajal.agent.router import decide_route
from carvajal.agent.routers import Router, build_router, router_name
from carvajal.agent.toolagent.graph import build_tool_agent
from carvajal.agent.toolagent.tools import ToolDeps
from carvajal.application.ports import Embedder, VectorStore
from carvajal.application.retrieval import LayerPriorityStore
from carvajal.infrastructure.agent_mode import agent_mode_status
from carvajal.infrastructure.settings import Settings
from carvajal.institutional import load_institutional

__all__ = ["AgentRuntime", "agent_runtime", "close_agent_runtime"]

logger = structlog.get_logger(__name__)


@dataclass
class AgentRuntime:
    """Recursos vivos del agente; se cierran al terminar el proceso.

    ``graph`` es el grafo ``routes`` (siempre presente: atajo y respaldo);
    ``tool_graph`` es el grafo ``tools`` y solo existe si ``AGENT_MODE=tools`` y la
    configuración lo permite (``agent_mode_status``).
    """

    graph: Any
    qdrant: QdrantVectorStore
    checkpointer: Any
    store: Any
    _stack: ExitStack
    tool_graph: Any = None

    @property
    def mode(self) -> str:
        """Modo que atiende los turnos: ``tools`` solo si su grafo se construyó."""

        return "tools" if self.tool_graph is not None else "routes"

    def health(self) -> dict[str, object]:
        """Estado del índice sin implicar la salud del proceso."""

        return self.qdrant.health()

    def close(self) -> None:
        """Cierra conexiones y el cliente de Qdrant."""

        self.qdrant.close()
        self._stack.close()


def _plain_postgres_url(url: str) -> str:
    """Quita el sufijo de driver de SQLAlchemy para psycopg/libpq."""

    parsed = make_url(url)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def _router(settings: Settings) -> Router:
    """Construye el enrutador configurado; si no se puede, el agente sigue con reglas."""

    try:
        return build_router(settings.agent_router, settings)
    except Exception as error:
        logger.warning(
            "agent.router_fallback",
            router=settings.agent_router,
            error=str(error),
            exc_info=True,
        )
        return decide_route


def _graphs(
    settings: Settings,
    *,
    vector_store: VectorStore,
    embedder: Embedder,
    checkpointer: Any,
    store: Any,
    router: Router,
) -> tuple[Any, Any | None]:
    """Compila el grafo ``routes`` y, con ``AGENT_MODE=tools``, también el ``tools``.

    Si la configuración no permite el modo ``tools`` (``LLM_PROVIDER`` distinto de
    ``deepseek`` o DeepSeek incompleto, p. ej. sin ``DEEPSEEK_API_KEY``), se registra un
    aviso sin traza con el motivo y el proceso sigue con ``routes``: es una configuración
    esperada (CI, ``make up`` con Ollama). Un fallo inesperado al construir el grafo se
    registra como error con traza y el proceso también sigue con ``routes``.

    Con el grafo ``tools`` activo, ``routes`` solo atiende el respaldo (FR-009, R8): se
    compila con ``decide_route`` para que el respaldo no llame a un ``LLMRouter`` (a menudo
    el mismo proveedor que acaba de fallar) ni sume su tiempo de espera.
    """

    def routes_graph(selected: Router) -> Any:
        return build_agent(
            AgentDeps(vector_store=vector_store, embedder=embedder, settings=settings),
            checkpointer=checkpointer,
            store=store,
            router=selected,
        )

    status = agent_mode_status(settings)
    if status.configured != "tools":
        return routes_graph(router), None
    if status.reason is not None:
        logger.warning(
            "agent.tools_mode_unavailable",
            mode=settings.agent_mode,
            reason=status.reason,
            llm_provider=settings.llm_provider,
        )
        return routes_graph(router), None
    try:
        tool_graph = build_tool_agent(
            ToolDeps(
                vector_store=vector_store,
                embedder=embedder,
                settings=settings,
                # Spec 045: el mismo Store del grafo guarda la memoria del dueño; la capa
                # institucional se carga una vez y declara ``no_disponible`` si falta.
                # ``quote_lookup`` (T080) y ``faq`` (T067) llegan con sus herramientas.
                memory=store if isinstance(store, BaseStore) else None,
                institutional=load_institutional(),
            ),
            _tool_chat(settings),
            checkpointer=checkpointer,
            store=store,
        )
    except Exception as error:
        logger.error(
            "agent.tools_mode_unavailable",
            mode=settings.agent_mode,
            reason="build_failed",
            error=str(error),
            exc_info=True,
        )
        return routes_graph(router), None
    if router is not decide_route:
        logger.info("agent.fallback_router", configured=router_name(router), fallback="rules")
    return routes_graph(decide_route), tool_graph


def _tool_chat(settings: Settings) -> Any:
    """``ChatOpenAI`` de DeepSeek para el modo ``tools``, acotado al plazo del turno.

    El plazo del turno (``agent_turn_timeout_s``, FR-010, R9) no espera al proveedor: el
    runner lo vence aunque la petición siga en vuelo. Para que esa petición no siga
    ocupando el hilo del grafo mucho después, el cliente espera como mucho
    ``min(llm_timeout_seconds, agent_turn_timeout_s)`` y no reintenta (el cliente de
    ``openai`` reintenta 2 veces por defecto): un reintento ya no llegaría a tiempo y el
    respaldo determinista atiende el turno.
    """

    timeout = min(settings.llm_timeout_seconds, settings.agent_turn_timeout_s)
    return build_deepseek_chat(
        settings, stream_usage=True, for_tools=True, timeout_s=timeout, max_retries=0
    )


def _build(settings: Settings) -> AgentRuntime:
    stack = ExitStack()
    qdrant = QdrantVectorStore(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        collection=settings.qdrant_collection,
        dimensions=settings.qdrant_dimensions,
        distance=settings.qdrant_distance,
    )
    try:
        if settings.agent_checkpointer.lower() == "sqlite":
            from langgraph.checkpoint.sqlite import SqliteSaver
            from langgraph.store.memory import InMemoryStore

            path = Path(settings.agent_sqlite_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            checkpointer = stack.enter_context(SqliteSaver.from_conn_string(str(path)))
            store = InMemoryStore()
        else:
            from langgraph.checkpoint.postgres import PostgresSaver
            from langgraph.store.postgres import PostgresStore

            url = _plain_postgres_url(settings.database_url)
            checkpointer = stack.enter_context(PostgresSaver.from_conn_string(url))
            checkpointer.setup()
            store = stack.enter_context(PostgresStore.from_conn_string(url))
            store.setup()
        router = _router(settings)
        graph, tool_graph = _graphs(
            settings,
            vector_store=LayerPriorityStore(qdrant, min_score=settings.rag_min_score),
            embedder=OllamaEmbedder(base_url=settings.ollama_base_url),
            checkpointer=checkpointer,
            store=store,
            router=router,
        )
        runtime = AgentRuntime(
            graph=graph,
            qdrant=qdrant,
            checkpointer=checkpointer,
            store=store,
            _stack=stack,
            tool_graph=tool_graph,
        )
        logger.info(
            "agent.runtime_ready",
            mode=runtime.mode,
            mode_configured=settings.agent_mode,
            # Modelo que atiende el modo ``tools``: la decisión (evidencia §10) vale para el
            # modelo evaluado y este campo permite comprobar que es el mismo.
            tools_model=settings.deepseek_model.strip() if runtime.mode == "tools" else None,
            router=router_name(router),
            checkpointer=settings.agent_checkpointer,
            collection=settings.qdrant_collection,
        )
        return runtime
    except Exception:
        qdrant.close()
        stack.close()
        raise


_RUNTIME_LOCK = threading.Lock()
_runtime: AgentRuntime | None = None


def agent_runtime() -> AgentRuntime:
    """Runtime único por proceso, construido una sola vez aunque haya concurrencia.

    ``functools.cache`` no impide que dos primeras preguntas simultáneas
    construyan dos runtimes; el segundo cierra conexiones que el primero está
    usando. El lock serializa la inicialización y los fallos se reintentan.
    """

    global _runtime
    if _runtime is None:
        with _RUNTIME_LOCK:
            if _runtime is None:
                _runtime = _build(Settings())
    return _runtime


def close_agent_runtime() -> None:
    """Cierra el runtime si llegó a construirse."""

    global _runtime
    with _RUNTIME_LOCK:
        if _runtime is not None:
            _runtime.close()
            _runtime = None


atexit.register(close_agent_runtime)
