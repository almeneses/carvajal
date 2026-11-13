"""Pruebas de integración del almacén del chat contra un Postgres real."""

from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Table, create_engine, delete, func, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.chat import SqlChatRepository

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = {
    "chunk_id": "historia-1",
    "slug": "historia",
    "section": "historia",
    "layer": "core",
    "score": 0.9,
    "excerpt": "Inició operaciones en 1904.",
}


def _count(session: Session, table: Table, column: str, value: str) -> int:
    query = select(func.count()).select_from(table).where(table.c[column] == value)
    return session.execute(query).scalar_one()


def _alembic_config(migration_url: str) -> Config:
    """Config apuntando a las migraciones del repositorio y a la base de prueba."""

    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    return config


def test_get_or_create_es_estable_y_append_persiste_el_turno(session: Session) -> None:
    # Arrange
    repository = SqlChatRepository(session)

    # Act
    first = repository.get_or_create("dueno-a")
    second = repository.get_or_create("dueno-a")
    repository.append_turn(
        first.id,
        question="¿Cuándo nació?",
        answer="Carvajal nació en 1904 [1].",
        sources=[_SOURCE],
    )
    history = repository.history(first.id)

    # Assert
    assert second.id == first.id
    assert [message.role for message in history] == ["user", "assistant"]
    assert history[0].content == "¿Cuándo nació?"
    assert history[0].sources is None
    assert history[1].content == "Carvajal nació en 1904 [1]."
    assert history[1].sources == [_SOURCE]
    active = repository.active("dueno-a")
    assert active is not None
    assert active.updated_at > first.created_at


def test_active_es_la_ultima_conversacion_actualizada(session: Session) -> None:
    # Arrange
    repository = SqlChatRepository(session)
    first = repository.create("dueno-a")
    second = repository.create("dueno-a")

    # Act / Assert: la más reciente gana.
    active = repository.active("dueno-a")
    assert active is not None
    assert active.id == second.id

    # Act: tocar la primera la vuelve a hacer activa.
    repository.touch(first.id, when=second.updated_at + timedelta(seconds=1))

    # Assert
    active = repository.active("dueno-a")
    assert active is not None
    assert active.id == first.id


def test_history_limita_a_los_50_mensajes_mas_recientes(session: Session) -> None:
    # Arrange
    repository = SqlChatRepository(session)
    conversation = repository.get_or_create("dueno-a")
    for turn in range(30):
        repository.append_turn(
            conversation.id,
            question=f"p{turn}",
            answer=f"a{turn}",
        )

    # Act
    history = repository.history(conversation.id)

    # Assert: del turno 5 en adelante, en orden ascendente.
    assert len(history) == 50
    assert [message.content for message in history[:3]] == ["p5", "a5", "p6"]
    assert history[-1].content == "a29"


def test_borrar_la_conversacion_arrastra_los_mensajes(session: Session) -> None:
    # Arrange
    repository = SqlChatRepository(session)
    conversation = repository.get_or_create("dueno-a")
    repository.append_turn(
        conversation.id,
        question="¿?",
        answer="Respuesta.",
        sources=[_SOURCE],
    )

    # Act
    session.execute(
        delete(tables.conversations).where(tables.conversations.c.id == conversation.id)
    )
    session.flush()

    # Assert
    assert _count(session, tables.messages, "conversation_id", conversation.id) == 0


def test_append_turn_falla_si_la_conversacion_no_existe(session: Session) -> None:
    # Arrange
    repository = SqlChatRepository(session)

    # Act / Assert
    with pytest.raises(LookupError, match="No existe la conversación"):
        repository.append_turn("inexistente", question="¿?", answer="No.")

    assert session.execute(select(func.count()).select_from(tables.messages)).scalar_one() == 0


def test_history_rechaza_limites_negativos_y_cero_devuelve_vacio(session: Session) -> None:
    # Arrange
    repository = SqlChatRepository(session)
    conversation = repository.get_or_create("dueno-a")

    # Act / Assert
    with pytest.raises(ValueError, match="limit"):
        repository.history(conversation.id, limit=-1)
    assert repository.history(conversation.id, limit=0) == []


def test_migracion_0003_crea_y_revierte_las_tablas(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Desde 0002: sube, inspecciona, baja y vuelve a subir sin residuos."""

    # Arrange
    monkeypatch.setenv("DATABASE_URL", migration_database_url)
    config = _alembic_config(migration_database_url)
    engine = create_engine(migration_database_url)

    try:
        command.upgrade(config, "0002")
        assert "conversations" not in set(inspect(engine).get_table_names())

        # Act
        command.upgrade(config, "head")

        # Assert: tablas, columnas, índices, cascada y check de rol.
        inspector = inspect(engine)
        names = set(inspector.get_table_names())
        assert {"conversations", "messages"} <= names
        assert {column["name"] for column in inspector.get_columns("conversations")} == {
            "id",
            "owner_key",
            "created_at",
            "updated_at",
        }
        message_columns = {column["name"]: column for column in inspector.get_columns("messages")}
        assert set(message_columns) == {
            "id",
            "conversation_id",
            "role",
            "content",
            "sources",
            "status",
            "client_turn_id",
            "created_at",
        }
        assert isinstance(message_columns["sources"]["type"], JSONB)

        assert "ix_conversations_owner_updated" in {
            index["name"] for index in inspector.get_indexes("conversations")
        }
        assert "ix_messages_conversation_created" in {
            index["name"] for index in inspector.get_indexes("messages")
        }
        references = inspector.get_foreign_keys("messages")
        assert any(
            reference["referred_table"] == "conversations"
            and reference["options"].get("ondelete") == "CASCADE"
            for reference in references
        )
        checks = inspector.get_check_constraints("messages")
        assert any(check["name"] == "ck_messages_role" for check in checks)
        assert any(check["name"] == "ck_messages_status" for check in checks)
        assert "uq_messages_turn" in {index["name"] for index in inspector.get_indexes("messages")}

        # Act: bajar solo 0003.
        command.downgrade(config, "0002")

        # Assert: las tablas desaparecen y se pueden volver a crear.
        remaining = set(inspect(engine).get_table_names())
        assert "conversations" not in remaining
        assert "messages" not in remaining
        command.upgrade(config, "head")
        assert {"conversations", "messages"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
