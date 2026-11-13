"""Endurecimiento del shell: CSRF por doble cookie y cabeceras de seguridad.

El token CSRF vive en una cookie ``HttpOnly`` y se incrusta en el formulario; el
POST debe repetirlo. Comparar ambos en bytes impide que un sitio externo envíe
un formulario en nombre del visitante y evita que un token no ASCII rompa la
comparación en tiempo constante.
"""

import hmac
import secrets
from http.cookies import SimpleCookie

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "CABECERAS",
    "CSRF_COOKIE",
    "CSRF_FIELD",
    "HSTS",
    "SecurityMiddleware",
    "csrf_context",
    "verify_csrf",
]

CSRF_COOKIE = "carvajal_csrf"
CSRF_FIELD = "csrf_token"
CSRF_MAX_AGE = 60 * 60 * 12  # doce horas
_STATIC_PREFIX = "/static"

CABECERAS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    ),
}
HSTS = "max-age=31536000; includeSubDomains"


def _cookie(valor: str, *, secure: bool) -> str:
    """Construye la cabecera ``Set-Cookie`` del token CSRF."""

    cookie = SimpleCookie()
    cookie[CSRF_COOKIE] = valor
    morsel = cookie[CSRF_COOKIE]
    morsel["httponly"] = True
    morsel["samesite"] = "lax"
    morsel["path"] = "/"
    morsel["max-age"] = str(CSRF_MAX_AGE)
    if secure:
        morsel["secure"] = True
    return morsel.OutputString()


def csrf_context(request: Request) -> dict[str, str]:
    """Expone el token del middleware a todas las plantillas."""

    return {"csrf_token": getattr(request.state, "csrf_token", "")}


def verify_csrf(request: Request, token: str) -> bool:
    """Indica si el token del formulario coincide con el de la cookie.

    La comparación se hace en bytes ASCII: ``hmac.compare_digest`` sobre ``str``
    solo admite ASCII y lanzaría ``TypeError`` con un token manipulado.
    """

    esperado = getattr(request.state, "csrf_token", "")
    if not esperado or not token:
        return False
    try:
        return hmac.compare_digest(esperado.encode("ascii"), token.encode("ascii"))
    except UnicodeEncodeError:
        return False


class SecurityMiddleware:
    """Emite el token CSRF y añade las cabeceras de seguridad a la respuesta.

    Es ASGI puro para no interferir con el streaming (SSE en W3.4) y para que
    las cabeceras lleguen también a respuestas de error. Las páginas con token
    no se cachean; los estáticos conservan su caché y no reciben la cookie.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        estado = scope.setdefault("state", {})
        token = request.cookies.get(CSRF_COOKIE, "")
        nueva = not token
        if nueva:
            token = secrets.token_urlsafe(32)
        estado["csrf_token"] = token
        secure = request.url.scheme == "https"
        estatico = str(scope.get("path", "")).startswith(_STATIC_PREFIX)

        async def enviar(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.update(CABECERAS)
                if secure:
                    headers["Strict-Transport-Security"] = HSTS
                if not estatico:
                    # Un token viejo servido desde una caché compartida no vale.
                    headers.setdefault("Cache-Control", "no-store")
                if nueva and not estatico:
                    headers.append("set-cookie", _cookie(token, secure=secure))
            await send(message)

        await self.app(scope, receive, enviar)
