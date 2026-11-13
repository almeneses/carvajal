"""Shell web del corpus Carvajal: FastAPI, Jinja2 y HTMX.

Los handlers reales se construyen al primer uso, nunca al importar; ``embedder``
y ``llm`` son singletons y cada petición abre su propia sesión.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from logging import getLogger

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from apps.api.runtime import APP_DIR, embedder, llm_provider, session_factory, templates
from apps.api.views import chat, corpus, documents, stream
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage import tables
from carvajal.adapters.vector.pgvector_store import PgVectorStore
from carvajal.application.ask import Answer, AskQuestion, LLMError

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

    factory = session_factory()
    embed = embedder()
    llm = llm_provider()

    def health() -> HealthStatus:
        with factory.begin() as session:
            documents = session.scalar(select(func.count()).select_from(tables.documents))
            chunks = session.scalar(select(func.count()).select_from(tables.chunks))
        return HealthStatus(documents or 0, chunks or 0)

    def ask(question: str) -> Answer:
        with factory.begin() as session:
            return AskQuestion(PgVectorStore(session), embed, llm).answer(question)

    return Handlers(ask=ask, health=health)


def _message(request: Request, status_code: int, message: str) -> Response:
    """Devuelve el parcial de error para HTMX, sin exponer trazas."""

    return templates.TemplateResponse(request, "_answer.html", {"error": message}, status_code)


def create_app(handlers: Handlers | None = None) -> FastAPI:
    """Crea la aplicación; sin ``handlers`` usa los reales, diferidos al primer uso."""

    app = FastAPI(title="Corpus Carvajal")
    app.mount("/static", StaticFiles(directory=APP_DIR / "static", check_dir=False), name="static")
    for module in (corpus, documents, chat, stream):
        app.include_router(module.router)

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
