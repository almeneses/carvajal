"""Puertos de la capa de aplicación: contratos que implementa la infraestructura.

Los casos de uso (``application``) dependen de estos ``Protocol`` y nunca de
``requests``, ``psycopg`` o LangChain. Cada adaptador de infraestructura debe
pasar las pruebas de contrato de ``tests/test_ports.py``.
"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from carvajal.domain.entities import Chunk, Document, DocumentVersion

__all__ = [
    "ChunkRepository",
    "DocumentRepository",
    "Extractor",
    "Fetcher",
    "LLMProvider",
    "ScoredChunk",
    "VectorStore",
]


@dataclass(frozen=True)
class ScoredChunk:
    """Resultado de una búsqueda vectorial: fragmento y puntuación de similitud."""

    chunk: Chunk
    score: float


@runtime_checkable
class DocumentRepository(Protocol):
    """Almacén de documentos del corpus (núcleo y capa extendida)."""

    def get(self, document_id: str) -> Document | None:
        """Devuelve el documento por ID, o ``None`` si no existe."""

        ...

    def get_by_slug(self, slug: str) -> Document | None:
        """Devuelve el documento por slug, o ``None`` si no existe."""

        ...

    def list(
        self,
        *,
        layer: str | None = None,
        section: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Document]:
        """Lista documentos, opcionalmente filtrados por capa y sección.

        ``limit=0`` es válido y devuelve una secuencia vacía. Lanza ``ValueError``
        si ``limit`` u ``offset`` son negativos.
        """

        ...

    def upsert(self, document: Document) -> Document:
        """Inserta o actualiza el documento y lo devuelve.

        El ``id`` es inmutable. Lanza ``ValueError`` si el ``slug`` ya pertenece
        a otro ``id``.
        """

        ...

    def list_versions(self, document_id: str) -> Sequence[DocumentVersion]:
        """Devuelve las versiones archivadas, de la más reciente a la más antigua.

        Devuelve una secuencia vacía si el documento no existe o no tiene
        versiones.
        """

        ...

    def delete(self, document_id: str) -> bool:
        """Borra el documento y su historial de versiones; devuelve si existía.

        Los fragmentos se eliminan aparte con ``ChunkRepository.delete_by_document``.
        """

        ...

    def count(self, *, layer: str | None = None) -> int:
        """Cuenta documentos, opcionalmente filtrados por capa."""

        ...


@runtime_checkable
class ChunkRepository(Protocol):
    """Almacén de fragmentos asociados a un documento."""

    def list_by_document(self, document_id: str) -> Sequence[Chunk]:
        """Devuelve los fragmentos del documento ordenados por posición."""

        ...

    def replace(self, document_id: str, chunks: Iterable[Chunk]) -> None:
        """Reemplaza de forma atómica todos los fragmentos del documento."""

        ...

    def delete_by_document(self, document_id: str) -> int:
        """Borra los fragmentos del documento y devuelve cuántos eliminó."""

        ...


@runtime_checkable
class VectorStore(Protocol):
    """Índice vectorial para recuperación semántica."""

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        """Inserta o actualiza fragmentos con sus vectores."""

        ...

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        """Devuelve los fragmentos más similares, de mayor a menor puntuación."""

        ...

    def delete_by_document(self, document_id: str) -> None:
        """Elimina del índice todos los fragmentos del documento."""

        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Proveedor de generación de texto, con y sin streaming."""

    def complete(self, *, system: str, prompt: str) -> str:
        """Devuelve una respuesta completa."""

        ...

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        """Emite la respuesta por fragmentos."""

        ...


@runtime_checkable
class Fetcher(Protocol):
    """Cliente HTTP que consume el pipeline de captura."""

    def can_fetch(self, url: str) -> bool:
        """Indica si la URL se puede descargar."""

        ...

    def fetch(self, url: str) -> str:
        """Devuelve el HTML de la URL."""

        ...


@runtime_checkable
class Extractor(Protocol):
    """Conversor de HTML a Markdown limpio."""

    def extract(self, html: str) -> str:
        """Devuelve el Markdown del HTML."""

        ...
