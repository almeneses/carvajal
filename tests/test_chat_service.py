"""Pruebas de integración del servicio de chat contra un Postgres real."""

from collections.abc import Iterator
from dataclasses import asdict

import pytest
from sqlalchemy import Engine, delete, func, select

from apps.api.chat.service import SERVICE_UNAVAILABLE, ChatService
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.application.ask import (
    NO_CONTEXT_ANSWER,
    AskQuestion,
    LLMError,
    PreparedQuestion,
    Source,
)

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


SOURCE = Source("historia-1", "historia", "historia", "core", 0.9, "Inició en 1904.")
PREPARED = PreparedQuestion(prompt="Contexto con [1].", sources=(SOURCE,))


class FailingStreamLLM:
    """El stream falla antes del primer token; ``complete`` entrega el respaldo."""

    def __init__(self) -> None:
        self.stream_calls = 0
        self.complete_calls = 0

    def complete(self, *, system: str, prompt: str) -> str:
        self.complete_calls += 1
        return "Respuesta completa de respaldo [1]."

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.stream_calls += 1
        raise LLMError("el stream se cayó")
        yield ""  # pragma: no cover - hace del método un generador


class BrokenAfterTokenLLM:
    """Falla después de entregar el primer fragmento."""

    def complete(self, *, system: str, prompt: str) -> str:
        raise AssertionError("No debe completarse una respuesta ya empezada.")

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        yield "Carvajal"
        raise LLMError("corte a mitad del stream")


def _fixed_prepare(self: AskQuestion, question: str) -> PreparedQuestion:
    """Evita la recuperación vectorial en las pruebas de fallo del LLM."""

    return PREPARED


def _delete_owner(engine: Engine, owner_key: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            delete(tables.conversations).where(tables.conversations.c.owner_key == owner_key)
        )


def test_stream_persiste_un_solo_turno_y_emite_done(engine: Engine) -> None:
    # Arrange: sin fragmentos, el LLM no se invoca y la respuesta es la ausencia.
    service = ChatService(build_session_factory(engine), FakeEmbedder(), FakeLLM())
    owner_key = "stream-ok"

    try:
        # Act
        events = list(service.stream(owner_key, "¿Dato del corpus?"))
        history = list(service.history(owner_key))

        # Assert: un único turno = exactamente dos mensajes, un append_turn.
        assert [event.name for event in events] == ["open", "meta", "token", "done"]
        assert events[0].data == {"stream": "chat"}
        assert events[1].data == {"sources": []}
        assert events[2].data == {"text": NO_CONTEXT_ANSWER}
        done = events[3].data
        assert done["conversation_id"] == history[0].conversation_id
        assert done["message_id"] == history[1].id
        assert isinstance(done["elapsed_ms"], int)
        assert [message.role for message in history] == ["user", "assistant"]
        assert history[0].content == "¿Dato del corpus?"
        assert history[1].content == NO_CONTEXT_ANSWER
        assert history[1].sources == []
    finally:
        _delete_owner(engine, owner_key)


def test_stream_falla_antes_del_primer_token_y_persiste_el_fallback(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: la recuperación ya está resuelta; el LLM falla al abrir el stream.
    monkeypatch.setattr(AskQuestion, "prepare", _fixed_prepare)
    llm = FailingStreamLLM()
    service = ChatService(build_session_factory(engine), FakeEmbedder(), llm)
    owner_key = "stream-fallback"

    try:
        # Act
        events = list(service.stream(owner_key, "¿Dato del corpus?"))
        history = list(service.history(owner_key))

        # Assert
        assert [event.name for event in events] == ["open", "meta", "fallback", "done"]
        assert events[2].data == {
            "text": "Respuesta completa de respaldo [1].",
            "sources": [asdict(SOURCE)],
        }
        assert llm.stream_calls == 1
        assert llm.complete_calls == 1
        assert [message.role for message in history] == ["user", "assistant"]
        assert history[1].content == "Respuesta completa de respaldo [1]."
        assert history[1].sources == [asdict(SOURCE)]
    finally:
        _delete_owner(engine, owner_key)


def test_stream_falla_tras_un_token_no_persiste_nada(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(AskQuestion, "prepare", _fixed_prepare)
    service = ChatService(build_session_factory(engine), FakeEmbedder(), BrokenAfterTokenLLM())
    owner_key = "stream-incompleto"

    # Act
    events = list(service.stream(owner_key, "¿Dato del corpus?"))

    # Assert
    assert [event.name for event in events] == ["open", "meta", "token", "error"]
    assert events[-1].data == {"message": SERVICE_UNAVAILABLE, "incomplete": True}
    with engine.connect() as connection:
        count = connection.execute(
            select(func.count())
            .select_from(tables.conversations)
            .where(tables.conversations.c.owner_key == owner_key)
        ).scalar_one()
    assert count == 0
    assert list(service.history(owner_key)) == []


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
