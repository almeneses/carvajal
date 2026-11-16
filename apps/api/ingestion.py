"""Ingesta de documentos subidos por la interfaz de configuración.

La carga reutiliza el pipeline del corpus: guarda el original en Bronze, el
Markdown limpio en Silver y el documento con *front matter* en Gold; después
importa Gold a PostgreSQL y reconstruye el índice de Qdrant. Todo se ejecuta
con las mismas piezas de la CLI para que una carga y una captura produzcan el
mismo formato.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog

from apps.api.document_formats import (
    EXTRACTED_EXTENSIONS,
    HTML_EXTENSIONS,
    TEXT_EXTENSIONS,
    ExtractError,
    extract_markdown,
)
from carvajal.domain.rules import content_hash, estimate_tokens
from carvajal.gold import merge_index
from carvajal.infrastructure.settings import Settings

__all__ = [
    "ALLOWED_EXTENSIONS",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "IngestReport",
    "UploadedDocument",
    "importar_gold",
    "indexar_gold",
    "ingest_uploads",
]

logger = structlog.get_logger(__name__)

ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | HTML_EXTENSIONS | EXTRACTED_EXTENSIONS
MAX_FILES = 100
MAX_FILE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class UploadedDocument:
    """Archivo recibido por el formulario, todavía sin procesar."""

    filename: str
    data: bytes
    content_type: str = ""


@dataclass(frozen=True)
class IngestReport:
    """Resultado de una carga: etapas, conteos y errores recuperables."""

    files: int = 0
    bronze: int = 0
    silver: int = 0
    gold: int = 0
    documents: int = 0
    chunks: int = 0
    indexed: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Indica si al menos un documento llegó a Gold e indexación."""

        return self.gold > 0 and not self.errors


def _slug(filename: str, digest: str) -> str:
    """Slug estable por contenido, seguro para el front matter y la base."""

    base = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.lower()).strip("-")
    return f"{base or 'documento'}-{digest[:8]}"


def _limpiar_titulo(titulo: str) -> str:
    """Recorta el título a una línea apta para el front matter."""

    return " ".join(titulo.split())[:140] or "Documento sin título"


def _gold(
    *,
    slug: str,
    titulo: str,
    markdown: str,
    digest: str,
    captured_at: datetime,
    source_type: str = "colaborador",
) -> str:
    """Construye el Markdown Gold con procedencia de carga local."""

    return (
        "---\n"
        f"url: upload://{slug}\n"
        f"title: {_limpiar_titulo(titulo)}\n"
        "section: Cargas\n"
        "domain: carga-local\n"
        f"source_type: {source_type}\n"
        f"captured_at: {captured_at.isoformat()}\n"
        f"hash: {digest}\n"
        f"chars: {len(markdown)}\n"
        "---\n\n"
        f"{markdown.strip()}\n"
    )


def importar_gold(gold_dir: Path, only: set[str]) -> tuple[int, int]:
    """Importa Gold a PostgreSQL y devuelve ``(documentos, fragmentos)``."""

    from apps.api.runtime import session_factory
    from carvajal.adapters.storage.import_corpus import migrate_gold

    with session_factory().begin() as session:
        report = migrate_gold(session, gold_dir=gold_dir, prune=False, only=only)
    return report.documents, report.chunks


def indexar_gold(gold_dir: Path, only: set[str]) -> int:
    """Reindexa Gold en Qdrant y devuelve los puntos indexados."""

    from apps.api.runtime import embedder
    from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
    from carvajal.indexing.qdrant import index_gold

    settings = Settings()
    store = QdrantVectorStore(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        collection=settings.qdrant_collection,
        dimensions=settings.qdrant_dimensions,
        distance=settings.qdrant_distance,
    )
    try:
        return index_gold(
            gold_dir=gold_dir,
            store=store,
            embedder=embedder(),
            prune=False,
            only=only,
        ).indexed
    finally:
        store.close()


