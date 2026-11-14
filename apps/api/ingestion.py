"""Ingesta de documentos subidos por la interfaz de configuración.

La carga reutiliza el pipeline del corpus: guarda el original en Bronze, el
Markdown limpio en Silver y el documento con *front matter* en Gold; después
importa Gold a PostgreSQL y reconstruye el índice de Qdrant. Todo se ejecuta
con las mismas piezas de la CLI para que una carga y una captura produzcan el
mismo formato.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import structlog

from carvajal.domain.frontmatter import strip_front_matter
from carvajal.domain.rules import content_hash, estimate_tokens
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

ALLOWED_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm"}
MAX_FILES = 100
MAX_FILE_BYTES = 5 * 1024 * 1024
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
    "ol",
}
_TITLE_RE = re.compile(r"^\s*#{1,6}\s+(.+)$", re.MULTILINE)


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


class _HtmlText(HTMLParser):
    """Extrae texto visible y título de un HTML sin dependencias externas."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        self._ignorar = 0
        self._en_titulo = False
        self.titulo = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._ignorar += 1
            return
        if tag == "title":
            self._en_titulo = True
            return
        if tag in _BLOCK_TAGS:
            self._partes.append("\n")
        elif tag in {"span", "a", "strong", "em", "b", "i", "code"}:
            self._partes.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._ignorar = max(0, self._ignorar - 1)
            return
        if tag == "title":
            self._en_titulo = False
            return
        if tag in _BLOCK_TAGS:
            self._partes.append("\n")
        elif tag in {"span", "a", "strong", "em", "b", "i", "code"}:
            self._partes.append(" ")

    def handle_data(self, data: str) -> None:
        if self._ignorar:
            return
        if self._en_titulo:
            self.titulo += data
        else:
            self._partes.append(data)

    def texto(self) -> str:
        """Normaliza las líneas y conserva párrafos separados."""

        lineas = [linea.strip() for linea in "".join(self._partes).splitlines()]
        return "\n\n".join(linea for linea in lineas if linea)


def _decode(data: bytes) -> str:
    """Decodifica UTF-8 con tolerancia para archivos de texto heredados."""

    return data.decode("utf-8-sig", errors="replace")


def _markdown(filename: str, data: bytes) -> tuple[str, str]:
    """Devuelve ``(markdown, título)`` según el tipo de archivo."""

    suffix = Path(filename).suffix.lower()
    if suffix in {".html", ".htm"}:
        parser = _HtmlText()
        parser.feed(_decode(data))
        parser.close()
        markdown = parser.texto()
        titulo = html.unescape(parser.titulo).strip()
        if not titulo:
            titulo = _titulo_desde_markdown(markdown) or Path(filename).stem
        return markdown, titulo

    markdown = strip_front_matter(_decode(data)).strip()
    return markdown, _titulo_desde_markdown(markdown) or Path(filename).stem


def _titulo_desde_markdown(markdown: str) -> str:
    """Toma el primer encabezado del Markdown si existe."""

    match = _TITLE_RE.search(markdown)
    return match.group(1).strip() if match else ""


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
) -> str:
    """Construye el Markdown Gold con procedencia de carga local."""

    return (
        "---\n"
        f"url: upload://{slug}\n"
        f"title: {_limpiar_titulo(titulo)}\n"
        "section: Cargas\n"
        "domain: carga-local\n"
        "source_type: colaborador\n"
        f"captured_at: {captured_at.isoformat()}\n"
        f"hash: {digest}\n"
        f"chars: {len(markdown)}\n"
        "---\n\n"
        f"{markdown.strip()}\n"
    )


def _merge_index(index_file: Path, entry: dict[str, object]) -> None:
    """Añade o actualiza la entrada del índice JSONL sin perder las demás."""

    index_file.parent.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, object]] = {}
    if index_file.is_file():
        for line in index_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                previous = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(previous, dict) and previous.get("slug"):
                entries[str(previous["slug"])] = previous
    entries[str(entry["slug"])] = entry
    index_file.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries.values()),
        encoding="utf-8",
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
) -> IngestReport:
    """Ejecuta Bronze → Silver → Gold → PostgreSQL → Qdrant para las cargas.

    ``importar`` e ``indexar`` permiten inyectar las etapas finales en pruebas
    sin tocar PostgreSQL ni Qdrant.
    """

    settings = settings or Settings()
    errors: list[str] = []
    archivos = bronze = silver = gold = 0
    slugs: set[str] = set()
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
        if len(document.data) > MAX_FILE_BYTES:
            errors.append(f"{nombre}: supera el límite de {MAX_FILE_BYTES // 1024 // 1024} MB.")
            continue
        if not document.data.strip():
            errors.append(f"{nombre}: está vacío.")
            continue

        markdown, titulo = _markdown(nombre, document.data)
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
            ),
            encoding="utf-8",
        )
        _merge_index(
            settings.index_file,
            {
                "url": f"upload://{slug}",
                "title": _limpiar_titulo(titulo),
                "slug": slug,
                "section": "Cargas",
                "domain": "carga-local",
                "source_type": "colaborador",
                "captured_at": captured_at.isoformat(),
                "hash": digest,
                "chars": len(markdown),
                "tokens_estimate": estimate_tokens(markdown),
            },
        )
        slugs.add(slug)
        archivos += 1
        bronze += 1
        silver += 1
        gold += 1
        logger.info("upload.staged", document=slug, filename=nombre)

    if gold == 0:
        return IngestReport(files=archivos, errors=tuple(errors))

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
