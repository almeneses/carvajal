"""Identidad anónima del chat: cookie firmada con HMAC-SHA256.

No es autenticación: solo delimita a qué dueño pertenece una conversación. La
cookie se renueva sin error si falta o si la firma no cuadra.

Si ``Settings.session_secret`` está vacío se usa un secreto aleatorio por
proceso. En producción debe fijarse ``SESSION_SECRET`` para que las cookies
sobrevivan reinicios y todos los workers compartan la misma clave.
"""

import hmac
import secrets
from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from typing import Protocol, runtime_checkable
from uuid import uuid4

from fastapi import Request

from carvajal.infrastructure.settings import Settings

__all__ = [
    "COOKIE_MAX_AGE",
    "COOKIE_NAME",
    "ChatIdentity",
    "SignedCookieIdentity",
    "build_identity",
    "default_identity",
]

COOKIE_NAME = "carvajal_sesion"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # seis meses

# Secreto de reserva cuando no hay SESSION_SECRET configurado. Es estable
# mientras viva el proceso; no sirve para varias instancias ni para reinicios.
_PROCESS_SECRET = secrets.token_hex(32)


@runtime_checkable
class ChatIdentity(Protocol):
    """Contrato mínimo que consume la vista para leer y emitir identidades."""

    def read(self, request: Request) -> str | None:
        """Devuelve el dueño si la cookie existe y la firma es válida."""

        ...

    def issue(self, owner_key: str) -> str:
        """Devuelve el valor firmado que se guarda en la cookie."""

        ...

    def new_owner_key(self) -> str:
        """Genera un identificador opaco nuevo."""

        ...


@dataclass(frozen=True)
class SignedCookieIdentity:
    """Firma y verifica el identificador anónimo con HMAC-SHA256."""

    secret: bytes
    cookie_name: str = COOKIE_NAME
    max_age: int = COOKIE_MAX_AGE

    def read(self, request: Request) -> str | None:
        """Lee la cookie de la petición y devuelve el dueño si es válida."""

        raw = request.cookies.get(self.cookie_name)
        return None if raw is None else self.verify(raw)

    def issue(self, owner_key: str) -> str:
        """Construye el valor ``<owner_key>.<hmac_sha256>``."""

        return f"{owner_key}.{self._signature(owner_key)}"

    def verify(self, value: str) -> str | None:
        """Verifica la firma y devuelve el dueño, o ``None`` si no cuadra."""

        owner_key, separator, signature = value.partition(".")
        if not separator or not owner_key:
            return None
        if not hmac.compare_digest(signature.encode(), self._signature(owner_key).encode()):
            return None
        return owner_key

    def new_owner_key(self) -> str:
        """Genera un identificador opaco de 32 caracteres hexadecimales."""

        return uuid4().hex

    def _signature(self, owner_key: str) -> str:
        """HMAC-SHA256 hexadecimal del identificador."""

        return hmac.new(self.secret, owner_key.encode("utf-8"), sha256).hexdigest()


def build_identity(settings: Settings | None = None) -> SignedCookieIdentity:
    """Construye la identidad con el secreto configurado o el de reserva."""

    secret = (settings or Settings()).session_secret.strip()
    return SignedCookieIdentity((secret or _PROCESS_SECRET).encode("utf-8"))


@cache
def default_identity() -> SignedCookieIdentity:
    """Identidad del proceso, creada una sola vez."""

    return build_identity()
