"""Pruebas de integración del importador Gold contra un Postgres real."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from carvajal.adapters.storage.import_corpus import GoldImportReport, migrate_gold
from carvajal.adapters.storage.repositories import SqlChunkRepository, SqlDocumentRepository
from carvajal.config import GOLD_DIR, LAYER_CORE

pytestmark = pytest.mark.integration

CAPTURED_AT = "2026-10-31T13:43:21+00:00"


def _write(path: Path, *, slug: str, body: str) -> None:
    """Escribe un Markdown Gold mínimo con slug, URL y fecha de captura."""

    path.write_text(
        f"---\nslug: {slug}\ntitle: {slug}\nurl: https://www.carvajal.com/{slug}/\n"
        f"captured_at: {CAPTURED_AT}\n---\n\n{body}",
        encoding="utf-8",
    )


def test_migrar_dos_veces_no_duplica_y_archiva_la_version_anterior(
    tmp_path: Path, session: Session
) -> None:
    """Reimportar es idempotente; cambiar el cuerpo deja una versión con el Markdown previo."""

    _write(tmp_path / "uno.md", slug="uno", body="Cuerpo uno.")
    _write(tmp_path / "dos.md", slug="dos", body="Cuerpo dos.")

    first = migrate_gold(session, gold_dir=tmp_path)
    chunks = SqlChunkRepository(session)
    before = [chunk.id for chunk in chunks.list_by_document("uno")]
    second = migrate_gold(session, gold_dir=tmp_path)

    repository = SqlDocumentRepository(session)
    assert first == second == GoldImportReport(documents=2, chunks=2, removed=0)
    assert repository.count(layer=LAYER_CORE) == 2
    assert before
    assert [chunk.id for chunk in chunks.list_by_document("uno")] == before
    assert repository.list_versions("uno") == []

    _write(tmp_path / "uno.md", slug="uno", body="Cuerpo uno corregido.")
    migrate_gold(session, gold_dir=tmp_path)

    versions = repository.list_versions("uno")
    assert [version.markdown for version in versions] == ["Cuerpo uno."]
    actualizado = repository.get("uno")
    assert actualizado is not None
    assert actualizado.markdown == "Cuerpo uno corregido."

    _write(tmp_path / "vacio.md", slug="vacio", body="")
    tercera = migrate_gold(session, gold_dir=tmp_path)
    assert tercera == GoldImportReport(documents=3, chunks=2, removed=0)
    assert SqlChunkRepository(session).list_by_document("vacio") == []


def test_podar_huerfanos_elimina_el_documento_y_su_cascada(
    tmp_path: Path, session: Session
) -> None:
    """Borrar un Markdown y reimportar poda el documento, sus chunks y sus versiones."""

    _write(tmp_path / "uno.md", slug="uno", body="Cuerpo uno.")
    _write(tmp_path / "dos.md", slug="dos", body="Cuerpo dos.")
    migrate_gold(session, gold_dir=tmp_path)

    # Antes de borrarlo, deja una versión archivada de ``uno``.
    _write(tmp_path / "uno.md", slug="uno", body="Cuerpo uno corregido.")
    migrate_gold(session, gold_dir=tmp_path)

    repository = SqlDocumentRepository(session)
    chunks = SqlChunkRepository(session)
    assert repository.list_versions("uno")
    assert chunks.list_by_document("uno")

    (tmp_path / "uno.md").unlink()
    podado = migrate_gold(session, gold_dir=tmp_path)

    assert podado == GoldImportReport(documents=1, chunks=1, removed=1)
    assert repository.count(layer=LAYER_CORE) == 1
    assert repository.get("uno") is None
    assert chunks.list_by_document("uno") == []
    assert repository.list_versions("uno") == []

    # Idempotencia: reimportar sin cambios no duplica ni poda nada.
    again = migrate_gold(session, gold_dir=tmp_path)
    assert again == GoldImportReport(documents=1, chunks=1, removed=0)
    assert repository.count(layer=LAYER_CORE) == 1


def test_corpus_real_coincide_con_el_indice(session: Session) -> None:
    """Cada entrada de ``index.jsonl`` tiene su documento con los mismos metadatos."""

    entries = [
        json.loads(line)
        for line in (GOLD_DIR / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    migrate_gold(session, gold_dir=GOLD_DIR)

    repository = SqlDocumentRepository(session)
    assert repository.count(layer=LAYER_CORE) == len(entries)
    for entry in entries:
        document = repository.get_by_slug(entry["slug"])
        assert document is not None
        assert document.content_hash == entry["hash"]
        assert document.captured_at == datetime.fromisoformat(entry["captured_at"])
        assert document.section == entry["section"]
        assert document.title == entry["title"]
        assert document.url == entry["url"]
