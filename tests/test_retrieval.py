"""Pruebas de recuperación por título en el índice vectorial (spec 037, US2)."""

from pathlib import Path

from qdrant_client import QdrantClient

from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
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
        gold_dir=nucleo,
        store=store,
        embedder=embedder,
        layer="core",
        prune=False,
    )
    index_gold(
        gold_dir=extendida,
        store=store,
        embedder=embedder,
        layer="extended",
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
