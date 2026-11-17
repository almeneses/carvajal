"""Ingesta de documentos subidos por la interfaz de configuración.

La carga reutiliza el pipeline del corpus: guarda el original en Bronze, el
Markdown limpio en Silver y el documento con *front matter* en Gold; después
importa Gold a PostgreSQL y reconstruye el índice de Qdrant. Todo se ejecuta
con las mismas piezas de la CLI para que una carga y una captura produzcan el
mismo formato.
"""

from __future__ import annotations

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
from carvajal.application.sync import CorpusLayer, SyncReport, sync_corpus
from carvajal.domain.rules import content_hash, estimate_tokens
from carvajal.gold import merge_index
from carvajal.identity import (
    assign_slug,
    raw_hash,
    slug_for_file,
    source_key_for_upload,
)
from carvajal.infrastructure.settings import Settings
from carvajal.registry import Registry, corpus_areas, load_registry

__all__ = [
    "ALLOWED_EXTENSIONS",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "IngestReport",
    "UploadedDocument",
    "ingest_uploads",
    "sync_gold",
]

logger = structlog.get_logger(__name__)

ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | HTML_EXTENSIONS | EXTRACTED_EXTENSIONS
MAX_FILES = 100
MAX_FILE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class UploadedDocument:
    """Archivo recibido por el formulario, todavía sin procesar.

    ``source_key`` es la identidad canónica de la fuente. La consola de carpetas
    la calcula con la ruta relativa; en una subida web queda vacía y se deriva
    del nombre del archivo.
    """

    filename: str
    data: bytes
    content_type: str = ""
    source_key: str = ""


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
    duplicates: tuple[str, ...] = field(default_factory=tuple)
    unchanged: tuple[str, ...] = field(default_factory=tuple)
    updated: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Indica si la carga terminó sin errores y con trabajo reconocido.

        Una reimportación sin cambios o con solo duplicados es un éxito: no
        escribió nada porque ya estaba todo en el corpus.
        """

        return not self.errors and (self.gold > 0 or bool(self.duplicates) or bool(self.unchanged))


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


def sync_gold(gold_dir: Path, only: set[str]) -> SyncReport:
    """Sincroniza los slugs cargados con PostgreSQL y Qdrant."""

    from apps.api.runtime import embedder, session_factory
    from carvajal.adapters.storage.sync_postgres import PostgresSync
    from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
    from carvajal.adapters.vector.sync_qdrant import QdrantSync

    settings = Settings()
    store = QdrantVectorStore(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        collection=settings.qdrant_collection,
        dimensions=settings.qdrant_dimensions,
        distance=settings.qdrant_distance,
    )
    try:
        return sync_corpus(
            [CorpusLayer("core", (gold_dir,))],
            database=PostgresSync(session_factory()),
            index=QdrantSync(store, embedder()),
            only=only,
            lock_root=settings.gold_dir.parent,
        )
    finally:
        store.close()


def _registry(settings: Settings) -> Registry:
    """Carga el registro del núcleo y de las capas extendidas del corpus."""

    return load_registry(corpus_areas(settings.gold_dir.parent))


def ingest_uploads(
    documents: list[UploadedDocument],
    *,
    settings: Settings | None = None,
    sincronizar: Callable[[Path, set[str]], SyncReport] | None = None,
    max_file_bytes: int = MAX_FILE_BYTES,
    solo_extraer: bool = False,
    source_type: str = "colaborador",
) -> IngestReport:
    """Ejecuta Bronze → Silver → Gold → PostgreSQL → Qdrant para las cargas.

    ``sincronizar`` permite inyectar la etapa final en pruebas sin tocar
    PostgreSQL ni Qdrant. ``solo_extraer`` se detiene en Gold y
    ``max_file_bytes`` ajusta el límite por archivo de la consola.
    """

    settings = settings or Settings()
    errors: list[str] = []
    duplicates: list[str] = []
    unchanged: list[str] = []
    updated: list[str] = []
    archivos = bronze = silver = gold = 0
    slugs: set[str] = set()
    entries: list[dict[str, str | int]] = []
    captured_at = datetime.now(UTC)
    registro = _registry(settings)
    ocupados = dict(registro.slug_owners())
    lote_fuentes: dict[str, str] = {}
    lote_raw: dict[str, str] = {}
    lote_texto: dict[str, str] = {}

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

        source_key = document.source_key or source_key_for_upload(nombre)
        digest_raw = raw_hash(document.data)
        existente = registro.by_source_key(source_key)

        previo = lote_fuentes.get(source_key)
        if previo is not None and previo != digest_raw:
            errors.append(
                f"{nombre}: la fuente {source_key} ya se procesó en esta carga con otro "
                "contenido; se omite."
            )
            continue
        if previo is not None:
            duplicates.append(f"{nombre}: duplicado exacto de {source_key} en esta carga.")
            continue
        if existente is not None and existente.raw_hash == digest_raw:
            unchanged.append(nombre)
            logger.info("upload.unchanged", document=existente.slug, filename=nombre)
            continue
        copia = registro.by_raw_hash(digest_raw)
        if copia is not None and copia.source_key != source_key:
            duplicates.append(f"{nombre}: copia exacta de {copia.slug}.")
            continue
        copia_lote = lote_raw.get(digest_raw)
        if copia_lote is not None:
            duplicates.append(f"{nombre}: copia exacta de {copia_lote} en esta carga.")
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
        if existente is not None and existente.content_hash == digest:
            unchanged.append(nombre)
            logger.info("upload.unchanged", document=existente.slug, filename=nombre)
            continue
        semantico = registro.by_content_hash(digest)
        if semantico is not None and semantico.source_key != source_key:
            duplicates.append(f"{nombre}: el texto ya existe en {semantico.slug}.")
            continue
        fuente_lote = lote_texto.get(digest)
        if fuente_lote is not None and fuente_lote != source_key:
            duplicates.append(
                f"{nombre}: el texto ya existe en otra fuente de esta carga ({fuente_lote})."
            )
            continue

        if existente is not None:
            slug = existente.slug
            updated.append(nombre)
        else:
            slug = assign_slug(slug_for_file(nombre), source_key, ocupados)
            ocupados[slug] = source_key

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
                "source_key": source_key,
                "raw_hash": digest_raw,
                "layer": "core",
                "updated_at": captured_at.isoformat(),
            }
        )
        lote_fuentes[source_key] = digest_raw
        lote_raw[digest_raw] = slug
        lote_texto[digest] = source_key
        slugs.add(slug)
        archivos += 1
        bronze += 1
        silver += 1
        gold += 1
        logger.info("upload.staged", document=slug, filename=nombre, updated=existente is not None)

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
            duplicates=tuple(duplicates),
            unchanged=tuple(unchanged),
            updated=tuple(updated),
            errors=tuple(errors),
        )

    documents_count = chunks = indexed = 0
    try:
        sync_report = (sincronizar or sync_gold)(settings.gold_dir, slugs)
        documents_count = sync_report.documents
        chunks = sync_report.chunks
        indexed = sync_report.indexed
        errors.extend(sync_report.errors)
    except Exception as error:  # la UI informa el fallo de la etapa
        logger.warning("upload.sync_failed", error=str(error))
        errors.append("No se pudo sincronizar el corpus; revisa la conexión y los logs.")

    return IngestReport(
        files=archivos,
        bronze=bronze,
        silver=silver,
        gold=gold,
        documents=documents_count,
        chunks=chunks,
        indexed=indexed,
        duplicates=tuple(duplicates),
        unchanged=tuple(unchanged),
        updated=tuple(updated),
        errors=tuple(errors),
    )
