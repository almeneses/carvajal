"""Identidad anónima del visitante: cookie firmada para historial y cuotas.

No es autenticación: no concede privilegios. Solo delimita a quién pertenece una
conversación y contra qué cubo se cuentan las cuotas. La cookie se firma con
``SESSION_SECRET`` y se renueva si falta o si la firma no cuadra.
"""

import secrets

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.signing import sign, verify

__all__ = ["COOKIE_NAME", "OwnerSessionMiddleware", "owner_key"]

COOKIE_NAME = "carvajal_sesion"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # seis meses
_STATIC_PREFIX = "/static"


def _cookie(value: str, *, secure: bool) -> str:
    """Construye la cabecera ``Set-Cookie`` firmada."""

    response = Response()
    response.set_cookie(
        COOKIE_NAME,
        sign(value),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return response.headers["set-cookie"]


class OwnerSessionMiddleware:
    """Asegura un identificador anónimo estable y firmado en cada petición HTTP.

    Se implementa como middleware ASGI puro para no interferir con respuestas
    en streaming (SSE en W3.4). Los estáticos no emiten cookie.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        raw = request.cookies.get(COOKIE_NAME, "")
        value = verify(raw)
        scope.setdefault("state", {})["owner_key"] = value or secrets.token_urlsafe(16)
        nueva = value is None and not scope["path"].startswith(_STATIC_PREFIX)
        secure = request.url.scheme == "https"

        async def send_with_cookie(message: Message) -> None:
            if nueva and message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.append("set-cookie", _cookie(scope["state"]["owner_key"], secure=secure))
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def owner_key(request: Request) -> str:
    """Dependencia de FastAPI: identificador anónimo del visitante."""

    return request.state.owner_key
