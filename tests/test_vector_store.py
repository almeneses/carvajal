"""Pruebas de integración del índice vectorial pgvector sobre Postgres real."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from carvajal.adapters.storage.repositories import SqlChunkRepository, SqlDocumentRepository
from carvajal.adapters.vector.pgvector_store import DIMENSIONS, PgVectorStore
from carvajal.application.ports import VectorStore
from carvajal.domain.entities import Chunk, Document

pytestmark = pytest.mark.integration


def _vector(*values: float) -> list[float]:
    """Completa el vector con ceros hasta la dimensión del esquema."""

    return [*values, *([0.0] * (DIMENSIONS - len(values)))]


def _document(document_id: str, *, section: str = "home", layer: str = "core") -> Document:
    return Document(
        id=document_id,
        url=f"https://www.carvajal.com/{document_id}/",
        title=f"Documento {document_id}",
        slug=document_id,
        section=section,
        layer=layer,
        markdown=f"# {document_id}",
        content_hash=f"hash-{document_id}",
        captured_at=datetime(2026, 11, 11, tzinfo=UTC),
    )


def _chunk(
    chunk_id: str,
    document_id: str,
    position: int,
    *,
    section: str = "home",
    layer: str = "core",
) -> Chunk:
    return Chunk(chunk_id, document_id, f"fragmento {chunk_id}", position, section, layer)


def _seeded(session: Session) -> tuple[PgVectorStore, list[Chunk]]:
    """Crea dos documentos y tres fragmentos con vectores conocidos."""

    documents = SqlDocumentRepository(session)
    chunks = SqlChunkRepository(session)
    documents.upsert(_document("d1"))
    documents.upsert(_document("d2", section="prensa", layer="extended"))
    core = [_chunk("c1", "d1", 0), _chunk("c2", "d1", 1)]
    extended = [_chunk("c3", "d2", 0, section="prensa", layer="extended")]
    chunks.replace("d1", core)
    chunks.replace("d2", extended)

    store = PgVectorStore(session)
    store.upsert(core, [_vector(1.0), _vector(0.9, 0.1)])
    store.upsert(extended, [_vector(0.0, 1.0)])
    return store, [*core, *extended]


def test_upsert_y_orden_por_similitud(session: Session) -> None:
    """Los resultados llegan de mayor a menor similitud coseno y sin duplicar filas."""

    store, chunks = _seeded(session)
    assert isinstance(store, VectorStore)

    results = store.search(_vector(1.0))
    assert [result.chunk.id for result in results] == ["c1", "c2", "c3"]
    assert results[0].chunk == chunks[0]
    assert results[0].score >= results[1].score >= results[2].score

    store.upsert([chunks[0]], [_vector(-1.0)])
    assert [result.chunk.id for result in store.search(_vector(1.0))] == ["c2", "c3", "c1"]
    assert len(SqlChunkRepository(session).list_by_document("d1")) == 2


def test_search_no_trae_el_vector_en_la_proyeccion(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La consulta pide la entidad y la distancia, nunca el vector de 768."""

    store, _ = _seeded(session)
    statements: list[Any] = []
    original = session.execute

    def capture(statement: Any, *args: Any, **kwargs: Any) -> Any:
        statements.append(statement)
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", capture)
    store.search(_vector(1.0), limit=1)

    columns = set(statements[-1].selected_columns.keys())
    assert columns == {"id", "document_id", "text", "position", "section", "layer", "distance"}


def test_upsert_rechaza_reutilizar_un_id_desde_otro_documento(session: Session) -> None:
    """Un id ya usado por otro documento no cambia de dueño ni de vector."""

    store, chunks = _seeded(session)
    before = [(result.chunk.id, result.score) for result in store.search(_vector(1.0))]

    with pytest.raises(ValueError, match="inmutable"):
        store.upsert([replace(chunks[0], document_id="d2", position=5)], [_vector(0.0, 1.0)])

    assert [(result.chunk.id, result.score) for result in store.search(_vector(1.0))] == before
    assert [chunk.id for chunk in SqlChunkRepository(session).list_by_document("d2")] == ["c3"]


def test_filtros_y_limites(session: Session) -> None:
    """Los filtros acotan por capa y sección; ``limit=0`` vale y el negativo falla."""

    store, _ = _seeded(session)

    assert [result.chunk.id for result in store.search(_vector(1.0), layer="extended")] == ["c3"]
    assert [result.chunk.id for result in store.search(_vector(1.0), section="home")] == [
        "c1",
        "c2",
    ]
    assert store.search(_vector(1.0), layer="core", section="prensa") == []
    assert len(store.search(_vector(1.0), limit=2)) == 2
    assert store.search(_vector(1.0), limit=0) == []
    assert store.search([0.1] * 10, limit=0) == []
    with pytest.raises(ValueError, match="limit"):
        store.search(_vector(1.0), limit=-1)


def test_delete_by_document_quita_del_indice_sin_borrar_filas(session: Session) -> None:
    """El borrado anula el vector y deja las filas al repositorio de fragmentos."""

    store, _ = _seeded(session)

    store.delete_by_document("d1")

    assert [result.chunk.id for result in store.search(_vector(1.0))] == ["c3"]
    assert [chunk.id for chunk in SqlChunkRepository(session).list_by_document("d1")] == [
        "c1",
        "c2",
    ]
    store.delete_by_document("inexistente")


def test_valida_dimensiones_y_cantidades(session: Session) -> None:
    """Un vector vacío, de otra dimensión o desparejado lanza ``ValueError``."""

    store, (chunk, _, _) = _seeded(session)

    with pytest.raises(ValueError, match="vacío"):
        store.upsert([chunk], [[]])
    with pytest.raises(ValueError, match=str(DIMENSIONS)):
        store.upsert([chunk], [[0.1] * (DIMENSIONS - 1)])
    with pytest.raises(ValueError, match="exactamente un vector"):
        store.upsert([chunk], [])
    with pytest.raises(ValueError, match=str(DIMENSIONS)):
        store.search([0.1] * 10)


def test_indice_vacio_devuelve_vacio(session: Session) -> None:
    """Sin fragmentos vectorizados la búsqueda no inventa resultados."""

    assert PgVectorStore(session).search(_vector(1.0)) == []
