"""Servicios compartidos por el shell web y sus vistas.

Se construyen de forma diferida (``cache``) para que importar el módulo no abra
conexiones ni lea el entorno: las pruebas inyectan dobles y no tocan la red.
Los clientes de embeddings y LLM se crean además en la primera pregunta, nunca
al arrancar ni al comprobar la vitalidad del contenedor.
"""

from functools import cache
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from carvajal.adapters.embeddings import OllamaEmbedder
from carvajal.adapters.llm import build_llm_provider
from carvajal.adapters.storage.database import build_engine, build_session_factory
from carvajal.application.ports import Embedder, LLMProvider
from carvajal.infrastructure.settings import Settings

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")

__all__ = [
    "APP_DIR",
    "ask_components",
    "embedder",
    "llm_provider",
    "session_factory",
    "templates",
]


@cache
def session_factory() -> sessionmaker[Session]:
    """Fábrica de sesiones síncronas contra la base configurada."""

    return build_session_factory(build_engine(Settings().database_url))


@cache
def ask_components() -> tuple[Embedder, LLMProvider]:
    """Embedder y LLM, creados en la primera pregunta.

    Importar LangChain tarda segundos y construir el proveedor puede fallar por
    configuración (por ejemplo, una clave ausente). Ni una cosa ni la otra deben
    afectar a ``/health``, ``/live`` o al arranque del contenedor.
    """

    return embedder(), llm_provider()


@cache
def embedder() -> OllamaEmbedder:
    """Embedder del corpus; se crea una sola vez por proceso."""

    return OllamaEmbedder()


@cache
def llm_provider() -> LLMProvider:
    """Proveedor de LLM configurado; se crea una sola vez por proceso."""

    return build_llm_provider(Settings())
