"""Índice vectorial Qdrant: recuperación coseno con payload de procedencia.

Qdrant es el índice evaluado del M2. Postgres sigue siendo la fuente de verdad
de documentos y fragmentos; este adaptador mantiene una copia denormalizada de
cada fragmento, con su URL y su tipo de fuente, para que una cita recuperada
pueda abrir el documento original.

Los IDs de punto son UUID5 estables derivados de ``documento:posición:hash``:
reindexar el mismo corpus produce los mismos IDs y la poda elimina los puntos
que ya no existen.
"""

from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
from uuid import UUID, uuid5

from qdrant_client import QdrantClient, models

from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk

__all__ = ["QdrantVectorStore", "payload_for", "point_id"]

_NAMESPACE = UUID("6f0b8d9a-3c2e-5a7b-9e4d-1f2a3b4c5d6e")


def point_id(chunk: Chunk, fingerprint: str = "") -> str:
    """UUID5 estable para un fragmento.

    ``fingerprint`` incluye el texto vectorizado (título, sección y ficha); si
    cambia la ficha, cambia el ID y la poda elimina el punto anterior. Sin
    fingerprint se usa el texto original, suficiente para pruebas y llamadas
    directas.
    """

    material = fingerprint or chunk.text
    digest = sha256(material.encode("utf-8")).hexdigest()[:16]
    return str(uuid5(_NAMESPACE, f"carvajal:{chunk.document_id}:{chunk.position}:{digest}"))


def payload_for(chunk: Chunk, metadata: Mapping[str, str] | None = None) -> dict[str, object]:
    """Payload que hace verificable una cita y permite filtrar por capa/sección."""

    extra = dict(metadata or {})
    return {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "slug": chunk.document_id,
        "text": chunk.text,
        "position": chunk.position,
        "section": chunk.section,
        "layer": chunk.layer,
        "url": extra.get("url", ""),
        "title": extra.get("title", ""),
        "source_type": extra.get("source_type", "oficial"),
        "content_hash": extra.get("content_hash", ""),
        "abstract": extra.get("abstract", ""),
    }


