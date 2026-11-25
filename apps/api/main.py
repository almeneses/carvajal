"""Shell web del corpus Carvajal: FastAPI, Jinja2 y HTMX.

``/live`` solo comprueba que el proceso responde; ``/health`` es la preparación
(readiness) y consulta la base. Los handlers reales se construyen al primer uso,
nunca al importar; el embedder y el LLM se crean en la primera pregunta y cada
petición abre su propia sesión.
"""

import os
import sys
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
from apps.api.security import SecurityMiddleware, operator_authorized
from apps.api.views import (
    api,
    catalogo_imagenes,
    chat,
    config,
    corpus,
    cotizacion_pdf,
    cotizador,
    documents,
    faq,
    foto,
    stream,
    voz,
    webhooks,
)
from carvajal.adapters.judge import food_photo_health, jev_health
from carvajal.adapters.speech import voice_health
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.usage import SqlUsageRepository
from carvajal.application.quota import DailyQuota, QuotaResult
from carvajal.infrastructure.agent_mode import agent_mode_status, chat_llm_blocker
from carvajal.infrastructure.monitoring import monitoring_status
from carvajal.infrastructure.settings import Settings

logger = structlog.get_logger(__name__)

DB_UNAVAILABLE = "La base de datos no responde."
# Qué variable falta para el LLM del chat, por motivo de ``chat_llm_blocker``.
LLM_UNCONFIGURED_DETAIL = {
    "missing_deepseek_api_key": (
        "Falta DEEPSEEK_API_KEY: el chat atiende por routes y las respuestas redactadas "
        "por el LLM fallarán hasta definirla."
    ),
    "missing_deepseek_model": "DEEPSEEK_MODEL está vacío: define el modelo de DeepSeek.",
    "missing_deepseek_base_url": "DEEPSEEK_BASE_URL está vacía: define la URL de DeepSeek.",
}
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
    # LangSmith cachea la lectura del entorno (``get_env_var`` usa lru_cache):
    # si alguien ya leyó el valor heredado, hay que invalidar la caché o el
    # tracing seguiría activo pese a las variables (FR-001).
    utilidades = sys.modules.get("langsmith.utils")
    cache = getattr(utilidades, "get_env_var", None)
    if cache is not None and hasattr(cache, "cache_clear"):
        cache.cache_clear()


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


def _jev_block(settings: Settings) -> dict[str, object]:
    """Bloque ``jev`` de ``/health`` con el juez y el modo del runtime si ya se construyó.

    ``/health`` nunca construye el runtime; sin él, ``mode`` es ``None`` y
    :func:`jev_health` informa la configuración (FR-024).
    """

    module = sys.modules.get("apps.api.agent_runtime")
    runtime = getattr(module, "_runtime", None)
    if runtime is None:
        return jev_health(settings)
    mode = getattr(runtime, "mode", None)
    return jev_health(
        settings, getattr(runtime, "judge", None), mode=mode if isinstance(mode, str) else None
    )


def _voice_block(app: FastAPI, settings: Settings, request: Request) -> dict[str, object]:
    """Bloque ``voice`` de ``/health`` (spec 047): sin red ni secretos y sin construir nada.

    El consumo del día y el último error solo los ve el operador (``/health`` es público).
    """

    runtime = getattr(app.state, "voice", None)
    detail = operator_authorized(request, settings=settings)
    return voice_health(settings, getattr(runtime, "client", None), detail=detail)


def _photo_block(app: FastAPI, settings: Settings) -> dict[str, object]:
    """Bloque ``food_photo`` de ``/health`` (spec 051): sin red; no cambia el estado global."""

    classifier = getattr(app.state, "food_classifier", None)
    if classifier is None and hasattr(app.state, "food_classifier"):
        # Construido (o inyectado) sin clasificador: apagado aunque haya clave.
        return {"status": "off", "calls_today": 0}
    return food_photo_health(settings, classifier)


def _log_webhook_status(settings: Settings) -> None:
    """Deja constancia del webhook de mensajería al arrancar, sin números ni secretos."""

    if not settings.webhook_secret.strip():
        return
    if webhooks.allows_any_sender(settings):
        # Modo abierto: decisión explícita del operador, visible en cada arranque.
        logger.warning("webhook.open", reason="any_sender")
        return
    allowed = len(webhooks.allowed_numbers(settings))
    invalid = webhooks.invalid_allowlist_entries(settings)
    if invalid:
        logger.warning("webhook.allowlist_invalid_entries", invalid=invalid)
    if allowed == 0:
        logger.warning("webhook.allowlist_empty", reason="rejects_all_senders")
    else:
        logger.info("webhook.enabled", allowed_numbers=allowed)


