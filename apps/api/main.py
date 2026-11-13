"""Shell web del corpus Carvajal: FastAPI, Jinja2 y HTMX.

``/live`` solo comprueba que el proceso responde; ``/health`` es la preparación
(readiness) y consulta la base. Los handlers reales se construyen al primer uso,
nunca al importar; el embedder y el LLM se crean en la primera pregunta y cada
petición abre su propia sesión.
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

from apps.api.chat.identity import ChatIdentity
from apps.api.runtime import APP_DIR, ask_components, session_factory, templates
from apps.api.views import chat, corpus, documents, stream
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.usage import SqlUsageRepository
from carvajal.adapters.vector.pgvector_store import PgVectorStore
from carvajal.application.ask import Answer, AskQuestion, LLMError
from carvajal.application.quota import DailyQuota, QuotaResult
from carvajal.infrastructure.settings import Settings

logger = getLogger(__name__)

DB_UNAVAILABLE = "La base de datos no responde."
SERVICE_UNAVAILABLE = "El servicio de respuestas no está disponible en este momento."


@dataclass(frozen=True)
class HealthStatus:
    """Conteos del corpus que publica ``GET /health``."""

    documents: int
    chunks: int


def _sin_limite(owner_key: str) -> QuotaResult:
    """Cupo por defecto: sin límite, para pruebas y desarrollo."""

    return QuotaResult(allowed=True)


@dataclass(frozen=True)
class Handlers:
    """Casos de uso que expone el shell, inyectables en las pruebas."""

    ask: Callable[[str], Answer]
    health: Callable[[], HealthStatus]
    quota: Callable[[str], QuotaResult] = _sin_limite


@cache
def _default_handlers() -> Handlers:
    """Construye los handlers reales una sola vez, al primer uso.

    Solo se crea la fábrica de sesiones: el embedder y el LLM se resuelven
    dentro de ``ask``, de modo que ``/health`` no importa LangChain ni exige
    credenciales del proveedor.
    """

    settings = Settings()
    factory = session_factory()

    def health() -> HealthStatus:
        with factory.begin() as session:
            documents = session.scalar(select(func.count()).select_from(tables.documents))
            chunks = session.scalar(select(func.count()).select_from(tables.chunks))
        return HealthStatus(documents or 0, chunks or 0)

    def ask(question: str) -> Answer:
        embed, llm = ask_components()
        with factory.begin() as session:
            return AskQuestion(PgVectorStore(session), embed, llm).answer(question)

    def quota(owner_key: str) -> QuotaResult:
        with factory.begin() as session:
            uso = SqlUsageRepository(session)
            return DailyQuota(
                uso,
                limit=settings.daily_requests_limit,
                timezone=settings.quota_timezone,
            ).consume(owner_key)

    return Handlers(ask=ask, health=health, quota=quota)


def _default_quota(owner_key: str) -> QuotaResult:
    """Cuota real, diferida hasta la primera petición que la necesita."""

    return _default_handlers().quota(owner_key)


def _message(
    request: Request,
    status_code: int,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    """Devuelve el parcial de error para HTMX, sin exponer trazas."""

    return templates.TemplateResponse(
        request, "_answer.html", {"error": message}, status_code, headers=headers
    )


def _emit_identity(
    response: Response,
    request: Request,
    identity: ChatIdentity,
    owner_key: str,
    *,
    new_owner: bool,
) -> Response:
    """Emite la cookie de identidad si el visitante aún no tenía una."""

    if new_owner:
        chat.set_identity_cookie(response, request, identity, owner_key)
    return response


def create_app(handlers: Handlers | None = None) -> FastAPI:
    """Crea la aplicación; sin ``handlers`` usa los reales, diferidos al primer uso."""

    app = FastAPI(title="Corpus Carvajal")
    app.state.quota = handlers.quota if handlers is not None else _default_quota
    app.mount("/static", StaticFiles(directory=APP_DIR / "static", check_dir=False), name="static")
    for module in (corpus, documents, chat, stream):
        app.include_router(module.router)

    @app.get("/live")
    def live() -> JSONResponse:
        """Vitalidad del proceso: no toca la base ni construye clientes de LLM."""

        return JSONResponse({"status": "ok"})

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

        identity = chat.request_identity(request)
        stored_owner = identity.read(request)
        new_owner = stored_owner is None
        owner_key = stored_owner or identity.new_owner_key()
        current = handlers or _default_handlers()

        try:
            quota = current.quota(owner_key)
        except SQLAlchemyError as error:
            logger.warning("No se pudo consultar la cuota diaria: %s", error)
            unavailable = _message(request, 503, SERVICE_UNAVAILABLE)
            return _emit_identity(unavailable, request, identity, owner_key, new_owner=new_owner)

        if not quota.allowed:
            limited = _message(
                request,
                429,
                chat.LIMITE_ALCANZADO,
                headers={"Retry-After": str(quota.retry_after)},
            )
            return _emit_identity(limited, request, identity, owner_key, new_owner=new_owner)

        try:
            answer = current.ask(text)
        except (EmbeddingError, LLMError, SQLAlchemyError) as error:
            logger.warning("Fallo al responder: %s", error)
            unavailable = _message(request, 503, SERVICE_UNAVAILABLE)
            return _emit_identity(unavailable, request, identity, owner_key, new_owner=new_owner)

        response = templates.TemplateResponse(request, "_answer.html", {"answer": answer})
        return _emit_identity(response, request, identity, owner_key, new_owner=new_owner)

    return app


app = create_app()
