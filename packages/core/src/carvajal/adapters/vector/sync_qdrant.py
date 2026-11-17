"""Adaptador Qdrant de la sincronización del corpus."""

from __future__ import annotations

from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.application.ports import Embedder
from carvajal.application.sync import CorpusLayer, gold_hashes
from carvajal.indexing.qdrant import index_gold

__all__ = ["QdrantSync"]


class QdrantSync:
    """Implementa ``SyncIndex`` sobre ``QdrantVectorStore``."""

    def __init__(self, store: QdrantVectorStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder

    def hashes(self, layer: str) -> dict[str, str]:
        """Huella de sincronización por documento de la capa (texto y metadatos)."""

        return self._store.document_hashes(layer)

    def index_gold(
        self,
        layer: CorpusLayer,
        *,
        only: set[str] | None,
        prune: bool,
    ) -> tuple[int, int]:
        """Indexa los documentos pedidos y poda los ausentes de la capa.

        ``only`` vacío significa que no hay documentos nuevos ni cambiados; la
        poda, si se pidió, se ejecuta igual.
        """

        indexed = 0
        if only is None or only:
            for directory in layer.directories:
                report = index_gold(
                    gold_dir=directory,
                    store=self._store,
                    embedder=self._embedder,
                    layer=layer.name,
                    only=only,
                    prune=False,
                )
                indexed += report.indexed

        deleted = 0
        if prune:
            valid = set(gold_hashes(layer))
            for document_id in sorted(self._store.document_ids(layer.name) - valid):
                self._store.delete_by_document(document_id)
                deleted += 1
        return indexed, deleted

    def count(self, layer: str) -> int:
        """Cuenta los documentos de la capa en el índice."""

        return len(self._store.document_hashes(layer))
