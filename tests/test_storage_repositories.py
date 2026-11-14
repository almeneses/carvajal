"""Pruebas de integración de los repositorios SQL sobre un Postgres real."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, Table, func, select
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.repositories import SqlChunkRepository, SqlDocumentRepository
from carvajal.application.ports import ChunkRepository, DocumentRepository
from carvajal.domain.entities import Chunk, Document
from test_ports import (
    assert_chunk_repository,
    assert_document_repository,
    assert_document_versions,
)

pytestmark = pytest.mark.integration


def _document(
    document_id: str, *, slug: str, markdown: str = "# Documento", content_hash: str | None = None
) -> Document:
    return Document(
        id=document_id,
        url=f"https://www.carvajal.com/{slug}/",
        title=f"Documento {document_id}",
        slug=slug,
        section="home",
        layer="core",
        markdown=markdown,
        content_hash=content_hash or f"hash-{document_id}",
        captured_at=datetime(2026, 11, 8, tzinfo=UTC),
    )


def _chunk(chunk_id: str, document_id: str, position: int) -> Chunk:
    return Chunk(chunk_id, document_id, f"fragmento {chunk_id}", position, "home", "core")


def _count(session: Session, table: Table, document_id: str) -> int:
    query = select(func.count()).select_from(table).where(table.c.document_id == document_id)
    return session.execute(query).scalar_one()


def test_contrato_de_documentos(session: Session) -> None:
    """Los adaptadores SQL cumplen los ``Protocol`` y el contrato de documentos."""

    repository = SqlDocumentRepository(session)
    assert isinstance(repository, DocumentRepository)
    assert_document_repository(repository)
    assert_document_versions(repository)


def test_contrato_de_fragmentos(session: Session) -> None:
    """El repositorio SQL de chunks pasa el contrato con un documento real."""

    SqlDocumentRepository(session).upsert(_document("d", slug="d"))
    repository = SqlChunkRepository(session)
    assert isinstance(repository, ChunkRepository)
    assert_chunk_repository(repository)


def test_list_rechaza_limites_negativos(session: Session) -> None:
    """``limit`` y ``offset`` negativos fallan; ``limit=0`` devuelve vacío."""

    repository = SqlDocumentRepository(session)
    repository.upsert(_document("limites", slug="limites"))

    with pytest.raises(ValueError, match="limit"):
        repository.list(limit=-1)
    with pytest.raises(ValueError, match="offset"):
        repository.list(offset=-1)

    assert repository.list(limit=0) == []
    assert [document.id for document in repository.list(limit=1)] == ["limites"]


def test_list_y_count_filtran_por_texto_y_tipo(session: Session) -> None:
    """La búsqueda es literal e insensible a mayúsculas y ``count`` coincide."""

    repository = SqlDocumentRepository(session)
    primero = replace(
        _document("texto-1", slug="texto-1"),
        title="Informe 100% Real",
        url="https://www.carvajal.com/informe-100/",
    )
    segundo = replace(
        _document("texto-2", slug="texto-2"),
        title="Informe 100X Real",
        url="https://www.carvajal.com/informe-100x/",
    )
    tercero = replace(
        _document("texto-3", slug="texto-3", markdown="# Nota"),
        title=r"Nota externa C:\Carvajal",
        url="https://example.com/nota/",
        section="prensa",
        layer="extended",
        source_type="tercero",
    )
    for document in (primero, segundo, tercero):
        repository.upsert(document)

    # ``%`` y ``_`` no actúan como comodines de LIKE.
    assert [item.id for item in repository.list(q="100%")] == ["texto-1"]
    assert repository.count(q="100%") == 1
    assert repository.list(q="informe_100") == []
    assert repository.count(q="informe_100") == 0

    # La búsqueda ignora mayúsculas en título, sección, slug y URL.
    assert [item.id for item in repository.list(q="INFORME")] == ["texto-1", "texto-2"]
    assert repository.count(q="INFORME") == 2
    assert [item.id for item in repository.list(q="EXAMPLE.COM")] == ["texto-3"]
    assert [item.id for item in repository.list(q=r"C:\Carvajal")] == ["texto-3"]
    assert repository.count(q=r"c:\carvajal") == 1

    # Filtros combinados y conteo sobre el conjunto completo.
    assert repository.count(section="prensa", layer="extended", source_type="tercero") == 1
    assert repository.count(source_type="oficial") == 2
    assert [item.id for item in repository.list(source_type="tercero")] == ["texto-3"]
    assert repository.count(layer="core", q="informe") == 2
    assert [item.id for item in repository.list(layer="core", q="informe")] == [
        "texto-1",
        "texto-2",
    ]


def test_versionado_y_slug_inmutable(session: Session) -> None:
    """v1→v2→v2→v3 deja dos versiones y un slug no puede cambiar de id."""

    repository = SqlDocumentRepository(session)
    first = _document("doc", slug="doc", markdown="# v1", content_hash="h1")
    second = replace(first, markdown="# v2", content_hash="h2")
    third = replace(first, markdown="# v3", content_hash="h3")

    repository.upsert(first)
    repository.upsert(second)
    repository.upsert(second)
    repository.upsert(third)

    versions = repository.list_versions("doc")
    assert [version.content_hash for version in versions] == ["h2", "h1"]
    assert [version.markdown for version in versions] == ["# v2", "# v1"]
    assert repository.get("doc") == third

    with pytest.raises(ValueError, match="slug"):
        repository.upsert(_document("impostor", slug="doc"))


def test_delete_borra_fragmentos_y_versiones(session: Session) -> None:
    """La cascada de la base elimina chunks y versiones del documento."""

    documents = SqlDocumentRepository(session)
    chunks = SqlChunkRepository(session)
    first = _document("borrar", slug="borrar", markdown="# uno", content_hash="h1")
    documents.upsert(first)
    documents.upsert(replace(first, markdown="# dos", content_hash="h2"))
    chunks.replace("borrar", [_chunk("c1", "borrar", 0)])

    assert documents.delete("borrar") is True
    assert documents.delete("borrar") is False
    assert _count(session, tables.chunks, "borrar") == 0
    assert _count(session, tables.document_versions, "borrar") == 0


def test_replace_reemplaza_y_valida_pertenencia_y_posicion(session: Session) -> None:
    """Reemplazar dos veces no acumula filas y las entradas inválidas no tocan nada."""

    documents = SqlDocumentRepository(session)
    chunks = SqlChunkRepository(session)
    documents.upsert(_document("con-chunks", slug="con-chunks"))

    chunks.replace("con-chunks", [_chunk("c1", "con-chunks", 0), _chunk("c2", "con-chunks", 1)])
    chunks.replace("con-chunks", [_chunk("c3", "con-chunks", 0)])
    assert [chunk.id for chunk in chunks.list_by_document("con-chunks")] == ["c3"]

    with pytest.raises(ValueError, match="pertenece"):
        chunks.replace("con-chunks", [_chunk("c4", "ajeno", 0)])
    with pytest.raises(ValueError, match="posiciones duplicadas"):
        chunks.replace(
            "con-chunks",
            [_chunk("c5", "con-chunks", 0), _chunk("c6", "con-chunks", 0)],
        )

    assert [chunk.id for chunk in chunks.list_by_document("con-chunks")] == ["c3"]


def test_upsert_en_carrera_no_pisa_un_id_ajeno(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si el SELECT no ve la fila y otra transacción la insertó, no se pisa."""

    repository = SqlDocumentRepository(session)
    repository.upsert(_document("ajeno", slug="carrera", content_hash="h1"))

    original = repository._select_for_update
    calls = {"n": 0}

    def select_sin_ver_la_fila(slug: str):
        calls["n"] += 1
        return None if calls["n"] == 1 else original(slug)

    monkeypatch.setattr(repository, "_select_for_update", select_sin_ver_la_fila)

    with pytest.raises(ValueError, match="slug"):
        repository.upsert(_document("nuevo", slug="carrera", content_hash="h2"))


def test_la_sesion_solo_publica_con_commit_externo(engine: Engine, session: Session) -> None:
    """Un ``commit()`` de la sesión no publica la transacción externa del fixture."""

    SqlDocumentRepository(session).upsert(_document("aislado", slug="aislado"))
    session.commit()

    query = select(tables.documents.c.id).where(tables.documents.c.id == "aislado")
    assert session.execute(query).scalar_one_or_none() == "aislado"

    with engine.connect() as otra_conexion:
        assert otra_conexion.execute(query).scalar_one_or_none() is None
