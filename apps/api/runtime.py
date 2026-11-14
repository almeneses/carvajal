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

from apps.api.rendering import render_answer
from apps.api.security import csrf_context
from carvajal.adapters.embeddings import OllamaEmbedder
from carvajal.adapters.llm import build_llm_provider
from carvajal.adapters.storage.database import build_engine, build_session_factory
from carvajal.adapters.transcription import SpeachesTranscriber
from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.application.ports import Embedder, LLMProvider
from carvajal.application.transcription import Transcriber
from carvajal.infrastructure.settings import Settings

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(
    directory=APP_DIR / "templates",
    context_processors=[csrf_context],
)
templates.env.filters["respuesta"] = render_answer

__all__ = [
    "APP_DIR",
    "ask_components",
    "embedder",
    "llm_provider",
    "qdrant_store",
    "session_factory",
    "templates",
    "transcriber",
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

    return OllamaEmbedder(base_url=Settings().ollama_base_url)


@cache
def llm_provider() -> LLMProvider:
    """Proveedor de LLM configurado; se crea una sola vez por proceso."""

    return build_llm_provider(Settings())


@cache
def transcriber() -> Transcriber | None:
    """Cliente de voz a texto o ``None`` si la función está desactivada.

    No se construye ningún cliente HTTP al importar ni al arrancar: solo se
    crea el adaptador cuando el endpoint de transcripción lo pide.
    """

    settings = Settings()
    if not settings.transcription_url.strip():
        return None
    return SpeachesTranscriber(
        settings.transcription_url,
        settings.transcription_model,
        api_key=settings.transcription_api_key,
        timeout_seconds=settings.transcription_timeout_seconds,
    )


@cache
def qdrant_store() -> QdrantVectorStore:
    """Cliente de Qdrant para readiness; no abre colección ni indexa."""

    settings = Settings()
    return QdrantVectorStore(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        collection=settings.qdrant_collection,
        dimensions=settings.qdrant_dimensions,
        distance=settings.qdrant_distance,
    )
