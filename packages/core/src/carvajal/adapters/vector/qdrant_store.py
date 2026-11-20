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
from carvajal.application.sync import sync_fingerprint
from carvajal.domain.entities import Chunk

__all__ = ["QdrantVectorStore", "fingerprint_for", "payload_for", "point_id"]

_NAMESPACE = UUID("6f0b8d9a-3c2e-5a7b-9e4d-1f2a3b4c5d6e")
# Documentos por consulta al buscar los IDs existentes: no existe una lista
# vacía y un lote enorme tampoco cabe en una sola petición.
_DOCUMENT_BATCH = 256


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


def fingerprint_for(chunk: Chunk, metadata: Mapping[str, str] | None = None) -> str:
    """Material que determina el ID: título, sección, ficha y texto."""

    extra = metadata or {}
    return f"{extra.get('title', '')}|{chunk.section}|{extra.get('abstract', '')}|{chunk.text}"


def payload_for(chunk: Chunk, metadata: Mapping[str, str] | None = None) -> dict[str, object]:
    """Payload que hace verificable una cita y permite filtrar por capa/sección."""

    extra = dict(metadata or {})
    payload: dict[str, object] = {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "slug": chunk.document_id,
        "text": chunk.text,
        "position": chunk.position,
        "section": chunk.section,
        "layer": extra.get("layer", chunk.layer),
        "url": extra.get("url", ""),
        "title": extra.get("title", ""),
        "source_type": extra.get("source_type", "oficial"),
        "content_hash": extra.get("content_hash", ""),
        "abstract": extra.get("abstract", ""),
        "chunks_total": extra.get("chunks_total", ""),
        "origin": extra.get("origin", ""),
        "category": extra.get("category", ""),
        "entity": extra.get("entity", ""),
    }
    # Solo se declara el modelo cuando el embedder lo dice: un doble de prueba
    # no lo hace y su índice queda como los anteriores al registro.
    model = extra.get("embedding_model", "")
    if model:
        payload["embedding_model"] = model
    return payload


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
                    id=point_id(chunk, fingerprint_for(chunk, extra)),
                    vector=vector,
                    payload=payload_for(chunk, extra),
                )
            )
        self._client.upsert(collection_name=self._collection, points=points, wait=True)

    def point_ids(
        self,
        chunks: Sequence[Chunk],
        metadata: Sequence[Mapping[str, str]] | None = None,
    ) -> list[str]:
        """IDs que ``upsert`` escribirá; la poda debe usar exactamente esta lista."""

        if metadata is not None and len(metadata) != len(chunks):
            raise ValueError("Cada fragmento necesita su metadata para calcular el ID.")
        return [
            point_id(chunk, fingerprint_for(chunk, None if metadata is None else metadata[index]))
            for index, chunk in enumerate(chunks)
        ]

    def existing_point_ids(self, document_ids: Sequence[str]) -> dict[str, set[str]]:
        """IDs de punto que hoy tiene cada documento, en una sola pasada.

        El indexador lo usa para distinguir un cambio de texto —que obliga a
        reembeber— de un cambio de metadatos, que solo exige reescribir el
        payload. Consulta por ``document_id`` en lotes para no recorrer la
        colección entera.
        """

        found: dict[str, set[str]] = {}
        unique = list(dict.fromkeys(document_ids))
        if not unique or not self._client.collection_exists(self._collection):
            return found
        for start in range(0, len(unique), _DOCUMENT_BATCH):
            batch = unique[start : start + _DOCUMENT_BATCH]
            scroll_filter = models.Filter(
                must=[models.FieldCondition(key="document_id", match=models.MatchAny(any=batch))]
            )
            offset = None
            while True:
                points, offset = self._client.scroll(
                    collection_name=self._collection,
                    limit=512,
                    offset=offset,
                    scroll_filter=scroll_filter,
                    with_payload=["document_id"],
                    with_vectors=False,
                )
                for point in points:
                    payload = point.payload or {}
                    document_id = str(payload.get("document_id") or payload.get("slug") or "")
                    if document_id:
                        found.setdefault(document_id, set()).add(str(point.id))
                if offset is None:
                    break
        return found

    def update_payload(self, point_ids: Sequence[str], payload: Mapping[str, object]) -> None:
        """Reescribe el payload de puntos existentes sin tocar sus vectores.

        Solo se usa cuando el ID del punto no cambió, es decir, cuando el texto
        vectorizado es el mismo: los campos de ``payload`` son metadatos
        compartidos por todos los fragmentos del documento.
        """

        if not point_ids:
            return
        self._client.set_payload(
            collection_name=self._collection,
            payload=dict(payload),
            points=list(point_ids),
            wait=True,
        )

    def count_embedding_model(self, model: str) -> int:
        """Puntos que registran ese modelo de embeddings en su payload.

        Cada fragmento guarda el modelo que produjo su vector: mezclar vectores
        de modelos distintos produce una recuperación silenciosamente mala,
        aunque las dimensiones coincidan.
        """

        return self._count(
            models.Filter(
                must=[
                    models.FieldCondition(
                        key="embedding_model", match=models.MatchValue(value=model)
                    )
                ]
            )
        )

    def count_with_embedding_model(self) -> int:
        """Puntos que declaran un modelo; los índices viejos no lo hacían."""

        return self._count(
            models.Filter(
                must_not=[
                    models.IsEmptyCondition(is_empty=models.PayloadField(key="embedding_model"))
                ]
            )
        )

    def _count(self, count_filter: models.Filter) -> int:
        if not self._client.collection_exists(self._collection):
            return 0
        return int(
            self._client.count(self._collection, count_filter=count_filter, exact=True).count
        )

    def adopt_embedding_model(self, model: str) -> None:
        """Registra el modelo actual en los puntos de un índice anterior al registro.

        Los índices creados antes de este campo no dicen qué modelo produjo sus
        vectores. Se adopta el configurado ahora —una sola operación de payload,
        sin reembeber— y desde ahí un cambio de modelo sí se detecta.
        """

        if not self._client.collection_exists(self._collection):
            return
        missing = models.Filter(
            must=[models.IsEmptyCondition(is_empty=models.PayloadField(key="embedding_model"))]
        )
        self._client.set_payload(
            collection_name=self._collection,
            payload={"embedding_model": model},
            points=models.FilterSelector(filter=missing),
            wait=True,
        )

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
            "origin": str(payload.get("origin", "")),
            "category": str(payload.get("category", "")),
            "entity": str(payload.get("entity", "")),
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

    def prune(self, valid_ids: Iterable[str], *, layer: str | None = None) -> int:
        """Elimina puntos que ya no pertenecen al corpus; devuelve cuántos borró.

        ``layer`` limita la poda a esa capa. La colección es compartida: indexar
        solo el núcleo (``carvajal index-qdrant``) no debe borrar los puntos de
        la capa extendida.
        """

        keep = set(valid_ids)
        scroll_filter = None
        if layer is not None:
            scroll_filter = models.Filter(
                must=[models.FieldCondition(key="layer", match=models.MatchValue(value=layer))]
            )
        stale: list[str] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                limit=256,
                offset=offset,
                scroll_filter=scroll_filter,
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

    def document_hashes(self, layer: str | None = None) -> dict[str, str]:
        """Huella de sincronización por documento, solo si están todos sus fragmentos.

        La huella cubre el texto y los metadatos que el payload publica (título,
        sección y tipo de fuente): corregir el título de un documento lo marca
        como cambiado aunque su cuerpo no cambie. Un documento con menos puntos
        que ``chunks_total`` quedó a medias (por ejemplo, una corrida interrumpida
        a mitad de sus lotes): no se publica su huella para que la siguiente
        sincronización lo reindexe en lugar de darlo por vigente.
        """

        hashes: dict[str, str] = {}
        seen: dict[str, int] = {}
        expected: dict[str, int] = {}
        for payload in self._payloads(layer):
            document_id = str(payload.get("document_id") or payload.get("slug") or "")
            if not document_id:
                continue
            seen[document_id] = seen.get(document_id, 0) + 1
            hashes[document_id] = sync_fingerprint(
                content_hash=str(payload.get("content_hash") or ""),
                title=str(payload.get("title") or ""),
                section=str(payload.get("section") or ""),
                source_type=str(payload.get("source_type") or ""),
                url=str(payload.get("url") or ""),
                origin=str(payload.get("origin") or ""),
                category=str(payload.get("category") or ""),
                entity=str(payload.get("entity") or ""),
            )
            total = str(payload.get("chunks_total") or "")
            expected[document_id] = int(total) if total.isdigit() else 0
        return {
            document_id: fingerprint
            for document_id, fingerprint in hashes.items()
            if expected[document_id] and seen[document_id] == expected[document_id]
        }

    def document_ids(self, layer: str | None = None) -> set[str]:
        """Documentos distintos presentes en el índice, filtrados por capa.

        A diferencia de ``document_hashes``, incluye los documentos incompletos:
        un documento con lotes pendientes no se publica como vigente, pero sus
        fragmentos ya existen y la poda debe poder eliminarlos cuando su Markdown
        desaparece de Gold.
        """

        ids: set[str] = set()
        for payload in self._payloads(layer):
            document_id = str(payload.get("document_id") or payload.get("slug") or "")
            if document_id:
                ids.add(document_id)
        return ids

    def prune_layer(self, layer: str, valid_documents: Iterable[str]) -> int:
        """Elimina los documentos de una capa que ya no están en Gold.

        Devuelve cuántos documentos podó. Solo toca la capa indicada: el resto
        del corpus conserva sus puntos.
        """

        keep = set(valid_documents)
        stale = sorted(self.document_ids(layer) - keep)
        for document_id in stale:
            self.delete_by_document(document_id)
        return len(stale)

    def _payloads(self, layer: str | None = None) -> Iterable[dict[str, object]]:
        """Recorre los payloads de la colección, opcionalmente por capa.

        Una colección que todavía no existe es un índice vacío: la etapa de escritura la
        crea, así que la lectura no debe impedir la primera sincronización.
        """

        if not self._client.collection_exists(self._collection):
            return
        scroll_filter = None
        if layer is not None:
            scroll_filter = models.Filter(
                must=[models.FieldCondition(key="layer", match=models.MatchValue(value=layer))]
            )
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                limit=256,
                offset=offset,
                scroll_filter=scroll_filter,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                yield dict(point.payload or {})
            if offset is None:
                break

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
        """Número de fragmentos indexados; una colección ausente no tiene puntos."""

        if not self._client.collection_exists(self._collection):
            return 0
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
