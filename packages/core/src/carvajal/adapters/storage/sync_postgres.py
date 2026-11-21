"""Adaptador PostgreSQL de la sincronización del corpus."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select

from carvajal.adapters.storage import tables
from carvajal.application.sync import GoldLayer, sync_fingerprint

__all__ = ["PostgresSync"]


class PostgresSync:
    """Implementa ``SyncDatabase`` sobre las tablas del corpus."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def hashes(self, layer: str) -> Mapping[str, str]:
        """Huella de sincronización por documento de la capa (texto y metadatos)."""

        with self._session_factory.begin() as session:
            rows = session.execute(
                select(
                    tables.documents.c.id,
                    tables.documents.c.content_hash,
                    tables.documents.c.title,
                    tables.documents.c.section,
                    tables.documents.c.source_type,
                    tables.documents.c.url,
                ).where(tables.documents.c.layer == layer)
            ).all()
        return {
            str(row[0]): sync_fingerprint(
                content_hash=str(row[1]),
                title=str(row[2]),
                section=str(row[3]),
                source_type=str(row[4]),
                url=str(row[5]),
            )
            for row in rows
        }

    def import_gold(
        self,
        gold: GoldLayer,
        *,
        only: set[str] | None,
        prune: bool,
    ) -> tuple[int, int, int]:
        """Escribe el lote Gold ya leído y devuelve ``(documentos, fragmentos, podados)``.

        La lectura de Gold la hizo el servicio de sincronización una sola vez;
        este adaptador no vuelve a tocar el disco. La poda opera sobre la capa
        completa: los documentos del lote son la lista autoritativa de la capa,
        así que una capa repartida en varias fuentes se poda con su unión.
        """

        from carvajal.adapters.storage.import_corpus import import_documents

        with self._session_factory.begin() as session:
            report = import_documents(
                session,
                gold.documents,
                layer=gold.layer.name,
                only=only,
                prune=prune,
            )
        return report.documents, report.chunks, report.removed

    def count(self, layer: str) -> int:
        """Cuenta los documentos de la capa."""

        with self._session_factory.begin() as session:
            total = session.execute(
                select(func.count())
                .select_from(tables.documents)
                .where(tables.documents.c.layer == layer)
            ).scalar_one()
        return int(total)
