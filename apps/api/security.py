"""Endurecimiento del shell: CSRF por doble cookie y cabeceras de seguridad.

El token CSRF vive en una cookie ``HttpOnly`` y se incrusta en el formulario; el
POST debe repetirlo. Comparar ambos en bytes impide que un sitio externo envíe
un formulario en nombre del visitante y evita que un token no ASCII rompa la
comparación en tiempo constante.
"""

import base64
import hmac
import secrets
import time
from hashlib import sha256
from http.cookies import SimpleCookie

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from carvajal.infrastructure.settings import Settings

__all__ = [
    "CABECERAS",
    "CSRF_COOKIE",
    "CSRF_FIELD",
    "HSTS",
    "OPERATOR_COOKIE",
    "OPERATOR_COOKIE_MAX_AGE",
    "PERMISOS_CHAT",
    "PERMISOS_POR_DEFECTO",
    "SecurityMiddleware",
    "build_csrf_secret",
    "clear_operator_cookie",
    "csrf_context",
    "issue_operator_cookie",
    "issue_token",
    "operator_authorized",
    "operator_configured",
    "operator_token",
    "politica_permisos",
    "set_operator_cookie",
    "valid_operator_cookie",
    "valid_token",
    "verify_csrf",
]

CSRF_COOKIE = "carvajal_csrf"
CSRF_FIELD = "csrf_token"
CSRF_MAX_AGE = 60 * 60 * 12  # doce horas
_STATIC_PREFIX = "/static"

# Sesión corta del operador: el token de despliegue se cambia en el entorno y
# la cookie firmada evita repetirlo en cada formulario y en el SSE de captura.
OPERATOR_COOKIE = "carvajal_operator"
OPERATOR_COOKIE_MAX_AGE = 60 * 60 * 8  # ocho horas

# El micrófono queda cerrado en todo el sitio; solo el documento HTML del chat
# lo habilita para su propio origen (spec 040). La excepción no toca CSP ni
# frame-ancestors, y exige contexto seguro en el navegador.
PERMISOS_POR_DEFECTO = "geolocation=(), microphone=(), camera=()"
PERMISOS_CHAT = "geolocation=(), microphone=(self), camera=()"


def politica_permisos(ruta: str, metodo: str) -> str:
    """Devuelve ``Permissions-Policy`` según la ruta y el método.

    Se limita al documento navegable del chat (GET/HEAD ``/chat``). El SSE del
    chat, sus POST, los parciales y el resto del sitio conservan el micrófono
    denegado.
    """

    if ruta == "/chat" and metodo.upper() in {"GET", "HEAD"}:
        return PERMISOS_CHAT
    return PERMISOS_POR_DEFECTO


# Secreto de reserva cuando no hay SESSION_SECRET (desarrollo y pruebas). El
# token va firmado para que una cookie fijada por un tercero no sirva: sin la
# firma del proceso, el middleware la descarta y emite una nueva.
_PROCESS_SECRET = secrets.token_hex(32)

CABECERAS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": PERMISOS_POR_DEFECTO,
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-src https://www.openstreetmap.org; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
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


def build_csrf_secret(settings: Settings | None = None) -> bytes:
    """Devuelve el secreto de firma del token CSRF.

    Usa ``SESSION_SECRET`` cuando existe para que el token sobreviva reinicios y
    varios workers compartan la firma. Sin él, cada proceso usa un secreto
    aleatorio: es suficiente para desarrollo y pruebas, no para producción.
    """

    secret = (settings or Settings()).session_secret.strip()
    return (secret or _PROCESS_SECRET).encode("utf-8")


def issue_token(secret: bytes) -> str:
    """Genera ``<nonce>.<firma>`` con entropía nueva."""

    nonce = secrets.token_urlsafe(32)
    return f"{nonce}.{_signature(secret, nonce)}"


def valid_token(secret: bytes, token: str) -> bool:
    """Comprueba que el token lo emitió este proceso (defensa contra *fixation*).

    Un atacante puede fijar la cookie ``carvajal_csrf`` desde un subdominio,
    pero no puede calcular la firma: el middleware la rechaza y emite otra.
    """

    nonce, separator, signature = token.partition(".")
    if not separator or not nonce or not signature:
        return False
    try:
        expected = _signature(secret, nonce)
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(expected, signature)


def _signature(secret: bytes, nonce: str) -> str:
    """HMAC-SHA256 hexadecimal de la parte aleatoria del token."""

    return hmac.new(secret, nonce.encode("ascii"), sha256).hexdigest()


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


def operator_token(settings: Settings | None = None) -> str:
    """Token compartido de operador; vacío deshabilita la escritura web."""

    return (settings or Settings()).operator_token.strip()


def operator_configured(settings: Settings | None = None) -> bool:
    """Indica si el despliegue habilitó la carga y la captura web."""

    return bool(operator_token(settings))


