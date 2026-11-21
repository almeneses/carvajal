"""Lectura y fragmentación de los Markdown Gold, compartidas por los almacenes.

El documento y el fragmento del corpus se construyen aquí y solo aquí: el
importador relacional y el indexado vectorial reutilizan este módulo para que un
cambio de formato o de *chunking* no pueda divergir entre PostgreSQL y Qdrant.
El módulo no conoce SQLAlchemy ni Qdrant: solo lee un archivo y produce
entidades de dominio.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from carvajal.domain.chunking import CHUNK_OVERLAP, CHUNK_SIZE
from carvajal.domain.entities import Chunk, Document
from carvajal.domain.frontmatter import parse_front_matter, strip_front_matter
from carvajal.domain.rules import content_hash, section_from_url

__all__ = [
    "GoldImportError",
    "load_document",
    "load_documents",
    "split_document",
]


class GoldImportError(ValueError):
    """El corpus Gold no es importable: slug inválido, duplicado o archivo corrupto."""


def load_document(path: Path, *, layer: str) -> Document:
    """Construye el documento de dominio a partir de un Markdown Gold.

    El *front matter* manda: ``slug`` (o el nombre del archivo), ``hash``,
    ``captured_at``, ``section``, ``title`` y la procedencia ``origin``,
    ``category`` y ``entity``. La capa la impone el llamador. Un ``captured_at``
    ausente, inválido o sin zona horaria cae a la mtime en UTC;
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
        origin=front_matter.get("origin") or "",
        category=front_matter.get("category") or "",
        entity=front_matter.get("entity") or "",
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


def load_documents(directory: Path, *, layer: str) -> list[Document]:
    """Carga y valida todos los Markdown de un directorio antes de escribir nada.

    Un directorio inexistente y un slug repetido en el mismo directorio son
    errores: escribir el primero y descubrir el segundo dejaría el corpus a
    medio importar.

    Raises:
        GoldImportError: si el directorio no existe o dos archivos publican el
            mismo slug.
    """

    if not directory.is_dir():
        raise GoldImportError(f"{directory}: el directorio Gold no existe o no es un directorio.")
    loaded: list[Document] = []
    sources: dict[str, Path] = {}
    for path in sorted(directory.glob("*.md")):
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
