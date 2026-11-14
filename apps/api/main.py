"""Shell web del corpus Carvajal: FastAPI, Jinja2 y HTMX.

``/live`` solo comprueba que el proceso responde; ``/health`` es la preparación
(readiness) y consulta la base. Los handlers reales se construyen al primer uso,
nunca al importar; el embedder y el LLM se crean en la primera pregunta y cada
petición abre su propia sesión.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

import structlog
from fastapi import FastAPI, Form, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from apps.api.capture_jobs import CaptureJobs
from apps.api.chat.identity import ChatIdentity
from apps.api.observability import (
    RequestContextMiddleware,
    UnhandledErrorMiddleware,
    capture_exception,
    configure_observability,
)
from apps.api.runtime import APP_DIR, ask_components, session_factory, templates
from apps.api.security import SecurityMiddleware, verify_csrf
from apps.api.views import api, chat, config, corpus, documents, faq, stream
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.usage import SqlUsageRepository
from carvajal.adapters.vector.pgvector_store import PgVectorStore
from carvajal.application.ask import Answer, AskQuestion, LLMError
from carvajal.application.quota import DailyQuota, QuotaResult
from carvajal.infrastructure.settings import Settings

logger = structlog.get_logger(__name__)

DB_UNAVAILABLE = "La base de datos no responde."
SERVICE_UNAVAILABLE = "El servicio de respuestas no está disponible en este momento."


@dataclass(frozen=True)
class HealthStatus:
    """Conteos del corpus y estado del índice que publica ``GET /health``."""

    documents: int
    chunks: int
    index: dict[str, object] | None = None


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
        from apps.api.runtime import qdrant_store

        return HealthStatus(documents or 0, chunks or 0, qdrant_store().health())

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
    """Devuelve el parcial de error para HTMX, sin exponer trazas.

    ``X-Carvajal-Error`` permite al JS distinguir un error propio (que puede
    pintar) de una página ajena de un proxy (que debe sustituir por un aviso).
    """

    return templates.TemplateResponse(
        request,
        "_answer.html",
        {"error": message},
        status_code,
        headers={"X-Carvajal-Error": "1", **(headers or {})},
    )


def _unhandled(request: Request, error: Exception) -> Response:
    """Respuesta genérica para un 500: sin trazas ni detalles internos."""

    return _message(request, 500, SERVICE_UNAVAILABLE)


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

    configure_observability()
    # Sin Swagger público: la interfaz de la app es el formulario HTML y el
    # esquema no aporta a un visitante anónimo.
    app = FastAPI(title="Corpus Carvajal", docs_url=None, redoc_url=None, openapi_url=None)
    settings = Settings()
    app.state.quota = handlers.quota if handlers is not None else _default_quota
    # Captura web: un registro por app para que las pruebas inyecten su runner.
    app.state.captures = CaptureJobs(
        rate_limit=settings.capture_requests_limit,
        window_seconds=settings.capture_window_seconds,
    )
    # El error no controlado se maneja dentro de la pila para que la respuesta
    # 500 pase por el request_id y por las cabeceras de seguridad.
    app.add_middleware(UnhandledErrorMiddleware, handler=_unhandled)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.frontend_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
    )
    app.mount("/static", StaticFiles(directory=APP_DIR / "static", check_dir=False), name="static")
    for module in (corpus, documents, chat, stream, api, config, faq):
        app.include_router(module.router)

    @app.get("/live")
    def live() -> JSONResponse:
        """Vitalidad del proceso: no toca la base ni construye clientes de LLM."""

        return JSONResponse({"status": "ok"})

    @app.get("/health")
    def health() -> JSONResponse:
        """Readiness: base e índice; ``/live`` sigue siendo solo el proceso."""

        try:
            status = (handlers or _default_handlers()).health()
        except (SQLAlchemyError, OSError) as error:  # la base no responde
            logger.warning("health.failed", error=str(error))
            return JSONResponse(
                {"status": "degraded", "detail": DB_UNAVAILABLE, "index": {"status": "unknown"}},
                status_code=503,
            )
        index = status.index or {"status": "not_configured"}
        healthy = index.get("status") in {"ok", "not_configured"}
        return JSONResponse(
            {
                "status": "ok" if healthy else "degraded",
                "documents": status.documents,
                "chunks": status.chunks,
                "index": index,
            },
            status_code=200 if healthy else 503,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        return templates.TemplateResponse(request, "index.html", {})

    @app.post("/ask", response_class=HTMLResponse)
    def ask(
        request: Request,
        question: str = Form(default=""),
        csrf_token: str = Form(default=""),
    ) -> Response:
        # CSRF antes de cualquier efecto: sin token no se toca cuota ni LLM.
        if not verify_csrf(request, csrf_token):
            logger.warning("csrf.rejected", path=request.url.path)
            return _message(request, 403, chat.CSRF_INVALIDO)

        text = question.strip()
        if not text:
            return _message(request, 422, "La pregunta no puede estar vacía.")

        identity = chat.request_identity(request)
        stored_owner = identity.read(request)
        new_owner = stored_owner is None
        owner_key = stored_owner or identity.new_owner_key()
        # A partir de aquí, todo log de la petición (incluido el consumo de
        # tokens del adaptador) queda asociado al visitante anónimo.
        structlog.contextvars.bind_contextvars(owner_key=owner_key)
        current = handlers or _default_handlers()

        try:
            quota = current.quota(owner_key)
        except SQLAlchemyError as error:
            logger.warning("quota.failed", error=str(error))
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
            logger.warning("ask.failed", error=str(error))
            if isinstance(error, LLMError):
                capture_exception(error)
            unavailable = _message(request, 503, SERVICE_UNAVAILABLE)
            return _emit_identity(unavailable, request, identity, owner_key, new_owner=new_owner)

        response = templates.TemplateResponse(request, "_answer.html", {"answer": answer})
        return _emit_identity(response, request, identity, owner_key, new_owner=new_owner)

    return app


app = create_app()
