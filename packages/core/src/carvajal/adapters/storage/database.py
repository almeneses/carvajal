"""Motor y fábrica de sesiones síncronas para el almacén relacional.

Los repositorios recibirán una ``Session`` y harán ``flush()``; el ``commit()``
es responsabilidad de la capa que abre la unidad de trabajo.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from carvajal.infrastructure.settings import Settings

__all__ = ["build_engine", "build_session_factory", "normalize_database_url"]

_PREFIXES = (
    "postgresql+psycopg2://",
    "postgresql://",
    "postgres://",
)


def normalize_database_url(url: str) -> str:
    """Fuerza el driver psycopg 3, sin tocar URLs que ya lo usan.

    Acepta las formas que dejan los servicios gestionados y las herramientas
    antiguas (``postgresql://``, ``postgres://``, ``postgresql+psycopg2://``) y
    las reescribe como ``postgresql+psycopg://``. La contraseña y los parámetros
    de consulta se conservan tal cual, incluidos los caracteres escapados.
    """

    for prefix in _PREFIXES:
        if url.startswith(prefix):
            return f"postgresql+psycopg://{url.removeprefix(prefix)}"
    return url


def build_engine(database_url: str | None = None) -> Engine:
    """Crea el motor síncrono, usando ``Settings`` cuando no se da una URL."""

    url = normalize_database_url(database_url or Settings().database_url)
    return create_engine(url, pool_pre_ping=True)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Devuelve la fábrica de sesiones; no expira objetos al hacer ``commit()``."""

    return sessionmaker(bind=engine, expire_on_commit=False)
