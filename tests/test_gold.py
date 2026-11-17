"""Pruebas del entregable Gold (front matter, procedencia e índice)."""

import json
from pathlib import Path

import pytest

from carvajal.gold import (
    domain_of,
    merge_index,
    read_index,
    source_type,
    to_gold_markdown,
    write_gold,
)
from carvajal.models import CapturedPage


def _temporales(directorio: Path) -> list[str]:
    """Temporales que dejó una escritura de Gold, incluidos los ocultos."""

    return sorted(path.name for path in directorio.iterdir() if path.name.endswith(".tmp"))


def _page(url: str) -> CapturedPage:
    """Construye una página capturada mínima para las pruebas."""

    return CapturedPage(url=url, html="<p>hola</p>", markdown="# Título\n\nCuerpo.")


def test_domain_of_quita_www() -> None:
    """El dominio se registra sin el prefijo ``www.``."""

    assert domain_of("https://www.carvajal.com/historia/") == "carvajal.com"


def test_source_type_distingue_oficial_de_tercero() -> None:
    """Solo el dominio de Carvajal se considera fuente oficial."""

    assert source_type("https://www.carvajal.com/historia/") == "oficial"
    assert source_type("https://www.larepublica.co/empresas/nota-4099828") == "tercero"
    assert source_type("https://carvajal.com.co/otro") == "tercero"


def test_to_gold_markdown_registra_procedencia() -> None:
    """El front matter incluye dominio y tipo de fuente junto a los metadatos base."""

    markdown = to_gold_markdown(_page("https://www.larepublica.co/empresas/nota"), "Nota")

    assert "url: https://www.larepublica.co/empresas/nota" in markdown
    assert "domain: larepublica.co" in markdown
    assert "source_type: tercero" in markdown
    assert "captured_at:" in markdown
    assert markdown.rstrip().endswith("Cuerpo.")


def test_to_gold_markdown_incluye_seccion_hash_y_tamano() -> None:
    """El front matter añade sección editorial, huella y número de caracteres."""

    page = _page("https://www.carvajal.com/inversionistas/inicio/")

    markdown = to_gold_markdown(page, "Inicio")

    assert "section: inversionistas" in markdown
    assert f"hash: {page.content_hash}" in markdown
    assert f"chars: {len(page.markdown)}" in markdown


def test_section_de_terceros_es_prensa() -> None:
    """Las fuentes externas se agrupan en la sección ``prensa``."""

    assert _page("https://www.elpais.com.co/economia/nota-0239.html").section == "prensa"
    assert _page("https://www.carvajal.com/").section == "home"


def test_to_gold_markdown_pagina_vacia_termina_en_un_salto() -> None:
    """Una página sin cuerpo no deja una línea en blanco al final del archivo."""

    page = CapturedPage(
        url="https://www.carvajal.com/sostenibilidad/",
        html="<html></html>",
        markdown="",
    )

    markdown = to_gold_markdown(page, "Sostenibilidad")

    assert markdown.endswith("---\n")
    assert not markdown.endswith("\n\n")


def test_write_gold_pagina_vacia_no_deja_linea_en_blanco(tmp_path: Path) -> None:
    """El archivo Gold escrito para una página vacía acaba en un único salto."""

    page = CapturedPage(url="https://www.carvajal.com/sostenibilidad/", html="", markdown="")

    target = write_gold(page, title="Sostenibilidad", gold_dir=tmp_path)

    assert target.read_text(encoding="utf-8").endswith("---\n")


def test_write_gold_reemplaza_el_archivo_en_vez_de_truncarlo(tmp_path: Path) -> None:
    """Reescribir un documento publica la versión nueva completa, nunca a medias."""

    page = _page("https://www.carvajal.com/historia/")
    target = write_gold(page, title="Historia", gold_dir=tmp_path)
    anterior = target.stat().st_ino

    write_gold(page, title="Historia renovada", gold_dir=tmp_path)

    # El ``rename`` sustituye el archivo: quien lea durante la escritura ve la
    # versión anterior completa, no la cabecera nueva con el cuerpo truncado.
    assert target.stat().st_ino != anterior
    assert "Historia renovada" in target.read_text(encoding="utf-8")
    assert _temporales(tmp_path) == []


def test_una_escritura_de_gold_interrumpida_conserva_la_version_anterior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si escribir Gold falla a medias, el documento publicado no se degrada."""

    page = _page("https://www.carvajal.com/historia/")
    target = write_gold(page, title="Historia", gold_dir=tmp_path)
    completo = target.read_text(encoding="utf-8")
    escribir = Path.write_text

    def cortar(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        escribir(self, data[:20], encoding, errors, newline)
        raise OSError("disco lleno")

    monkeypatch.setattr(Path, "write_text", cortar)

    with pytest.raises(OSError):
        write_gold(page, title="Historia renovada", gold_dir=tmp_path)

    assert target.read_text(encoding="utf-8") == completo
    assert _temporales(tmp_path) == []


def test_merge_index_conserva_identidad_y_huellas(tmp_path: Path) -> None:
    """Una entrada nueva sin identidad no borra la que ya conocía el registro."""

    # Arrange
    index_file = tmp_path / "index.jsonl"
    merge_index(
        index_file,
        [
            {
                "slug": "informe",
                "source_key": "file:corpus/informe.pdf",
                "raw_hash": "bytes",
                "layer": "core",
                "updated_at": "2026-11-16T12:00:00+00:00",
            }
        ],
    )

    # Act
    merge_index(index_file, [{"slug": "informe", "title": "Informe actualizado"}])

    # Assert
    assert read_index(index_file)["informe"] == {
        "slug": "informe",
        "source_key": "file:corpus/informe.pdf",
        "raw_hash": "bytes",
        "layer": "core",
        "updated_at": "2026-11-16T12:00:00+00:00",
        "title": "Informe actualizado",
    }


def test_merge_index_deja_mandar_a_la_entrada_nueva(tmp_path: Path) -> None:
    """Los campos que la entrada nueva declara sustituyen a los anteriores."""

    # Arrange
    index_file = tmp_path / "index.jsonl"
    merge_index(index_file, [{"slug": "informe", "raw_hash": "viejo", "layer": "core"}])

    # Act
    merge_index(index_file, [{"slug": "informe", "raw_hash": "nuevo", "layer": "extended"}])

    # Assert
    entrada = read_index(index_file)["informe"]
    assert entrada["raw_hash"] == "nuevo"
    assert entrada["layer"] == "extended"


def test_read_index_ignora_lineas_corruptas_y_entradas_sin_slug(tmp_path: Path) -> None:
    # Arrange
    index_file = tmp_path / "index.jsonl"
    index_file.write_text(
        '{"slug": "vigente", "hash": "a"}\n{roto}\n{"sin": "slug"}\n', encoding="utf-8"
    )

    # Act
    entradas = read_index(index_file)

    # Assert
    assert list(entradas) == ["vigente"]


def test_read_index_sin_archivo_devuelve_vacio(tmp_path: Path) -> None:
    # Arrange / Act
    entradas = read_index(tmp_path / "no-existe.jsonl")

    # Assert
    assert entradas == {}


def test_merge_index_escribe_ordenado_por_slug(tmp_path: Path) -> None:
    # Arrange
    index_file = tmp_path / "index.jsonl"

    # Act
    merge_index(index_file, [{"slug": "zeta"}, {"slug": "alfa"}])

    # Assert
    lineas = index_file.read_text(encoding="utf-8").splitlines()
    slugs = [json.loads(linea)["slug"] for linea in lineas]
    assert slugs == ["alfa", "zeta"]
