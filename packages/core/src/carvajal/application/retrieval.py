"""Recuperación con prioridad de capas: el núcleo antes que la extendida.

Política de producto: una respuesta oficial no puede quedar debajo de un
documento de un colaborador. El envoltorio consulta primero ``layer=core`` y solo
completa con ``layer=extended`` cuando el núcleo no llena el cupo o cuando su
mejor resultado no supera el umbral de relevancia.

Vive en la aplicación, no en el adaptador de Qdrant: la prioridad es una decisión
del caso de uso y debe poder probarse sin un índice real.
"""

from __future__ import annotations

from collections.abc import Sequence

from carvajal.application.ports import ScoredChunk, VectorStore
from carvajal.domain.entities import Chunk

__all__ = ["LayerPriorityStore"]


class LayerPriorityStore:
    """Envoltorio de ``VectorStore`` que antepone el núcleo a la capa extendida.

    Args:
        store: Índice real (Qdrant u otro adaptador del puerto).
        min_score: Umbral bajo el cual un fragmento no cuenta como respuesta.
            La capa extendida solo se consulta si el núcleo no alcanza el cupo
            con fragmentos que superen este umbral.
        core_layer: Nombre de la capa núcleo en el payload.
        extended_layer: Nombre de la capa extendida en el payload.
    """

    def __init__(
        self,
        store: VectorStore,
        *,
        min_score: float = 0.0,
        core_layer: str = "core",
        extended_layer: str = "extended",
    ) -> None:
        self._store = store
        self._min_score = min_score
        self._core = core_layer
        self._extended = extended_layer

    @property
    def min_score(self) -> float:
        """Umbral configurado para considerar relevante un fragmento."""

        return self._min_score

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        """Delega la escritura en el índice envuelto."""

        self._store.upsert(chunks, embeddings)

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        """Busca primero en el núcleo y completa con la capa extendida."""

        threshold = self._min_score
        if layer is not None:
            # Un llamador que fija la capa ya tomó la decisión: se respeta.
            return self._accepted(
                self._store.search(embedding, limit=limit, layer=layer, section=section),
                threshold,
            )[:limit]
        core = self._accepted(
            self._store.search(embedding, limit=limit, layer=self._core, section=section),
            threshold,
        )
        if len(core) >= limit:
            return core[:limit]
        extended = self._accepted(
            self._store.search(
                embedding, limit=limit - len(core), layer=self._extended, section=section
            ),
            threshold,
        )
        return (core + extended)[:limit]

    def search_text(
        self,
        question: str,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
        min_score: float | None = None,
    ) -> Sequence[ScoredChunk]:
        """Variante híbrida de la búsqueda, con la misma prioridad de capas."""

        threshold = self._min_score if min_score is None else min_score
        if layer is not None:
            return self._accepted(
                self._text_search(
                    question,
                    embedding,
                    limit=limit,
                    layer=layer,
                    section=section,
                    min_score=threshold,
                ),
                threshold,
            )[:limit]
        core = self._accepted(
            self._text_search(
                question,
                embedding,
                limit=limit,
                layer=self._core,
                section=section,
                min_score=threshold,
            ),
            threshold,
        )
        if len(core) >= limit:
            return core[:limit]
        extended = self._accepted(
            self._text_search(
                question,
                embedding,
                limit=limit - len(core),
                layer=self._extended,
                section=section,
                min_score=threshold,
            ),
            threshold,
        )
        return (core + extended)[:limit]

    def delete_by_document(self, document_id: str) -> None:
        """Delega el borrado por documento en el índice envuelto."""

        self._store.delete_by_document(document_id)

    def _text_search(
        self,
        question: str,
        embedding: Sequence[float],
        *,
        limit: int,
        layer: str,
        section: str | None,
        min_score: float,
    ) -> Sequence[ScoredChunk]:
        """Usa la búsqueda híbrida del adaptador si existe; si no, la vectorial."""

        search_text = getattr(self._store, "search_text", None)
        if callable(search_text):
            return search_text(
                question,
                embedding,
                limit=limit,
                layer=layer,
                section=section,
                min_score=min_score,
            )
        return self._store.search(embedding, limit=limit, layer=layer, section=section)

    @staticmethod
    def _accepted(results: Sequence[ScoredChunk], threshold: float) -> list[ScoredChunk]:
        """Conserva los fragmentos que alcanzan el umbral, en su orden."""

        return [result for result in results if result.score >= threshold]
