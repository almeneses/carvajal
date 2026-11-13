"""Observabilidad del shell: logs JSON, ``request_id`` y Sentry.

El objetivo es poder seguir una petición de principio a fin sin abrir el código:
cada línea de log lleva el mismo ``request_id`` que devuelve la respuesta, y los
errores no controlados se envían a Sentry cuando hay ``SENTRY_DSN``.
"""

import re
import time
import uuid
from collections.abc import Callable
from logging import getLevelNamesMapping

import structlog
from fastapi import Request, Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from carvajal.infrastructure.settings import Settings

__all__ = [
    "REQUEST_ID_HEADER",
    "RequestContextMiddleware",
    "UnhandledErrorMiddleware",
    "capture_exception",
    "configure_observability",
    "init_sentry",
]

REQUEST_ID_HEADER = "X-Request-ID"
_VALIDO = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_ESTATICOS = "/static"

logger = structlog.get_logger("carvajal.http")
_iniciado = False
_sentry_iniciado = False

ErrorHandler = Callable[[Request, Exception], Response]


def _configurar_logs(level: str) -> None:
    """Configura structlog para emitir una línea JSON por evento."""

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getLevelNamesMapping().get(level.upper(), 20)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def init_sentry(settings: Settings) -> bool:
    """Inicializa Sentry si hay DSN; devuelve si quedó activo.

    ``send_default_pii=False`` evita enviar cookies, IPs o cabeceras con datos
    personales. El muestreo de trazas es explícito: por defecto, cero.
    """

    global _sentry_iniciado
    if not settings.sentry_dsn:
        return False
    if _sentry_iniciado:
        return True

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
    _sentry_iniciado = True
    return True


def capture_exception(error: BaseException) -> None:
    """Envía una excepción ya controlada a Sentry, si está activo.

    Los errores de proveedor convertidos en 503 no llegan solos a Sentry porque
    la vista los captura; esta llamada deja la traza sin cambiar la respuesta.
    """

    if not _sentry_iniciado:
        return
    import sentry_sdk

    sentry_sdk.capture_exception(error)


def configure_observability(settings: Settings | None = None) -> None:
    """Configura logs y Sentry una sola vez, al crear la aplicación."""

    global _iniciado
    if _iniciado:
        return
    settings = settings or Settings()
    _configurar_logs(settings.log_level)
    init_sentry(settings)
    _iniciado = True


def _request_id(scope: Scope) -> str:
    """Usa el ``X-Request-ID`` recibido si es válido; si no, genera uno."""

    recibido = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
    if _VALIDO.match(recibido):
        return recibido
    return uuid.uuid4().hex


def _etiquetar_sentry(request_id: str) -> None:
    """Adjunta el ``request_id`` al evento de Sentry de esta petición."""

    if not _sentry_iniciado:
        return
    import sentry_sdk

    sentry_sdk.set_tag("request_id", request_id)


class RequestContextMiddleware:
    """Fija el ``request_id``, lo devuelve en la respuesta y registra el acceso.

    Es ASGI puro para no interferir con respuestas en streaming (SSE en W3.4).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        _etiquetar_sentry(request_id)
        inicio = time.perf_counter()
        estado = {"codigo": 0}

        async def enviar(message: Message) -> None:
            if message["type"] == "http.response.start":
                estado["codigo"] = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, enviar)
        finally:
            campos = {
                "request_id": request_id,
                "method": scope.get("method", ""),
                "path": scope.get("path", ""),
                "status": estado["codigo"],
                "duration_ms": round((time.perf_counter() - inicio) * 1000, 1),
            }
            # Los estáticos no aportan a la traza de una petición de negocio.
            if str(scope.get("path", "")).startswith(_ESTATICOS):
                logger.debug("request.completed", **campos)
            else:
                logger.info("request.completed", **campos)
            structlog.contextvars.clear_contextvars()


class UnhandledErrorMiddleware:
    """Convierte una excepción no controlada en una respuesta controlada.

    Vive dentro de ``RequestContextMiddleware`` para que el 500 también lleve
    ``X-Request-ID`` y, cuando exista, las cabeceras de seguridad. Sustituye a
    propósito al ``ServerErrorMiddleware`` de Starlette: la excepción se registra
    y se envía a Sentry de forma explícita, y la respuesta 500 atraviesa la pila
    interna en lugar de saltársela. Si la respuesta ya empezó (SSE), se re-lanza
    para no intentar enviar un segundo cuerpo.
    """

    def __init__(self, app: ASGIApp, handler: ErrorHandler) -> None:
        self.app = app
        self.handler = handler

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        iniciada = False

        async def enviar(message: Message) -> None:
            nonlocal iniciada
            if message["type"] == "http.response.start":
                iniciada = True
            await send(message)

        try:
            await self.app(scope, receive, enviar)
        except Exception as error:
            # Se registra siempre, incluso si la respuesta ya empezó y no se
            # puede sustituir por un 500 (SSE).
            capture_exception(error)
            logger.exception("request.unhandled", error=str(error))
            if iniciada:
                # La respuesta ya empezó (SSE): no se puede enviar otro 500.
                raise
            request = Request(scope, receive)
            response = self.handler(request, error)
            await response(scope, receive, enviar)
