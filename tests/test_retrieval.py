"""Pruebas de la recuperación: índice por capas y prioridad núcleo → extendida.

La primera prueba indexa dos áreas Gold en un Qdrant en memoria y comprueba que
cada capa se recupera con su ``layer`` y su procedencia. El resto cubre el
envoltorio ``LayerPriorityStore``: el núcleo se cita primero y solo se completa
con la capa extendida cuando no llena el cupo o queda bajo el umbral.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

from qdrant_client import QdrantClient

from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.application.ask import AskQuestion, Source
from carvajal.application.ports import ScoredChunk
from carvajal.application.retrieval import LayerPriorityStore
from carvajal.domain.entities import Chunk
from carvajal.indexing.qdrant import index_gold


class BowEmbedder:
    """Embedder determinista de bolsa de palabras para probar la recuperación."""

    VOCABULARIO = ("historia", "productos", "informe", "externo", "empresa")

    def embed(self, text: str) -> list[float]:
        words = text.lower().split()
        vector = [float(words.count(word)) for word in self.VOCABULARIO]
        return vector if any(vector) else [1.0, *([0.0] * (len(self.VOCABULARIO) - 1))]


def _documento(directorio: Path, slug: str, titulo: str, cuerpo: str) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    (directorio / f"{slug}.md").write_text(
        "---\n"
        f"title: {titulo}\n"
        f"url: https://carvajal.com/{slug}\n"
        f"section: {slug}\n"
        "source_type: oficial\n"
        "---\n\n"
        f"{cuerpo}\n",
        encoding="utf-8",
    )


def test_una_muestra_de_cada_capa_se_recupera_por_titulo(tmp_path: Path) -> None:
    """Hasta diez documentos por capa aparecen en el top-10 al buscar su título."""

    # Arrange
    nucleo = tmp_path / "gold"
    extendida = tmp_path / "external" / "donante" / "gold"
    _documento(nucleo, "historia", "Historia de Carvajal", "Fundada en Cali.")
    _documento(nucleo, "productos", "Productos de empaque", "Cajas y empaques.")
    _documento(extendida, "informe", "Informe externo", "Documento de un tercero.")

    store = QdrantVectorStore(
        client=QdrantClient(location=":memory:"),
        collection="retrieval_titulos",
        dimensions=len(BowEmbedder.VOCABULARIO),
    )
    embedder = BowEmbedder()
    store.ensure_collection()

    # Act
    index_gold(
        gold_dirs=((nucleo, "core"), (extendida, "extended")),
        store=store,
        embedder=embedder,
        prune=False,
    )

    # Assert
    consulta_nucleo = store.search(
        embedder.embed("Historia"),
        limit=10,
        layer="core",
    )
    consulta_extendida = store.search(
        embedder.embed("Informe externo"),
        limit=10,
        layer="extended",
    )

    assert consulta_nucleo
    assert consulta_nucleo[0].chunk.layer == "core"
    assert "Historia" in consulta_nucleo[0].metadata["title"]
    assert consulta_extendida
    assert consulta_extendida[0].chunk.layer == "extended"
    assert consulta_extendida[0].metadata["title"] == "Informe externo"


QUESTION = "¿Qué ofrece el programa de empaques?"


def _chunk(slug: str, *, layer: str) -> Chunk:
    """Fragmento mínimo con su capa."""

    return Chunk(
        id=f"{slug}:0",
        document_id=slug,
        text=f"Contenido de {slug}.",
        position=0,
        section="empresa",
        layer=layer,
    )


def _result(
    slug: str,
    *,
    layer: str,
    score: float,
    origin: str | None = None,
    category: str | None = None,
    entity: str | None = None,
) -> ScoredChunk:
    """Resultado con la procedencia que publica el payload del índice."""

    metadata: dict[str, str] = {
        "url": f"https://example.com/{slug}",
        "title": slug.replace("-", " ").title(),
        "source_type": "oficial" if layer == "core" else "tercero",
    }
    if origin is not None:
        metadata["origin"] = origin
    if category is not None:
        metadata["category"] = category
    if entity is not None:
        metadata["entity"] = entity
    return ScoredChunk(chunk=_chunk(slug, layer=layer), score=score, metadata=metadata)


class FakeIndex:
    """Índice por capas que registra cada consulta."""

    def __init__(self, by_layer: Mapping[str, Sequence[ScoredChunk]]) -> None:
        self.by_layer = {layer: list(results) for layer, results in by_layer.items()}
        self.calls: list[tuple[str, str | None, int]] = []

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise NotImplementedError

    def delete_by_document(self, document_id: str) -> None:
        raise NotImplementedError

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        self.calls.append(("search", layer, limit))
        return self.by_layer.get(layer or "", [])[:limit]

    def search_text(
        self,
        question: str,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
        min_score: float | None = None,
    ) -> Sequence[ScoredChunk]:
        self.calls.append(("search_text", layer, limit))
        results = self.by_layer.get(layer or "", [])[:limit]
        if min_score is not None:
            results = [result for result in results if result.score >= min_score]
        return results


class StubEmbedder:
    """Embedder determinista."""

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


class StubLLM:
    """LLM que devuelve una respuesta fija."""

    def complete(self, *, system: str, prompt: str) -> str:
        return "Respuesta [1]."

    def stream(self, *, system: str, prompt: str):  # pragma: no cover - no se usa aquí
        yield "Respuesta [1]."


def _ask(store: FakeIndex, *, limit: int = 3, min_score: float = 0.3) -> AskQuestion:
    """Caso de uso real sobre el envoltorio y dobles de embedder/LLM."""

    return AskQuestion(
        LayerPriorityStore(store, min_score=min_score),
        StubEmbedder(),
        StubLLM(),
        limit=limit,
        min_score=min_score,
    )


def test_el_nucleo_llena_el_cupo_y_no_consulta_la_extendida() -> None:
    # Arrange: el núcleo devuelve el cupo completo con puntuaciones altas.
    store = FakeIndex(
        {
            "core": [_result("historia", layer="core", score=0.9)],
            "extended": [_result("prensa", layer="extended", score=0.8)],
        }
    )

    # Act
    prepared = _ask(store, limit=1).prepare(QUESTION)

    # Assert
    assert [source.slug for source in prepared.sources] == ["historia"]
    assert store.calls == [("search_text", "core", 1)]


def test_la_extendida_completa_cuando_el_nucleo_no_alcanza() -> None:
    # Arrange: el núcleo solo aporta un fragmento y el cupo es de dos.
    store = FakeIndex(
        {
            "core": [_result("historia", layer="core", score=0.9)],
            "extended": [_result("prensa", layer="extended", score=0.7)],
        }
    )

    # Act
    prepared = _ask(store, limit=2).prepare(QUESTION)

    # Assert: el núcleo va primero y la extendida ocupa el hueco restante.
    assert [source.slug for source in prepared.sources] == ["historia", "prensa"]
    assert [source.layer for source in prepared.sources] == ["core", "extended"]
    assert store.calls == [("search_text", "core", 2), ("search_text", "extended", 1)]


def test_el_nucleo_por_debajo_del_umbral_cede_el_paso_a_la_extendida() -> None:
    # Arrange: el único resultado del núcleo no supera el umbral.
    store = FakeIndex(
        {
            "core": [_result("historia", layer="core", score=0.2)],
            "extended": [_result("prensa", layer="extended", score=0.8)],
        }
    )

    # Act
    prepared = _ask(store, limit=2, min_score=0.5).prepare(QUESTION)

    # Assert
    assert [source.slug for source in prepared.sources] == ["prensa"]
    assert prepared.sources[0].layer == "extended"


def test_la_pregunta_respondida_por_ambas_capas_cita_el_nucleo() -> None:
    # Arrange: ambas capas tienen respuesta; el núcleo llena el cupo pedido.
    store = FakeIndex(
        {
            "core": [
                _result("historia", layer="core", score=0.9),
                _result("productos", layer="core", score=0.8),
            ],
            "extended": [_result("prensa", layer="extended", score=0.95)],
        }
    )

    # Act
    answer = _ask(store, limit=2).answer(QUESTION)

    # Assert
    assert [source.layer for source in answer.sources] == ["core", "core"]
    assert all(source.slug != "prensa" for source in answer.sources)


def test_la_cita_extendida_conserva_origen_categoria_y_entidad() -> None:
    # Arrange: documento extendido con la procedencia del front matter.
    store = FakeIndex(
        {
            "core": [],
            "extended": [
                _result(
                    "informe-empaques",
                    layer="extended",
                    score=0.8,
                    origin="Cámara de Comercio de Cali",
                    category="informe",
                    entity="Carvajal Empaques",
                )
            ],
        }
    )

    # Act
    answer = _ask(store, limit=2).answer(QUESTION)

    # Assert
    source = answer.sources[0]
    assert isinstance(source, Source)
    assert source.origin == "Cámara de Comercio de Cali"
    assert source.category == "informe"
    assert source.entity == "Carvajal Empaques"
    assert source.source_type == "tercero"


def test_un_documento_sin_procedencia_no_falla_y_la_cita_la_omite() -> None:
    # Arrange: el payload no trae origin/category/entity.
    store = FakeIndex({"core": [_result("historia", layer="core", score=0.9)], "extended": []})

    # Act
    answer = _ask(store).answer(QUESTION)

    # Assert
    source = answer.sources[0]
    assert (source.origin, source.category, source.entity) == ("", "", "")
    assert source.slug == "historia"
