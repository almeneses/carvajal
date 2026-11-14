"""Pruebas del adaptador Qdrant con un cliente falso; sin red."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from qdrant_client import models

from carvajal.adapters.vector.qdrant_store import QdrantVectorStore, payload_for, point_id
from carvajal.domain.entities import Chunk

CHUNK = Chunk(
    id="historia:2",
    document_id="historia",
    text="Carvajal fue fundada en Cali en 1904.",
    position=2,
    section="historia",
    layer="core",
)
METADATA = {
    "url": "https://www.carvajal.com/historia/",
    "title": "Historia",
    "source_type": "oficial",
    "content_hash": "hash-1",
}


class FakeClient:
    """Doble mínimo con la superficie que usa el adaptador."""

    def __init__(self, exists: bool = True, size: int = 768) -> None:
        self.exists = exists
        self.size = size
        self.created: list[dict[str, Any]] = []
        self.points: list[models.PointStruct] = []
        self.deleted: list[Any] = []
        self.records: list[models.Record] = []
        self.fail = False
        self.last_query: dict[str, Any] = {}

    def collection_exists(self, name: str) -> bool:
        if self.fail:
            raise ConnectionError("Qdrant caído")
        return self.exists

    def create_collection(self, collection_name: str, vectors_config: Any) -> None:
        self.created.append({"name": collection_name, "config": vectors_config})
        self.exists = True

    def get_collection(self, name: str) -> Any:
        return SimpleNamespace(
            status=models.CollectionStatus.GREEN,
            optimizer_status=models.OptimizersStatusOneOf.OK,
            points_count=len(self.points),
            indexed_vectors_count=0,
            segments_count=2,
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=models.VectorParams(size=self.size, distance=models.Distance.COSINE)
                )
            ),
        )

    def upsert(self, collection_name: str, points: list[Any], wait: bool) -> None:
        self.points.extend(points)

    def query_points(self, **kwargs: Any) -> Any:
        self.last_query = kwargs
        return SimpleNamespace(
            points=[
                models.ScoredPoint(
                    id=point_id(CHUNK),
                    version=1,
                    score=0.87,
                    payload=payload_for(CHUNK, METADATA),
                )
            ]
        )

    def scroll(self, **kwargs: Any) -> tuple[list[models.Record], None]:
        return self.records, None

    def delete(self, collection_name: str, points_selector: Any, wait: bool) -> None:
        self.deleted.append(points_selector)

    def count(self, name: str, exact: bool) -> Any:
        return SimpleNamespace(count=len(self.points))

    def close(self) -> None:
        return None


def test_ensure_collection_crea_con_dimension_y_coseno() -> None:
    # Arrange
    client = FakeClient(exists=False)
    store = QdrantVectorStore(cast(Any, client))

    # Act
    created = store.ensure_collection()

    # Assert
    assert created is True
    assert client.created[0]["name"] == "carvajal_chunks"
    assert client.created[0]["config"].size == 768
    assert client.created[0]["config"].distance == models.Distance.COSINE


def test_ensure_collection_rechaza_dimension_incompatible() -> None:
    # Arrange
    store = QdrantVectorStore(cast(Any, FakeClient(size=384)))

    # Act / Assert
    with pytest.raises(ValueError, match="384"):
        store.ensure_collection()


def test_upsert_es_idempotente_y_guarda_procedencia() -> None:
    # Arrange
    client = FakeClient()
    store = QdrantVectorStore(cast(Any, client))

    # Act
    store.upsert([CHUNK], [[0.2] * 768], metadata=[METADATA])
    store.upsert([CHUNK], [[0.2] * 768], metadata=[METADATA])

    # Assert
    assert len(client.points) == 2
    assert client.points[0].id == client.points[1].id
    assert client.points[0].id == store.point_ids([CHUNK], [METADATA])[0]
    payload = client.points[0].payload or {}
    assert payload["url"] == METADATA["url"]
    assert payload["chunk_id"] == CHUNK.id


def test_upsert_rechaza_dimension_incorrecta() -> None:
    store = QdrantVectorStore(cast(Any, FakeClient()))

    with pytest.raises(ValueError, match="768"):
        store.upsert([CHUNK], [[0.2] * 10], metadata=[METADATA])


def test_search_traduce_score_payload_y_filtros() -> None:
    # Arrange
    client = FakeClient()
    store = QdrantVectorStore(cast(Any, client))

    # Act
    results = store.search([0.1] * 768, limit=5, layer="core", section="historia")

    # Assert
    assert results[0].score == 0.87
    assert results[0].chunk.document_id == "historia"
    assert results[0].metadata["url"] == METADATA["url"]
    assert client.last_query["limit"] == 5
    assert client.last_query["query_filter"] is not None


def test_prune_elimina_solo_los_puntos_obsoletos() -> None:
    # Arrange
    client = FakeClient()
    client.records = [
        models.Record(id=point_id(CHUNK), payload={}),
        models.Record(id="00000000-0000-0000-0000-000000000001", payload={}),
    ]
    store = QdrantVectorStore(cast(Any, client))

    # Act
    deleted = store.prune([point_id(CHUNK)])

    # Assert
    assert deleted == 1
    selector = client.deleted[0]
    assert isinstance(selector, models.PointIdsList)
    assert selector.points == ["00000000-0000-0000-0000-000000000001"]


def test_health_no_lanza_si_qdrant_no_responde() -> None:
    # Arrange
    client = FakeClient()
    client.fail = True
    store = QdrantVectorStore(cast(Any, client))

    # Act
    health = store.health()

    # Assert
    assert health["status"] == "unavailable"
    assert health["detail"] == "ConnectionError"


def test_info_resume_la_coleccion_para_metricas() -> None:
    # Arrange
    store = QdrantVectorStore(cast(Any, FakeClient()))

    # Act
    info = store.info()

    # Assert
    assert info["status"] == "green"
    assert info["optimizer_status"] == "ok"
    assert info["collection"] == "carvajal_chunks"
    assert info["points"] == 0
    assert info["segments"] == 2
    assert info["dimensions"] == 768
    assert info["distance"] == "Cosine"


def test_info_reporta_coleccion_ausente() -> None:
    # Arrange
    store = QdrantVectorStore(cast(Any, FakeClient(exists=False)))

    # Act
    info = store.info()

    # Assert
    assert info == {"status": "missing", "collection": "carvajal_chunks", "points": 0}


def test_info_no_lanza_si_qdrant_no_responde() -> None:
    # Arrange
    client = FakeClient()
    client.fail = True
    store = QdrantVectorStore(cast(Any, client))

    # Act
    info = store.info()

    # Assert
    assert info["status"] == "unavailable"
    assert info["detail"] == "ConnectionError"


def test_la_busqueda_hibrida_prioriza_la_ficha_relevante(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: el vector crudo prefiere el documento genérico; el léxico corrige.
    from carvajal.adapters.vector.qdrant_store import ScoredChunk

    store = QdrantVectorStore(cast(Any, FakeClient()))
    generico = ScoredChunk(
        chunk=Chunk("home:0", "home", "Portada", 0, "home", "core"),
        score=0.80,
        metadata={"abstract": "Portada de Carvajal."},
    )
    fundacion = ScoredChunk(
        chunk=Chunk("quienes:0", "quienes_somos", "En 1904 en Cali", 0, "quienes", "core"),
        score=0.70,
        metadata={"abstract": "Carvajal fue fundada en la ciudad de Cali en 1904."},
    )
    monkeypatch.setattr(store, "search", lambda *args, **kwargs: [generico, fundacion])

    # Act
    results = store.search_text("ciudad fundada Carvajal", [0.1] * 768, limit=1)

    # Assert
    assert results[0].chunk.id == "quienes:0"


def test_payload_incluye_la_url_para_abrir_la_fuente() -> None:
    payload = payload_for(CHUNK, METADATA)

    assert payload["url"] == "https://www.carvajal.com/historia/"
    assert payload["source_type"] == "oficial"
    assert payload["text"] == CHUNK.text


def test_point_id_cambia_si_cambia_la_ficha() -> None:
    assert point_id(CHUNK, "ficha-a") != point_id(CHUNK, "ficha-b")


def test_la_busqueda_hibrida_aplica_el_umbral_vectorial_antes_del_lexico(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: un candidato léxicamente perfecto pero con vector bajo.
    from carvajal.adapters.vector.qdrant_store import ScoredChunk

    store = QdrantVectorStore(cast(Any, FakeClient()))
    bajo = ScoredChunk(
        chunk=Chunk("home:0", "home", "Cali 1904", 0, "home", "core"),
        score=0.1,
        metadata={"abstract": "Cali 1904"},
    )
    monkeypatch.setattr(store, "search", lambda *args, **kwargs: [bajo])

    # Act
    results = store.search_text("Cali 1904", [0.1] * 768, limit=1, min_score=0.3)

    # Assert
    assert results == []
