"""Pruebas de la sincronización Gold → PostgreSQL → Qdrant (spec 037, US2)."""

import threading
import time
from pathlib import Path

import pytest

from carvajal.application.sync import CorpusLayer, corpus_layers, gold_hashes, sync_corpus


def _con_titulo(path: Path, titulo: str) -> None:
    """Corrige el título del front matter sin tocar el cuerpo ni su huella."""

    texto = path.read_text(encoding="utf-8")
    anterior = next(line for line in texto.splitlines() if line.startswith("title: "))
    path.write_text(texto.replace(anterior, f"title: {titulo}"), encoding="utf-8")


def _document(directorio: Path, slug: str, hash_texto: str, cuerpo: str = "Contenido.") -> None:
    """Escribe un Markdown Gold mínimo con su huella en el front matter."""

    directorio.mkdir(parents=True, exist_ok=True)
    (directorio / f"{slug}.md").write_text(
        "---\n"
        f"title: {slug.title()}\n"
        f"url: https://carvajal.com/{slug}\n"
        f"hash: {hash_texto}\n"
        "---\n\n"
        f"{cuerpo}\n",
        encoding="utf-8",
    )


class FakeDatabase:
    """Doble de PostgreSQL: guarda huellas y registra las importaciones."""

    def __init__(self, gold: dict[str, str]) -> None:
        self.gold = gold
        self.hashes_map: dict[str, str] = {}
        self.imports: list[tuple[set[str] | None, bool]] = []
        self.fail = False

    def hashes(self, layer: str) -> dict[str, str]:
        return dict(self.hashes_map)

    def import_gold(
        self, layer: CorpusLayer, *, only: set[str] | None, prune: bool
    ) -> tuple[int, int, int]:
        self.imports.append((set(only) if only is not None else None, prune))
        if self.fail:
            raise ConnectionError("PostgreSQL caído")
        removed = 0
        if prune:
            removed = len(set(self.hashes_map) - set(self.gold))
            self.hashes_map = dict(self.gold)
        elif only:
            self.hashes_map.update({slug: self.gold[slug] for slug in only})
        selected = self.gold if prune else {slug: self.gold[slug] for slug in (only or ())}
        return len(selected), len(selected) * 2, removed

    def count(self, layer: str) -> int:
        return len(self.hashes_map)


class SlowDatabase(FakeDatabase):
    """Doble que tarda en importar para comprobar la exclusión entre corridas."""

    def __init__(self, gold: dict[str, str]) -> None:
        super().__init__(gold)
        self.activos = 0
        self.maximo = 0
        self._candado = threading.Lock()

    def import_gold(
        self, layer: CorpusLayer, *, only: set[str] | None, prune: bool
    ) -> tuple[int, int, int]:
        with self._candado:
            self.activos += 1
            self.maximo = max(self.maximo, self.activos)
        time.sleep(0.15)
        try:
            return super().import_gold(layer, only=only, prune=prune)
        finally:
            with self._candado:
                self.activos -= 1


class FakeIndex:
    """Doble de Qdrant: guarda huellas y registra las indexaciones."""

    def __init__(self, gold: dict[str, str]) -> None:
        self.gold = gold
        self.hashes_map: dict[str, str] = {}
        self.imports: list[tuple[set[str] | None, bool]] = []
        self.fail = False

    def hashes(self, layer: str) -> dict[str, str]:
        return dict(self.hashes_map)

    def index_gold(
        self, layer: CorpusLayer, *, only: set[str] | None, prune: bool
    ) -> tuple[int, int]:
        self.imports.append((set(only) if only is not None else None, prune))
        if self.fail:
            raise ConnectionError("Qdrant caído")
        removed = 0
        if prune:
            removed = len(set(self.hashes_map) - set(self.gold))
            self.hashes_map = dict(self.gold)
        elif only:
            self.hashes_map.update({slug: self.gold[slug] for slug in only})
        selected = self.gold if prune else {slug: self.gold[slug] for slug in (only or ())}
        return len(selected), removed

    def count(self, layer: str) -> int:
        return len(self.hashes_map)


