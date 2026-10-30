"""Pruebas de la importación del corpus externo a la capa extendida."""

import json
from pathlib import Path

import pytest

from carvajal import importer
from carvajal.importer import import_external, normalize_url

PARAGRAPH = (
    "Fundada en 1904 en Cali, Colombia, Carvajal inició como una pequeña imprenta "
    "familiar y con el paso de las décadas se convirtió en uno de los grupos "
    "empresariales más grandes del país, con presencia en varios sectores "
    "industriales y en mercados internacionales."
)

HTML = (
    "<html><body><nav>Menú principal</nav><h1>Historia</h1>"
    f"<p>{PARAGRAPH}</p><footer>Pie de página</footer></body></html>"
)


@pytest.fixture(autouse=True)
def _sin_nucleo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aísla las pruebas del corpus núcleo real del repositorio."""

    monkeypatch.setattr(importer, "_core_urls", lambda: set())


def _record(
    source_id: str,
    category: str = "identity",
    url: str = "https://www.carvajal.com/historia/",
) -> dict:
    """Construye una línea de catálogo con la ruta Bronze de un corpus de prueba."""

    return {
        "id": source_id,
        "url": url,
        "title": "Historia de Carvajal",
        "entity": "Carvajal S.A.",
        "category": category,
        "priority": 2,
        "depth": 1,
        "fetched_at": "2026-10-23T19:46:16+00:00",
        "path": f"data/bronze/{category}/{source_id}.html",
        "status": "fetched",
        "text_present": True,
    }


def _build_corpus(root: Path, records: list[dict]) -> None:
    """Escribe el catálogo y el HTML Bronze de un corpus donante mínimo."""

    catalog = root / "data" / "gold" / "modulo_1" / "scraping_todos.jsonl"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    for record in records:
        bronze = root / str(record["path"])
        bronze.parent.mkdir(parents=True, exist_ok=True)
        bronze.write_text(HTML, encoding="utf-8")


def test_import_external_escribe_capa_extendida(tmp_path: Path) -> None:
    """La importación crea Gold, índice y manifiesto con la procedencia."""

    corpus = tmp_path / "donante"
    _build_corpus(corpus, [_record("abc123")])
    output = tmp_path / "external" / "alejandro"

    report = import_external(corpus, output)

    assert report.imported == 1
    gold = (output / "gold" / "ext_abc123.md").read_text(encoding="utf-8")
    assert "url: https://www.carvajal.com/historia/" in gold
    assert "category: identity" in gold
    assert "source_type: colaborador" in gold
    assert "origin: alejandro" in gold
    assert "layer: extended" in gold
    assert "captured_at: 2026-10-23T19:46:16+00:00" in gold
    assert "# Historia" in gold
    assert PARAGRAPH in gold
    assert "Menú principal" not in gold
    assert "Pie de página" not in gold

    index = (output / "gold" / "index.jsonl").read_text(encoding="utf-8")
    assert len(index.splitlines()) == 1
    assert json.loads(index)["category"] == "identity"

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"] == 1
    assert manifest["by_category"] == {"identity": 1}


def test_import_external_descarta_urls_del_nucleo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un documento cuya URL ya está en el núcleo no entra a la capa extendida."""

    record = _record("abc123")
    monkeypatch.setattr(importer, "_core_urls", lambda: {normalize_url(record["url"])})
    corpus = tmp_path / "donante"
    _build_corpus(corpus, [record])

    report = import_external(corpus, tmp_path / "external" / "alejandro")

    assert report.imported == 0


def test_import_external_deduplica_por_hash_y_filtra_categorias(tmp_path: Path) -> None:
    """El mismo texto se importa una vez y las categorías ajenas quedan fuera."""

    corpus = tmp_path / "donante"
    _build_corpus(
        corpus,
        [
            _record("uno", url="https://www.carvajal.com/historia/"),
            _record("dos", url="https://www.carvajal.com/historia-espejo/"),
            _record("tres", category="discovery", url="https://www.carvajal.com/ideas/"),
        ],
    )

    report = import_external(corpus, tmp_path / "external" / "alejandro")

    assert report.imported == 1
    assert report.duplicates == 1


def test_import_external_reemplaza_la_salida_anterior(tmp_path: Path) -> None:
    """Volver a importar no deja documentos de una corrida anterior."""

    corpus = tmp_path / "donante"
    output = tmp_path / "external" / "alejandro"
    _build_corpus(corpus, [_record("abc123")])
    import_external(corpus, output)
    old = output / "gold" / "ext_abc123.md"
    assert old.exists()

    _build_corpus(corpus, [_record("nuevo", url="https://www.carvajal.com/nueva/")])
    import_external(corpus, output)

    assert not old.exists()
    assert (output / "gold" / "ext_nuevo.md").exists()


def test_normalize_url_iguala_variantes() -> None:
    """Esquema, ``www``, barra final y fragmento no alteran la comparación."""

    assert normalize_url("http://www.Carvajal.com/historia/") == "https://carvajal.com/historia"
    assert normalize_url("https://carvajal.com/historia#top") == "https://carvajal.com/historia"
    assert normalize_url("https://www.carvajal.com/historia/?page=2") == (
        "https://carvajal.com/historia?page=2"
    )
