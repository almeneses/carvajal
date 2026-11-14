"""Identidad anónima del chat: cookie firmada con HMAC-SHA256.

No es autenticación: solo delimita a qué dueño pertenece una conversación. La
cookie se renueva sin error si falta o si la firma no cuadra.

Si ``Settings.session_secret`` está vacío se usa un secreto aleatorio por
proceso. En producción debe fijarse ``SESSION_SECRET`` para que las cookies
sobrevivan reinicios y todos los workers compartan la misma clave.
"""

import hmac
import re
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
    "VISITOR_COOKIE",
    "ChatIdentity",
    "SignedCookieIdentity",
    "build_identity",
    "default_identity",
]

COOKIE_NAME = "carvajal_sesion"
VISITOR_COOKIE = "carvajal_visitante"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # seis meses
_VISITOR_RE = re.compile(r"^[0-9a-f]{64}$")

# Secreto de reserva cuando no hay SESSION_SECRET configurado. Es estable
# mientras viva el proceso; no sirve para varias instancias ni para reinicios.
_PROCESS_SECRET = secrets.token_hex(32)


@runtime_checkable
class ChatIdentity(Protocol):
    """Contrato mínimo que consume la vista para leer y emitir identidades."""

    def read(self, request: Request) -> str | None:
        """Devuelve el dueño del visitante o de la cookie firmada."""

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
    visitor_cookie: str = VISITOR_COOKIE
    max_age: int = COOKIE_MAX_AGE

    def read(self, request: Request) -> str | None:
        """Devuelve el dueño derivado del visitante o de la cookie firmada.

        La identidad de localStorage se envía en ``carvajal_visitante``. El
        dueño de la base no es ese valor: se deriva con HMAC para que el hash
        del navegador no se use directamente como clave. Si la cookie de
        visitante falta o no tiene el formato esperado, se conserva el
        comportamiento histórico de la cookie firmada.
        """

        visitor = request.cookies.get(self.visitor_cookie, "").strip().lower()
        if _VISITOR_RE.fullmatch(visitor):
            return self._visitor_owner(visitor)
        raw = request.cookies.get(self.cookie_name)
        return None if raw is None else self.verify(raw)

    def _visitor_owner(self, visitor: str) -> str:
        """Deriva un dueño estable y opaco a partir del hash de localStorage."""

        return hmac.new(
            self.secret,
            f"visitante:{visitor}".encode("ascii"),
            sha256,
        ).hexdigest()

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