def _secure_equal(expected: str, candidate: str) -> bool:
    """Compara dos secretos en tiempo constante, sin romper con Unicode."""

    if not expected or not candidate:
        return False
    try:
        return hmac.compare_digest(expected.encode("utf-8"), candidate.encode("utf-8"))
    except UnicodeEncodeError:
        return False


def issue_operator_cookie(
    settings: Settings | None = None,
    *,
    now: int | None = None,
) -> str:
    """Firma una caducidad con el token de operador.

    La cookie no contiene el token: solo ``<caducidad>.<firma>``. Cambiar el
    token invalida todas las sesiones emitidas, sin tocar la base.
    """

    token = operator_token(settings)
    if not token:
        raise ValueError("No hay token de operador configurado.")
    expiry = int(time.time() if now is None else now) + OPERATOR_COOKIE_MAX_AGE
    signature = hmac.new(
        token.encode("utf-8"),
        f"operator:{expiry}".encode("ascii"),
        sha256,
    ).hexdigest()
    return f"{expiry}.{signature}"


def valid_operator_cookie(
    value: str,
    settings: Settings | None = None,
    *,
    now: int | None = None,
) -> bool:
    """Comprueba la firma y la vigencia de la cookie de operador."""

    token = operator_token(settings)
    if not token or not value:
        return False
    expiry_text, separator, signature = value.partition(".")
    if not separator or not expiry_text or not signature:
        return False
    try:
        expiry = int(expiry_text)
    except ValueError:
        return False
    if expiry <= int(time.time() if now is None else now):
        return False
    expected = hmac.new(
        token.encode("utf-8"),
        f"operator:{expiry}".encode("ascii"),
        sha256,
    ).hexdigest()
    try:
        return hmac.compare_digest(expected, signature)
    except UnicodeEncodeError:
        return False


def _basic_password(header: str) -> str:
    """Extrae la contraseña de un ``Authorization: Basic`` bien formado."""

    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded.strip():
        return ""
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""
    _, _, password = decoded.partition(":")
    return password


def operator_authorized(
    request: Request,
    token: str = "",
    *,
    settings: Settings | None = None,
) -> bool:
    """Autoriza al operador por formulario, cabecera o cookie firmada.

    El token del formulario es el mecanismo histórico de Configuración. Las
    cabeceras ``Authorization: Bearer``, ``Authorization: Basic`` y
    ``X-Operator-Token`` permiten automatizar la operación sin acoplar la CLI
    al HTML. La cookie firmada la emite un POST autorizado y sirve al SSE.
    """

    expected = operator_token(settings)
    if not expected:
        return False
    if _secure_equal(expected, (token or "").strip()):
        return True
    if _secure_equal(expected, request.headers.get("x-operator-token", "").strip()):
        return True
    authorization = request.headers.get("authorization", "").strip()
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and _secure_equal(expected, value.strip()):
            return True
        if _secure_equal(expected, _basic_password(authorization)):
            return True
    return valid_operator_cookie(request.cookies.get(OPERATOR_COOKIE, ""), settings)


def set_operator_cookie(
    response: Response,
    request: Request,
    settings: Settings | None = None,
) -> None:
    """Renueva la sesión de operador tras una autenticación correcta."""

    if not operator_configured(settings):
        return
    response.set_cookie(
        OPERATOR_COOKIE,
        issue_operator_cookie(settings),
        max_age=OPERATOR_COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )


def clear_operator_cookie(response: Response) -> None:
    """Cierra la sesión de operador en el navegador."""

    response.delete_cookie(OPERATOR_COOKIE, path="/")


class SecurityMiddleware:
    """Emite el token CSRF y añade las cabeceras de seguridad a la respuesta.

    Es ASGI puro para no interferir con el streaming (SSE en W3.4) y para que
    las cabeceras lleguen también a respuestas de error. Las páginas con token
    no se cachean; los estáticos conservan su caché y no reciben la cookie.
    """

    def __init__(self, app: ASGIApp, *, secret: bytes | None = None) -> None:
        self.app = app
        self._secret = secret or build_csrf_secret()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        estado = scope.setdefault("state", {})
        token = request.cookies.get(CSRF_COOKIE, "")
        nueva = not valid_token(self._secret, token)
        if nueva:
            token = issue_token(self._secret)
        estado["csrf_token"] = token
        secure = request.url.scheme == "https"
        estatico = str(scope.get("path", "")).startswith(_STATIC_PREFIX)

        async def enviar(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.update(CABECERAS)
                headers["Permissions-Policy"] = politica_permisos(
                    str(scope.get("path", "")), str(scope.get("method", ""))
                )
                if secure:
                    headers["Strict-Transport-Security"] = HSTS
                if not estatico:
                    # Un token viejo servido desde una caché compartida no vale.
                    # Asignación directa: setdefault dejaría pasar un valor
                    # previo (por ejemplo, ``public``) y cachearía la página.
                    headers["Cache-Control"] = "no-store"
                if nueva and not estatico:
                    headers.append("set-cookie", _cookie(token, secure=secure))
            await send(message)

        await self.app(scope, receive, enviar)
