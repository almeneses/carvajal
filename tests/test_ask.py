"""Pruebas del caso de uso ``AskQuestion`` con dobles en memoria, sin red."""

from collections.abc import Iterator, Sequence

import pytest

from carvajal.adapters.embeddings import EmbeddingError
from carvajal.application.ask import EXCERPT_CHARS, Answer, AskQuestion, LLMError, Source
from carvajal.application.ports import Embedder, LLMProvider, ScoredChunk, VectorStore
from carvajal.domain.entities import Chunk

QUESTION = "¿Cuándo nació la empresa?"


class StubEmbedder:
    """Devuelve un vector fijo y registra los textos recibidos."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return [1.0, 0.0]


class FailingEmbedder:
    """Embedder que simula un fallo del proveedor."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def embed(self, text: str) -> list[float]:
        raise self.error


class StubVectorStore:
    """Índice con resultados precargados que registra el ``limit`` pedido."""

    def __init__(self, results: Sequence[ScoredChunk] = ()) -> None:
        self.results = list(results)
        self.limits: list[int] = []

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
        self.limits.append(limit)
        return self.results[:limit]

    def delete_by_document(self, document_id: str) -> None:
        raise NotImplementedError


class StubLLM:
    """LLM determinista que registra los mensajes recibidos."""

    def __init__(self, reply: str = "La empresa nació en 1904 [1].") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self.reply

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        yield self.complete(system=system, prompt=prompt)


def _scored(
    chunk_id: str,
    document_id: str,
    position: int,
    *,
    text: str | None = None,
    section: str = "historia",
    score: float = 0.9,
) -> ScoredChunk:
    chunk = Chunk(
        id=chunk_id,
        document_id=document_id,
        text=text if text is not None else f"texto de {chunk_id}",
        position=position,
        section=section,
        layer="core",
    )
    return ScoredChunk(chunk=chunk, score=score)


def _use_case(
    results: Sequence[ScoredChunk],
) -> tuple[AskQuestion, StubEmbedder, StubVectorStore, StubLLM]:
    embedder = StubEmbedder()
    store = StubVectorStore(results)
    llm = StubLLM()
    return AskQuestion(store, embedder, llm), embedder, store, llm


def test_respuesta_incluye_fuentes_con_chunk_id_en_orden() -> None:
    # Arrange
    results = [
        _scored("historia:0", "historia", 0, text="  Fundada   en 1904 ", score=0.92),
        _scored("historia:1", "historia", 1, score=0.81),
    ]
    use_case, embedder, store, llm = _use_case(results)

    # Act
    answer = use_case.answer(f"  {QUESTION}  ")

    # Assert
    assert isinstance(embedder, Embedder)
    assert isinstance(store, VectorStore) and isinstance(llm, LLMProvider)
    assert answer == Answer(
        text=llm.reply,
        sources=(
            Source(
                chunk_id="historia:0",
                slug="historia",
                section="historia",
                layer="core",
                score=0.92,
                excerpt="Fundada en 1904",
            ),
            Source(
                chunk_id="historia:1",
                slug="historia",
                section="historia",
                layer="core",
                score=0.81,
                excerpt="texto de historia:1",
            ),
        ),
    )
    assert embedder.texts == [QUESTION]


def test_prompt_incluye_contexto_numerado_y_reglas() -> None:
    # Arrange
    results = [
        _scored("historia:0", "historia", 0),
        _scored("home:0", "home", 0, section="home", score=0.7),
    ]
    use_case, _, _, llm = _use_case(results)

    # Act
    use_case.answer(QUESTION)

    # Assert
    system, prompt = llm.calls[0]
    assert "[1] (historia · historia)" in prompt
    assert "[2] (home · home)" in prompt
    assert "texto de historia:0" in prompt
    assert f"Pregunta: {QUESTION}" in prompt
    assert "únicamente con el CONTEXTO" in system
    assert "[n]" in system
    assert "español" in system
    assert "no dispones de esa información" in system


def test_pregunta_vacia_lanza_value_error() -> None:
    # Arrange
    use_case, embedder, store, llm = _use_case([])

    # Act / Assert
    with pytest.raises(ValueError):
        use_case.answer("   \n\t ")
    assert embedder.texts == []
    assert store.limits == []
    assert llm.calls == []


def test_sin_resultados_declara_ausencia_sin_llamar_al_llm() -> None:
    # Arrange
    use_case, _, store, llm = _use_case([])

    # Act
    answer = use_case.answer(QUESTION)

    # Assert
    assert "No dispongo de información" in answer.text
    assert list(answer.sources) == []
    assert store.limits == [8]
    assert llm.calls == []


def test_limit_se_respeta() -> None:
    # Arrange
    results = [_scored(f"historia:{position}", "historia", position) for position in range(3)]
    embedder, store, llm = StubEmbedder(), StubVectorStore(results), StubLLM()
    use_case = AskQuestion(store, embedder, llm, limit=2)

    # Act
    answer = use_case.answer(QUESTION)

    # Assert
    assert store.limits == [2]
    assert [source.chunk_id for source in answer.sources] == ["historia:0", "historia:1"]


@pytest.mark.parametrize("limit", [0, -1])
def test_limit_no_positivo_lanza_value_error(limit: int) -> None:
    # Arrange
    embedder, store, llm = StubEmbedder(), StubVectorStore(), StubLLM()

    # Act / Assert
    with pytest.raises(ValueError, match="limit"):
        AskQuestion(store, embedder, llm, limit=limit)
    assert store.limits == []


def test_respuesta_vacia_del_llm_lanza_llm_error() -> None:
    # Arrange
    results = [_scored("historia:0", "historia", 0)]
    use_case = AskQuestion(StubVectorStore(results), StubEmbedder(), StubLLM(reply="   "))

    # Act / Assert
    with pytest.raises(LLMError, match="respuesta vacía"):
        use_case.answer(QUESTION)


def test_error_del_embedder_se_propaga_sin_llamar_al_llm() -> None:
    # Arrange
    llm = StubLLM()
    use_case = AskQuestion(StubVectorStore(), FailingEmbedder(EmbeddingError("sin Ollama")), llm)

    # Act / Assert
    with pytest.raises(EmbeddingError, match="sin Ollama"):
        use_case.answer(QUESTION)
    assert llm.calls == []


def test_respuesta_del_llm_se_recorta() -> None:
    # Arrange
    results = [_scored("historia:0", "historia", 0)]
    llm = StubLLM(reply="  respuesta  ")
    use_case = AskQuestion(StubVectorStore(results), StubEmbedder(), llm)

    # Act
    answer = use_case.answer(QUESTION)

    # Assert
    assert answer.text == "respuesta"


def test_extracto_corta_en_palabra_completa() -> None:
    # Arrange
    text = " ".join(["palabra"] * 100)
    results = [_scored("historia:0", "historia", 0, text=text)]
    use_case, _, _, _ = _use_case(results)

    # Act
    excerpt = use_case.answer(QUESTION).sources[0].excerpt

    # Assert
    assert excerpt.endswith("…")
    assert len(excerpt) <= EXCERPT_CHARS + 1
    assert text.startswith(excerpt[:-1])
    assert text[len(excerpt) - 1] == " "
