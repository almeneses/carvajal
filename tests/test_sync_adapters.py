"""Pruebas de los adaptadores de sincronización (spec 037, specs 045)."""

from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.orm import Session

from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.adapters.vector.sync_qdrant import QdrantSync
from carvajal.application.ports import Embedder
from carvajal.application.sync import CorpusLayer, GoldLayer, read_gold_layer
from carvajal.domain.entities import Document


class FakeStore:
    """Doble mínimo de ``QdrantVectorStore`` para el adaptador."""

    def __init__(self, hashes: dict[str, str]) -> None:
        self._hashes = dict(hashes)
        self.deleted: list[str] = []

    def document_hashes(self, layer: str | None = None) -> dict[str, str]:
        return dict(self._hashes)

    def document_ids(self, layer: str | None = None) -> set[str]:
        return set(self._hashes)

    def delete_by_document(self, document_id: str) -> None:
        self.deleted.append(document_id)
        self._hashes.pop(document_id, None)

    def prune_layer(self, layer: str, valid_documents: Iterable[str]) -> int:
        stale = set(self._hashes) - set(valid_documents)
        for document_id in sorted(stale):
            self.delete_by_document(document_id)
        return len(stale)


def test_qdrant_sync_indexa_solo_lo_pedido_y_poda_lo_ausente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / "uno.md").write_text("---\ntitle: Uno\nhash: h1\n---\n\nCuerpo.", encoding="utf-8")
    store = FakeStore({"uno": "h1", "viejo": "h0"})
    llamadas: list[dict[str, object]] = []

    def indexar(
        *,
        gold_dirs: tuple[tuple[Path, str], ...],
        store: QdrantVectorStore,
        embedder: Embedder,
        only: set[str] | None,
        prune: bool,
        documents: Sequence[Document] | None = None,
    ) -> SimpleNamespace:
        llamadas.append(
            {"gold_dirs": gold_dirs, "only": only, "prune": prune, "documents": documents}
        )
        return SimpleNamespace(indexed=len(only) if only else 0)

    monkeypatch.setattr("carvajal.adapters.vector.sync_qdrant.index_gold", indexar)
    sync = QdrantSync(
        cast(QdrantVectorStore, store),
        cast(Embedder, object()),
    )
    layer = read_gold_layer(CorpusLayer("core", (gold,)))

    # Act
    indexed, deleted = sync.index_gold(layer, only={"uno"}, prune=True)

    # Assert: el lote leído llega al indexador sin volver a abrir Gold.
    assert indexed == 1 and deleted == 1
    assert store.deleted == ["viejo"]
    assert llamadas[0]["gold_dirs"] == ((gold, "core"),)
    assert llamadas[0]["only"] == {"uno"}
    assert llamadas[0]["documents"] == layer.documents
    assert sync.count("core") == 1


class _FabricaSesiones:
    """Fábrica de sesiones vacía: la importación está sustituida en la prueba."""

    def begin(self) -> object:
        return nullcontext(object())


def _gold_layer(layer: CorpusLayer) -> GoldLayer:
    """Lote vacío de una capa: lo que el servicio entrega cuando no hay cambios."""

    return GoldLayer(layer=layer, documents=(), hashes={}, index_hashes={})


