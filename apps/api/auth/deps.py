"""Dependencias de autenticación: repositorio, cookie y usuario actual."""

from collections.abc import Iterator
from functools import cache
from hashlib import sha256
from typing import Annotated, Protocol

import structlog
from fastapi import Depends, Request
from sqlalchemy.exc import SQLAlchemyError

from carvajal.adapters.storage.auth import SessionUser, SqlAuthRepository, User
from carvajal.adapters.storage.database import build_engine, build_session_factory
from carvajal.infrastructure.settings import Settings

__all__ = [
    "AUTH_COOKIE",
    "current_user",
    "current_user_from_request",
    "get_auth_repository",
    "huella",
]

logger = structlog.get_logger(__name__)

AUTH_COOKIE = "carvajal_auth"


class UserLookup(Protocol):
    """Lectura mínima que necesita el resolutor de sesión."""

    def find_session(self, session_id: str) -> SessionUser | None:
        """Devuelve la sesión válida o ``None``."""

        ...


@cache
def _session_factory():
    settings = Settings()
    return build_session_factory(build_engine(settings.database_url))


def huella(token: str) -> str:
    """Hash con el que se guarda la sesión: el token nunca toca la base."""

    return sha256(token.encode()).hexdigest()


def get_auth_repository(request: Request) -> Iterator[SqlAuthRepository]:
    """Repositorio por petición; las pruebas inyectan uno falso en ``app.state``."""

    inyectado = getattr(request.app.state, "auth_repository", None)
    if inyectado is not None:
        yield inyectado
        return
    with _session_factory().begin() as session:
        yield SqlAuthRepository(session)


def _resolver(request: Request, repositorio: UserLookup) -> User | None:
    if getattr(request.state, "user_cargado", False):
        return getattr(request.state, "user", None)
    token = request.cookies.get(AUTH_COOKIE, "")
    usuario = None
    if token:
        fila = repositorio.find_session(huella(token))
        usuario = fila.user if fila is not None else None
    request.state.user = usuario
    request.state.user_cargado = True
    return usuario


def current_user(
    request: Request,
    repositorio: Annotated[SqlAuthRepository, Depends(get_auth_repository)],
) -> User | None:
    """Usuario de la sesión, o ``None`` si no hay cookie válida."""

    return _resolver(request, repositorio)


def current_user_from_request(request: Request) -> User | None:
    """Variante para procesadores de plantilla, que no pueden usar ``Depends``.

    Si la base no responde, la página sigue sirviéndose en modo anónimo: la
    portada no es el lugar para un 500 por una consulta de sesión.
    """

    if getattr(request.state, "user_cargado", False):
        return getattr(request.state, "user", None)

    if not request.cookies.get(AUTH_COOKIE):
        request.state.user = None
        request.state.user_cargado = True
        return None

    inyectado = getattr(request.app.state, "auth_repository", None)
    try:
        if inyectado is not None:
            return _resolver(request, inyectado)
        with _session_factory().begin() as session:
            return _resolver(request, SqlAuthRepository(session))
    except (SQLAlchemyError, OSError) as error:
        logger.warning("auth.resolve_failed", error=str(error))
        request.state.user = None
        request.state.user_cargado = True
        return None
