"""Pruebas de integración de las migraciones Alembic (ida y vuelta)."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_TABLES = {"documents", "chunks", "document_versions"}


def _alembic_config(migration_url: str) -> Config:
    """Config apuntando a las migraciones del repositorio y a la base de prueba."""

    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    return config


def _assert_initial_schema(engine: Engine) -> None:
    """Comprueba tablas, columnas e índices que debe dejar la revisión 0001."""

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
    assert "embedding" not in columns["chunks"]

    document_indexes = {index["name"] for index in inspector.get_indexes("documents")}
    version_indexes = {index["name"] for index in inspector.get_indexes("document_versions")}
    assert "ix_documents_layer_section" in document_indexes
    assert "ix_document_versions_document_created" in version_indexes

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
        # Act / Assert: esquema inicial
        command.upgrade(config, "head")
        _assert_initial_schema(engine)

        # Act / Assert: downgrade total
        command.downgrade(config, "base")
        remaining = set(inspect(engine).get_table_names())
        assert not (_SCHEMA_TABLES & remaining)

        # Act / Assert: se puede volver a aplicar
        command.upgrade(config, "head")
        _assert_initial_schema(engine)
    finally:
        engine.dispose()
