"""Pruebas de la carga del catálogo curado de empaques (spec 036, T004-T006)."""

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from carvajal.catalog import CatalogError, load_catalog
from carvajal.catalog.models import Reference

FIXTURE = Path(__file__).parent / "fixtures" / "catalog"


@pytest.fixture(autouse=True)
def _sin_cache() -> None:
    """Cada prueba carga desde cero: ``load_catalog`` cachea por proceso."""

    load_catalog.cache_clear()


def _copia(tmp_path: Path) -> Path:
    destino = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, destino)
    return destino


def _reemplaza(path: Path, viejo: str, nuevo: str) -> None:
    texto = path.read_text(encoding="utf-8")
    assert viejo in texto
    path.write_text(texto.replace(viejo, nuevo, 1), encoding="utf-8")


def test_carga_el_catalogo_valido() -> None:
    """Las tres tablas se cargan, con vacíos como ``None`` y materiales por clave."""

    catalogo = load_catalog(str(FIXTURE))

    assert len(catalogo.references) == 6
    assert set(catalogo.materials) == {"PP", "EPS", "PS", "PET", "ALUMINIO", "PAPEL"}
    assert len(catalogo.distributors) == 3
    vaso = catalogo.references[0]
    assert vaso.price == Decimal("1450.00")
    assert vaso.uses == ("bebida_caliente", "para_llevar")
    sin_precio = next(ref for ref in catalogo.references if ref.country == "CO")
    assert sin_precio.price is None
    assert sin_precio.currency is None


def test_fila_invalida_reporta_archivo_y_linea(tmp_path: Path) -> None:
    """Un precio negativo detiene la carga y nombra el archivo y la línea."""

    base = _copia(tmp_path)
    _reemplaza(base / "referencias.csv", "1450.00", "-1")

    with pytest.raises(CatalogError, match=r"referencias\.csv:2: price"):
        load_catalog(str(base))


def test_archivo_ausente(tmp_path: Path) -> None:
    """Sin uno de los tres CSV no hay catálogo."""

    base = _copia(tmp_path)
    (base / "distribuidores.csv").unlink()

    with pytest.raises(CatalogError, match="no existe"):
        load_catalog(str(base))


def test_sku_repetido_en_el_mismo_pais(tmp_path: Path) -> None:
    """El par SKU-país identifica la referencia."""

    base = _copia(tmp_path)
    _reemplaza(base / "referencias.csv", "FX-VASO-PET-16", "FX-VASO-PAPEL-12")

    with pytest.raises(CatalogError, match=r"referencias\.csv:3: SKU repetido .*línea 2"):
        load_catalog(str(base))


def test_material_repetido(tmp_path: Path) -> None:
    """Cada material tiene una sola regla."""

    base = _copia(tmp_path)
    _reemplaza(base / "materiales.csv", "EPS,-20", "PP,-20")

    with pytest.raises(CatalogError, match=r"materiales\.csv:3: material repetido .*línea 2"):
        load_catalog(str(base))


def _fila(**cambios: object) -> dict[str, object]:
    fila: dict[str, object] = {
        "sku": "X",
        "name": "Vaso",
        "family": "vaso",
        "use": "bebida_fria",
        "material": "pp",
        "units_per_box": "1000",
        "country": "MX",
        "source_url": "https://ejemplo.test/x",
        "captured_at": "2026-10-23",
    }
    fila.update(cambios)
    return fila


def test_precio_exige_moneda_e_impuestos() -> None:
    """Un precio sin moneda o sin nota de impuestos no es una referencia válida."""

    with pytest.raises(ValueError, match="moneda"):
        Reference.model_validate(_fila(price="100"))


def test_unidades_por_caja_coherentes() -> None:
    """Paquetes por unidades por paquete debe dar las unidades por caja."""

    with pytest.raises(ValueError, match="no coincide"):
        Reference.model_validate(_fila(units_per_pack="50", packs_per_box="10"))


def test_material_se_normaliza_y_url_debe_ser_http() -> None:
    """El material se guarda en mayúsculas; la fuente debe ser una URL."""

    assert Reference.model_validate(_fila()).material == "PP"
    with pytest.raises(ValueError, match="source_url"):
        Reference.model_validate(_fila(source_url="ficha.pdf"))
