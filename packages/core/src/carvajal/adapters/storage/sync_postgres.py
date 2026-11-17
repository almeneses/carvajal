"""Adaptador PostgreSQL de la sincronización del corpus."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select

from carvajal.adapters.storage import tables
from carvajal.application.sync import CorpusLayer, gold_hashes

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

        La poda opera sobre la capa completa. Con un único directorio,
        ``migrate_gold`` es autoritativo y poda por sí mismo. Con varias fuentes,
        ningún directorio lo es: podar con la lista de uno borraría los
        documentos de los otros, así que se importa sin poda y al terminar se
        eliminan los slugs que no estén en la unión de todas las fuentes.
        """

        from carvajal.adapters.storage.import_corpus import migrate_gold, prune_layer

        documents = chunks = removed = 0
        single = len(layer.directories) == 1
        with self._session_factory.begin() as session:
            for directory in layer.directories:
                report = migrate_gold(
                    session,
                    gold_dir=directory,
                    layer=layer.name,
                    # Un directorio vacío solo es aceptable cuando hay una poda
                    # autoritativa detrás que decide el estado de la capa.
                    allow_empty=prune,
                    only=only,
                    prune=prune and single,
                )
                documents += report.documents
                chunks += report.chunks
                removed += report.removed
            if prune and not single:
                removed += prune_layer(session, layer=layer.name, keep=set(gold_hashes(layer)))
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
