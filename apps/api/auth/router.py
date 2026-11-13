"""Rutas de acceso con Google: login, callback y logout."""

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from apps.api.auth import google
from apps.api.auth.deps import AUTH_COOKIE, get_auth_repository, huella
from apps.api.security import CSRF_INVALIDO, verify_csrf
from apps.api.signing import dumps, loads
from carvajal.adapters.storage.auth import SqlAuthRepository
from carvajal.infrastructure.settings import Settings

__all__ = ["OAUTH_COOKIE", "router"]

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

OAUTH_COOKIE = "carvajal_oauth"
OAUTH_MAX_AGE = 600  # diez minutos para completar el consentimiento


def _error(mensaje: str, status_code: int = 400) -> Response:
    """Página mínima de error del flujo de acceso, sin datos del proveedor."""

    return HTMLResponse(
        '<!doctype html><html lang="es"><meta charset="utf-8" />'
        "<title>Acceso</title>"
        f"<p>{mensaje}</p>"
        '<p><a href="/">Volver al asistente</a></p></html>',
        status_code=status_code,
    )


@router.get("/login")
def login(request: Request) -> Response:
    """Redirige a Google con ``state`` y PKCE; el estado vive en una cookie firmada."""

    settings = Settings()
    if not settings.google_client_id or not settings.google_client_secret:
        logger.warning("auth.not_configured")
        return _error("El acceso con Google no está configurado.", 503)

    estado = secrets.token_urlsafe(24)
    verificador, reto = google.pkce()
    destino = google.authorization_url(
        client_id=settings.google_client_id,
        redirect_uri=settings.auth_redirect_uri,
        state=estado,
        challenge=reto,
    )
    respuesta = RedirectResponse(destino, status_code=302)
    respuesta.set_cookie(
        OAUTH_COOKIE,
        dumps({"state": estado, "verifier": verificador}),
        max_age=OAUTH_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/auth",
    )
    return respuesta


@router.get("/callback")
def callback(
    request: Request,
    repositorio: Annotated[SqlAuthRepository, Depends(get_auth_repository)],
    code: str = "",
    state: str = "",
) -> Response:
    """Canjea el código, crea el usuario si hace falta y abre una sesión."""

    if not code or not state:
        return _error("Google no devolvió el código de acceso.")

    datos = loads(request.cookies.get(OAUTH_COOKIE, ""))
    if datos is None:
        logger.warning("auth.state_rejected")
        return _error("La sesión de acceso expiró. Inténtalo de nuevo.")
    esperado = str(datos.get("state", ""))
    if not esperado or not hmac.compare_digest(esperado, state):
        logger.warning("auth.state_rejected")
        return _error("La sesión de acceso expiró. Inténtalo de nuevo.")

    settings = Settings()
    try:
        token = google.access_token(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=settings.auth_redirect_uri,
            code=code,
            verifier=str(datos.get("verifier", "")),
        )
        perfil = google.userinfo(token)
    except google.AuthError as error:
        logger.warning("auth.exchange_failed", error=str(error))
        return _error("No se pudo completar el acceso con Google.", 502)

    usuario = repositorio.upsert_user(
        provider="google",
        subject=perfil.subject,
        email=perfil.email,
        name=perfil.name,
    )
    sesion = secrets.token_urlsafe(32)
    repositorio.create_session(
        session_id=huella(sesion),
        user_id=usuario.id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.auth_session_days),
    )
    logger.info("auth.login", user_id=usuario.id)

    respuesta = RedirectResponse("/", status_code=303)
    respuesta.set_cookie(
        AUTH_COOKIE,
        sesion,
        max_age=settings.auth_session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    respuesta.delete_cookie(OAUTH_COOKIE, path="/auth")
    return respuesta


@router.post("/logout")
def logout(
    request: Request,
    repositorio: Annotated[SqlAuthRepository, Depends(get_auth_repository)],
    csrf_token: str = Form(default=""),
) -> Response:
    """Revoca la sesión y borra la cookie; exige token CSRF."""

    if not verify_csrf(request, csrf_token):
        return _error(CSRF_INVALIDO, 403)

    token = request.cookies.get(AUTH_COOKIE, "")
    if token:
        repositorio.revoke_session(huella(token))
        logger.info("auth.logout")

    respuesta = RedirectResponse("/", status_code=303)
    respuesta.delete_cookie(AUTH_COOKIE, path="/")
    return respuesta