def test_postgres_importa_el_lote_sin_releer_gold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El adaptador SQL no vuelve a tocar el disco: recibe los documentos ya leídos."""

    from carvajal.adapters.storage import import_corpus
    from carvajal.adapters.storage.sync_postgres import PostgresSync

    recibidas: list[dict[str, object]] = []

    def importar(
        session: object,
        documents: Sequence[Document],
        *,
        layer: str,
        prune: bool,
        only: set[str] | None = None,
    ) -> import_corpus.GoldImportReport:
        recibidas.append(
            {
                "documents": len(documents),
                "layer": layer,
                "prune": prune,
                "only": only,
            }
        )
        return import_corpus.GoldImportReport(len(documents), len(documents) * 2, 0)

    monkeypatch.setattr(import_corpus, "import_documents", importar)
    sync = PostgresSync(_FabricaSesiones())

    # Act: capa vacía con poda (debe vaciar la capa) y capa con documentos.
    vacia = CorpusLayer("extended", ())
    sync.import_gold(_gold_layer(vacia), only=None, prune=True)
    sync.import_gold(_gold_layer(vacia), only=None, prune=False)
    _gold(tmp_path / "uno", "uno")
    lote = read_gold_layer(CorpusLayer("core", (tmp_path / "uno",)))
    documents, chunks, removed = sync.import_gold(lote, only=None, prune=True)

    # Assert: una sola importación por llamada, con la capa y la poda pedidas.
    assert [item["prune"] for item in recibidas] == [True, False, True]
    assert [item["documents"] for item in recibidas] == [0, 0, 1]
    assert recibidas[2]["layer"] == "core"
    assert (documents, chunks, removed) == (1, 2, 0)


def test_postgres_poda_la_capa_con_la_union_de_sus_fuentes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una capa con varias fuentes se poda con la unión de los documentos del lote."""

    from carvajal.adapters.storage import import_corpus
    from carvajal.adapters.storage.sync_postgres import PostgresSync

    primera = tmp_path / "donante_uno" / "gold"
    segunda = tmp_path / "donante_dos" / "gold"
    _gold(primera, "uno")
    _gold(segunda, "dos")
    capa = CorpusLayer("extended", (primera, segunda))
    lote = read_gold_layer(capa)
    llamadas: list[Sequence[Document]] = []

    def importar(
        session: object,
        documents: Sequence[Document],
        *,
        layer: str,
        prune: bool,
        only: set[str] | None = None,
    ) -> import_corpus.GoldImportReport:
        llamadas.append(documents)
        return import_corpus.GoldImportReport(len(documents), len(documents), 1)

    monkeypatch.setattr(import_corpus, "import_documents", importar)

    # Act
    documents, chunks, removed = PostgresSync(_FabricaSesiones()).import_gold(
        lote, only=None, prune=True
    )

    # Assert: una llamada con la unión de las dos fuentes, no una por directorio.
    assert len(llamadas) == 1
    assert [document.id for document in llamadas[0]] == ["uno", "dos"]
    assert (documents, chunks, removed) == (2, 2, 1)


def _gold(directory: Path, slug: str) -> None:
    """Escribe un Markdown Gold mínimo con su slug."""

    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{slug}.md").write_text(
        f"---\nslug: {slug}\ntitle: {slug}\n---\n\nCuerpo de {slug}.",
        encoding="utf-8",
    )


class _FabricaDeSesion:
    """Reutiliza la sesión de la prueba para que la transacción se revierta."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def begin(self) -> AbstractContextManager[Session]:
        return nullcontext(self._session)


@pytest.mark.integration
def test_postgres_poda_la_capa_extendida_con_la_union_de_sus_fuentes(
    tmp_path: Path, session: Session
) -> None:
    """Un documento que desaparece de una fuente se borra de PostgreSQL al podar."""

    from carvajal.adapters.storage.sync_postgres import PostgresSync

    primera = tmp_path / "fuente_uno" / "gold"
    segunda = tmp_path / "fuente_dos" / "gold"
    _gold(primera, "uno")
    _gold(primera, "compartido")
    _gold(segunda, "dos")
    capa = CorpusLayer("extended", (primera, segunda))
    sync = PostgresSync(_FabricaDeSesion(session))

    # Act: la primera corrida importa las dos fuentes.
    sync.import_gold(read_gold_layer(capa), only=None, prune=True)
    assert sync.count("extended") == 3

    # Act: una fuente pierde su único documento y la otra lo conserva.
    (segunda / "dos.md").unlink()
    documents, _, removed = sync.import_gold(read_gold_layer(capa), only=None, prune=True)

    # Assert: la poda usa la unión, no la lista de la primera fuente.
    assert documents == 2
    assert removed == 1
    assert sync.count("extended") == 2


def test_los_hashes_de_un_qdrant_vacio_permiten_la_primera_corrida(tmp_path: Path) -> None:
    """Sin colección no hay huellas, pero tampoco un error que impida crearla."""

    from qdrant_client import QdrantClient

    store = QdrantVectorStore(
        client=QdrantClient(location=":memory:"),
        collection="primera-corrida",
        dimensions=1,
    )
    sync = QdrantSync(store, cast(Embedder, object()))

    assert sync.hashes("core") == {}
    assert sync.snapshot("core").hashes == {}
    assert sync.snapshot("core").document_ids == frozenset()
    assert sync.count("core") == 0
