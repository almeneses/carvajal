"""Redirección a HTTPS de las navegaciones que llegan por la IP y el puerto publicados.

En producción la app se publica en ``0.0.0.0:APP_PORT`` y, además, nginx del host la
sirve por HTTPS en el dominio de ``PUBLIC_BASE_URL``. Quien abre ``http://IP:8080`` queda
fuera de un contexto seguro y el navegador le niega el micrófono (dictado por voz). Este
middleware manda a esa persona, con un 307, a la misma ruta bajo ``PUBLIC_BASE_URL``.

Solo redirige navegaciones de navegador (GET/HEAD que aceptan ``text/html``) cuyo
``Host`` no es el público. Nunca toca ``/live``, ``/health``, ``/api/*`` (webhooks
incluidos), ``/mcp``, ``/static``, el PDF firmado ni las fotos del catálogo, ni a quien
llega por loopback (túnel SSH, healthchecks, ``deploy.sh``): ``localhost`` ya es un
contexto seguro. Decidir por ``Host`` y no por el esquema evita bucles aunque
``X-Forwarded-Proto`` no llegue. Sin ``PUBLIC_BASE_URL`` https o con
``HTTPS_REDIRECT=false`` no se instala.

Traslado de la identidad anónima: los hilos y las cotizaciones web cuelgan de las
cookies ``carvajal_visitante`` y ``carvajal_sesion``, que el navegador separa por host.
Para que quien ya usaba la IP no pierda su historial al cambiar de host, el 307 lleva
``carvajal_traslado``: un testigo firmado con el secreto de la identidad que caduca en
dos minutos. El host público lo verifica, fija esas cookies solo si el navegador aún
no tiene las suyas (nunca pisa una identidad existente) y redirige a la misma URL sin
el parámetro. ``identidad.js`` adopta después la cookie de visitante en su
``localStorage``.
"""

import hmac
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from apps.api.chat.identity import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    VISITOR_COOKIE,
    ChatIdentity,
    default_identity,
)

__all__ = ["TRANSFER_PARAM", "HttpsRedirectMiddleware", "PublicOrigin", "public_origin"]

# Parámetro de la URL que lleva la identidad anónima del host de la IP al público.
TRANSFER_PARAM = "carvajal_traslado"
# Vigencia del testigo: basta para seguir el 307 y acota su reutilización.
_TRANSFER_TTL_SECONDS = 120
_VISITOR_RE = re.compile(r"^[0-9a-f]{64}$")
_OWNER_RE = re.compile(r"^[0-9a-f]{32,64}$")

_HTTPS_PORT = 443
_NAVIGATION_METHODS = frozenset({"GET", "HEAD"})
# Hosts de loopback: túnel SSH del ensayo, healthchecks del contenedor y deploy.sh.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# Rutas exactas o prefijos (con ``/`` de frontera) que nunca se redirigen: sondas,
# API y webhooks, MCP, estáticos y recursos que descargan clientes no navegadores
# (OpenClaw baja el PDF por ``MEDIA_BASE_URL``, que puede ser http con IP).
_EXEMPT_PREFIXES = (
    "/live",
    "/health",
    "/api",
    "/mcp",
    "/static",
    "/cotizaciones",
    "/catalogo/imagenes",
)


@dataclass(frozen=True)
class PublicOrigin:
    """Origen HTTPS público: base para el ``Location`` y host para no redirigirse a sí mismo."""

    base: str
    hostname: str
    port: int


def public_origin(public_base_url: str) -> PublicOrigin | None:
    """Devuelve el origen público si ``public_base_url`` es una URL https válida.

    Vacía, con http o mal formada devuelve ``None``: la redirección no se instala.
    """

    raw = public_base_url.strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    return PublicOrigin(
        base=raw.rstrip("/"),
        hostname=parts.hostname.lower(),
        port=port or _HTTPS_PORT,
    )


def _host_parts(host: str) -> tuple[str, int | None] | None:
    """Separa ``Host`` en nombre (en minúsculas) y puerto; ``None`` si no se entiende."""

    try:
        parts = urlsplit(f"//{host}")
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname.lower(), port


def _exempt_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _EXEMPT_PREFIXES)


