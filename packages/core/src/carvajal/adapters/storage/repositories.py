"""Repositorios SQL del corpus sobre SQLAlchemy Core.

Cada repositorio recibe una ``Session`` ajena y solo hace ``flush()``: la
transacción —y por tanto el ``commit()``— pertenece a quien abre la unidad de
trabajo. Sin ORM declarativo: las consultas se escriben sobre las ``Table`` de
``tables.py``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import CursorResult, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.domain.entities import Chunk, Document, DocumentVersion

__all__ = ["SqlChunkRepository", "SqlDocumentRepository"]


class SqlDocumentRepository:
    """Implementación PostgreSQL de ``DocumentRepository``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, document_id: str) -> Document | None:
        """Devuelve el documento por ID, o ``None`` si no existe."""

        row = (
            self._session.execute(
                select(tables.documents).where(tables.documents.c.id == document_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else Document(**row)

    def get_by_slug(self, slug: str) -> Document | None:
        """Devuelve el documento por slug, o ``None`` si no existe."""

        row = (
            self._session.execute(select(tables.documents).where(tables.documents.c.slug == slug))
            .mappings()
            .one_or_none()
        )
        return None if row is None else Document(**row)

    def list(
        self,
        *,
        layer: str | None = None,
        section: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Document]:
        """Lista documentos por slug, filtrando por capa y sección."""

        query = select(tables.documents)
        if layer is not None:
            query = query.where(tables.documents.c.layer == layer)
        if section is not None:
            query = query.where(tables.documents.c.section == section)
        query = query.order_by(tables.documents.c.slug).limit(limit).offset(offset)
        return [Document(**row) for row in self._session.execute(query).mappings()]

    def upsert(self, document: Document) -> Document:
        """Inserta o actualiza por slug, archivando el contenido anterior.

        El ``id`` es inmutable: si el slug ya existe con otro ``id`` se lanza
        ``ValueError``. Solo se crea una versión cuando cambia ``content_hash``.
        """

        row = (
            self._session.execute(
                select(tables.documents)
                .where(tables.documents.c.slug == document.slug)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )

        if row is None:
            self._session.execute(insert(tables.documents).values(**asdict(document)))
        else:
            if row["id"] != document.id:
                raise ValueError(
                    f"El slug {document.slug!r} ya pertenece al documento {row['id']!r}; "
                    "el id no se puede cambiar."
                )
            if row["content_hash"] != document.content_hash:
                self._session.execute(
                    insert(tables.document_versions).values(
                        id=uuid4().hex,
                        document_id=row["id"],
                        markdown=row["markdown"],
                        content_hash=row["content_hash"],
                        captured_at=row["captured_at"],
                        created_at=datetime.now(UTC),
                    )
                )
            self._session.execute(
                update(tables.documents)
                .where(tables.documents.c.id == document.id)
                .values(**asdict(document))
            )

        self._session.flush()
        return document

    def list_versions(self, document_id: str) -> Sequence[DocumentVersion]:
        """Devuelve las versiones archivadas, de la más reciente a la más antigua."""

        query = (
            select(tables.document_versions)
            .where(tables.document_versions.c.document_id == document_id)
            .order_by(
                tables.document_versions.c.created_at.desc(),
                tables.document_versions.c.id.desc(),
            )
        )
        return [DocumentVersion(**row) for row in self._session.execute(query).mappings()]

    def delete(self, document_id: str) -> bool:
        """Borra el documento —y en cascada sus chunks y versiones—; devuelve si existía."""

        result = cast(
            CursorResult[Any],
            self._session.execute(
                delete(tables.documents).where(tables.documents.c.id == document_id)
            ),
        )
        self._session.flush()
        return result.rowcount > 0

    def count(self, *, layer: str | None = None) -> int:
        """Cuenta documentos, opcionalmente filtrados por capa."""

        query = select(func.count()).select_from(tables.documents)
        if layer is not None:
            query = query.where(tables.documents.c.layer == layer)
        return self._session.execute(query).scalar_one()


class SqlChunkRepository:
    """Implementación PostgreSQL de ``ChunkRepository``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_document(self, document_id: str) -> Sequence[Chunk]:
        """Devuelve los fragmentos del documento ordenados por posición."""

        query = (
            select(tables.chunks)
            .where(tables.chunks.c.document_id == document_id)
            .order_by(tables.chunks.c.position)
        )
        return [Chunk(**row) for row in self._session.execute(query).mappings()]

    def replace(self, document_id: str, chunks: Iterable[Chunk]) -> None:
        """Reemplaza todos los fragmentos del documento, validando pertenencia y posición."""

        rows = list(chunks)
        for chunk in rows:
            if chunk.document_id != document_id:
                raise ValueError(
                    f"El fragmento {chunk.id!r} pertenece a {chunk.document_id!r}, "
                    f"no a {document_id!r}."
                )
        positions = [chunk.position for chunk in rows]
        if len(set(positions)) != len(positions):
            raise ValueError(f"Hay posiciones duplicadas en los fragmentos de {document_id!r}.")

        self._session.execute(
            delete(tables.chunks).where(tables.chunks.c.document_id == document_id)
        )
        if rows:
            self._session.execute(
                postgresql_insert(tables.chunks).values([asdict(chunk) for chunk in rows])
            )
        self._session.flush()

    def delete_by_document(self, document_id: str) -> int:
        """Borra los fragmentos del documento y devuelve cuántos eliminó."""

        result = cast(
            CursorResult[Any],
            self._session.execute(
                delete(tables.chunks).where(tables.chunks.c.document_id == document_id)
            ),
        )
        self._session.flush()
        return result.rowcount
