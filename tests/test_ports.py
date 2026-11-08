"""Pruebas de contrato de los puertos de aplicación.

Cada adaptador real (Postgres, pgvector, DeepSeek…) debe poder ejecutar estas
mismas aserciones contra su implementación. Los dobles en memoria de este
archivo son la referencia de comportamiento.
"""

from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime

from carvajal.application.ports import (
    ChunkRepository,
    DocumentRepository,
    Extractor,
    Fetcher,
    LLMProvider,
    ScoredChunk,
    VectorStore,
)
from carvajal.domain.entities import Chunk, Document

CAPTURED_AT = datetime(2026, 11, 8, tzinfo=UTC)


def _document(
    document_id: str,
    *,
    slug: str,
    layer: str = "core",
    section: str = "home",
    title: str = "Documento",
) -> Document:
    return Document(
        id=document_id,
        url=f"https://www.carvajal.com/{slug}/",
        title=title,
        slug=slug,
        section=section,
        layer=layer,
        markdown=f"# {title}",
        content_hash=f"hash-{document_id}",
        captured_at=CAPTURED_AT,
    )


def _chunk(chunk_id: str, document_id: str, position: int, layer: str = "core") -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id=document_id,
        text=f"fragmento {chunk_id}",
        position=position,
        section="home",
        layer=layer,
    )


