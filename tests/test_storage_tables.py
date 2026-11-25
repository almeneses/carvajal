"""Pruebas del esquema Core: tablas, claves foráneas y restricciones."""

from sqlalchemy import CheckConstraint, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from carvajal.adapters.storage import tables


def _unique_column_sets(table: Table) -> set[tuple[str, ...]]:
    """Columnas agrupadas por cada ``UniqueConstraint`` de la tabla."""

    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_exporta_las_tablas_del_esquema() -> None:
    """El metadata contiene corpus, chat, cupo, marcas (038), memoria (042) y webhook."""

    assert set(tables.metadata.tables) == {
        "documents",
        "chunks",
        "document_versions",
        "conversations",
        "messages",
        "usage_day",
        "chat_feedback",
        "visitor_memory",
        "quote_drafts",
        "quotes",
        "webhook_deliveries",
    }


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


def test_los_fragmentos_no_guardan_vectores() -> None:
    """US5 retira ``embedding``: el único índice de respuestas es Qdrant."""

    assert "embedding" not in tables.chunks.c
    assert "embedding" not in tables.documents.c
    assert "embedding" not in tables.document_versions.c


def test_el_esquema_del_chat_tiene_cascada_rol_e_indices() -> None:
    """W3.3 relaciona los mensajes con su conversación y limita el rol."""

    foreign_key = next(iter(tables.messages.c.conversation_id.foreign_keys))
    assert foreign_key.target_fullname == "conversations.id"
    assert foreign_key.ondelete == "CASCADE"

    checks = {
        constraint.name
        for constraint in tables.messages.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_messages_role" in checks
    assert isinstance(tables.messages.c.sources.type, JSONB)
    assert tables.messages.c.sources.nullable is True

    assert "ix_conversations_owner_updated" in {
        index.name for index in tables.conversations.indexes
    }
    assert "ix_messages_conversation_created" in {index.name for index in tables.messages.indexes}


def test_la_cotizacion_versionada_admite_total_ausente() -> None:
    """El contrato de la 043 separa importes y deja el total nulo si falta un componente."""

    assert tables.quotes.c.total.nullable is True
    assert tables.quotes.c.valid_until.nullable is False
    assert {
        "version",
        "issued_at",
        "subtotal",
        "tax_status",
        "tax_rate",
        "shipping_cost",
        "commercial_valid_until",
    } <= set(tables.quotes.c.keys())


def test_el_cupo_se_identifica_por_visitante_y_dia() -> None:
    """``usage_day`` usa (``owner_key``, ``day``) como clave primaria."""

    assert [column.name for column in tables.usage_day.primary_key] == ["owner_key", "day"]
    assert tables.usage_day.c.requests.nullable is False
    assert tables.usage_day.c.tokens.nullable is False