def _layer(tmp_path: Path, docs: dict[str, str]) -> tuple[CorpusLayer, dict[str, str]]:
    directorio = tmp_path / "gold"
    for slug, hash_texto in docs.items():
        _document(directorio, slug, hash_texto)
    layer = CorpusLayer("core", (directorio,))
    return layer, gold_hashes(layer)


def test_sync_corpus_separa_nuevos_actualizados_y_sin_cambios(tmp_path: Path) -> None:
    # Arrange
    layer, gold = _layer(tmp_path, {"uno": "h1", "dos": "h2", "tres": "h3"})
    db = FakeDatabase(gold)
    db.hashes_map = {"uno": gold["uno"], "dos": "viejo"}
    index = FakeIndex(gold)
    index.hashes_map = {"uno": gold["uno"], "dos": "viejo"}

    # Act
    report = sync_corpus([layer], database=db, index=index)

    # Assert
    assert (report.new, report.updated, report.unchanged) == (1, 1, 1)
    assert db.imports == [({"dos", "tres"}, False)]
    assert index.imports == [({"dos", "tres"}, False)]
    assert report.documents == 2 and report.indexed == 2


def test_sync_corpus_repara_qdrant_en_la_segunda_corrida(tmp_path: Path) -> None:
    # Arrange
    layer, gold = _layer(tmp_path, {"uno": "h1"})
    db = FakeDatabase(gold)
    index = FakeIndex(gold)
    index.fail = True

    # Act
    primera = sync_corpus([layer], database=db, index=index)
    index.fail = False
    segunda = sync_corpus([layer], database=db, index=index)

    # Assert
    assert primera.errors and "Qdrant" in primera.errors[0]
    assert db.hashes_map == gold
    assert index.hashes_map == gold
    assert segunda.ok
    assert segunda.indexed == 1
    assert (tmp_path / "gold" / "uno.md").is_file()


class _DatabaseIlegible(FakeDatabase):
    """Doble cuya lectura de huellas falla, como un PostgreSQL sin conexión."""

    def hashes(self, layer: str) -> dict[str, str]:
        raise ConnectionError("PostgreSQL no responde")


def test_sync_corpus_reconcilia_qdrant_si_postgres_no_responde(tmp_path: Path) -> None:
    """Un fallo al leer la base no impide indexar ni podar el índice vectorial."""

    # Arrange
    layer, gold = _layer(tmp_path, {"uno": "h1"})
    db = _DatabaseIlegible(gold)
    index = FakeIndex(gold)

    # Act
    report = sync_corpus([layer], database=db, index=index)

    # Assert: la base queda pendiente, pero Qdrant sí se reconcilia.
    assert any("PostgreSQL" in error for error in report.errors)
    assert db.imports == []
    assert index.hashes_map == gold
    assert report.indexed == 1


def test_verify_reporta_qdrant_aunque_postgres_no_responda(tmp_path: Path) -> None:
    """La verificación sigue viendo el índice cuando la base no responde."""

    # Arrange
    layer, gold = _layer(tmp_path, {"uno": "h1"})
    db = _DatabaseIlegible(gold)
    index = FakeIndex(gold)

    # Act
    report = sync_corpus([layer], database=db, index=index, verify=True)

    # Assert
    assert any("PostgreSQL" in error for error in report.errors)
    assert any("falta en Qdrant" in difference for difference in report.differences)
    assert report.ok is False


def test_sync_corpus_poda_documentos_ausentes(tmp_path: Path) -> None:
    # Arrange
    layer, gold = _layer(tmp_path, {"uno": "h1"})
    db = FakeDatabase(gold)
    db.hashes_map = {"uno": gold["uno"], "viejo": "h0"}
    index = FakeIndex(gold)
    index.hashes_map = {"uno": gold["uno"], "viejo": "h0"}

    # Act
    report = sync_corpus([layer], database=db, index=index, prune=True)

    # Assert
    assert db.imports == [(None, True)]
    assert index.imports == [(set(), True)]
    assert report.pruned == 2


