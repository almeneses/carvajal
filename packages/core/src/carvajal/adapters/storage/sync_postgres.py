"""Adaptador PostgreSQL de la sincronización del corpus."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select

from carvajal.adapters.storage import tables
from carvajal.application.sync import CorpusLayer

__all__ = ["PostgresSync"]


class PostgresSync:
    """Implementa ``SyncDatabase`` sobre las tablas del corpus."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def hashes(self, layer: str) -> Mapping[str, str]:
        """Huella de texto vigente por documento de la capa."""

        with self._session_factory.begin() as session:
            rows = session.execute(
                select(tables.documents.c.id, tables.documents.c.content_hash).where(
                    tables.documents.c.layer == layer
                )
            ).all()
        return {str(row[0]): str(row[1]) for row in rows}

    def import_gold(
        self,
        layer: CorpusLayer,
        *,
        only: set[str] | None,
        prune: bool,
    ) -> tuple[int, int, int]:
        """Importa los directorios de la capa y suma sus conteos.

        La poda solo se aplica cuando la capa tiene un único directorio: con
        varios, ``migrate_gold`` borraría los documentos de las otras fuentes.
        """

        from carvajal.adapters.storage.import_corpus import migrate_gold

        documents = chunks = removed = 0
        authoritative = prune and len(layer.directories) == 1
        with self._session_factory.begin() as session:
            for directory in layer.directories:
                report = migrate_gold(
                    session,
                    gold_dir=directory,
                    layer=layer.name,
                    allow_empty=authoritative,
                    only=only,
                    prune=authoritative,
                )
                documents += report.documents
                chunks += report.chunks
                removed += report.removed
        return documents, chunks, removed

    def count(self, layer: str) -> int:
        """Cuenta los documentos de la capa."""

        with self._session_factory.begin() as session:
            total = session.execute(
                select(func.count())
                .select_from(tables.documents)
                .where(tables.documents.c.layer == layer)
            ).scalar_one()
        return int(total)
