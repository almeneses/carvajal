"""Pruebas de los adaptadores de sincronización (spec 037, US2)."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.adapters.vector.sync_qdrant import QdrantSync
from carvajal.application.ports import Embedder
from carvajal.application.sync import CorpusLayer


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
        gold_dir: Path,
        store: QdrantVectorStore,
        embedder: Embedder,
        layer: str,
        only: set[str] | None,
        prune: bool,
    ) -> SimpleNamespace:
        llamadas.append({"layer": layer, "only": only, "prune": prune})
        return SimpleNamespace(indexed=len(only) if only else 0)

    monkeypatch.setattr("carvajal.adapters.vector.sync_qdrant.index_gold", indexar)
    sync = QdrantSync(
        cast(QdrantVectorStore, store),
        cast(Embedder, object()),
    )

    # Act
    indexed, deleted = sync.index_gold(
        CorpusLayer("core", (gold,)),
        only={"uno"},
        prune=True,
    )

    # Assert
    assert indexed == 1 and deleted == 1
    assert store.deleted == ["viejo"]
    assert llamadas[0]["layer"] == "core"
    assert llamadas[0]["only"] == {"uno"}
    assert sync.count("core") == 1
