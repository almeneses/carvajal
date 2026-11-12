"""Pruebas del esquema Core: tablas, claves foráneas y restricciones."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import Table, UniqueConstraint

from carvajal.adapters.storage import tables


def _unique_column_sets(table: Table) -> set[tuple[str, ...]]:
    """Columnas agrupadas por cada ``UniqueConstraint`` de la tabla."""

    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_exporta_las_tres_tablas_del_esquema() -> None:
    """El metadata contiene exactamente las tablas de W1.2."""

    assert set(tables.metadata.tables) == {"documents", "chunks", "document_versions"}


def test_claves_foraneas_borran_en_cascada() -> None:
    """Chunks y versiones desaparecen con su documento."""

    for table in (tables.chunks, tables.document_versions):
        foreign_key = next(iter(table.c.document_id.foreign_keys))
        assert foreign_key.target_fullname == "documents.id"
        assert foreign_key.ondelete == "CASCADE"


def test_slug_de_documento_es_unico() -> None:
    """Un slug identifica a un solo documento."""

    assert ("slug",) in _unique_column_sets(tables.documents)


def test_posicion_de_chunk_es_unica_por_documento() -> None:
    """La posición no se repite dentro del mismo documento."""

    assert ("document_id", "position") in _unique_column_sets(tables.chunks)


def test_indices_de_consulta_existen() -> None:
    """Los índices de filtrado y de historial están declarados."""

    assert "ix_documents_layer_section" in {index.name for index in tables.documents.indexes}
    assert "ix_document_versions_document_created" in {
        index.name for index in tables.document_versions.indexes
    }


def test_el_vector_vive_solo_en_los_fragmentos() -> None:
    """W2.1 añade ``embedding`` a ``chunks`` como vector de 768 dimensiones."""

    assert isinstance(tables.chunks.c.embedding.type, Vector)
    assert tables.chunks.c.embedding.type.dim == 768
    assert tables.chunks.c.embedding.nullable is True
    assert "embedding" not in tables.documents.c
    assert "embedding" not in tables.document_versions.c
