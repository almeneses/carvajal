"""Pruebas de integración del servicio de chat contra un Postgres real."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, delete, func, select

from apps.api.chat.service import ChatService
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.application.ask import NO_CONTEXT_ANSWER

pytestmark = pytest.mark.integration


class FakeEmbedder:
    """Embedder de 768 dimensiones; opcionalmente falla sin tocar la red."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def embed(self, text: str) -> list[float]:
        if self._error is not None:
            raise self._error
        return [0.0] * 768


class FakeLLM:
    """Sin fragmentos recuperados el caso de uso no debe llamar al LLM."""

    def complete(self, *, system: str, prompt: str) -> str:
        raise AssertionError("El LLM no debería invocarse sin contexto.")

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        raise AssertionError("El LLM no debería invocarse sin contexto.")


def test_servicio_persiste_usuario_y_asistente_al_terminar_bien(engine: Engine) -> None:
    # Arrange: sin fragmentos, el caso de uso responde sin llamar al LLM.
    service = ChatService(build_session_factory(engine), FakeEmbedder(), FakeLLM())
    owner_key = "servicio-ok"

    try:
        # Act
        answer = service.ask(owner_key, "¿Dato del corpus?")
        history = list(service.history(owner_key))

        # Assert
        assert answer.text == NO_CONTEXT_ANSWER
        assert [message.role for message in history] == ["user", "assistant"]
        assert history[0].content == "¿Dato del corpus?"
        assert history[1].content == NO_CONTEXT_ANSWER
        assert history[1].sources == []
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(tables.conversations).where(tables.conversations.c.owner_key == owner_key)
            )


def test_servicio_revierte_la_conversacion_si_falla_el_embedder(engine: Engine) -> None:
    # Arrange
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(EmbeddingError("Ollama no responde.")),
        FakeLLM(),
    )
    owner_key = "servicio-fallo"

    # Act / Assert
    with pytest.raises(EmbeddingError):
        service.ask(owner_key, "¿Dato del corpus?")

    with engine.connect() as connection:
        count = connection.execute(
            select(func.count())
            .select_from(tables.conversations)
            .where(tables.conversations.c.owner_key == owner_key)
        ).scalar_one()
    assert count == 0
