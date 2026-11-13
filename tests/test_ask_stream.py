"""Pruebas del modo streaming de ``AskQuestion`` sin red ni LLM real."""

from collections.abc import Iterator, Sequence

import pytest

from carvajal.application.ask import NO_CONTEXT_ANSWER, AskQuestion, PreparedQuestion
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk

QUESTION = "¿Cuándo nació la empresa?"


class StubEmbedder:
    """Vector fijo que registra el texto recibido."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return [1.0, 0.0]


class StubVectorStore:
    """Índice con resultados precargados."""

    def __init__(self, results: Sequence[ScoredChunk] = ()) -> None:
        self.results = list(results)

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise NotImplementedError

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        return self.results[:limit]

    def delete_by_document(self, document_id: str) -> None:
        raise NotImplementedError


class StubLLM:
    """Doble que emite fragmentos fijos y registra ambas llamadas."""

    def __init__(self, fragments: Sequence[str] = ("Hola", " mundo")) -> None:
        self.fragments = list(fragments)
        self.complete_calls: list[tuple[str, str]] = []
        self.stream_calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, prompt: str) -> str:
        self.complete_calls.append((system, prompt))
        return "".join(self.fragments)

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.stream_calls.append((system, prompt))
        yield from self.fragments


def _scored(chunk_id: str, position: int, *, text: str = "Fundada en 1904.") -> ScoredChunk:
    chunk = Chunk(
        id=chunk_id,
        document_id="historia",
        text=text,
        position=position,
        section="historia",
        layer="core",
    )
    return ScoredChunk(chunk=chunk, score=0.9)


def test_prepare_recupera_y_compone_el_prompt_sin_llamar_al_llm() -> None:
    # Arrange
    llm = StubLLM()
    use_case = AskQuestion(StubVectorStore([_scored("historia:0", 0)]), StubEmbedder(), llm)

    # Act
    prepared = use_case.prepare(QUESTION)

    # Assert
    assert isinstance(prepared, PreparedQuestion)
    assert prepared.prompt is not None
    assert QUESTION in prepared.prompt
    assert "Fundada en 1904." in prepared.prompt
    assert [source.chunk_id for source in prepared.sources] == ["historia:0"]
    assert llm.complete_calls == []
    assert llm.stream_calls == []


def test_stream_emite_los_fragmentos_del_doble() -> None:
    # Arrange
    llm = StubLLM(fragments=("Carvajal ", "nació ", "en 1904 [1]."))
    use_case = AskQuestion(StubVectorStore([_scored("historia:0", 0)]), StubEmbedder(), llm)

    # Act
    fragments = list(use_case.stream(QUESTION))

    # Assert
    assert fragments == ["Carvajal ", "nació ", "en 1904 [1]."]
    assert len(llm.stream_calls) == 1
    assert llm.complete_calls == []
    assert "Fundada en 1904." in llm.stream_calls[0][1]


def test_sin_contexto_emite_la_ausencia_sin_llamar_al_llm() -> None:
    # Arrange
    llm = StubLLM()
    use_case = AskQuestion(StubVectorStore(), StubEmbedder(), llm)

    # Act
    fragments = list(use_case.stream(QUESTION))

    # Assert
    assert fragments == [NO_CONTEXT_ANSWER]
    assert llm.complete_calls == []
    assert llm.stream_calls == []


def test_prepare_rechaza_la_pregunta_vacia() -> None:
    # Arrange
    embedder = StubEmbedder()
    use_case = AskQuestion(StubVectorStore(), embedder, StubLLM())

    # Act / Assert
    with pytest.raises(ValueError, match="vacía"):
        use_case.prepare("   ")
    assert embedder.texts == []
