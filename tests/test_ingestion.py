"""Pruebas de la ingesta de documentos: capas y metadatos, sin servicios."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from apps.api.document_formats import extract_markdown
from apps.api.ingest_cli import main
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
        [UploadedDocument(filename="datos.bin", data=b"datos")],
        settings=settings,
    )

    # Assert
    assert report.gold == 0
    assert any("formato no admitido" in error for error in report.errors)
    assert not settings.gold_dir.exists() or not list(settings.gold_dir.glob("*.md"))


def test_pdf_conserva_el_numero_de_pagina(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.api.document_formats._pdf_text", lambda _data: b"Primera pagina\f\fTercera pagina\f"
    )
    result = extract_markdown(".pdf", b"%PDF-1.7")

    assert result == "## Página 1\n\nPrimera pagina\n\n## Página 3\n\nTercera pagina"


def test_ingesta_conserva_los_datos_y_continua_ante_un_archivo_invalido(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    documents = [
        UploadedDocument("malformado.json", b"{"),
        UploadedDocument("entidades.xml", b'<!DOCTYPE r [<!ENTITY dato "D">]><r>&dato;</r>'),
        UploadedDocument("contrato.xml", b'<contrato moneda="COP">Contrato 51</contrato>'),
        UploadedDocument("registro.json", b'{"periodo": 2025, "total": 42}'),
        UploadedDocument("filas.jsonl", b'{"id": 5}\n{"id": 6}\n'),
        UploadedDocument("cuentas.csv", b"cuenta,total\nActivo,42\n"),
        UploadedDocument("cuentas.tsv", b"cuenta\ttotal\nActivo\t42\n"),
    ]

    report = ingest_uploads(documents, settings=settings, solo_extraer=True)

    assert report.gold == report.bronze == report.silver == 5
    assert report.documents == report.indexed == 0
    assert len(report.errors) == 2
    assert report.errors[0].startswith("malformado.json:")
    assert report.errors[1].startswith("entidades.xml:")
    for document in documents[2:]:
        raw = list((settings.raw_dir / "uploads").glob(f"*{Path(document.filename).suffix}"))
        assert any(path.read_bytes() == document.data for path in raw)
    assert 'moneda="COP"' in next(settings.gold_dir.glob("contrato-*.md")).read_text()


def test_cli_extrae_carpetas_por_lotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_dir = tmp_path / "originales" / "subcarpeta"
    input_dir.mkdir(parents=True)
    (input_dir / "a.xml").write_text("<fuente>Alfa</fuente>")
    (input_dir / "b.json").write_text('{"fuente": "Beta"}')
    (input_dir / "archivo.bin").write_bytes(b"binario")
    monkeypatch.setenv("GOLD_DIR", str(tmp_path / "gold"))
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("PROCESSED_DIR", str(tmp_path / "silver"))
    monkeypatch.setattr("apps.api.ingest_cli.MAX_FILES", 1)

    assert main([str(input_dir.parent), "--solo-extraer"]) == 0
    assert len(list((tmp_path / "gold").glob("*.md"))) == 2


def test_merge_index_conserva_todas_las_cargas_concurrentes(tmp_path: Path) -> None:
    index = tmp_path / "index.jsonl"
    entries = [{"slug": f"doc-{number}"} for number in range(10)]

    def update(entry: dict[str, str | int]) -> None:
        merge_index(index, [entry])

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(update, entries))

    assert {json.loads(line)["slug"] for line in index.read_text().splitlines()} == {
        entry["slug"] for entry in entries
    }
