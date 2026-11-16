"""Integridad del catálogo curado versionado en ``data/catalog/empaques`` (T012)."""

import json
from pathlib import Path

import pytest

from carvajal.catalog.loader import DEFAULT_DIR, load_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOGO = ROOT / DEFAULT_DIR


@pytest.fixture(scope="module")
def manifiesto() -> dict:
    return json.loads((CATALOGO / "manifest.json").read_text(encoding="utf-8"))


def test_el_catalogo_versionado_carga() -> None:
    """Los tres CSV validan y hay datos en cada uno."""

    load_catalog.cache_clear()
    catalogo = load_catalog(str(CATALOGO))

    assert len(catalogo.references) >= 50
    assert set(catalogo.materials) == {"PP", "EPS", "PS", "PET", "ALUMINIO"}
    assert len(catalogo.distributors) >= 25


def test_todo_material_tiene_regla_o_esta_declarado(manifiesto: dict) -> None:
    """Un material sin regla en la guía debe figurar en el manifiesto."""

    load_catalog.cache_clear()
    catalogo = load_catalog(str(CATALOGO))
    sin_regla = set(manifiesto["materials_without_rule"])

    for referencia in catalogo.references:
        assert referencia.material in catalogo.materials or referencia.material in sin_regla, (
            referencia.sku
        )


def test_precios_de_mexico_completos() -> None:
    """Toda referencia de México tiene precio en MXN con IVA y vigencia."""

    load_catalog.cache_clear()
    for referencia in load_catalog(str(CATALOGO)).references:
        if referencia.country == "MX":
            assert referencia.price is not None, referencia.sku
            assert referencia.currency == "MXN"
            assert referencia.tax_included is True
            assert referencia.source_url.startswith("https://mexico.carvajalempaques.com/")


def test_distribuidores_sin_datos_personales() -> None:
    """El directorio guarda empresa, ciudad, dirección y teléfono; sin correos ni contactos."""

    texto = (CATALOGO / "distribuidores.csv").read_text(encoding="utf-8")

    assert "@" not in texto
    assert "contacto" not in texto.lower()


def test_el_manifiesto_documenta_cada_fuente(manifiesto: dict) -> None:
    """Cada archivo tiene origen y fecha de captura."""

    for seccion in ("referencias_mx", "materiales", "distribuidores"):
        assert manifiesto["sources"][seccion]["captured_at"] == "2026-10-23"
        assert manifiesto["sources"][seccion]["origin"]
