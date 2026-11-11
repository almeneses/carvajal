"""Índice vectorial sobre pgvector: similitud coseno con ordenación HNSW.

``PgVectorStore`` no posee las filas de ``chunks``: las administra
``ChunkRepository``. Aquí solo se escribe la columna ``embedding``.
"""

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk

__all__ = ["DIMENSIONS", "PgVectorStore"]

DIMENSIONS = 768

_CHUNK_FIELDS = ("id", "document_id", "text", "position", "section", "layer")
_CHUNK_COLUMNS = tuple(tables.chunks.c[field] for field in _CHUNK_FIELDS)


class PgVectorStore:
    """Implementación PostgreSQL/pgvector de ``VectorStore``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        """Inserta o actualiza los fragmentos con sus vectores.

        Si el fragmento no existe se inserta completo; si existe con el mismo
        ``document_id`` solo se reescribe su ``embedding``. El ``id`` es inmutable:
        reutilizarlo desde otro documento lanza ``ValueError`` y no deja cambios.
        Hace ``flush()``, nunca ``commit()``.
        """

        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Cada fragmento necesita exactamente un vector; recibidos {len(chunks)} "
                f"fragmentos y {len(embeddings)} vectores."
            )
        rows = [
            {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "text": chunk.text,
                "position": chunk.position,
                "section": chunk.section,
                "layer": chunk.layer,
                "embedding": _validated(embedding),
            }
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ]
        if not rows:
            return
        statement = postgresql_insert(tables.chunks).values(rows)
        result = self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[tables.chunks.c.id],
                set_={"embedding": statement.excluded.embedding},
                where=tables.chunks.c.document_id == statement.excluded.document_id,
            ).returning(tables.chunks.c.id)
        )
        if len(result.all()) != len(rows):
            raise ValueError(
                "Algún fragmento ya existe con otro documento; el id del fragmento es inmutable."
            )
        self._session.flush()

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        """Devuelve los fragmentos más similares (``score = 1 - distancia``).

        ``limit=0`` devuelve vacío, un negativo lanza ``ValueError`` y los
        fragmentos sin vector no participan.
        """

        if limit < 0:
            raise ValueError(f"limit debe ser no negativo; recibido {limit!r}.")
        if limit == 0:
            return []
        vector = _validated(embedding)
        distance = tables.chunks.c.embedding.cosine_distance(vector)
        query = select(*_CHUNK_COLUMNS, distance.label("distance")).where(
            tables.chunks.c.embedding.is_not(None)
        )
        if layer is not None:
            query = query.where(tables.chunks.c.layer == layer)
        if section is not None:
            query = query.where(tables.chunks.c.section == section)
        query = query.order_by(distance).limit(limit)
        rows = self._session.execute(query).mappings()
        return [
            ScoredChunk(
                chunk=Chunk(**{field: row[field] for field in _CHUNK_FIELDS}),
                score=1.0 - row["distance"],
            )
            for row in rows
        ]

    def delete_by_document(self, document_id: str) -> None:
        """Quita del índice los vectores del documento (``embedding = NULL``).

        Las filas no se borran: son propiedad de ``ChunkRepository``. Con el
        vector a ``NULL`` el fragmento sale del índice ANN y de ``search``.
        """

        self._session.execute(
            update(tables.chunks)
            .where(tables.chunks.c.document_id == document_id)
            .values(embedding=None)
        )
        self._session.flush()


def _validated(embedding: Sequence[float]) -> list[float]:
    """Convierte el vector a ``list[float]`` y exige dimensión 768 y no vacío."""

    vector = [float(value) for value in embedding]
    if not vector:
        raise ValueError("El vector no puede estar vacío.")
    if len(vector) != DIMENSIONS:
        raise ValueError(f"El vector debe tener {DIMENSIONS} dimensiones; recibidas {len(vector)}.")
    return vector
