"""Pruebas de integración de las migraciones Alembic (ida y vuelta)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, select
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.quotes import SqlQuoteRepository

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_TABLES = {
    "documents",
    "chunks",
    "document_versions",
    "conversations",
    "messages",
    "usage_day",
    "chat_feedback",
    "webhook_deliveries",
}


def _alembic_config(migration_url: str) -> Config:
    """Config apuntando a las migraciones del repositorio y a la base de prueba."""

    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    return config


def _assert_head_schema(engine: Engine) -> None:
    """Comprueba tablas, columnas e índices que debe dejar la migración vigente."""

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= _SCHEMA_TABLES

    columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in _SCHEMA_TABLES
    }
    assert columns["documents"] == {
        "id",
        "url",
        "title",
        "slug",
        "section",
        "layer",
        "markdown",
        "content_hash",
        "captured_at",
        "source_type",
    }
    assert columns["chunks"] == {
        "id",
        "document_id",
        "text",
        "position",
        "section",
        "layer",
    }
    assert columns["document_versions"] == {
        "id",
        "document_id",
        "markdown",
        "content_hash",
        "captured_at",
        "created_at",
    }
    assert columns["conversations"] == {"id", "owner_key", "created_at", "updated_at"}
    assert columns["messages"] == {
        "id",
        "conversation_id",
        "role",
        "content",
        "sources",
        "status",
        "client_turn_id",
        "created_at",
    }
    assert columns["usage_day"] == {"owner_key", "day", "requests", "tokens"}

    usage_pk = inspector.get_pk_constraint("usage_day")["constrained_columns"]
    assert usage_pk == ["owner_key", "day"]

    assert columns["webhook_deliveries"] == {
        "owner_key",
        "delivery_id",
        "status",
        "reply",
        "created_at",
        "updated_at",
    }
    delivery_pk = inspector.get_pk_constraint("webhook_deliveries")["constrained_columns"]
    assert delivery_pk == ["owner_key", "delivery_id"]
    delivery_indexes = {index["name"] for index in inspector.get_indexes("webhook_deliveries")}
    assert "ix_webhook_deliveries_updated" in delivery_indexes

    document_indexes = {index["name"] for index in inspector.get_indexes("documents")}
    version_indexes = {index["name"] for index in inspector.get_indexes("document_versions")}
    assert "ix_documents_layer_section" in document_indexes
    assert "ix_document_versions_document_created" in version_indexes

    chunk_indexes = {index["name"] for index in inspector.get_indexes("chunks")}
    assert "ix_chunks_embedding_hnsw" not in chunk_indexes

    chunk_uniques = {
        tuple(unique["column_names"]) for unique in inspector.get_unique_constraints("chunks")
    }
    assert ("document_id", "position") in chunk_uniques

    document_uniques = {
        tuple(unique["column_names"]) for unique in inspector.get_unique_constraints("documents")
    }
    assert ("slug",) in document_uniques

    for table in ("chunks", "document_versions"):
        references = inspector.get_foreign_keys(table)
        assert any(
            fk["referred_table"] == "documents" and fk["options"].get("ondelete") == "CASCADE"
            for fk in references
        )

    defaults = {column["name"]: column["default"] for column in inspector.get_columns("documents")}
    assert "oficial" in (defaults["source_type"] or "")


def test_upgrade_y_downgrade_son_reversibles(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Desde cero: sube, inspecciona, baja y vuelve a subir sin residuos."""

    # Arrange
    monkeypatch.setenv("DATABASE_URL", migration_database_url)
    config = _alembic_config(migration_database_url)
    engine = create_engine(migration_database_url)

    try:
        # Act / Assert: esquema vigente
        command.upgrade(config, "head")
        _assert_head_schema(engine)

        # Act / Assert: downgrade total
        command.downgrade(config, "base")
        remaining = set(inspect(engine).get_table_names())
        assert not (_SCHEMA_TABLES & remaining)

        # Act / Assert: se puede volver a aplicar
        command.upgrade(config, "head")
        _assert_head_schema(engine)
    finally:
        engine.dispose()


def test_0010_conserva_y_lee_una_cotizacion_historica(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La migración versionada no borra ni reescribe las filas de la 042."""

    # Arrange: base en 0009 con una fila histórica
    monkeypatch.setenv("DATABASE_URL", migration_database_url)
    config = _alembic_config(migration_database_url)
    engine = create_engine(migration_database_url)
    payload = {
        "kind": "cotizacion",
        "primary": {"currency": "COP", "presentation": {"subtotal": "3751"}},
        "total": "3751",
    }
    try:
        command.upgrade(config, "0009")
        with engine.begin() as connection:
            connection.execute(
                tables.quotes.insert().values(
                    id="q-history",
                    owner_key="cot:ana",
                    valid_until=date(2026, 11, 29),
                    total=Decimal("3751"),
                    currency="COP",
                    payload=payload,
                )
            )

        # Act: sube al contrato versionado
        command.upgrade(config, "head")
        with Session(engine) as session:
            record = SqlQuoteRepository(session).resolve("q-history", "cot:ana")

        # Assert: el histórico sigue legible con el contrato normalizado
        assert record is not None
        assert record.version == 1
        assert record.total == Decimal("3751")
        assert record.subtotal == Decimal("3751")
        assert record.payload["total"] == "3751"

        # Assert: la vuelta a 0009 conserva la fila
        command.downgrade(config, "0009")
        with engine.connect() as connection:
            total = connection.execute(
                select(tables.quotes.c.total).where(tables.quotes.c.id == "q-history")
            ).scalar_one()
        assert total == Decimal("3751")
    finally:
        engine.dispose()


def test_0006_retira_y_restaura_la_columna_de_vectores(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El paso 0006 es reversible: baja a 0005 y vuelve a subir."""

    # Arrange
    monkeypatch.setenv("DATABASE_URL", migration_database_url)
    config = _alembic_config(migration_database_url)
    engine = create_engine(migration_database_url)

    def columns() -> set[str]:
        return {column["name"] for column in inspect(engine).get_columns("chunks")}

    try:
        # Act / Assert: en head no hay columna de vectores
        command.upgrade(config, "head")
        assert "embedding" not in columns()

        # Act / Assert: el downgrade la restaura
        command.downgrade(config, "0005")
        assert "embedding" in columns()

        # Act / Assert: volver a subir la retira otra vez
        command.upgrade(config, "head")
        assert "embedding" not in columns()
    finally:
        engine.dispose()
