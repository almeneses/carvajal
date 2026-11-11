"""Pruebas del adaptador de embeddings de Ollama, sin red."""

from collections.abc import Callable, Sequence

import pytest

from carvajal.adapters.embeddings import ollama


class _FakeOllamaEmbeddings:
    """Doble de ``OllamaEmbeddings`` que devuelve un vector fijo."""

    def __init__(self, vector: Sequence[float]) -> None:
        self.vector = list(vector)

    def embed_query(self, text: str) -> list[float]:
        return self.vector


def _install(
    monkeypatch: pytest.MonkeyPatch,
    vector: Sequence[float],
) -> dict[str, object]:
    """Sustituye ``OllamaEmbeddings`` y devuelve la configuración capturada."""

    captured: dict[str, object] = {}

    def build(**kwargs: object) -> _FakeOllamaEmbeddings:
        captured.update(kwargs)
        return _FakeOllamaEmbeddings(vector)

    factory: Callable[..., _FakeOllamaEmbeddings] = build
    monkeypatch.setattr(ollama, "OllamaEmbeddings", factory)
    return captured


def test_ollama_embedder_configura_timeout_y_convierte_a_float(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    captured = _install(monkeypatch, vector=(1, 0))
    embedder = ollama.OllamaEmbedder(
        model="nomic-embed-text",
        base_url="http://ollama.test:11434",
        timeout=5.0,
    )

    # Act
    vector = embedder.embed("hola")

    # Assert
    assert vector == [1.0, 0.0]
    assert all(isinstance(value, float) for value in vector)
    assert captured["model"] == "nomic-embed-text"
    assert captured["base_url"] == "http://ollama.test:11434"
    assert captured["client_kwargs"] == {"timeout": 5.0}


def test_ollama_embedder_usa_el_timeout_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    captured = _install(monkeypatch, vector=(1, 0))

    # Act
    ollama.OllamaEmbedder()

    # Assert
    assert captured["client_kwargs"] == {"timeout": ollama.DEFAULT_TIMEOUT}


class _FailingOllamaEmbeddings:
    """Doble que simula un fallo de conexión con Ollama."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def embed_query(self, text: str) -> list[float]:
        raise self.error


def test_ollama_embedder_rechaza_un_vector_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _install(monkeypatch, vector=())
    embedder = ollama.OllamaEmbedder()

    # Act / Assert
    with pytest.raises(ValueError, match="vector vacío"):
        embedder.embed("hola")


def test_ollama_embedder_envuelve_el_fallo_de_conexion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    failure = ConnectionError("conexión rechazada")
    fake = _FailingOllamaEmbeddings(failure)
    monkeypatch.setattr(ollama, "OllamaEmbeddings", lambda **_: fake)
    embedder = ollama.OllamaEmbedder(base_url="http://ollama.test:11434")

    # Act / Assert
    with pytest.raises(ollama.EmbeddingError, match=r"ollama\.test") as captured:
        embedder.embed("hola")
    assert isinstance(captured.value, RuntimeError)
    assert captured.value.__cause__ is failure
