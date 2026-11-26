"""Vista previa de los hilos del panel lateral: la primera pregunta de la persona."""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.chat import SqlChatRepository


@pytest.fixture()
def session() -> Iterator[Session]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    with motor.begin() as conexion:
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    with Session(motor) as sesion:
        yield sesion
    motor.dispose()


def _previews(repo: SqlChatRepository, owner: str) -> dict[str, Any]:
    return {item.id: item.preview for item in repo.summarize(owner)}


def test_la_vista_previa_es_la_primera_pregunta_de_la_persona(session: Session) -> None:
    # Arrange: el último mensaje es la respuesta del asistente («Para seguir me falta…»).
    repo = SqlChatRepository(session)
    hilo = repo.create("dueno")
    repo.append_turn(hilo.id, question="Cotízame 300 portacomidas C1", answer="¿Cuál de las tres?")
    repo.append_turn(
        hilo.id, question="Medellín", answer="Para seguir me falta un dato: la ciudad."
    )

    # Act
    previews = _previews(repo, "dueno")

    # Assert
    assert previews[hilo.id] == "Cotízame 300 portacomidas C1"


def test_un_hilo_vacio_sigue_sin_vista_previa(session: Session) -> None:
    # Arrange
    repo = SqlChatRepository(session)
    hilo = repo.create("dueno")

    # Act / Assert
    assert _previews(repo, "dueno")[hilo.id] == ""
