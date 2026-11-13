"""Pruebas de integración de las migraciones Alembic (ida y vuelta)."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_TABLES = {"documents", "chunks", "document_versions", "users", "sessions"}


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
        "embedding",
    }
    assert columns["document_versions"] == {
        "id",
        "document_id",
        "markdown",
        "content_hash",
        "captured_at",
        "created_at",
    }
    assert columns["users"] == {
        "id",
        "provider",
        "subject",
        "email",
        "name",
        "created_at",
        "last_login_at",
    }
    assert columns["sessions"] == {
        "id",
        "user_id",
        "created_at",
        "expires_at",
        "revoked_at",
    }

    user_uniques = {
        tuple(unique["column_names"]) for unique in inspector.get_unique_constraints("users")
    }
    assert ("provider", "subject") in user_uniques

    session_references = inspector.get_foreign_keys("sessions")
    assert any(
        fk["referred_table"] == "users" and fk["options"].get("ondelete") == "CASCADE"
        for fk in session_references
    )

    document_indexes = {index["name"] for index in inspector.get_indexes("documents")}
    version_indexes = {index["name"] for index in inspector.get_indexes("document_versions")}
    assert "ix_documents_layer_section" in document_indexes
    assert "ix_document_versions_document_created" in version_indexes

    chunk_indexes = {index["name"]: index for index in inspector.get_indexes("chunks")}
    hnsw = chunk_indexes["ix_chunks_embedding_hnsw"]["dialect_options"]
    assert hnsw["postgresql_using"] == "hnsw"
    assert hnsw["postgresql_ops"] == {"embedding": "vector_cosine_ops"}

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
