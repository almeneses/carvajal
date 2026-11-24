"""Capa institucional curada de la spec 045 (T005, T022; research R12).

``data/institucional/empresas.json`` no añade hechos: cada fila cita literalmente una página
del núcleo (``data/gold/<slug>.md``) y su URL es la de ese slug en ``index.jsonl``. Estas
pruebas protegen esa garantía (enmienda A, principio II).
"""

import json
from pathlib import Path
from typing import Any

import pytest

GOLD = Path("data") / "gold"
EMPRESAS = Path("data") / "institucional" / "empresas.json"
# Unidades de la lista «Nuestros Sectores» de quienes_somos.md (R12).
SECTORES = {
    "Carvajal Empaques",
    "Carvajal Pulpa y Papel",
    "Carvajal Educación",
    "Carvajal Soluciones de Comunicación",
    "Carvajal Tecnología y Servicios",
    "Américas BPS",
    "Carvajal Servicios",
    "Inmobiliario",
    "Carvajal Espacios",
}
MARCAS = {"Bioform", "Norma", "Mepal", "LiquidezYa"}


def _urls() -> dict[str, str]:
    lines = (GOLD / "index.jsonl").read_text(encoding="utf-8").splitlines()
    return {row["slug"]: row["url"] for row in (json.loads(line) for line in lines if line)}


@pytest.fixture(scope="module")
def empresas() -> dict[str, Any]:
    return json.loads(EMPRESAS.read_text(encoding="utf-8"))


def test_cada_cita_y_alias_aparece_literal_en_su_pagina_del_nucleo(
    empresas: dict[str, Any],
) -> None:
    for item in empresas["items"]:
        # Arrange
        text = (GOLD / f"{item['slug']}.md").read_text(encoding="utf-8")

        # Act
        citas = [item["cita"], *(alias["cita"] for alias in item["alias"])]

        # Assert
        assert all(cita in text for cita in citas), item["nombre"]


def test_la_url_es_la_del_slug_en_el_indice(empresas: dict[str, Any]) -> None:
    urls = _urls()

    assert all(item["url"] == urls[item["slug"]] for item in empresas["items"])


def test_cada_cita_nombra_su_empresa_o_marca(empresas: dict[str, Any]) -> None:
    """La compuerta exige que el nombre devuelto aparezca en el ``excerpt`` (la cita)."""

    for item in empresas["items"]:
        assert item["nombre"].casefold() in item["cita"].casefold(), item["nombre"]
        for alias in item["alias"]:
            assert alias["nombre"].casefold() in alias["cita"].casefold(), alias["nombre"]


def test_incluye_las_unidades_de_nuestros_sectores_y_las_marcas(
    empresas: dict[str, Any],
) -> None:
    unidades = {item["nombre"] for item in empresas["items"] if item["tipo"] == "unidad"}
    marcas = {item["nombre"] for item in empresas["items"] if item["tipo"] == "marca"}
    nombres = [item["nombre"] for item in empresas["items"]]

    assert unidades >= SECTORES
    assert marcas == MARCAS
    assert len(nombres) == len(set(nombres))
    assert {item["tipo"] for item in empresas["items"]} == {"unidad", "marca"}


def test_propal_es_alias_citado_de_pulpa_y_papel(empresas: dict[str, Any]) -> None:
    [pulpa] = [item for item in empresas["items"] if item["nombre"] == "Carvajal Pulpa y Papel"]

    assert [alias["nombre"] for alias in pulpa["alias"]] == ["PROPAL"]
    assert pulpa["slug"] == "pulpa_y_papel"


def test_nombres_que_el_nucleo_no_menciona_no_estan(empresas: dict[str, Any]) -> None:
    """Contraejemplo del quickstart: «Alpina» no está en Gold y no se lista."""

    texto = json.dumps(empresas, ensure_ascii=False).casefold()

    assert "alpina" not in texto
    assert "alpina" not in (GOLD / "quienes_somos.md").read_text(encoding="utf-8").casefold()


# --- Carga (T022) ----------------------------------------------------------------------------


def test_carga_empresas_e_hitos_del_repositorio() -> None:
    # Arrange
    from carvajal.institutional import AVAILABLE, load_institutional

    # Act
    data = load_institutional()

    # Assert
    assert data.companies_status == AVAILABLE
    assert data.history_status == AVAILABLE
    assert {company.name for company in data.companies} >= SECTORES
    assert data.history_url == _urls()["historia"]
    # 68 encabezados ``## AAAA``; 1962 no tiene texto en la captura actual.
    assert len(data.milestones) == 67
    assert 1962 not in {milestone.year for milestone in data.milestones}
    assert data.milestones[0].year == 1904
    assert "EL LEGADO" not in data.milestones[-1].text
    assert "Capitulo" not in data.milestones[-1].text
    assert load_institutional() is data  # se carga una sola vez


def test_el_hito_es_texto_literal_de_historia_sin_subtitulos() -> None:
    # Arrange
    from carvajal.institutional import load_institutional

    historia = (GOLD / "historia.md").read_text(encoding="utf-8")

    # Act
    hitos = {milestone.year: milestone.text for milestone in load_institutional().milestones}

    # Assert
    assert "1960" in {str(year) for year in hitos}
    assert all(line in historia for text in hitos.values() for line in text.splitlines())
    assert not any(line.startswith("#") for text in hitos.values() for line in text.splitlines())


def test_archivos_ausentes_dan_no_disponible_sin_excepcion(tmp_path: Path) -> None:
    # Arrange
    from carvajal.institutional import UNAVAILABLE, load_institutional

    # Act
    data = load_institutional(tmp_path / "empresas.json", tmp_path / "gold")

    # Assert
    assert data.companies_status == UNAVAILABLE
    assert data.history_status == UNAVAILABLE
    assert data.companies == ()
    assert data.milestones == ()


def test_json_invalido_da_no_disponible(tmp_path: Path) -> None:
    # Arrange
    from carvajal.institutional import UNAVAILABLE, load_institutional

    roto = tmp_path / "empresas.json"
    roto.write_text('{"items": [{"nombre": "X", "tipo": "otro"}]}', encoding="utf-8")

    # Act
    data = load_institutional(roto, GOLD)

    # Assert
    assert data.companies_status == UNAVAILABLE
    assert data.milestones  # la historia no depende del JSON
