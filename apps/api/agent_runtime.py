"""Runtime del agente: checkpointer, Store, Qdrant y grafo compilado.

Se construye en la primera pregunta, nunca al importar ni al comprobar la
vitalidad. ``PostgresSaver`` y ``PostgresStore`` usan el Postgres existente;
``agent_checkpointer=sqlite`` deja una demo local con archivo persistente.
"""

import atexit
from contextlib import ExitStack
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.engine import make_url

from carvajal.adapters.embeddings import OllamaEmbedder
from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.agent.graph import AgentDeps, build_agent
from carvajal.infrastructure.settings import Settings

__all__ = ["AgentRuntime", "agent_runtime", "close_agent_runtime"]

logger = structlog.get_logger(__name__)


@dataclass
class AgentRuntime:
    """Recursos vivos del agente; se cierran al terminar el proceso."""

    graph: Any
    qdrant: QdrantVectorStore
    checkpointer: Any
    store: Any
    _stack: ExitStack

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
        graph = build_agent(
            AgentDeps(
                vector_store=qdrant,
                embedder=OllamaEmbedder(base_url=settings.ollama_base_url),
                settings=settings,
            ),
            checkpointer=checkpointer,
            store=store,
        )
        logger.info(
            "agent.runtime_ready",
            checkpointer=settings.agent_checkpointer,
            collection=settings.qdrant_collection,
        )
        return AgentRuntime(
            graph=graph,
            qdrant=qdrant,
            checkpointer=checkpointer,
            store=store,
            _stack=stack,
        )
    except Exception:
        qdrant.close()
        stack.close()
        raise


@cache
def agent_runtime() -> AgentRuntime:
    """Runtime único por proceso; los fallos de arranque se reintentan."""

    return _build(Settings())


def close_agent_runtime() -> None:
    """Cierra el runtime si llegó a construirse."""

    if agent_runtime.cache_info().currsize:
        agent_runtime().close()
        agent_runtime.cache_clear()


atexit.register(close_agent_runtime)