class HttpsRedirectMiddleware:
    """Redirige con 307 a ``PUBLIC_BASE_URL`` las navegaciones que llegan por otro host.

    Es ASGI puro, como el resto de la pila, para no interferir con el streaming (SSE).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        origin: PublicOrigin,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.app = app
        self._origin = origin
        self._clock = clock

    def _is_public_host(self, host: str) -> bool:
        parts = _host_parts(host)
        if parts is None:
            return False
        hostname, port = parts
        return hostname == self._origin.hostname and port in {None, self._origin.port}

    def _should_redirect(self, scope: Scope) -> bool:
        if str(scope.get("method", "")).upper() not in _NAVIGATION_METHODS:
            return False
        # Destino en forma absoluta (``GET http://otro/x``): ningún navegador la manda a
        # un servidor de origen; no se arma un ``Location`` con ella.
        if not str(scope.get("path", "")).startswith("/"):
            return False
        # Ya es seguro (X-Forwarded-Proto de nginx creído por uvicorn): nada que hacer.
        if scope.get("scheme") == "https":
            return False
        if _exempt_path(str(scope.get("path", ""))):
            return False
        headers = Headers(scope=scope)
        if "text/html" not in headers.get("accept", "").lower():
            return False
        # htmx y fetch no son navegaciones: su página ya se cargó (y ya se redirigió).
        if headers.get("hx-request"):
            return False
        mode = headers.get("sec-fetch-mode", "").lower()
        if mode and mode != "navigate":
            return False
        host = headers.get("host", "")
        parts = _host_parts(host)
        if parts is None:
            return False
        if parts[0] in _LOOPBACK_HOSTS:
            return False
        # Mismo host público (con su puerto o sin puerto): nunca se redirige, así no hay
        # bucles aunque el esquema del proxy no llegue.
        return not self._is_public_host(host)

    def _location(self, scope: Scope, *, extra: str = "") -> str:
        """``PUBLIC_BASE_URL`` + ruta + query sin traslado previo (+ ``extra``)."""

        raw_path = scope.get("raw_path")
        path = raw_path.decode("latin-1") if raw_path else str(scope.get("path", ""))
        # Solo rutas de origen: nada de forma absoluta pegada tras el dominio público.
        if not path.startswith("/"):
            path = "/"
        query = "&".join(part for part in [*_query_without_transfer(scope), extra] if part)
        return f"{self._origin.base}{path}" + (f"?{query}" if query else "")

    def _transfer_token(self, identity: ChatIdentity, headers: Headers) -> str | None:
        """Testigo firmado con la identidad del host actual, o ``None`` si no hay."""

        cookies = _cookies(headers)
        visitor = cookies.get(VISITOR_COOKIE, "").strip().lower()
        visitor = visitor if _VISITOR_RE.fullmatch(visitor) else ""
        owner = _signed_owner(identity, cookies.get(COOKIE_NAME, ""))
        if not visitor and not owner:
            return None
        expires = int(self._clock()) + _TRANSFER_TTL_SECONDS
        return identity.issue(f"traslado-{expires}-{visitor}-{owner}")

    def _read_transfer(self, identity: ChatIdentity, token: str) -> tuple[str, str] | None:
        """Devuelve ``(visitante, dueño)`` si el testigo es auténtico y vigente."""

        payload, _, _ = token.partition(".")
        if not hmac.compare_digest(identity.issue(payload).encode(), token.encode()):
            return None
        kind, _, rest = payload.partition("-")
        expires, _, rest = rest.partition("-")
        visitor, _, owner = rest.partition("-")
        if kind != "traslado" or not expires.isdigit():
            return None
        if int(expires) < self._clock():
            return None
        if visitor and not _VISITOR_RE.fullmatch(visitor):
            return None
        if owner and not _OWNER_RE.fullmatch(owner):
            return None
        return visitor, owner

    async def _accept_transfer(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Fija la identidad trasladada si falta y quita el parámetro de la URL."""

        headers = Headers(scope=scope)
        identity = _identity(scope)
        response = RedirectResponse(self._location(scope), status_code=307)
        transfer = self._read_transfer(identity, _transfer_value(scope))
        if transfer is not None:
            visitor, owner = transfer
            cookies = _cookies(headers)
            secure = scope.get("scheme") == "https"
            # Nunca se pisa una identidad que el navegador ya tiene en el host público.
            if visitor and not _VISITOR_RE.fullmatch(
                cookies.get(VISITOR_COOKIE, "").strip().lower()
            ):
                # Sin HttpOnly: identidad.js la lee y la copia en localStorage.
                response.set_cookie(
                    VISITOR_COOKIE,
                    visitor,
                    max_age=COOKIE_MAX_AGE,
                    samesite="lax",
                    secure=secure,
                    path="/",
                )
            if owner and not _signed_owner(identity, cookies.get(COOKIE_NAME, "")):
                response.set_cookie(
                    COOKIE_NAME,
                    identity.issue(owner),
                    max_age=COOKIE_MAX_AGE,
                    httponly=True,
                    samesite="lax",
                    secure=secure,
                    path="/",
                )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self._should_redirect(scope):
            token = self._transfer_token(_identity(scope), Headers(scope=scope))
            extra = f"{TRANSFER_PARAM}={token}" if token else ""
            response = RedirectResponse(self._location(scope, extra=extra), status_code=307)
            await response(scope, receive, send)
            return
        if (
            _transfer_value(scope)
            and str(scope.get("method", "")).upper() in _NAVIGATION_METHODS
            and str(scope.get("path", "")).startswith("/")
            and self._is_public_host(Headers(scope=scope).get("host", ""))
        ):
            await self._accept_transfer(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _identity(scope: Scope) -> ChatIdentity:
    """La identidad inyectada en ``app.state.chat_identity`` o la del proceso."""

    app = scope.get("app")
    injected = getattr(getattr(app, "state", None), "chat_identity", None)
    return injected if injected is not None else default_identity()


def _cookies(headers: Headers) -> dict[str, str]:
    jar = SimpleCookie()
    try:
        jar.load(headers.get("cookie", ""))
    except CookieError:  # una cabecera Cookie rota equivale a no tener cookies
        return {}
    return {name: morsel.value for name, morsel in jar.items()}


def _signed_owner(identity: ChatIdentity, raw: str) -> str:
    """Dueño de la cookie firmada si la firma cuadra y tiene el formato esperado."""

    owner, separator, _ = raw.partition(".")
    if not separator or not _OWNER_RE.fullmatch(owner):
        return ""
    return owner if hmac.compare_digest(identity.issue(owner).encode(), raw.encode()) else ""


def _query_parts(scope: Scope) -> list[str]:
    query = bytes(scope.get("query_string", b"")).decode("latin-1")
    return [part for part in query.split("&") if part]


def _query_without_transfer(scope: Scope) -> list[str]:
    return [part for part in _query_parts(scope) if part.partition("=")[0] != TRANSFER_PARAM]


def _transfer_value(scope: Scope) -> str:
    for part in _query_parts(scope):
        name, _, value = part.partition("=")
        if name == TRANSFER_PARAM:
            return value
    return ""
