"""Shell web del corpus Carvajal: FastAPI, Jinja2 y HTMX.

``/live`` solo comprueba que el proceso responde; ``/health`` es la preparación
(readiness) y consulta la base. Los handlers reales se construyen al primer uso,
nunca al importar; el embedder y el LLM se crean en la primera pregunta y cada
petición abre su propia sesión.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from apps.api.capture_jobs import CaptureJobs
from apps.api.observability import (
    RequestContextMiddleware,
    UnhandledErrorMiddleware,
    configure_observability,
)
from apps.api.runtime import APP_DIR, session_factory, templates
from apps.api.security import SecurityMiddleware
from apps.api.views import api, chat, config, corpus, documents, faq, stream
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.usage import SqlUsageRepository
from carvajal.application.quota import DailyQuota, QuotaResult
from carvajal.infrastructure.monitoring import monitoring_status
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

    health: Callable[[], HealthStatus]
    quota: Callable[[str], QuotaResult] = _sin_limite


@cache
def _default_handlers() -> Handlers:
    """Construye los handlers reales una sola vez, al primer uso.

    Solo se crea la fábrica de sesiones: el chat resuelve el embedder y el LLM
    en su primera pregunta, de modo que ``/health`` no importa LangChain ni
    exige credenciales del proveedor.
    """

    settings = Settings()
    factory = session_factory()

    def health() -> HealthStatus:
        with factory.begin() as session:
            documents = session.scalar(select(func.count()).select_from(tables.documents))
            chunks = session.scalar(select(func.count()).select_from(tables.chunks))
        from apps.api.runtime import qdrant_store

        return HealthStatus(documents or 0, chunks or 0, qdrant_store().health())

    def quota(owner_key: str) -> QuotaResult:
        with factory.begin() as session:
            uso = SqlUsageRepository(session)
            return DailyQuota(
                uso,
                limit=settings.daily_requests_limit,
                timezone=settings.quota_timezone,
            ).consume(owner_key)

    return Handlers(health=health, quota=quota)


def force_global_tracing_off() -> None:
    """Apaga el tracing global de LangChain al arrancar (FR-001, FR-006).

    Una variable heredada del entorno encendería las trazas de todo el
    proceso, sin muestreo y sin la redacción del monitoreo. El encendido es
    explícito por turno, así que aquí se neutralizan ambas variables.
    """

    for nombre in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        if os.environ.get(nombre, "").strip().lower() in {"1", "true", "yes", "on"}:
            logger.warning("monitoring.global_tracing_forced_off", variable=nombre)
        os.environ[nombre] = "false"


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
        "_aviso.html",
        {"error": message},
        status_code,
        headers={"X-Carvajal-Error": "1", **(headers or {})},
    )


def _unhandled(request: Request, error: Exception) -> Response:
    """Respuesta genérica para un 500: sin trazas ni detalles internos."""

    return _message(request, 500, SERVICE_UNAVAILABLE)


def create_app(handlers: Handlers | None = None) -> FastAPI:
    """Crea la aplicación; sin ``handlers`` usa los reales, diferidos al primer uso."""

    force_global_tracing_off()
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
    app.mount("/static", StaticFiles(directory=APP_DIR / "static", check_dir=False), name="static")
    for module in (corpus, documents, chat, stream, api, config, faq):
        app.include_router(module.router)
    app.include_router(faq.page_router)

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
                {
                    "status": "degraded",
                    "detail": DB_UNAVAILABLE,
                    "index": {"status": "unknown"},
                    "monitoring": monitoring_status(settings).as_health(),
                },
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
                "monitoring": monitoring_status(settings).as_health(),
            },
            status_code=200 if healthy else 503,
        )

    @app.get("/")
    def index() -> RedirectResponse:
        """La portada redirige al chat: la pestaña Asistente ya no se muestra."""

        return RedirectResponse(url="/chat", status_code=303)

    return app


app = create_app()
