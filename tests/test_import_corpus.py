"""Pruebas unitarias del importador Gold: parser, carga y fragmentación."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from carvajal.adapters.storage.import_corpus import load_document, split_document
from carvajal.domain.chunking import CHUNK_SIZE
from carvajal.domain.frontmatter import parse_front_matter, strip_front_matter
from carvajal.domain.rules import content_hash

COMPLETO = """---
url: https://www.carvajal.com/historia/
title: Historia
section: historia
hash: abc123
captured_at: 2026-10-31T13:43:21+00:00
source_type: oficial
---

## 1904
"""


def test_parse_front_matter_con_y_sin_bloque() -> None:
    """El bloque YAML se separa del cuerpo; sin bloque no hay metadatos."""

    content = "---\nurl: https://x/\ntitle: Título\n---\n\nCuerpo."

    assert parse_front_matter(content) == {"url": "https://x/", "title": "Título"}
    assert strip_front_matter(content) == "Cuerpo."
    assert parse_front_matter("# Solo cuerpo") == {}
    assert strip_front_matter("# Solo cuerpo") == "# Solo cuerpo"


def test_load_document_completo_y_con_campos_faltantes(tmp_path: Path) -> None:
    """El front matter manda; sin él se usa el archivo, la URL y ``home``."""

    path = tmp_path / "historia.md"
    path.write_text(COMPLETO, encoding="utf-8")
    document = load_document(path, layer="core")

    assert (document.id, document.slug, document.layer) == ("historia", "historia", "core")
    assert document.title == "Historia"
    assert document.url == "https://www.carvajal.com/historia/"
    assert document.section == "historia"
    assert document.markdown == "## 1904"
    assert document.content_hash == "abc123"
    assert document.captured_at == datetime(2026, 10, 31, 13, 43, 21, tzinfo=UTC)

    plain = tmp_path / "sin_metadatos.md"
    plain.write_text("# Solo cuerpo\n", encoding="utf-8")
    document = load_document(plain, layer="core")

    assert (document.id, document.title) == ("sin_metadatos",) * 2
    assert document.section == "home"
    assert document.url == ""
    assert document.content_hash == content_hash("# Solo cuerpo")

    con_url = tmp_path / "con_url.md"
    con_url.write_text("---\nurl: https://www.carvajal.com/empresa/\n---\n\nX", "utf-8")
    assert load_document(con_url, layer="core").section == "empresa"


@pytest.mark.parametrize("valor", ["ayer", "2026-10-31T13:43:21"])
def test_captured_at_invalido_o_naive_usa_la_mtime(tmp_path: Path, valor: str) -> None:
    """Una fecha inválida o sin zona horaria cae a la mtime del archivo en UTC."""

    path = tmp_path / "nota.md"
    path.write_text(f"---\ncaptured_at: {valor}\n---\n\nCuerpo.", encoding="utf-8")

    assert load_document(path, layer="core").captured_at == datetime.fromtimestamp(
        path.stat().st_mtime, tz=UTC
    )


def test_split_document_chunks_contiguos_estables_y_cuerpo_vacio(tmp_path: Path) -> None:
    """Un cuerpo de ~2700 caracteres da varios fragmentos; el vacío no da ninguno."""

    path = tmp_path / "largo.md"
    path.write_text("---\ntitle: Largo\n---\n\n" + "Frase de prueba. " * 160, encoding="utf-8")
    document = load_document(path, layer="core")

    chunks = split_document(document)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= CHUNK_SIZE for chunk in chunks)
    assert [chunk.position for chunk in chunks] == list(range(len(chunks)))
    assert [chunk.id for chunk in chunks] == [f"largo:{i}" for i in range(len(chunks))]
    assert all(
        (chunk.document_id, chunk.section, chunk.layer) == ("largo", "home", "core")
        for chunk in chunks
    )
    assert [chunk.text for chunk in split_document(document)] == [chunk.text for chunk in chunks]

    empty = tmp_path / "vacio.md"
    empty.write_text("---\ntitle: Vacío\n---\n", encoding="utf-8")
    assert split_document(load_document(empty, layer="core")) == []
