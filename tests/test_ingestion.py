"""Pruebas de la ingesta de documentos: capas y metadatos, sin servicios."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.api.document_formats import ExtractError, extract_markdown
from apps.api.ingest_cli import main, run
from apps.api.ingestion import IngestReport, UploadedDocument, ingest_uploads
from carvajal.adapters.storage.import_corpus import load_document
from carvajal.gold import merge_index
from carvajal.infrastructure.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        gold_dir=tmp_path / "gold",
        raw_dir=tmp_path / "raw",
        processed_dir=tmp_path / "processed",
    )


def test_ingest_uploads_escribe_bronze_silver_gold_y_llama_las_etapas(
    tmp_path: Path,
) -> None:
    # Arrange
    settings = _settings(tmp_path)
    llamadas: list[tuple[str, Path]] = []

    def importar(gold_dir: Path, only: set[str]) -> tuple[int, int]:
        llamadas.append(("importar", gold_dir))
        assert only
        return 7, 21

    def indexar(gold_dir: Path, only: set[str]) -> int:
        llamadas.append(("indexar", gold_dir))
        assert only
        return 35

    documento = UploadedDocument(
        filename="politica.md",
        data=b"# Politica\n\nContenido **util** para el corpus.",
    )

    # Act
    report = ingest_uploads([documento], settings=settings, importar=importar, indexar=indexar)

    # Assert
    assert report == IngestReport(
        files=1,
        bronze=1,
        silver=1,
        gold=1,
        documents=7,
        chunks=21,
        indexed=35,
    )
    raw = next((settings.raw_dir / "uploads").glob("*.md"))
    silver = next((settings.processed_dir / "uploads").glob("*.md"))
    gold = next(settings.gold_dir.glob("politica-*.md"))
    assert raw.read_bytes().startswith(b"# Politica")
    assert "Contenido **util**" in silver.read_text(encoding="utf-8")
    gold_text = gold.read_text(encoding="utf-8")
    assert "section: Cargas" in gold_text
    assert "source_type: colaborador" in gold_text
    assert "url: upload://" in gold_text
    cargado = load_document(gold, layer="core")
    assert cargado.section == "Cargas"
    assert cargado.source_type == "colaborador"
    assert (settings.gold_dir / "index.jsonl").is_file()
    assert llamadas == [("importar", settings.gold_dir), ("indexar", settings.gold_dir)]


def test_ingest_uploads_extrae_texto_de_html(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    documento = UploadedDocument(
        filename="aviso.html",
        data=b"<html><title>Aviso</title><body><h1>Aviso</h1><p>Texto visible.</p></body></html>",
    )

    # Act
    report = ingest_uploads(
        [documento],
        settings=settings,
        importar=lambda gold_dir, only: (1, 1),
        indexar=lambda gold_dir, only: 1,
    )

    # Assert
    assert report.gold == 1
    gold = next(settings.gold_dir.glob("aviso-*.md"))
    texto = gold.read_text(encoding="utf-8")
    assert "title: Aviso" in texto
    assert "Texto visible." in texto
    assert "<h1>" not in texto


def test_ingest_uploads_rechaza_extensiones_no_admitidas(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)

    # Act
    report = ingest_uploads(
        [UploadedDocument(filename="datos.docx", data=b"contenido")],
        settings=settings,
    )

    # Assert
    assert report.gold == 0
    assert any("formato no admitido" in error for error in report.errors)
    assert not settings.gold_dir.exists() or not list(settings.gold_dir.glob("*.md"))


def test_ingest_uploads_admite_datos_estructurados_sin_tocar_servicios(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    documentos = [
        UploadedDocument(filename="datos.xml", data=b"<raiz><dato>1</dato></raiz>"),
        UploadedDocument(filename="datos.json", data=b'{"a": 1}'),
        UploadedDocument(filename="datos.jsonl", data=b'{"a": 1}\n{"a": 2}\n'),
        UploadedDocument(filename="datos.csv", data=b"a,b\n1,2\n"),
        UploadedDocument(filename="datos.tsv", data=b"a\tb\n1\t2\n"),
    ]

    # Act
    report = ingest_uploads(documentos, settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 5
    assert report.documents == 0 and report.indexed == 0
    assert report.errors == ()
    textos = [path.read_text(encoding="utf-8") for path in settings.gold_dir.glob("*.md")]
    assert any("```xml" in texto for texto in textos)
    assert any("```jsonl" in texto for texto in textos)


def test_ingest_uploads_reporta_un_json_malo_sin_frenar_el_lote(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    documentos = [
        UploadedDocument(filename="roto.json", data=b"{no es json"),
        UploadedDocument(filename="bien.md", data=b"# Bien\n\nContenido del corpus."),
    ]

    # Act
    report = ingest_uploads(documentos, settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 1
    assert any("roto.json" in error and "JSON" in error for error in report.errors)


def test_ingest_uploads_rechaza_xml_con_dtd(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    documento = UploadedDocument(
        filename="externo.xml",
        data=b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
    )

    # Act
    report = ingest_uploads([documento], settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 0
    assert any("DTD" in error for error in report.errors)


def test_extract_markdown_pdf_conserva_pagina_y_omite_vacias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    salida = b"texto de la uno\f\ftexto de la tres\f"
    monkeypatch.setattr(
        "apps.api.document_formats.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=salida),
    )

    # Act
    markdown, _ = extract_markdown("informe.pdf", b"%PDF-1.7")

    # Assert
    assert "## Página 1" in markdown and "## Página 3" in markdown
    assert "## Página 2" not in markdown


def test_extract_markdown_pdf_sin_texto_avisa_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setattr(
        "apps.api.document_formats.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b"\f\f"),
    )

    # Act / Assert
    with pytest.raises(ExtractError, match="OCR"):
        extract_markdown("escaneado.pdf", b"%PDF-1.7")


def test_extract_markdown_pdf_sin_extractor_nombra_la_dependencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    def falta(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("pdftotext")

    monkeypatch.setattr("apps.api.document_formats.subprocess.run", falta)

    # Act / Assert
    with pytest.raises(ExtractError, match="poppler-utils"):
        extract_markdown("informe.pdf", b"%PDF-1.7")


def test_ingest_cli_recorre_carpetas_e_ignora_archivos_ajenos(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    carpeta = tmp_path / "corpus"
    (carpeta / "sub").mkdir(parents=True)
    (carpeta / "a.md").write_text("# A\n\nContenido A.", encoding="utf-8")
    (carpeta / "sub" / "b.txt").write_text("Contenido B.", encoding="utf-8")
    (carpeta / "sub" / "c.bin").write_bytes(b"\x00\x01")

    # Act
    report = run([carpeta], settings=settings, solo_extraer=True)

    # Assert
    assert report.files == 2 and report.gold == 2
    assert {path.stem.split("-")[0] for path in settings.gold_dir.glob("*.md")} == {"a", "b"}


def test_ingest_cli_solo_extraer_no_llama_a_postgres_ni_qdrant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    settings = _settings(tmp_path)
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "a.md").write_text("# A\n\nContenido.", encoding="utf-8")

    def prohibido(*args: object, **kwargs: object) -> None:
        raise AssertionError("no debe tocar servicios con --solo-extraer")

    monkeypatch.setattr("apps.api.ingestion.importar_gold", prohibido)
    monkeypatch.setattr("apps.api.ingestion.indexar_gold", prohibido)

    # Act
    report = run([tmp_path / "corpus"], settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 1
    assert report.documents == 0 and report.indexed == 0


def test_ingest_cli_procesa_en_lotes_y_devuelve_error_si_falta_la_ruta(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    carpeta = tmp_path / "corpus"
    carpeta.mkdir()
    for nombre in ("a.md", "b.md", "c.md"):
        (carpeta / nombre).write_text(f"# {nombre}\n\nContenido.", encoding="utf-8")

    # Act
    report = run([carpeta, tmp_path / "no-existe"], settings=settings, solo_extraer=True, lote=1)

    # Assert
    assert report.gold == 3
    assert any("no existe" in error for error in report.errors)


def test_merge_index_conserva_las_entradas_concurrentes(tmp_path: Path) -> None:
    # Arrange
    index_file = tmp_path / "gold" / "index.jsonl"
    index_file.parent.mkdir()

    def fusionar(numero: int) -> None:
        merge_index(index_file, [{"slug": f"doc-{numero}", "title": f"Doc {numero}"}])

    # Act
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(fusionar, range(10)))

    # Assert
    lineas = index_file.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 10
    assert sorted(line.split('"slug": "')[1].split('"')[0] for line in lineas) == sorted(
        f"doc-{numero}" for numero in range(10)
    )
    assert not list(index_file.parent.glob("*.tmp"))


def test_merge_index_poda_entradas_sin_documento_en_gold(tmp_path: Path) -> None:
    # Arrange
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "vigente.md").write_text("# Vigente", encoding="utf-8")
    index_file = gold_dir / "index.jsonl"
    merge_index(index_file, [{"slug": "vigente"}, {"slug": "borrado"}])

    # Act
    merge_index(index_file, [{"slug": "vigente"}], gold_dir=gold_dir)

    # Assert
    lineas = index_file.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 1 and "vigente" in lineas[0]


def test_ingest_cli_devuelve_1_sin_archivos_admitidos(tmp_path: Path) -> None:
    # Arrange
    carpeta = tmp_path / "vacia"
    carpeta.mkdir()
    (carpeta / "notas.bin").write_bytes(b"\x00")

    # Act
    codigo = main([str(carpeta)])

    # Assert
    assert codigo == 1


def test_captura_y_carga_conservan_las_entradas_del_indice(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    settings.gold_dir.mkdir(parents=True)
    (settings.gold_dir / "capturado.md").write_text("# Capturado", encoding="utf-8")
    merge_index(
        settings.index_file,
        [{"slug": "capturado", "title": "Capturado", "source_type": "oficial"}],
        gold_dir=settings.gold_dir,
    )
    documento = UploadedDocument(filename="carga.md", data=b"# Carga\n\nContenido.")

    # Act
    ingest_uploads([documento], settings=settings, solo_extraer=True)

    # Assert
    slugs = {
        line.split('"slug": "')[1].split('"')[0]
        for line in settings.index_file.read_text(encoding="utf-8").splitlines()
    }
    assert "capturado" in slugs and len(slugs) == 2
