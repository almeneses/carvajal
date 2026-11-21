"""Adaptador Qdrant de la sincronización del corpus."""

from __future__ import annotations

from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.application.ports import Embedder
from carvajal.application.sync import GoldLayer, IndexSnapshot
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

    def snapshot(self, layer: str) -> IndexSnapshot:
        """Huellas y documentos de la capa en un solo recorrido del índice."""

        return self._store.snapshot(layer)

    def document_ids(self, layer: str) -> set[str]:
        """Todos los documentos con puntos, incluidos los incompletos."""

        return self._store.document_ids(layer)

    def index_gold(
        self,
        gold: GoldLayer,
        *,
        only: set[str] | None,
        prune: bool,
    ) -> tuple[int, int]:
        """Indexa el lote Gold pedido y poda los ausentes de la capa.

        ``only`` vacío significa que no hay documentos nuevos ni cambiados; la
        poda, si se pidió, se ejecuta igual. El lote ya leído evita que el
        indexador vuelva a abrir los Markdown de la capa.
        """

        indexed = 0
        if only is None or only:
            report = index_gold(
                gold_dirs=tuple(
                    (directory, gold.layer.name) for directory in gold.layer.directories
                ),
                store=self._store,
                embedder=self._embedder,
                only=only,
                prune=False,
                documents=gold.documents,
            )
            indexed = report.indexed

        deleted = 0
        if prune:
            deleted = self._store.prune_layer(gold.layer.name, gold.hashes)
        return indexed, deleted

    def count(self, layer: str) -> int:
        """Cuenta los documentos de la capa en el índice."""

        return len(self._store.document_hashes(layer))
