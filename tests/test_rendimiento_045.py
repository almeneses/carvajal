"""Medición instrumentada de la fase 045 (antes/después de la optimización).

Cuenta lo que la capacidad vigila, sin depender de tiempos de pared:

* pasadas completas sobre Gold por capa en un ``sync`` con cambios;
* pasadas completas sobre el índice por capa en ``sync --verify``;
* llamadas al embedder por fragmento.

Las aserciones de este archivo documentan el conteo de la línea base; la
implementación los reduce y las aserciones se actualizan en el mismo commit que
demuestra la mejora.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import Engine

from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.application.sync import CorpusLayer, gold_hashes, sync_corpus

DOCS = {
    "med045_uno": "---\nslug: med045_uno\ntitle: Uno\nhash: h1\n---\n\nCuerpo uno.",
    "med045_dos": "---\nslug: med045_dos\ntitle: Dos\nhash: h2\n---\n\nCuerpo dos.",
}


class EmbedderDeterminista:
    """Embedder de 768 dimensiones que cuenta sus llamadas."""

    def __init__(self) -> None:
        self.llamadas = 0

    def embed(self, text: str) -> list[float]:
        """Devuelve un vector válido y contabiliza la llamada."""

        self.llamadas += 1
        return [1.0, *([0.0] * 767)]


class FakeDatabase:
    """PostgreSQL de solo lectura contable: no lee Gold."""

    def __init__(self, hashes: Mapping[str, str] | None = None, count: int = 0) -> None:
        self._hashes = dict(hashes or {})
        self._count = count
        self.imports = 0

    def hashes(self, layer: str) -> Mapping[str, str]:
        """Huellas que la base dice tener."""

        return dict(self._hashes)

    def import_gold(self, *args: object, **kwargs: object) -> tuple[int, int, int]:
        """Cuenta la llamada; el costo de Gold lo mide la lectura de archivos."""

        self.imports += 1
        return (0, 0, 0)

    def count(self, layer: str) -> int:
        """Documentos en la base."""

        return self._count


def _escribir_gold(directory: Path) -> None:
    """Escribe los dos documentos Gold de la medición."""

    directory.mkdir(parents=True, exist_ok=True)
    for slug, content in DOCS.items():
        (directory / f"{slug}.md").write_text(content, encoding="utf-8")


def _store_memoria() -> QdrantVectorStore:
    """Índice Qdrant en memoria, sin Docker."""

    return QdrantVectorStore(
        client=QdrantClient(location=":memory:"),
        collection="medicion_045",
        dimensions=768,
    )


def _contar_lecturas_gold(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Envuelve ``load_document`` en todos los módulos que lo tengan ligado."""

    from carvajal.adapters.storage import import_corpus

    owner: Any
    try:
        from carvajal import gold_documents as gold_owner

        owner = gold_owner
        original: Any = gold_owner.load_document
    except (ImportError, AttributeError):  # antes de mover la lectura al dominio
        owner = import_corpus
        original = import_corpus.load_document
    llamadas = [0]

    def contado(*args: Any, **kwargs: Any) -> Any:
        llamadas[0] += 1
        return original(*args, **kwargs)

    from carvajal.application import sync as sync_module
    from carvajal.indexing import qdrant as indexador

    for module in (owner, import_corpus, sync_module, indexador):
        if getattr(module, "load_document", None) is original:
            monkeypatch.setattr(module, "load_document", contado)
    return llamadas


@contextmanager
def _contar_pasadas_indice(monkeypatch: pytest.MonkeyPatch) -> Generator[list[int]]:
    """Cuenta cada invocación de ``_payloads`` (una pasada completa del índice)."""

    original = QdrantVectorStore._payloads
    pasadas = [0]

    def contado(self: QdrantVectorStore, layer: str | None = None) -> Iterator[object]:
        pasadas[0] += 1
        yield from original(self, layer)

    monkeypatch.setattr(QdrantVectorStore, "_payloads", contado)
    yield pasadas


def _indexar(store: QdrantVectorStore, gold: Path, embedder: EmbedderDeterminista) -> None:
    """Indexa el Gold de la prueba en el índice en memoria."""

    from carvajal.indexing.qdrant import index_gold

    index_gold(gold_dir=gold, store=store, embedder=embedder, prune=False)