def ingest_uploads(
    documents: list[UploadedDocument],
    *,
    settings: Settings | None = None,
    importar: Callable[[Path, set[str]], tuple[int, int]] | None = None,
    indexar: Callable[[Path, set[str]], int] | None = None,
    max_file_bytes: int = MAX_FILE_BYTES,
    solo_extraer: bool = False,
    source_type: str = "colaborador",
) -> IngestReport:
    """Ejecuta Bronze → Silver → Gold → PostgreSQL → Qdrant para las cargas.

    ``importar`` e ``indexar`` permiten inyectar las etapas finales en pruebas
    sin tocar PostgreSQL ni Qdrant. ``solo_extraer`` se detiene en Gold y
    ``max_file_bytes`` ajusta el límite por archivo de la consola.
    """

    settings = settings or Settings()
    errors: list[str] = []
    archivos = bronze = silver = gold = 0
    slugs: set[str] = set()
    entries: list[dict[str, str | int]] = []
    captured_at = datetime.now(UTC)

    if len(documents) > MAX_FILES:
        errors.append(f"Solo se procesan {MAX_FILES} archivos por carga.")
        documents = documents[:MAX_FILES]

    settings.gold_dir.mkdir(parents=True, exist_ok=True)
    for document in documents:
        nombre = Path(document.filename or "").name
        if not nombre:
            errors.append("Se recibió un archivo sin nombre.")
            continue
        sufijo = Path(nombre).suffix.lower()
        if sufijo not in ALLOWED_EXTENSIONS:
            errors.append(f"{nombre}: formato no admitido ({sufijo or 'sin extensión'}).")
            continue
        if len(document.data) > max_file_bytes:
            errors.append(f"{nombre}: supera el límite de {max_file_bytes // 1024 // 1024} MB.")
            continue
        if not document.data.strip():
            errors.append(f"{nombre}: está vacío.")
            continue

        try:
            markdown, titulo = extract_markdown(nombre, document.data)
        except ExtractError as error:
            errors.append(f"{nombre}: {error}")
            continue
        if not markdown.strip():
            errors.append(f"{nombre}: no se extrajo texto legible.")
            continue

        digest = content_hash(markdown)
        slug = _slug(nombre, digest)
        raw_path = settings.raw_dir / "uploads" / f"{slug}{sufijo}"
        silver_path = settings.processed_dir / "uploads" / f"{slug}.md"
        gold_path = settings.gold_dir / f"{slug}.md"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        silver_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(document.data)
        silver_path.write_text(markdown.strip() + "\n", encoding="utf-8")
        gold_path.write_text(
            _gold(
                slug=slug,
                titulo=titulo,
                markdown=markdown,
                digest=digest,
                captured_at=captured_at,
                source_type=source_type,
            ),
            encoding="utf-8",
        )
        entries.append(
            {
                "url": f"upload://{slug}",
                "title": _limpiar_titulo(titulo),
                "slug": slug,
                "section": "Cargas",
                "domain": "carga-local",
                "source_type": source_type,
                "captured_at": captured_at.isoformat(),
                "hash": digest,
                "chars": len(markdown),
                "tokens_estimate": estimate_tokens(markdown),
            }
        )
        slugs.add(slug)
        archivos += 1
        bronze += 1
        silver += 1
        gold += 1
        logger.info("upload.staged", document=slug, filename=nombre)

    if entries:
        # Una sola fusión por lote: con cientos de archivos, reescribir el
        # índice por archivo multiplica el trabajo y la ventana de bloqueo.
        merge_index(settings.index_file, entries, gold_dir=settings.gold_dir)

    if gold == 0 or solo_extraer:
        return IngestReport(
            files=archivos,
            bronze=bronze,
            silver=silver,
            gold=gold,
            errors=tuple(errors),
        )

    documents_count = 0
    chunks = 0
    import_ok = False
    try:
        documents_count, chunks = (importar or importar_gold)(settings.gold_dir, slugs)
        import_ok = True
    except Exception as error:  # la UI informa el fallo de la etapa
        logger.warning("upload.import_failed", error=str(error))
        errors.append("No se pudo importar a PostgreSQL; revisa la conexión y los logs.")

    indexed = 0
    if import_ok:
        try:
            indexed = (indexar or indexar_gold)(settings.gold_dir, slugs)
        except Exception as error:  # Ollama o Qdrant pueden no estar disponibles
            logger.warning("upload.index_failed", error=str(error))
            errors.append("No se pudo indexar en Qdrant; revisa Qdrant y Ollama.")
    else:
        errors.append("Se omitió la indexación porque PostgreSQL no recibió los documentos.")

    return IngestReport(
        files=archivos,
        bronze=bronze,
        silver=silver,
        gold=gold,
        documents=documents_count,
        chunks=chunks,
        indexed=indexed,
        errors=tuple(errors),
    )
