"""Pruebas unitarias del importador Gold: parser, carga y fragmentación."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from carvajal.adapters.storage.import_corpus import (
    GoldImportError,
    _load_documents,
    load_document,
    split_document,
)
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


def test_parse_front_matter_exige_lineas_delimitadoras() -> None:
    """Solo una línea ``---`` cierra el bloque; un ``---`` embebido no cuenta."""

    con_guiones = "---\ntitle: A --- B\n---\n\nCuerpo"
    assert parse_front_matter(con_guiones) == {"title": "A --- B"}
    assert strip_front_matter(con_guiones) == "Cuerpo"

    cuerpo_con_guiones = "---\ntitle: T\n---\n\n---\nSección"
    assert parse_front_matter(cuerpo_con_guiones) == {"title": "T"}
    assert strip_front_matter(cuerpo_con_guiones) == "---\nSección"

    sin_cierre = "---\ntitle: T\n\nCuerpo"
    assert parse_front_matter(sin_cierre) == {}
    assert strip_front_matter(sin_cierre) == sin_cierre

    con_espacios = "  ---  \ntitle: T\n  ---  \n\nCuerpo"
    assert parse_front_matter(con_espacios) == {"title": "T"}
    assert strip_front_matter(con_espacios) == "Cuerpo"


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


def test_captured_at_con_offset_se_normaliza_a_utc(tmp_path: Path) -> None:
    """Una fecha válida con offset se convierte a UTC antes de guardarse."""

    path = tmp_path / "offset.md"
    path.write_text("---\ncaptured_at: 2026-10-31T13:43:21+02:00\n---\n\nCuerpo.", "utf-8")

    document = load_document(path, layer="core")

    assert document.captured_at == datetime(2026, 10, 31, 11, 43, 21, tzinfo=UTC)
    assert document.captured_at.tzinfo is UTC


@pytest.mark.parametrize("slug", ["a/b", "a\\b", "..", ".", "a..b"])
def test_slug_invalido_lanza_error(tmp_path: Path, slug: str) -> None:
    """Un slug con separadores de ruta o ``..`` no puede convertirse en id."""

    path = tmp_path / "doc.md"
    path.write_text(f"---\nslug: {slug}\n---\n\nCuerpo.", encoding="utf-8")

    with pytest.raises(GoldImportError, match=r"doc\.md: el slug"):
        load_document(path, layer="core")


def test_slug_en_blanco_cae_al_nombre_del_archivo(tmp_path: Path) -> None:
    """Un ``slug`` vacío tras ``strip()`` se sustituye por el nombre del archivo."""

    path = tmp_path / "archivo.md"
    path.write_text("---\nslug:    \n---\n\nCuerpo.", encoding="utf-8")

    assert load_document(path, layer="core").id == "archivo"


def test_slugs_duplicados_abortan_antes_de_importar(tmp_path: Path) -> None:
    """Dos Markdown con el mismo slug no se sobrescriben en silencio."""

    (tmp_path / "a.md").write_text("---\nslug: repetido\n---\n\nA", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\nslug: repetido\n---\n\nB", encoding="utf-8")

    with pytest.raises(GoldImportError, match=r"b\.md: el slug 'repetido' ya lo usa a\.md"):
        _load_documents(tmp_path, layer="core")


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
