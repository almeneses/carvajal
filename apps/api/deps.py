"""Piezas compartidas del shell web: plantillas, handlers y avisos de error.

Las rutas de ``apps/api/routers`` dependen solo de este módulo y nunca de
``main``, de modo que cada área añade su router sin tocar los de las demás.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from fastapi import Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_engine, build_session_factory
from carvajal.adapters.vector.pgvector_store import PgVectorStore
from carvajal.application.ask import Answer, AskQuestion
from carvajal.application.ports import Embedder, LLMProvider
from carvajal.infrastructure.settings import Settings

__all__ = [
    "APP_DIR",
    "DB_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "Handlers",
    "HealthStatus",
    "default_handlers",
    "get_handlers",
    "message",
    "templates",
]

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")

DB_UNAVAILABLE = "La base de datos no responde."
SERVICE_UNAVAILABLE = "El servicio de respuestas no está disponible en este momento."


@dataclass(frozen=True)
class HealthStatus:
    """Conteos del corpus que publica ``GET /health``."""

    documents: int
    chunks: int


@dataclass(frozen=True)
class Handlers:
    """Casos de uso que expone el shell, inyectables en las pruebas."""

    ask: Callable[[str], Answer]
    health: Callable[[], HealthStatus]


@cache
def _ask_components() -> tuple[Embedder, LLMProvider]:
    """Clientes de embeddings y LLM, creados en la primera pregunta.

    Importar LangChain tarda segundos y construir un proveedor puede fallar por
    configuración (por ejemplo, una clave ausente). Ni una cosa ni la otra deben
    afectar a ``/health`` o al arranque del contenedor.
    """

    from carvajal.adapters.embeddings import OllamaEmbedder
    from carvajal.adapters.llm import build_llm_provider

    return OllamaEmbedder(), build_llm_provider()


@cache
def default_handlers() -> Handlers:
    """Construye los handlers reales una sola vez, al primer uso."""

    settings = Settings()
    session_factory = build_session_factory(build_engine(settings.database_url))

    def health() -> HealthStatus:
        with session_factory.begin() as session:
            documents = session.scalar(select(func.count()).select_from(tables.documents))
            chunks = session.scalar(select(func.count()).select_from(tables.chunks))
        return HealthStatus(documents or 0, chunks or 0)

    def ask(question: str) -> Answer:
        embedder, llm = _ask_components()
        with session_factory.begin() as session:
            return AskQuestion(PgVectorStore(session), embedder, llm).answer(question)

    return Handlers(ask=ask, health=health)


def get_handlers(request: Request) -> Handlers:
    """Devuelve los handlers inyectados en la app o los reales, diferidos."""

    inyectados = getattr(request.app.state, "handlers", None)
    return inyectados or default_handlers()


def message(request: Request, status_code: int, text: str) -> Response:
    """Devuelve el parcial de error para HTMX, sin exponer trazas."""

    return templates.TemplateResponse(request, "_answer.html", {"error": text}, status_code)