class QdrantVectorStore:
    """Implementación Qdrant del puerto ``VectorStore``."""

    def __init__(
        self,
        client: QdrantClient | None = None,
        *,
        url: str = "http://127.0.0.1:6333",
        api_key: str = "",
        collection: str = "carvajal_chunks",
        dimensions: int = 768,
        distance: str = "Cosine",
    ) -> None:
        self._client = client or QdrantClient(url=url, api_key=api_key or None, timeout=10)
        self._collection = collection
        self._dimensions = dimensions
        try:
            self._distance = models.Distance[distance.strip().upper()]
        except KeyError as error:
            valid = ", ".join(item.value for item in models.Distance)
            raise ValueError(
                f"Distancia Qdrant desconocida: {distance!r}. Válidas: {valid}."
            ) from error

    @property
    def collection(self) -> str:
        """Nombre de la colección que consulta este adaptador."""

        return self._collection

    @property
    def client(self) -> QdrantClient:
        """Cliente subyacente; la CLI de indexación lo reutiliza."""

        return self._client

    @property
    def dimensions(self) -> int:
        """Dimensión configurada para la colección."""

        return self._dimensions

    def ensure_collection(self) -> bool:
        """Crea la colección si falta y valida dimensión/métrica.

        Devuelve ``True`` si la creó en esta llamada. Una colección existente
        con otra dimensión o distancia es un error de configuración: mezclar
        vectores incompatibles produce resultados silenciosamente malos.
        """

        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=models.VectorParams(
                    size=self._dimensions,
                    distance=self._distance,
                ),
            )
            return True
        info = self._client.get_collection(self._collection)
        vectors = info.config.params.vectors
        if isinstance(vectors, models.VectorParams):
            if vectors.size != self._dimensions:
                raise ValueError(
                    f"La colección {self._collection!r} tiene {vectors.size} dimensiones; "
                    f"la configuración exige {self._dimensions}."
                )
            if vectors.distance != self._distance:
                raise ValueError(
                    f"La colección {self._collection!r} usa {vectors.distance}; "
                    f"la configuración exige {self._distance}."
                )
        return False

    def upsert(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        *,
        metadata: Sequence[Mapping[str, str]] | None = None,
    ) -> None:
        """Inserta o actualiza puntos de forma idempotente.

        ``metadata`` es opcional por compatibilidad con el puerto, pero el
        indexador siempre la envía. La operación es idempotente por el ID
        estable del fragmento.
        """

        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Cada fragmento necesita exactamente un vector; recibidos {len(chunks)} "
                f"fragmentos y {len(embeddings)} vectores."
            )
        if metadata is not None and len(metadata) != len(chunks):
            raise ValueError("Cada fragmento necesita su metadata de procedencia.")
        if not chunks:
            return
        points = []
        for position, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            vector = [float(value) for value in embedding]
            if len(vector) != self._dimensions:
                raise ValueError(
                    f"El vector debe tener {self._dimensions} dimensiones; recibidas {len(vector)}."
                )
            extra = None if metadata is None else metadata[position]
            points.append(
                models.PointStruct(
                    id=point_id(chunk),
                    vector=vector,
                    payload=payload_for(chunk, extra),
                )
            )
        self._client.upsert(collection_name=self._collection, points=points, wait=True)

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        """Devuelve el top-k con score coseno y el payload de procedencia."""

        if limit < 0:
            raise ValueError(f"limit debe ser no negativo; recibido {limit!r}.")
        if limit == 0:
            return []
        conditions = []
        if layer is not None:
            conditions.append(
                models.FieldCondition(key="layer", match=models.MatchValue(value=layer))
            )
        if section is not None:
            conditions.append(
                models.FieldCondition(key="section", match=models.MatchValue(value=section))
            )
        query_filter = models.Filter(must=conditions) if conditions else None
        resultado = self._client.query_points(
            collection_name=self._collection,
            query=[float(value) for value in embedding],
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )
        return [self._scored(point) for point in resultado.points]

    def search_text(
        self,
        query: str,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
        min_score: float | None = None,
    ) -> Sequence[ScoredChunk]:
        """Búsqueda híbrida: ANN + reordenamiento léxico sobre la ficha.

        Los embeddings multilingües pequeños confunden preguntas como «¿en qué
        ciudad fue fundada?» con la portada. El reordenamiento suma una pequeña
        bonificación por coincidencia de términos (con prefijos de 5 letras)
        entre la pregunta y la ficha/fragmento. No sustituye al vector: solo
        desempata candidatos ya recuperados.
        """

        candidates = list(
            self.search(embedding, limit=max(limit * 4, 20), layer=layer, section=section)
        )
        if min_score is not None:
            # El umbral se aplica al score vectorial, antes de la bonificación
            # léxica: una coincidencia de términos no convierte por sí sola un
            # fragmento poco similar en evidencia.
            candidates = [result for result in candidates if result.score >= min_score]
        terms = _terms(query)
        if not terms:
            return candidates[:limit]
        ranked = sorted(
            candidates,
            key=lambda result: (
                result.score
                + 0.35
                * _overlap(
                    terms,
                    _terms(result.metadata.get("abstract", "") + " " + result.chunk.text),
                )
            ),
            reverse=True,
        )
        return ranked[:limit]

    def _scored(self, point: models.ScoredPoint) -> ScoredChunk:
        """Traduce un punto de Qdrant al contrato de la aplicación."""

        payload = point.payload or {}
        chunk = Chunk(
            id=str(payload.get("chunk_id", point.id)),
            document_id=str(payload.get("slug") or payload.get("document_id", "")),
            text=str(payload.get("text", "")),
            position=int(payload.get("position", 0)),
            section=str(payload.get("section", "")),
            layer=str(payload.get("layer", "")),
        )
        metadata = {
            "url": str(payload.get("url", "")),
            "title": str(payload.get("title", "")),
            "source_type": str(payload.get("source_type", "oficial")),
            "content_hash": str(payload.get("content_hash", "")),
            "abstract": str(payload.get("abstract", "")),
        }
        return ScoredChunk(chunk=chunk, score=float(point.score), metadata=metadata)

    def delete_by_document(self, document_id: str) -> None:
        """Elimina todos los puntos del documento."""

        self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id", match=models.MatchValue(value=document_id)
                        )
                    ]
                )
            ),
            wait=True,
        )

    def prune(self, valid_ids: Iterable[str]) -> int:
        """Elimina puntos que ya no pertenecen al corpus; devuelve cuántos borró."""

        keep = set(valid_ids)
        stale: list[str] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            stale.extend(str(point.id) for point in points if str(point.id) not in keep)
            if offset is None:
                break
        if not stale:
            return 0
        self._client.delete(
            collection_name=self._collection,
            points_selector=models.PointIdsList(points=stale),
            wait=True,
        )
        return len(stale)

    def all_point_ids(self) -> list[str]:
        """IDs actuales de la colección; la evaluación y la poda los usan."""

        ids: list[str] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            ids.extend(str(point.id) for point in points)
            if offset is None:
                break
        return ids

    def count(self) -> int:
        """Número de fragmentos indexados."""

        return int(self._client.count(self._collection, exact=True).count)

    def health(self) -> dict[str, object]:
        """Estado del índice sin tumbar la vitalidad del proceso."""

        try:
            if not self._client.collection_exists(self._collection):
                return {"status": "missing", "collection": self._collection, "points": 0}
            return {
                "status": "ok",
                "collection": self._collection,
                "points": self.count(),
                "dimensions": self._dimensions,
            }
        except Exception as error:  # Qdrant caído o inaccesible
            return {
                "status": "unavailable",
                "collection": self._collection,
                "points": 0,
                "detail": type(error).__name__,
            }

    def close(self) -> None:
        """Cierra el cliente HTTP."""

        self._client.close()


_STOPWORDS = {
    "para",
    "como",
    "cuando",
    "donde",
    "sobre",
    "entre",
    "desde",
    "hasta",
    "tiene",
    "tienen",
    "cual",
    "cuales",
    "quien",
    "quienes",
    "carvajal",
}


def _terms(text: str) -> set[str]:
    """Términos de contenido con prefijo de 5 letras para tolerar flexiones."""

    words = "".join(char if char.isalnum() else " " for char in text.casefold()).split()
    return {word[:5] for word in words if len(word) >= 4 and word not in _STOPWORDS}


def _overlap(query_terms: set[str], text_terms: set[str]) -> float:
    """Fracción de términos de la pregunta presentes en el texto."""

    if not query_terms:
        return 0.0
    return len(query_terms & text_terms) / len(query_terms)
