"""Importador del corpus Gold al almacén relacional (CLI: ``migrate-gold``).

Migra los Markdown de ``data/gold`` a Postgres de forma idempotente: cada
documento se identifica por su slug y cada fragmento por ``documento:posición``.
La función solo hace ``flush()``; la transacción la abre quien la invoca (la CLI).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy.orm import Session

from carvajal.adapters.storage.repositories import SqlChunkRepository, SqlDocumentRepository
from carvajal.domain.chunking import CHUNK_OVERLAP, CHUNK_SIZE
from carvajal.domain.entities import Chunk, Document
from carvajal.domain.frontmatter import parse_front_matter, strip_front_matter
from carvajal.domain.rules import content_hash, section_from_url

PAGE_SIZE = 200


class GoldImportError(ValueError):
    """El corpus Gold no es importable: slug inválido, duplicado o archivo corrupto."""


@dataclass(frozen=True)
class GoldImportReport:
    """Totales de una corrida de importación.

    ``removed`` cuenta los documentos de la capa que ya no existen en el
    directorio y se podaron de la base.
    """

    documents: int
    chunks: int
    removed: int = 0


def load_document(path: Path, *, layer: str) -> Document:
    """Construye el documento de dominio a partir de un Markdown Gold.

    El *front matter* manda: ``slug`` (o el nombre del archivo), ``hash``,
    ``captured_at``, ``section`` y ``title``. La capa la impone el llamador. Un
    ``captured_at`` ausente, inválido o sin zona horaria cae a la mtime en UTC;
    uno válido se normaliza a UTC. Un slug vacío cae al nombre del archivo y uno
    con separadores de ruta lanza ``GoldImportError``.
    """

    content = path.read_text(encoding="utf-8")
    front_matter = parse_front_matter(content)
    slug = _validated_slug(front_matter.get("slug"), path)
    url = front_matter.get("url", "")
    markdown = strip_front_matter(content)
    section = front_matter.get("section") or (section_from_url(url) if url else "home")
    return Document(
        id=slug,
        url=url,
        title=front_matter.get("title") or slug,
        slug=slug,
        section=section,
        layer=layer,
        markdown=markdown,
        content_hash=front_matter.get("hash") or content_hash(markdown),
        captured_at=_captured_at(front_matter, path),
        source_type=front_matter.get("source_type") or "oficial",
    )


def split_document(document: Document) -> list[Chunk]:
    """Fragmenta el Markdown del documento con posiciones contiguas desde 0."""

    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    texts = [text for text in splitter.split_text(document.markdown) if text.strip()]
    return [
        Chunk(
            id=f"{document.id}:{position}",
            document_id=document.id,
            text=text,
            position=position,
            section=document.section,
            layer=document.layer,
        )
        for position, text in enumerate(texts)
    ]


def migrate_gold(session: Session, *, gold_dir: Path, layer: str = "core") -> GoldImportReport:
    """Importa los ``*.md`` de ``gold_dir`` y devuelve los totales.

    El directorio es autoritativo para la capa: tras importar se podan los
    documentos de esa capa cuyo slug ya no existe en disco (p. ej. si se importa
    con ``--gold`` un subconjunto, el resto se elimina). La FK en cascada limpia
    fragmentos y versiones. Reimportar no duplica documentos ni fragmentos; solo
    el cambio de cuerpo archiva una versión. Un slug repetido aborta la corrida
    antes de escribir para no sobrescribir en silencio.
    """

    documents = SqlDocumentRepository(session)
    chunks = SqlChunkRepository(session)
    loaded = _load_documents(gold_dir, layer=layer)

    total_chunks = 0
    for document in loaded:
        document_chunks = split_document(document)
        documents.upsert(document)
        chunks.replace(document.id, document_chunks)
        total_chunks += len(document_chunks)

    removed = _prune(documents, layer=layer, keep={document.id for document in loaded})
    return GoldImportReport(documents=len(loaded), chunks=total_chunks, removed=removed)


def _load_documents(gold_dir: Path, *, layer: str) -> list[Document]:
    """Carga y valida todos los Markdown antes de tocar la base."""

    if not gold_dir.is_dir():
        raise GoldImportError(f"{gold_dir}: el directorio Gold no existe o no es un directorio.")
    loaded: list[Document] = []
    sources: dict[str, Path] = {}
    for path in sorted(gold_dir.glob("*.md")):
        document = load_document(path, layer=layer)
        previous = sources.get(document.id)
        if previous is not None:
            raise GoldImportError(
                f"{path.name}: el slug {document.id!r} ya lo usa {previous.name}."
            )
        sources[document.id] = path
        loaded.append(document)
    return loaded


def _validated_slug(raw: str | None, path: Path) -> str:
    """Normaliza el slug y rechaza valores que generarían ids ambiguos."""

    slug = (raw or "").strip() or path.stem.strip()
    if not slug:
        raise GoldImportError(f"{path.name}: no se pudo derivar un slug del archivo.")
    if "/" in slug or "\\" in slug or ".." in slug or slug == ".":
        raise GoldImportError(
            f"{path.name}: el slug {slug!r} no puede contener separadores de ruta ni '..'."
        )
    return slug


def _prune(documents: SqlDocumentRepository, *, layer: str, keep: set[str]) -> int:
    """Borra los documentos de la capa cuyo id no está en ``keep``.

    Pagina primero y borra después: borrar mientras se pagina con ``offset``
    desplazaría el resultado y dejaría huérfanos.
    """

    orphan_ids: list[str] = []
    offset = 0
    while True:
        page = documents.list(layer=layer, limit=PAGE_SIZE, offset=offset)
        orphan_ids.extend(document.id for document in page if document.id not in keep)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    for document_id in orphan_ids:
        documents.delete(document_id)
    return len(orphan_ids)


def _captured_at(front_matter: dict[str, str], path: Path) -> datetime:
    """Devuelve la fecha del front matter normalizada a UTC, o la mtime en UTC."""

    raw = front_matter.get("captured_at", "")
    if raw:
        try:
            captured = datetime.fromisoformat(raw)
        except ValueError:
            captured = None
        if captured is not None and captured.tzinfo is not None:
            return captured.astimezone(UTC)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