class InMemoryDocumentRepository:
    """Doble en memoria del repositorio de documentos."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}

    def get(self, document_id: str) -> Document | None:
        return self._documents.get(document_id)

    def get_by_slug(self, slug: str) -> Document | None:
        return next((doc for doc in self._documents.values() if doc.slug == slug), None)

    def list(
        self,
        *,
        layer: str | None = None,
        section: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Document]:
        documents = [
            doc
            for doc in self._documents.values()
            if (layer is None or doc.layer == layer) and (section is None or doc.section == section)
        ]
        documents.sort(key=lambda doc: doc.slug)
        return documents[offset : offset + limit]

    def upsert(self, document: Document) -> Document:
        self._documents[document.id] = document
        return document

    def delete(self, document_id: str) -> bool:
        return self._documents.pop(document_id, None) is not None

    def count(self, *, layer: str | None = None) -> int:
        return sum(1 for doc in self._documents.values() if layer is None or doc.layer == layer)


class InMemoryChunkRepository:
    """Doble en memoria del repositorio de fragmentos."""

    def __init__(self) -> None:
        self._chunks: dict[str, list[Chunk]] = {}

    def list_by_document(self, document_id: str) -> Sequence[Chunk]:
        return sorted(self._chunks.get(document_id, []), key=lambda chunk: chunk.position)

    def replace(self, document_id: str, chunks: Iterable[Chunk]) -> None:
        self._chunks[document_id] = list(chunks)

    def delete_by_document(self, document_id: str) -> int:
        return len(self._chunks.pop(document_id, []))


class InMemoryVectorStore:
    """Doble en memoria del índice vectorial, con similitud coseno."""

    def __init__(self) -> None:
        self._vectors: dict[str, tuple[Chunk, tuple[float, ...]]] = {}

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Cada fragmento necesita exactamente un vector.")
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            self._vectors[chunk.id] = (chunk, tuple(embedding))

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        scored = [
            ScoredChunk(chunk=chunk, score=_cosine(embedding, vector))
            for chunk, vector in self._vectors.values()
            if (layer is None or chunk.layer == layer)
            and (section is None or chunk.section == section)
        ]
        scored.sort(key=lambda result: result.score, reverse=True)
        return scored[:limit]

    def delete_by_document(self, document_id: str) -> None:
        for chunk_id in [
            key for key, (chunk, _) in self._vectors.items() if chunk.document_id == document_id
        ]:
            del self._vectors[chunk_id]


class FakeLLM:
    """Proveedor LLM determinista: repite el contexto recibido."""

    def complete(self, *, system: str, prompt: str) -> str:
        return f"{system}|{prompt}"

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        yield self.complete(system=system, prompt=prompt)


class FakeFetcher:
    """Cliente HTTP con páginas precargadas."""

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def can_fetch(self, url: str) -> bool:
        return url in self._pages

    def fetch(self, url: str) -> str:
        return self._pages[url]


class FakeExtractor:
    """Extractor que normaliza espacios en blanco."""

    def extract(self, html: str) -> str:
        return " ".join(html.split())


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return numerator / (norm_left * norm_right)


def assert_document_repository(repository: DocumentRepository) -> None:
    """Contrato común de todo repositorio de documentos."""

    first = _document("a", slug="uno")
    assert repository.get("a") is None
    assert repository.upsert(first) == first
    assert repository.get("a") == first
    assert repository.get_by_slug("uno") == first

    updated = Document(
        id=first.id,
        url=first.url,
        title="Actualizado",
        slug=first.slug,
        section=first.section,
        layer=first.layer,
        markdown=first.markdown,
        content_hash=first.content_hash,
        captured_at=first.captured_at,
    )
    repository.upsert(updated)
    assert repository.get("a") == updated

    repository.upsert(_document("b", slug="dos", layer="extended", section="prensa"))
    assert [doc.id for doc in repository.list()] == ["b", "a"]
    assert [doc.id for doc in repository.list(layer="core")] == ["a"]
    assert [doc.id for doc in repository.list(section="prensa")] == ["b"]
    assert [doc.id for doc in repository.list(limit=1, offset=1)] == ["a"]
    assert repository.count() == 2
    assert repository.count(layer="extended") == 1
    assert repository.delete("a") is True
    assert repository.delete("a") is False


def assert_chunk_repository(repository: ChunkRepository) -> None:
    """Contrato común de todo repositorio de fragmentos."""

    assert repository.list_by_document("d") == []
    repository.replace("d", [_chunk("c2", "d", 2), _chunk("c1", "d", 1)])
    assert [chunk.id for chunk in repository.list_by_document("d")] == ["c1", "c2"]

    repository.replace("d", [_chunk("c3", "d", 1)])
    assert [chunk.id for chunk in repository.list_by_document("d")] == ["c3"]
    assert repository.delete_by_document("d") == 1
    assert repository.list_by_document("d") == []


def assert_vector_store(store: VectorStore) -> None:
    """Contrato común de todo índice vectorial."""

    core = [_chunk("c1", "d1", 0), _chunk("c2", "d1", 1)]
    extended = [_chunk("c3", "d2", 0, layer="extended")]
    store.upsert(core, [[1.0, 0.0], [0.9, 0.1]])
    store.upsert(extended, [[0.0, 1.0]])

    results = store.search([1.0, 0.0], limit=2)
    assert [result.chunk.id for result in results] == ["c1", "c2"]
    assert results[0].score >= results[1].score
    assert [result.chunk.id for result in store.search([1.0, 0.0], layer="extended")] == ["c3"]

    store.delete_by_document("d1")
    assert [result.chunk.id for result in store.search([1.0, 0.0])] == ["c3"]


def assert_llm_provider(provider: LLMProvider) -> None:
    """Contrato común de todo proveedor de generación de texto."""

    expected = "sistema|pregunta"
    assert provider.complete(system="sistema", prompt="pregunta") == expected
    assert "".join(provider.stream(system="sistema", prompt="pregunta")) == expected


def test_repositorio_de_documentos_en_memoria() -> None:
    repository = InMemoryDocumentRepository()

    assert isinstance(repository, DocumentRepository)
    assert_document_repository(repository)


def test_repositorio_de_fragmentos_en_memoria() -> None:
    repository = InMemoryChunkRepository()

    assert isinstance(repository, ChunkRepository)
    assert_chunk_repository(repository)


def test_indice_vectorial_en_memoria() -> None:
    store = InMemoryVectorStore()

    assert isinstance(store, VectorStore)
    assert_vector_store(store)


def test_proveedor_llm_de_prueba() -> None:
    provider = FakeLLM()

    assert isinstance(provider, LLMProvider)
    assert_llm_provider(provider)


def test_cliente_http_de_prueba() -> None:
    fetcher = FakeFetcher({"https://ejemplo.test/": "<html></html>"})

    assert isinstance(fetcher, Fetcher)
    assert fetcher.can_fetch("https://ejemplo.test/") is True
    assert fetcher.can_fetch("https://bloqueado.test/") is False
    assert fetcher.fetch("https://ejemplo.test/") == "<html></html>"


def test_extractor_de_prueba() -> None:
    extractor = FakeExtractor()

    assert isinstance(extractor, Extractor)
    assert extractor.extract("<p>hola \n mundo</p>") == "<p>hola mundo</p>"