def create_app(handlers: Handlers | None = None) -> FastAPI:
    """Crea la aplicación; sin ``handlers`` usa los reales, diferidos al primer uso."""

    force_global_tracing_off()
    configure_observability()
    # Sin Swagger público: la interfaz de la app es el formulario HTML y el
    # esquema no aporta a un visitante anónimo.
    app = FastAPI(title="Corpus Carvajal", docs_url=None, redoc_url=None, openapi_url=None)
    settings = Settings()
    app.state.settings = settings
    app.state.quota = handlers.quota if handlers is not None else _default_quota
    if not settings.operator_token.strip():
        # La lectura sigue abierta; la escritura web queda solo en la CLI.
        logger.warning("operator.write_disabled", reason="missing_operator_token")
    llm_blocker = chat_llm_blocker(settings)
    if llm_blocker is not None:
        # Mismo motivo que publica /health en ``agent.tools_unavailable_reason``: el
        # arranque no falla (``/live`` y ``/health`` siguen sirviendo), pero el aviso queda
        # una vez y sin valores de credenciales.
        logger.warning(
            "llm.provider_unconfigured",
            llm_provider=settings.llm_provider,
            reason=llm_blocker,
            detail=LLM_UNCONFIGURED_DETAIL.get(llm_blocker, llm_blocker),
        )
    _log_webhook_status(settings)
    if settings.voice_provider == "openai" and not settings.voice_server_configured:
        # Sin clave el chat sigue con la voz del navegador; el aviso queda una vez.
        logger.warning("voice.provider_unconfigured", reason="missing_openai_api_key")
    # La bandera decide si el chat ofrece los botones de marca (spec 038, US4).
    app.state.monitoring = monitoring_status(settings).enabled
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
    for module in (
        corpus,
        documents,
        chat,
        stream,
        api,
        config,
        faq,
        cotizador,
        cotizacion_pdf,
        catalogo_imagenes,
        webhooks,
        voz,
        foto,
    ):
        app.include_router(module.router)
    app.include_router(faq.page_router)

    @app.get("/live")
    def live() -> JSONResponse:
        """Vitalidad del proceso: no toca la base ni construye clientes de LLM."""

        return JSONResponse({"status": "ok"})

    @app.get("/health")
    def health(request: Request) -> JSONResponse:
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
                    "agent": agent_mode_status(settings).as_health(),
                    "jev": _jev_block(settings),
                    "voice": _voice_block(app, settings, request),
                    "food_photo": _photo_block(app, settings),
                },
                status_code=503,
            )
        index = status.index or {"status": "not_configured"}
        catalog = api.commercial_catalog_health()
        healthy = index.get("status") in {"ok", "not_configured"} and catalog["status"] == "ok"
        return JSONResponse(
            {
                "status": "ok" if healthy else "degraded",
                "documents": status.documents,
                "chunks": status.chunks,
                "index": index,
                "catalog": catalog,
                "monitoring": monitoring_status(settings).as_health(),
                # Modo del agente según la configuración, sin construir el runtime (que es
                # perezoso): ``mode_expected=routes`` con ``mode_configured=tools`` avisa
                # de que el modo ``tools`` no arrancará. No cambia el estado: ``routes``
                # atiende igual.
                "agent": agent_mode_status(settings).as_health(),
                # Spec 046 (FR-024): Jev sin llamar a TypeSafe; no cambia el estado global
                # porque el asistente atiende igual sin Jev.
                "jev": _jev_block(settings),
                # Spec 047: voz del chat por el servidor; opcional, no cambia el estado.
                "voice": _voice_block(app, settings, request),
                # Spec 051: fotos de alimentos con Jev; opcional, no cambia el estado.
                "food_photo": _photo_block(app, settings),
            },
            status_code=200 if healthy else 503,
        )

    @app.get("/")
    def index() -> RedirectResponse:
        """La portada redirige al chat: la pestaña Asistente ya no se muestra."""

        return RedirectResponse(url="/chat", status_code=303)

    return app


app = create_app()
