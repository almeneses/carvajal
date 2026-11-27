"""Pruebas del adaptador Qdrant con un cliente falso; sin red."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from qdrant_client import models

from carvajal.adapters.vector.qdrant_store import QdrantVectorStore, payload_for, point_id
from carvajal.application.sync import sync_fingerprint
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
        self.payload_schema: dict[str, Any] = {}
        self.indexed: list[str] = []

    def collection_exists(self, name: str) -> bool:
        if self.fail:
            raise ConnectionError("Qdrant caído")
        return self.exists

    def create_collection(self, collection_name: str, vectors_config: Any) -> None:
        self.created.append({"name": collection_name, "config": vectors_config})
        self.exists = True

    def get_collection(self, name: str) -> Any:
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=models.VectorParams(size=self.size, distance=models.Distance.COSINE)
                )
            ),
            payload_schema=dict(self.payload_schema),
        )

    def create_payload_index(
        self, collection_name: str, field_name: str, field_schema: Any, wait: bool
    ) -> None:
        self.indexed.append(field_name)
        self.payload_schema[field_name] = field_schema

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
        records = self.records
        query_filter = kwargs.get("scroll_filter")
        if query_filter is not None:
            for condition in query_filter.must or ():
                key = condition.key
                value = condition.match.value
                records = [record for record in records if (record.payload or {}).get(key) == value]
        return records, None

    def delete(self, collection_name: str, points_selector: Any, wait: bool) -> None:
        self.deleted.append(points_selector)

    def count(self, name: str, exact: bool) -> Any:
        if not self.exists:
            raise ValueError(f"Collection {name} not found")
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


def test_ensure_collection_crea_los_indices_de_payload_de_los_filtros() -> None:
    # Arrange
    client = FakeClient(exists=False)
    store = QdrantVectorStore(cast(Any, client))

    # Act
    store.ensure_collection()

    # Assert
    assert client.indexed == ["layer", "section", "document_id"]
    assert client.payload_schema["layer"] == models.PayloadSchemaType.KEYWORD


def test_ensure_collection_no_crea_indices_sobre_una_coleccion_existente() -> None:
    """La app web pasa por ``ensure_collection`` (Configuración, captura): no construye
    índices sobre ~118 k puntos dentro de una petición; eso es de ``index-qdrant``."""

    # Arrange
    client = FakeClient(exists=True)

    # Act
    created = QdrantVectorStore(cast(Any, client)).ensure_collection()

    # Assert
    assert created is False
    assert client.indexed == []


def test_los_indices_de_payload_son_idempotentes_sobre_una_coleccion_existente() -> None:
    # Arrange: producción tenía ``payload_schema`` vacío; una segunda corrida no repite nada.
    client = FakeClient(exists=True)
    client.payload_schema = {"layer": models.PayloadSchemaType.KEYWORD}
    store = QdrantVectorStore(cast(Any, client))

    # Act
    first = store.ensure_payload_indexes()
    again = store.ensure_payload_indexes()

    # Assert
    assert first == ["section", "document_id"]
    assert client.indexed == ["section", "document_id"]
    assert again == []


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


def test_metadata_keys_cubre_los_campos_mutables_del_payload() -> None:
    """La firma del atajo debe cubrir todo lo que cambia sin tocar el texto."""

    from carvajal.adapters.vector.qdrant_store import METADATA_KEYS

    inmutables = {"chunk_id", "document_id", "slug", "text", "position", "section"}
    mutables = set(payload_for(CHUNK, METADATA)) - inmutables

    assert mutables <= set(METADATA_KEYS)


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


def test_una_coleccion_ausente_es_un_indice_vacio() -> None:
    """La primera sincronización lee huellas antes de que la colección exista."""

    store = QdrantVectorStore(cast(Any, FakeClient(exists=False)))

    assert store.document_hashes("core") == {}
    assert store.count() == 0


def test_la_huella_solo_se_publica_con_todos_los_fragmentos() -> None:
    """Un documento a medias no se da por sincronizado: la siguiente corrida lo rehace."""

    def registro(ident: int) -> models.Record:
        return models.Record(
            id=ident,
            payload={
                "document_id": "historia",
                "layer": "core",
                "content_hash": "hash-1",
                "chunks_total": "3",
            },
        )

    completo = FakeClient()
    completo.records = [registro(1), registro(2), registro(3)]
    parcial = FakeClient()
    parcial.records = [registro(1), registro(2)]

    # Act / Assert
    esperado = sync_fingerprint(content_hash="hash-1", title="", section="", source_type="")
    assert QdrantVectorStore(cast(Any, completo)).document_hashes("core") == {"historia": esperado}
    assert QdrantVectorStore(cast(Any, parcial)).document_hashes("core") == {}


def test_los_documentos_incompletos_cuentan_para_la_poda() -> None:
    """Sus fragmentos existen: si el Markdown desaparece, la poda debe borrarlos."""

    def registro(ident: int, *, total: str) -> models.Record:
        return models.Record(
            id=ident,
            payload={
                "document_id": "historia",
                "layer": "core",
                "content_hash": "hash-1",
                "chunks_total": total,
            },
        )

    cliente = FakeClient()
    cliente.records = [registro(1, total="3"), registro(2, total="3")]
    store = QdrantVectorStore(cast(Any, cliente))

    # Act / Assert
    assert store.document_hashes("core") == {}
    assert store.document_ids("core") == {"historia"}


def test_delete_by_document_usa_el_document_id_no_el_titulo() -> None:
    """Dos documentos con el mismo título se distinguen por ``document_id``."""

    # Arrange
    client = FakeClient()
    store = QdrantVectorStore(cast(Any, client))

    # Act
    store.delete_by_document("doc-a")

    # Assert
    selector = client.deleted[-1]
    condition = selector.filter.must[0]
    assert condition.key == "document_id"
    assert condition.match.value == "doc-a"


def test_document_hashes_y_ids_filtran_por_capa() -> None:
    """La verificación compara huellas y documentos filtrados por capa."""

    # Arrange
    client = FakeClient()
    client.records = [
        models.Record(
            id="1",
            payload={
                "document_id": "historia",
                "content_hash": "h1",
                "layer": "core",
                "chunks_total": "1",
            },
        ),
        models.Record(
            id="2",
            payload={
                "document_id": "externo",
                "content_hash": "h2",
                "layer": "extended",
                "chunks_total": "1",
            },
        ),
    ]
    store = QdrantVectorStore(cast(Any, client))

    # Act
    hashes = store.document_hashes("core")

    # Assert
    assert hashes == {
        "historia": sync_fingerprint(content_hash="h1", title="", section="", source_type="")
    }
    assert store.document_ids("core") == {"historia"}
