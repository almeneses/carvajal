"""Pruebas de la ingesta de documentos: capas y metadatos, sin servicios."""

from pathlib import Path

from apps.api.ingestion import IngestReport, UploadedDocument, ingest_uploads
from carvajal.adapters.storage.import_corpus import load_document
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

    def importar(gold_dir: Path) -> tuple[int, int]:
        llamadas.append(("importar", gold_dir))
        return 7, 21

    def indexar(gold_dir: Path) -> int:
        llamadas.append(("indexar", gold_dir))
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
        importar=lambda gold_dir: (1, 1),
        indexar=lambda gold_dir: 1,
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
        [UploadedDocument(filename="datos.pdf", data=b"%PDF-1.7")],
        settings=settings,
    )

    # Assert
    assert report.gold == 0
    assert any("formato no admitido" in error for error in report.errors)
    assert not settings.gold_dir.exists() or not list(settings.gold_dir.glob("*.md"))
