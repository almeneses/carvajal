"""Shell web del corpus Carvajal: FastAPI, Jinja2 y HTMX.

Los handlers reales se construyen al primer uso, nunca al importar; ``embedder``
y ``llm`` son singletons y cada petición abre su propia sesión.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from logging import getLogger
from pathlib import Path

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from carvajal.adapters.embeddings import EmbeddingError, OllamaEmbedder
from carvajal.adapters.llm import build_llm_provider
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_engine, build_session_factory
from carvajal.adapters.vector.pgvector_store import PgVectorStore
from carvajal.application.ask import Answer, AskQuestion, LLMError
from carvajal.infrastructure.settings import Settings

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")
logger = getLogger(__name__)

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
def _default_handlers() -> Handlers:
    """Construye los handlers reales una sola vez, al primer uso."""

    settings = Settings()
    session_factory = build_session_factory(build_engine(settings.database_url))
    embedder = OllamaEmbedder()
    llm = build_llm_provider(settings)

    def health() -> HealthStatus:
        with session_factory.begin() as session:
            documents = session.scalar(select(func.count()).select_from(tables.documents))
            chunks = session.scalar(select(func.count()).select_from(tables.chunks))
        return HealthStatus(documents or 0, chunks or 0)

    def ask(question: str) -> Answer:
        with session_factory.begin() as session:
            return AskQuestion(PgVectorStore(session), embedder, llm).answer(question)

    return Handlers(ask=ask, health=health)


def _message(request: Request, status_code: int, message: str) -> Response:
    """Devuelve el parcial de error para HTMX, sin exponer trazas."""

    return templates.TemplateResponse(request, "_answer.html", {"error": message}, status_code)


def create_app(handlers: Handlers | None = None) -> FastAPI:
    """Crea la aplicación; sin ``handlers`` usa los reales, diferidos al primer uso."""

    app = FastAPI(title="Corpus Carvajal")
    app.mount("/static", StaticFiles(directory=APP_DIR / "static", check_dir=False), name="static")

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            status = (handlers or _default_handlers()).health()
        except (SQLAlchemyError, OSError) as error:  # la base no responde
            logger.warning("Health check falló: %s", error)
            return JSONResponse({"status": "degraded", "detail": DB_UNAVAILABLE}, status_code=503)
        return JSONResponse(
            {"status": "ok", "documents": status.documents, "chunks": status.chunks}
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        return templates.TemplateResponse(request, "index.html", {})

    @app.post("/ask", response_class=HTMLResponse)
    def ask(request: Request, question: str = Form(default="")) -> Response:
        text = question.strip()
        if not text:
            return _message(request, 422, "La pregunta no puede estar vacía.")
        try:
            answer = (handlers or _default_handlers()).ask(text)
        except (EmbeddingError, LLMError, SQLAlchemyError) as error:
            logger.warning("Fallo al responder: %s", error)
            return _message(request, 503, SERVICE_UNAVAILABLE)
        return templates.TemplateResponse(request, "_answer.html", {"answer": answer})

    return app


app = create_app()