def test_verify_recorre_el_indice_una_vez_por_capa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La instantánea trae huellas y documentos en una sola pasada por capa."""

    # Arrange
    gold = tmp_path / "gold"
    _escribir_gold(gold)
    layer = CorpusLayer("core", (gold,))
    store = _store_memoria()
    embedder = EmbedderDeterminista()
    _indexar(store, gold, embedder)
    hashes = gold_hashes(layer)
    from carvajal.adapters.vector.sync_qdrant import QdrantSync

    index = QdrantSync(store, embedder)  # type: ignore[arg-type]

    # Act
    with _contar_pasadas_indice(monkeypatch) as pasadas:
        report = sync_corpus(
            [layer],
            database=FakeDatabase(hashes, count=len(hashes)),  # type: ignore[arg-type]
            index=index,
            verify=True,
        )

    # Assert: un corpus al día no tiene diferencias y el índice se recorre una vez.
    assert report.differences == ()
    assert pasadas[0] == 1


def test_sync_con_cambios_lee_gold_una_vez_por_capa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El servicio lee Gold una vez y entrega el mismo lote a los dos almacenes."""

    # Arrange
    gold = tmp_path / "gold"
    _escribir_gold(gold)
    layer = CorpusLayer("core", (gold,))
    store = _store_memoria()
    embedder = EmbedderDeterminista()
    from carvajal.adapters.vector.sync_qdrant import QdrantSync

    index = QdrantSync(store, embedder)  # type: ignore[arg-type]

    # Act: la base está vacía, así que los dos documentos son nuevos.
    lecturas = _contar_lecturas_gold(monkeypatch)
    report = sync_corpus(
        [layer],
        database=FakeDatabase(),  # type: ignore[arg-type]
        index=index,
    )

    # Assert: una pasada (la lectura del servicio); el indexador recibe el lote.
    assert report.new == 2
    assert lecturas[0] == len(DOCS)


def test_verify_no_escribe_en_ningun_almacen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los dobles de escritura revientan: la verificación solo lee."""

    # Arrange
    gold = tmp_path / "gold"
    _escribir_gold(gold)
    layer = CorpusLayer("core", (gold,))
    store = _store_memoria()
    embedder = EmbedderDeterminista()
    _indexar(store, gold, embedder)
    puntos_antes = store.count()
    from carvajal.adapters.vector.sync_qdrant import QdrantSync

    class BaseQueRevienta(FakeDatabase):
        def import_gold(self, *args: object, **kwargs: object) -> tuple[int, int, int]:
            raise AssertionError("verify no debe importar")

    class IndiceQueRevienta(QdrantSync):
        def index_gold(self, *args: object, **kwargs: object) -> tuple[int, int]:
            raise AssertionError("verify no debe indexar")

    # Act
    report = sync_corpus(
        [layer],
        database=BaseQueRevienta(gold_hashes(layer), count=len(DOCS)),  # type: ignore[arg-type]
        index=IndiceQueRevienta(store, embedder),  # type: ignore[arg-type]
        verify=True,
    )

    # Assert
    assert report.differences == ()
    assert store.count() == puntos_antes
    assert embedder.llamadas == len(DOCS)


@pytest.mark.integration
def test_sync_completo_lee_gold_una_vez_por_capa_con_postgres_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: Engine
) -> None:
    """Con PostgreSQL real y poda, la capa se recorre una vez por corrida."""

    # Arrange
    from sqlalchemy import text

    from carvajal.adapters.storage.database import build_session_factory
    from carvajal.adapters.storage.sync_postgres import PostgresSync
    from carvajal.adapters.vector.sync_qdrant import QdrantSync

    gold = tmp_path / "gold"
    _escribir_gold(gold)
    layer = CorpusLayer("core_med045", (gold,))
    store = _store_memoria()
    embedder = EmbedderDeterminista()
    index = QdrantSync(store, embedder)  # type: ignore[arg-type]
    database = PostgresSync(build_session_factory(engine))

    # Act
    lecturas = _contar_lecturas_gold(monkeypatch)
    report = sync_corpus([layer], database=database, index=index, prune=True)

    # Assert: una sola pasada por capa, antes 4 (huellas, importación, indexado
    # y poda del índice).
    assert report.documents == len(DOCS)
    assert lecturas[0] == len(DOCS)
    assert embedder.llamadas == len(DOCS)
    with engine.begin() as connection:
        connection.execute(text("delete from documents where layer = 'core_med045'"))
