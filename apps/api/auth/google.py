"""Cliente mínimo de Google OIDC: autorización, canje y perfil.

No se valida el ID token en local: el código se canjea contra Google por TLS y
el perfil se lee del endpoint ``userinfo`` con el access token, que es la fuente
autoritativa. ``state`` protege el flujo y PKCE (S256) ata el código al cliente.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

__all__ = [
    "AuthError",
    "GoogleUser",
    "access_token",
    "authorization_url",
    "pkce",
    "userinfo",
]

AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPE = "openid email profile"
TIMEOUT = 10.0


class AuthError(RuntimeError):
    """El proveedor no completó el intercambio o no devolvió un perfil válido."""


@dataclass(frozen=True)
class GoogleUser:
    """Perfil mínimo que la app guarda del proveedor."""

    subject: str
    email: str
    name: str


def pkce() -> tuple[str, str]:
    """Genera el verificador y el reto S256 del flujo."""

    verificador = secrets.token_urlsafe(64)
    resumen = hashlib.sha256(verificador.encode()).digest()
    reto = base64.urlsafe_b64encode(resumen).rstrip(b"=").decode()
    return verificador, reto


def authorization_url(*, client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    """URL de la pantalla de consentimiento de Google."""

    parametros = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{AUTORIZAR}?{urlencode(parametros)}"


def access_token(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    verifier: str,
) -> str:
    """Canjea el código por un access token."""

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            respuesta = client.post(
                TOKEN,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                },
            )
            respuesta.raise_for_status()
            datos = respuesta.json()
    except (httpx.HTTPError, ValueError) as error:
        raise AuthError("No se pudo canjear el código de Google.") from error

    token = datos.get("access_token")
    if not isinstance(token, str) or not token:
        raise AuthError("Google no devolvió un access token.")
    return token


def userinfo(token: str) -> GoogleUser:
    """Lee el perfil asociado al access token."""

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            respuesta = client.get(USERINFO, headers={"Authorization": f"Bearer {token}"})
            respuesta.raise_for_status()
            datos = respuesta.json()
    except (httpx.HTTPError, ValueError) as error:
        raise AuthError("No se pudo leer el perfil de Google.") from error

    subject = datos.get("sub")
    email = datos.get("email")
    if not subject or not email:
        raise AuthError("Google no devolvió un perfil válido.")
    return GoogleUser(
        subject=str(subject),
        email=str(email),
        name=str(datos.get("name") or email),
    )
