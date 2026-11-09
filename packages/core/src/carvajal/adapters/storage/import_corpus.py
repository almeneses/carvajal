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


@dataclass(frozen=True)
class GoldImportReport:
    """Totales de una corrida de importación."""

    documents: int
    chunks: int


def load_document(path: Path, *, layer: str) -> Document:
    """Construye el documento de dominio a partir de un Markdown Gold.

    El *front matter* manda: ``slug`` (o el nombre del archivo), ``hash``,
    ``captured_at``, ``section`` y ``title``. La capa la impone el llamador. Un
    ``captured_at`` ausente, inválido o sin zona horaria cae a la mtime en UTC.
    """

    content = path.read_text(encoding="utf-8")
    front_matter = parse_front_matter(content)
    slug = front_matter.get("slug") or path.stem
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

    Solo toca la capa indicada: el corpus extendido se importa en otro paso.
    Reimportar no duplica documentos, fragmentos ni versiones; solo el cambio
    de cuerpo archiva una versión.
    """

    documents = SqlDocumentRepository(session)
    chunks = SqlChunkRepository(session)
    total_documents = total_chunks = 0
    for path in sorted(gold_dir.glob("*.md")):
        document = load_document(path, layer=layer)
        document_chunks = split_document(document)
        documents.upsert(document)
        chunks.replace(document.id, document_chunks)
        total_documents += 1
        total_chunks += len(document_chunks)
    return GoldImportReport(documents=total_documents, chunks=total_chunks)


def _captured_at(front_matter: dict[str, str], path: Path) -> datetime:
    """Devuelve la fecha del front matter o, si no sirve, la mtime en UTC."""

    raw = front_matter.get("captured_at", "")
    if raw:
        try:
            captured = datetime.fromisoformat(raw)
        except ValueError:
            captured = None
        if captured is not None and captured.tzinfo is not None:
            return captured
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
