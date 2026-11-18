"""Pruebas de la ingesta de documentos: capas y metadatos, sin servicios."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.api.document_formats import ExtractError, extract_markdown
from apps.api.ingest_cli import main, run
from apps.api.ingestion import IngestReport, UploadedDocument, ingest_uploads
from carvajal.adapters.storage.import_corpus import load_document
from carvajal.application.sync import SyncReport
from carvajal.gold import merge_index
from carvajal.infrastructure.settings import Settings


def _temporales(directorio: Path) -> list[str]:
    """Temporales que dejó una escritura de Gold, incluidos los ocultos."""

    return sorted(path.name for path in directorio.iterdir() if path.name.endswith(".tmp"))


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

    def sincronizar(gold_dir: Path, only: set[str]) -> SyncReport:
        llamadas.append(("sincronizar", gold_dir))
        assert only
        return SyncReport(documents=7, chunks=21, indexed=35)

    documento = UploadedDocument(
        filename="politica.md",
        data=b"# Politica\n\nContenido **util** para el corpus.",
    )

    # Act
    report = ingest_uploads([documento], settings=settings, sincronizar=sincronizar)

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
    gold = settings.gold_dir / "politica.md"
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
    assert llamadas == [("sincronizar", settings.gold_dir)]


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
        sincronizar=lambda gold_dir, only: SyncReport(documents=1, chunks=1, indexed=1),
    )

    # Assert
    assert report.gold == 1
    gold = settings.gold_dir / "aviso.md"
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

    monkeypatch.setattr("apps.api.ingestion.sync_gold", prohibido)

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


def test_ingest_cli_lee_por_lote_y_conserva_lo_procesado(tmp_path: Path, monkeypatch) -> None:
    # Arrange: si el lote se construye antes de procesar, el fallo del tercer
    # archivo impediría que los dos primeros llegasen a Gold.
    settings = _settings(tmp_path)
    carpeta = tmp_path / "corpus"
    carpeta.mkdir()
    for nombre in ("a.md", "b.md", "c.md"):
        (carpeta / nombre).write_text(f"# {nombre}\n\nContenido.", encoding="utf-8")

    original = Path.read_bytes

    def leer(self: Path) -> bytes:
        if self.name == "c.md":
            raise OSError(5, "fallo simulado")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", leer)

    # Act
    report = run([carpeta], settings=settings, solo_extraer=True, lote=1)

    # Assert
    assert report.gold == 2
    assert any("no se pudo leer (fallo simulado)" in error for error in report.errors)


def test_ingest_cli_limita_los_bytes_por_lote(tmp_path: Path, monkeypatch) -> None:
    # Arrange: dos archivos que juntos superan 1 KB no caben en el mismo lote.
    settings = _settings(tmp_path)
    carpeta = tmp_path / "corpus"
    carpeta.mkdir()
    (carpeta / "a.md").write_text("# a\n\n" + "x" * 600, encoding="utf-8")
    (carpeta / "b.md").write_text("# b\n\n" + "y" * 600, encoding="utf-8")
    lotes: list[int] = []

    def ingestar(documents, **kwargs):
        lotes.append(len(documents))
        return IngestReport(files=len(documents), gold=len(documents))

    monkeypatch.setattr("apps.api.ingest_cli.ingest_uploads", ingestar)

    # Act
    run([carpeta], settings=settings, solo_extraer=True, lote=25, max_batch_bytes=1024)

    # Assert
    assert lotes == [1, 1]


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


def _entradas_index(settings: Settings) -> dict[str, dict[str, str | int]]:
    """Lee el índice del corpus indexado por slug."""

    return {
        entrada["slug"]: entrada
        for entrada in (
            json.loads(linea)
            for linea in settings.index_file.read_text(encoding="utf-8").splitlines()
        )
    }


def test_ingest_uploads_descarta_una_copia_exacta_con_otro_nombre(tmp_path: Path) -> None:
    """Una copia con otro nombre se reporta como duplicada y no escribe capas."""

    # Arrange
    settings = _settings(tmp_path)
    original = UploadedDocument(filename="informe.md", data=b"# Informe\n\nContenido unico.")
    copia = UploadedDocument(filename="copia.md", data=original.data)

    # Act
    report = ingest_uploads([original, copia], settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 1
    assert report.files == 1
    assert len(report.duplicates) == 1 and "informe" in report.duplicates[0]
    assert not (settings.gold_dir / "copia.md").exists()
    assert not (settings.raw_dir / "uploads" / "copia.md").exists()
    assert set(_entradas_index(settings)) == {"informe"}


def test_ingest_uploads_no_reescribe_capas_si_no_cambia(tmp_path: Path) -> None:
    """Reimportar el mismo archivo no escribe capas ni vuelve a embeber."""

    # Arrange
    settings = _settings(tmp_path)
    documento = UploadedDocument(filename="informe.md", data=b"# Informe\n\nContenido estable.")
    llamadas: list[str] = []
    ingest_uploads([documento], settings=settings, solo_extraer=True)
    antes = (settings.gold_dir / "informe.md").read_bytes()

    def sincronizar(gold_dir: Path, only: set[str]) -> SyncReport:
        llamadas.append("sincronizar")
        return SyncReport(documents=1, chunks=1, indexed=1)

    # Act
    report = ingest_uploads([documento], settings=settings, sincronizar=sincronizar)

    # Assert
    assert report.gold == 0 and report.bronze == 0 and report.silver == 0
    assert report.unchanged == ("informe.md",)
    assert llamadas == []
    assert (settings.gold_dir / "informe.md").read_bytes() == antes
    assert list(_entradas_index(settings)) == ["informe"]


def test_una_carga_que_falla_al_escribir_gold_conserva_la_version_anterior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La sincronización nunca ve un Gold truncado con la huella nueva."""

    # Arrange
    settings = _settings(tmp_path)
    ingest_uploads(
        [UploadedDocument(filename="informe.md", data=b"# Informe\n\nVersion uno.")],
        settings=settings,
        solo_extraer=True,
    )
    gold = settings.gold_dir / "informe.md"
    completo = gold.read_bytes()
    escribir = Path.write_text

    def cortar(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if data.startswith("---"):
            escribir(self, data[:20], encoding, errors, newline)
            raise OSError("disco lleno")
        return escribir(self, data, encoding, errors, newline)

    monkeypatch.setattr(Path, "write_text", cortar)

    # Act
    with pytest.raises(OSError):
        ingest_uploads(
            [UploadedDocument(filename="informe.md", data=b"# Informe\n\nVersion dos.")],
            settings=settings,
            solo_extraer=True,
        )

    # Assert
    assert gold.read_bytes() == completo
    assert _temporales(settings.gold_dir) == []


def test_ingest_uploads_actualiza_el_mismo_slug_cuando_cambia(tmp_path: Path) -> None:
    """Un archivo modificado conserva su slug; la base archiva la versión anterior."""

    # Arrange
    settings = _settings(tmp_path)
    ingest_uploads(
        [UploadedDocument(filename="informe.md", data=b"# Informe\n\nVersion uno.")],
        settings=settings,
        solo_extraer=True,
    )
    primera = _entradas_index(settings)["informe"]

    # Act
    report = ingest_uploads(
        [UploadedDocument(filename="informe.md", data=b"# Informe\n\nVersion dos.")],
        settings=settings,
        solo_extraer=True,
    )

    # Assert
    assert report.updated == ("informe.md",) and report.gold == 1
    assert sorted(path.name for path in settings.gold_dir.glob("*.md")) == ["informe.md"]
    segunda = _entradas_index(settings)["informe"]
    assert segunda["hash"] != primera["hash"]
    assert segunda["source_key"] == primera["source_key"] == "upload:informe.md"


def test_ingest_cli_da_identidad_distinta_a_dos_homonimos_en_carpetas(tmp_path: Path) -> None:
    """Dos ``informe.md`` con contenido distinto no se pisan: cada uno tiene su fuente."""

    # Arrange
    settings = _settings(tmp_path)
    carpeta = tmp_path / "corpus"
    (carpeta / "uno").mkdir(parents=True)
    (carpeta / "dos").mkdir(parents=True)
    (carpeta / "uno" / "informe.md").write_text("# Uno\n\nContenido uno.", encoding="utf-8")
    (carpeta / "dos" / "informe.md").write_text("# Dos\n\nContenido dos.", encoding="utf-8")

    # Act
    report = run([carpeta], settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 2
    entradas = _entradas_index(settings)
    assert set(entradas) == {"informe", "informe-2"}
    assert {entrada["source_key"] for entrada in entradas.values()} == {
        "file:corpus/uno/informe.md",
        "file:corpus/dos/informe.md",
    }


def test_ingest_uploads_detecta_duplicado_semantico_de_html(tmp_path: Path) -> None:
    """Dos HTML con distinto contenedor y el mismo texto no crean dos documentos."""

    # Arrange
    settings = _settings(tmp_path)
    uno = UploadedDocument(
        filename="uno.html",
        data=b"<html><body><div><p>Texto compartido.</p></div></body></html>",
    )
    dos = UploadedDocument(
        filename="dos.html",
        data=b"<html><body><section><p>Texto compartido.</p></section></body></html>",
    )

    # Act
    report = ingest_uploads([uno, dos], settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 1
    assert len(report.duplicates) == 1 and "uno" in report.duplicates[0]
    assert set(_entradas_index(settings)) == {"uno"}


def test_ingest_uploads_detecta_pdf_recodificado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un PDF re-guardado con los mismos bytes de texto es un duplicado semántico."""

    # Arrange
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "apps.api.document_formats.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b"Contenido del informe\f"),
    )
    original = UploadedDocument(filename="informe.pdf", data=b"%PDF-1.7 original")
    recodificado = UploadedDocument(filename="copia.pdf", data=b"%PDF-1.7 recodificado")

    # Act
    report = ingest_uploads([original, recodificado], settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 1
    assert len(report.duplicates) == 1 and "informe" in report.duplicates[0]
    assert set(_entradas_index(settings)) == {"informe"}


def test_ingest_cli_reporta_un_archivo_movido_como_duplicado(tmp_path: Path) -> None:
    """Mover un archivo no crea documento: conserva la identidad anterior."""

    # Arrange
    settings = _settings(tmp_path)
    carpeta = tmp_path / "corpus"
    (carpeta / "uno").mkdir(parents=True)
    origen = carpeta / "uno" / "informe.md"
    origen.write_text("# Informe\n\nContenido.", encoding="utf-8")
    run([carpeta], settings=settings, solo_extraer=True)
    (carpeta / "dos").mkdir()
    movido = origen.rename(carpeta / "dos" / "informe.md")

    # Act
    report = run([carpeta], settings=settings, solo_extraer=True)

    # Assert
    assert movido.is_file()
    assert report.gold == 0
    assert len(report.duplicates) == 1 and "informe" in report.duplicates[0]
    assert set(_entradas_index(settings)) == {"informe"}


def test_ingest_uploads_rechaza_la_misma_fuente_con_otro_contenido(tmp_path: Path) -> None:
    """Dos archivos homónimos en una carga web: el segundo se rechaza con su nombre."""

    # Arrange
    settings = _settings(tmp_path)
    primero = UploadedDocument(filename="informe.md", data=b"# Primero\n\nContenido A.")
    segundo = UploadedDocument(filename="informe.md", data=b"# Segundo\n\nContenido B.")

    # Act
    report = ingest_uploads([primero, segundo], settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 1 and report.files == 1
    assert any("informe.md" in error and "otro contenido" in error for error in report.errors)
    gold = (settings.gold_dir / "informe.md").read_text(encoding="utf-8")
    assert "Contenido A." in gold and "Contenido B." not in gold


def test_ingest_uploads_conserva_el_primero_de_dos_homonimos_web(tmp_path: Path) -> None:
    """En una carga web con dos homónimos, el primero llega a Gold."""

    # Arrange
    settings = _settings(tmp_path)
    documentos = [
        UploadedDocument(filename="aviso.md", data=b"# Aviso\n\nPrimera version."),
        UploadedDocument(filename="aviso.md", data=b"# Aviso\n\nSegunda version."),
    ]

    # Act
    report = ingest_uploads(documentos, settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 1
    assert (settings.gold_dir / "aviso.md").exists()
    assert "Primera version." in (settings.gold_dir / "aviso.md").read_text(encoding="utf-8")
    assert _entradas_index(settings)["aviso"]["source_key"] == "upload:aviso.md"


def test_limites_de_carga_web_y_consola_se_conservan(tmp_path: Path) -> None:
    """La web mantiene 100 archivos y 5 MB; la consola, 100 MB por archivo."""

    # Arrange
    from apps.api.ingest_cli import DEFAULT_MAX_FILE_MB
    from apps.api.ingestion import MAX_FILE_BYTES, MAX_FILES

    assert MAX_FILES == 100
    assert MAX_FILE_BYTES == 5 * 1024 * 1024
    assert DEFAULT_MAX_FILE_MB == 100

    settings = _settings(tmp_path)
    documentos = [
        UploadedDocument(
            filename=f"doc-{numero}.md",
            data=f"# Doc {numero}\n\nContenido {numero}.".encode(),
        )
        for numero in range(MAX_FILES + 1)
    ]

    # Act
    report = ingest_uploads(documentos, settings=settings, solo_extraer=True)

    # Assert
    assert report.files == MAX_FILES
    assert any("Solo se procesan 100 archivos" in error for error in report.errors)


def test_la_carga_web_rechaza_un_archivo_mayor_de_cinco_mb(tmp_path: Path) -> None:
    """Un archivo por encima del límite no se escribe ni se extrae."""

    # Arrange
    from apps.api.ingestion import MAX_FILE_BYTES

    settings = _settings(tmp_path)
    documento = UploadedDocument(
        filename="grande.md",
        data=b"x" * (MAX_FILE_BYTES + 1),
    )

    # Act
    report = ingest_uploads([documento], settings=settings, solo_extraer=True)

    # Assert
    assert report.gold == 0
    assert any("supera el límite" in error for error in report.errors)


def test_la_consola_respeta_el_limite_por_archivo(tmp_path: Path) -> None:
    """La consola aplica el ``max_file_bytes`` configurado a cada archivo."""

    # Arrange
    settings = _settings(tmp_path)
    carpeta = tmp_path / "corpus"
    carpeta.mkdir()
    (carpeta / "grande.md").write_bytes(b"x" * 2048)

    # Act
    report = run(
        [carpeta],
        settings=settings,
        solo_extraer=True,
        max_file_bytes=1024,
    )

    # Assert
    assert report.gold == 0
    assert any("supera el límite" in error for error in report.errors)


def test_el_informe_sanea_rutas_y_credenciales_de_la_sincronizacion(tmp_path: Path) -> None:
    """La UI no publica rutas internas ni contraseñas del almacén."""

    # Arrange: la sincronización devuelve un fallo con ruta y URL con contraseña.
    settings = _settings(tmp_path)
    documento = UploadedDocument(filename="informe.pdf", data=b"%PDF-1.4 texto")
    error = (
        "/srv/carvajal/data/gold/informe.md: connection to "
        "postgresql://carvajal:s3creto@db:5432/carvajal failed"
    )

    def sincronizar(gold_dir: Path, only: set[str]) -> SyncReport:
        return SyncReport(errors=(error,))

    # Act
    report = ingest_uploads([documento], settings=settings, sincronizar=sincronizar)

    # Assert
    assert report.errors
    assert all("s3creto" not in mensaje for mensaje in report.errors)
    assert all("/srv/carvajal" not in mensaje for mensaje in report.errors)