def test_sync_corpus_serializa_dos_corridas(tmp_path: Path) -> None:
    # Arrange
    layer, gold = _layer(tmp_path, {"uno": "h1"})
    db = SlowDatabase(gold)
    index = FakeIndex(gold)

    # Act
    hilos = [
        threading.Thread(
            target=sync_corpus,
            args=([layer],),
            kwargs={
                "database": db,
                "index": index,
                "lock_root": tmp_path,
            },
        )
        for _ in range(2)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    # Assert
    assert db.maximo == 1


def test_sync_corpus_verify_no_escribe_y_reporta_diferencias(tmp_path: Path) -> None:
    # Arrange
    layer, gold = _layer(tmp_path, {"uno": "h1"})
    db = FakeDatabase(gold)
    db.hashes_map = {"uno": gold["uno"]}
    index = FakeIndex(gold)
    index.hashes_map = {}

    # Act
    report = sync_corpus([layer], database=db, index=index, verify=True)

    # Assert
    assert report.verified is True
    assert db.imports == [] and index.imports == []
    assert any("Qdrant" in difference for difference in report.differences)
    assert report.ok is False


def test_un_cambio_solo_de_metadatos_reimporta_y_reindexa(tmp_path: Path) -> None:
    """Corregir el título en Gold repara los almacenes aunque el cuerpo no cambie."""

    # Arrange: la primera corrida deja los dos almacenes al día.
    directorio = tmp_path / "gold"
    _document(directorio, "historia", "h1", cuerpo="Fundada en Cali.")
    layer = CorpusLayer("core", (directorio,))
    gold = gold_hashes(layer)
    db = FakeDatabase(gold)
    index = FakeIndex(gold)
    sync_corpus([layer], database=db, index=index)
    assert db.imports and index.imports

    # Act: solo cambia el título del front matter; el cuerpo y su hash no.
    _con_titulo(directorio / "historia.md", "Historia renovada")
    report = sync_corpus([layer], database=db, index=index)

    # Assert
    assert (report.new, report.updated, report.unchanged) == (0, 1, 0)
    assert db.imports[-1] == ({"historia"}, False)
    assert index.imports[-1] == ({"historia"}, False)


def test_la_huella_de_qdrant_cubre_los_metadatos(tmp_path: Path) -> None:
    """El índice publica la misma huella que Gold cuando cambia solo el título."""

    # Arrange
    from carvajal.indexing.qdrant import index_gold

    class UnEmbedder:
        """Embedder de una dimensión: basta para publicar una huella."""

        def embed(self, text: str) -> list[float]:
            return [1.0]

    nucleo = tmp_path / "gold"
    _document(nucleo, "historia", "h1", cuerpo="Fundada en Cali.")
    store = _store("metadatos")
    index_gold(gold_dir=nucleo, store=store, embedder=UnEmbedder(), prune=False)
    antes = store.document_hashes("core")

    # Act: se reindexa el documento con el título corregido.
    _con_titulo(nucleo / "historia.md", "Historia renovada")
    index_gold(gold_dir=nucleo, store=store, embedder=UnEmbedder(), prune=False)

    # Assert
    assert store.document_hashes("core") != antes
    assert store.document_hashes("core") == gold_hashes(CorpusLayer("core", (nucleo,)))


class _EmbedderUnaDimension:
    """Embedder de una dimensión: basta para contar puntos y podar."""

    def embed(self, text: str) -> list[float]:
        return [1.0]


def _store(nombre: str):
    from qdrant_client import QdrantClient

    from carvajal.adapters.vector.qdrant_store import QdrantVectorStore

    return QdrantVectorStore(
        client=QdrantClient(location=":memory:"),
        collection=nombre,
        dimensions=1,
    )


def test_un_documento_actualizado_reemplaza_sus_fragmentos(tmp_path: Path) -> None:
    """Reindexar un documento más corto no deja fragmentos de la versión vieja."""

    # Arrange
    from carvajal.indexing.qdrant import index_gold

    nucleo = tmp_path / "gold"
    _document(nucleo, "largo", "v1", cuerpo="Palabra. " * 400)
    store = _store("actualizado")
    embedder = _EmbedderUnaDimension()
    index_gold(gold_dir=nucleo, store=store, embedder=embedder, prune=False)
    antes = store.count()
    assert antes > 1

    # Act
    _document(nucleo, "largo", "v2", cuerpo="Texto corto.")
    index_gold(
        gold_dir=nucleo,
        store=store,
        embedder=embedder,
        only={"largo"},
        prune=False,
    )

    # Assert
    assert store.count() == 1
    assert store.document_hashes("core") == gold_hashes(CorpusLayer("core", (nucleo,)))


class _EmbedderOtraDimension:
    """Embedder que devuelve vectores de dos dimensiones, como un proveedor mal configurado."""

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


def test_un_vector_de_otra_dimension_no_borra_la_version_indexada(tmp_path: Path) -> None:
    """Un proveedor mal configurado no debe dejar el documento sin ningún punto."""

    # Arrange
    from carvajal.indexing.qdrant import index_gold

    nucleo = tmp_path / "gold"
    _document(nucleo, "historia", "v1", cuerpo="Fundada en Cali.")
    store = _store("dimension_incorrecta")
    index_gold(gold_dir=nucleo, store=store, embedder=_EmbedderUnaDimension(), prune=False)
    antes = store.count()
    assert antes == 1

    # Act / Assert
    with pytest.raises(ValueError, match="dimensiones"):
        index_gold(
            gold_dir=nucleo,
            store=store,
            embedder=_EmbedderOtraDimension(),
            only={"historia"},
            prune=False,
        )
    assert store.count() == antes
    assert store.document_hashes("core") == gold_hashes(CorpusLayer("core", (nucleo,)))


def test_corpus_layers_conserva_la_capa_extendida_sin_fuentes(tmp_path: Path) -> None:
    """Una capa sin fuentes sigue siendo una capa: se puede podar y verificar."""

    # Arrange
    nucleo = tmp_path / "gold"
    nucleo.mkdir()
    externo = tmp_path / "external"
    externo.mkdir()

    # Act
    layers = corpus_layers(nucleo, externo, which="extended")

    # Assert
    assert [layer.name for layer in layers] == ["extended"]
    assert layers[0].directories == ()
    assert gold_hashes(layers[0]) == {}


def test_la_capa_vacia_se_poda_en_los_dos_almacenes() -> None:
    """Sin fuentes, ``sync --layer extended --prune`` no debe dejar residuos."""

    # Arrange
    capa = CorpusLayer("extended", ())
    db = FakeDatabase({})
    db.hashes_map = {"viejo": "h0"}
    index = FakeIndex({})
    index.hashes_map = {"viejo": "h0"}

    # Act
    report = sync_corpus([capa], database=db, index=index, prune=True)

    # Assert
    assert report.ok
    assert report.pruned == 2
    assert db.hashes_map == {}
    assert index.hashes_map == {}


def test_la_poda_de_una_capa_conserva_los_puntos_de_la_otra(tmp_path: Path) -> None:
    """Reindexar solo el núcleo con poda no debe borrar la capa extendida."""

    # Arrange
    from carvajal.indexing.qdrant import index_gold

    nucleo = tmp_path / "gold"
    extendida = tmp_path / "external" / "donante" / "gold"
    _document(nucleo, "historia", "h1")
    _document(extendida, "informe", "h2")
    store = _store("poda_por_capa")
    embedder = _EmbedderUnaDimension()
    index_gold(gold_dir=extendida, store=store, embedder=embedder, layer="extended", prune=False)

    # Act: ``make index`` indexa únicamente el núcleo y poda.
    index_gold(gold_dir=nucleo, store=store, embedder=embedder, layer="core", prune=True)

    # Assert
    assert store.document_hashes("core") == gold_hashes(CorpusLayer("core", (nucleo,)))
    assert store.document_hashes("extended") == gold_hashes(CorpusLayer("extended", (extendida,)))
    assert store.count() == 2
