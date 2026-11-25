"""Servicios compartidos por el shell web y sus vistas.

Se construyen de forma diferida (``cache``) para que importar el módulo no abra
conexiones ni lea el entorno: las pruebas inyectan dobles y no tocan la red.
Los clientes de embeddings y LLM se crean además en la primera pregunta, nunca
al arrancar ni al comprobar la vitalidad del contenedor.
"""

from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import structlog
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from apps.api.rendering import render_answer
from apps.api.security import csrf_context
from carvajal.adapters.embeddings import OllamaEmbedder
from carvajal.adapters.llm import build_llm_provider
from carvajal.adapters.storage.database import build_engine, build_session_factory
from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.application.ports import Embedder, LLMProvider
from carvajal.infrastructure.settings import Settings

APP_DIR = Path(__file__).resolve().parent
logger = structlog.get_logger(__name__)
templates = Jinja2Templates(
    directory=APP_DIR / "templates",
    context_processors=[csrf_context],
)
templates.env.filters["respuesta"] = render_answer


def _spoken_summary(message: object) -> str:
    """Resumen hablado de una respuesta del hilo (``data-voz-resumen``); vacío si falla.

    Se importa al usarse: ``apps.api.voice`` arrastra el puente de WhatsApp y el cliente
    de voz, que esta capa compartida no necesita al arrancar.
    """

    from apps.api.voice import spoken_message
    from carvajal.adapters.storage.chat import ChatMessage

    if not isinstance(message, ChatMessage):
        return ""
    try:
        return spoken_message(message)
    except Exception as error:  # un resumen roto no impide pintar el hilo: se lee el DOM
        # Queda en el log, como en el stream: un fallo sistemático no pasa inadvertido.
        logger.warning(
            "chat.spoken_summary_failed", error_type=type(error).__name__, source="thread"
        )
        return ""


templates.env.filters["resumen_hablado"] = _spoken_summary

BOGOTA = ZoneInfo("America/Bogota")


def _local_date(value: object) -> str:
    """Fecha ISO (UTC) como ``AAAA-MM-DD`` en hora de Bogotá; si no se entiende, se deja.

    Una cotización emitida a las 8 p. m. en Bogotá ya es el día siguiente en UTC: cortar
    el texto ISO mostraba «mañana» como fecha de emisión.
    """

    text = str(value or "")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text[:10]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(BOGOTA).date().isoformat()


def _catalog_photo(sku: object) -> str:
    """URL de la foto del SKU (``/catalogo/imagenes``) o ``""`` si no tiene foto."""

    from carvajal.catalog.images import catalog_image

    code = str(sku or "")
    if not code or len(code) > 64:
        return ""
    try:
        return f"/catalogo/imagenes/{quote(code)}.jpg" if catalog_image(code) else ""
    # Una foto nunca tumba el turno: un manifiesto ilegible o mal codificado (OSError,
    # UnicodeDecodeError, csv.Error…) deja la tarjeta con el ícono de su familia.
    except Exception as error:
        logger.warning("catalog.photo_failed", error_type=type(error).__name__)
        return ""


def _readable_label(value: object) -> str:
    """Slug legible para la interfaz: ``canal-clientes`` → ``Canal clientes``.

    Solo sube la primera letra; el resto se conserva (siglas y nombres propios no se
    bajan, como haría ``capitalize``).
    """

    text = str(value or "").replace("-", " ").replace("_", " ").strip()
    return text[:1].upper() + text[1:]


templates.env.filters["fecha_local"] = _local_date
templates.env.filters["legible"] = _readable_label
templates.env.filters["foto_catalogo"] = _catalog_photo

__all__ = [
    "APP_DIR",
    "ask_components",
    "embedder",
    "llm_provider",
    "qdrant_store",
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

    return OllamaEmbedder(base_url=Settings().ollama_base_url)


@cache
def llm_provider() -> LLMProvider:
    """Proveedor de LLM configurado; se crea una sola vez por proceso."""

    return build_llm_provider(Settings())


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
