"""Pruebas del extractor de fichas de México y de la orden ``catalog-build`` (T007)."""

import csv
import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from carvajal.catalog.build import (
    build_mx_references,
    parse_mx_product,
    update_manifest,
    write_references,
)
from carvajal.catalog.loader import load_catalog
from carvajal.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "catalog"
CAPTURA = date(2026, 10, 23)


def _ficha(
    nombre: str = "Vasos de plástico 16oz PET - Caja con 800 pzs",
    corta: str = (
        "Vasos de plástico 16oz PET 98.5mm transparente Converpro caja 16x50 - 800 piezas. "
        "Marca: CONVERPRO Materiales: PET Temperatura de uso recomendada: Desde -20°C / 40°C "
        "Color: Transparente Cantidad por caja: 800 Unidades"
    ),
    slug: str = "vaso-plastico-pet-16_oz",
    ofertas: list[dict[str, Any]] | None = None,
) -> str:
    url = f"https://mexico.carvajalempaques.com/producto/{slug}/"
    ld = {
        "@graph": [
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"item": {"name": "Home"}},
                    {"item": {"name": "Usos y aplicaciones"}},
                    {"item": {"name": "Fiestas y Eventos"}},
                    {"item": {"name": nombre}},
                ],
            },
            {
                "@type": "Product",
                "name": nombre,
                "url": url,
                "sku": "123",
                "offers": ofertas
                if ofertas is not None
                else [
                    {
                        "priceSpecification": [
                            {
                                "price": "1519.00",
                                "priceCurrency": "MXN",
                                "valueAddedTaxIncluded": False,
                                "validThrough": "2027-12-31",
                            }
                        ]
                    }
                ],
            },
        ]
    }
    return (
        f'<html><head><link rel="canonical" href="{url}" />'
        f'<script type="application/ld+json">{json.dumps(ld)}</script></head><body>'
        f"<h1>{nombre} (IVA Incluido)</h1>"
        f'<div class="woocommerce-product-details__short-description"><p>{corta}</p></div>'
        "<form>quantity</form></body></html>"
    )


def test_extrae_la_ficha_completa() -> None:
    """SKU, precio, caja, material, temperatura y uso salen de la ficha."""

    referencia = parse_mx_product(_ficha(), CAPTURA)

    assert referencia is not None
    assert referencia.sku == "123"
    assert referencia.family == "vaso"
    assert referencia.material == "PET"
    assert referencia.capacity_oz == 16
    assert (referencia.packs_per_box, referencia.units_per_pack) == (16, 50)
    assert referencia.units_per_box == 800
    assert (referencia.temp_min_c, referencia.temp_max_c) == (-20, 40)
    assert referencia.price == Decimal("1519.00")
    assert referencia.uses == ("fiestas_y_eventos", "bebida_fria")


def test_el_iva_visible_prevalece_sobre_el_json_ld() -> None:
    """WooCommerce marca ``valueAddedTaxIncluded: false`` por defecto; manda el texto."""

    referencia = parse_mx_product(_ficha(), CAPTURA)

    assert referencia is not None
    assert referencia.tax_included is True


def test_los_combos_no_son_referencias() -> None:
    """Un combo no se vende por caja: no entra al catálogo."""

    assert parse_mx_product(_ficha(nombre="Combo Platos Fiestas"), CAPTURA) is None


def test_contradicciones_de_la_ficha_se_anotan_y_no_se_usan() -> None:
    """Título contra descripción y temperatura contra uso quedan como avisos."""

    avisos: list[str] = []
    ficha = _ficha(
        nombre="Vasos de papel 12oz 90mm - Caja con 400 pzs",
        corta=(
            "Vasos de papel 12oz 90mm bebida caliente caja 20x50 - 1000 piezas. "
            "Marca: BIOFORM Materiales: Papel Temperatura de uso recomendada: Desde -30 C/ 40 C "
            "Color: Impreso Cantidad por caja: 1000 Unidades"
        ),
    )

    referencia = parse_mx_product(ficha, CAPTURA, avisos)

    assert referencia is not None
    assert referencia.units_per_box == 1000
    assert referencia.temp_min_c is None
    assert "bebida_caliente" in referencia.uses
    assert len(avisos) == 2


def test_las_tapas_no_declaran_capacidad() -> None:
    """«12oz a 24oz» en una tapa es compatibilidad, no capacidad."""

    ficha = _ficha(
        nombre="Tapas de plástico PET 12oz a 24oz - Caja con 800 pzs",
        corta="Marca: CONVERPRO Materiales: PET Cantidad por caja: 800 Unidades",
    )

    referencia = parse_mx_product(ficha, CAPTURA)

    assert referencia is not None
    assert referencia.family == "tapa"
    assert referencia.capacity_oz is None


def test_regenerar_conserva_las_filas_de_otros_paises(tmp_path: Path) -> None:
    """Las filas curadas a mano (Colombia) sobreviven a la regeneración."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None

    total = write_references(base, [referencia], allow_missing=True)

    with (base / "referencias.csv").open(encoding="utf-8", newline="") as handle:
        filas = list(csv.DictReader(handle))
    assert total == 3
    assert [fila["country"] for fila in filas] == ["MX", "CO", "CO"]
    assert filas[0]["tax_included"] == "true"
    load_catalog.cache_clear()
    assert len(load_catalog(str(base)).references) == 3


def test_regenerar_respeta_el_orden_de_columnas_del_contrato(tmp_path: Path) -> None:
    """El CSV regenerado conserva el orden publicado en el plan de la spec 036."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None

    write_references(base, [referencia], allow_missing=True)

    with (base / "referencias.csv").open(encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == [
            "sku",
            "name",
            "family",
            "use",
            "capacity_oz",
            "diameter_mm",
            "material",
            "brand",
            "units_per_pack",
            "packs_per_box",
            "units_per_box",
            "temp_min_c",
            "temp_max_c",
            "price",
            "currency",
            "tax_included",
            "price_valid_until",
            "country",
            "source_url",
            "captured_at",
        ]


def test_baja_autorizada_queda_en_las_notas(tmp_path: Path) -> None:
    """Con ``allow_missing`` los SKU retirados se informan antes de reescribir."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None
    notas: list[str] = []

    write_references(base, [referencia], allow_missing=True, notes=notas)

    assert len(notas) == 1
    assert "baja autorizada" in notas[0]
    for sku in ("FX-VASO-PAPEL-12", "FX-VASO-PET-16", "FX-CONT-EPS-7X7", "FX-ENV-PP-4"):
        assert sku in notas[0]  # listado completo, sin truncar a cinco


def test_manifest_falla_no_toca_el_csv(tmp_path: Path) -> None:
    """Si el manifiesto no se puede actualizar, el CSV anterior queda intacto."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    archivo = base / "referencias.csv"
    (base / "manifest.json").write_text("{roto", encoding="utf-8")
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None
    original = archivo.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        write_references(
            base,
            [referencia],
            allow_missing=True,
            manifest=("referencias_mx", {"rows": 1}),
        )

    assert archivo.read_text(encoding="utf-8") == original
    assert not list(base.glob(".*.tmp"))


def test_material_no_se_confunde_por_prefijo() -> None:
    """«PSA» no es PS: las claves cortas exigen coincidencia exacta."""

    ficha = _ficha(
        nombre="Vasos de plástico 16oz - Caja con 800 pzs",
        corta="Marca: CONVERPRO Materiales: PSA Cantidad por caja: 800 Unidades",
    )

    with pytest.raises(ValueError, match="material no reconocido"):
        parse_mx_product(ficha, CAPTURA)


def test_la_oferta_vencida_no_es_el_precio_de_referencia() -> None:
    """Una promoción caducada se descarta frente a la oferta regular vigente."""

    ofertas = [
        {
            "priceSpecification": [
                {"price": "999.00", "priceCurrency": "MXN", "validThrough": "2026-01-01"}
            ]
        },
        {
            "priceSpecification": [
                {"price": "1519.00", "priceCurrency": "MXN", "validThrough": "2027-12-31"}
            ]
        },
    ]

    referencia = parse_mx_product(_ficha(ofertas=ofertas), CAPTURA)

    assert referencia is not None
    assert referencia.price == Decimal("1519.00")


def test_si_falla_el_reemplazo_del_manifest_restaura_el_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CSV y manifiesto se confirman juntos: un fallo del segundo deshace el primero."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    archivo = base / "referencias.csv"
    manifiesto = base / "manifest.json"
    manifiesto.write_text('{"spec": "036-asistente-empaques"}\n', encoding="utf-8")
    original_csv = archivo.read_text(encoding="utf-8")
    original_manifest = manifiesto.read_text(encoding="utf-8")
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None
    real_replace = Path.replace

    def replace(self: Path, target: Path) -> Path:
        if self.name == ".manifest.json.tmp":
            raise OSError("sin espacio")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", replace)

    with pytest.raises(OSError):
        write_references(
            base,
            [referencia],
            allow_missing=True,
            manifest=("referencias_mx", {"rows": 1}),
        )

    assert archivo.read_text(encoding="utf-8") == original_csv
    assert manifiesto.read_text(encoding="utf-8") == original_manifest
    assert not list(base.glob(".*.tmp"))
    assert not list(base.glob(".*.bak"))


def test_si_falla_el_respaldo_no_quedan_temporales(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un fallo al crear el respaldo no deja ``.tmp`` ni ``.bak`` huérfanos."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    archivo = base / "referencias.csv"
    original = archivo.read_text(encoding="utf-8")
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None
    real_write_bytes = Path.write_bytes

    def write_bytes(self: Path, data: bytes) -> int:
        if self.name.endswith(".bak"):
            raise OSError("disco lleno")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)

    with pytest.raises(OSError, match="disco lleno"):
        write_references(
            base,
            [referencia],
            allow_missing=True,
            manifest=("referencias_mx", {"rows": 1}),
        )

    assert archivo.read_text(encoding="utf-8") == original
    assert not list(base.glob(".*.tmp"))
    assert not list(base.glob(".*.bak"))


def test_si_falla_la_restauracion_se_conserva_el_error_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El error de restauración se anota, pero se relanza la causa original."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    (base / "manifest.json").write_text('{"spec": "036-asistente-empaques"}\n', encoding="utf-8")
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None
    real_replace = Path.replace

    def replace(self: Path, target: Path) -> Path:
        if self.name == ".manifest.json.tmp":
            raise OSError("sin espacio")
        if self.name == ".referencias.csv.bak":
            raise OSError("permiso denegado")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", replace)

    with pytest.raises(OSError, match="sin espacio") as excinfo:
        write_references(
            base,
            [referencia],
            allow_missing=True,
            manifest=("referencias_mx", {"rows": 1}),
        )

    assert any("no se pudo restaurar referencias.csv" in note for note in excinfo.value.__notes__)
    assert not list(base.glob(".*.tmp"))
    assert not list(base.glob(".*.bak"))


def test_si_falla_la_validacion_final_restaura_ambos_archivos(tmp_path: Path) -> None:
    """Un ``check`` posterior deshace los dos reemplazos, no solo uno."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    archivo = base / "referencias.csv"
    manifiesto = base / "manifest.json"
    manifiesto.write_text('{"spec": "036-asistente-empaques"}\n', encoding="utf-8")
    original_csv = archivo.read_text(encoding="utf-8")
    original_manifest = manifiesto.read_text(encoding="utf-8")
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None

    def check() -> None:
        raise ValueError("el catálogo no valida")

    with pytest.raises(ValueError, match="no valida"):
        write_references(
            base,
            [referencia],
            allow_missing=True,
            manifest=("referencias_mx", {"rows": 1}),
            check=check,
        )

    assert archivo.read_text(encoding="utf-8") == original_csv
    assert manifiesto.read_text(encoding="utf-8") == original_manifest
    assert not list(base.glob(".*.tmp"))
    assert not list(base.glob(".*.bak"))


def test_regenerar_aborta_si_falta_una_referencia(tmp_path: Path) -> None:
    """Un corpus parcial no debe borrar en silencio referencias ya presentes."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    archivo = base / "referencias.csv"
    original = archivo.read_text(encoding="utf-8")
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None

    with pytest.raises(ValueError, match=r"no incluye 4 referencia"):
        write_references(base, [referencia])

    assert archivo.read_text(encoding="utf-8") == original


def test_build_ignora_paginas_ajenas(tmp_path: Path) -> None:
    """Solo las fichas de producto de la tienda de México se procesan."""

    (tmp_path / "ficha.html").write_text(_ficha(), encoding="utf-8")
    (tmp_path / "otra.html").write_text("<html><body>Inicio</body></html>", encoding="utf-8")

    referencias, notas = build_mx_references(sorted(tmp_path.glob("*.html")), CAPTURA)

    assert [ref.sku for ref in referencias] == ["123"]
    assert notas == []


def test_la_url_se_busca_en_toda_la_pagina(tmp_path: Path) -> None:
    """Una cabecera larga no hace pasar la ficha por «página ajena»."""

    (tmp_path / "larga.html").write_text("<html>" + "x" * 9000 + _ficha(), encoding="utf-8")

    referencias, notas = build_mx_references(sorted(tmp_path.glob("*.html")), CAPTURA)

    assert [ref.sku for ref in referencias] == ["123"]
    assert notas == []


def test_sku_repetido_en_el_corpus_se_anota(tmp_path: Path) -> None:
    """Dos fichas con el mismo SKU no se descartan en silencio."""

    (tmp_path / "a.html").write_text(_ficha(), encoding="utf-8")
    (tmp_path / "b.html").write_text(_ficha(slug="vaso-repetido"), encoding="utf-8")

    referencias, notas = build_mx_references(sorted(tmp_path.glob("*.html")), CAPTURA)

    assert [ref.sku for ref in referencias] == ["123"]
    assert len(notas) == 1
    assert "SKU 123 repetido" in notas[0]
    assert "ya estaba en" in notas[0]


def test_ficha_sin_nombre_o_url_se_anota_y_el_lote_sigue(tmp_path: Path) -> None:
    """Un ``Product`` incompleto se anota con su URL y no aborta la regeneración."""

    sin_nombre = _ficha(slug="sin-nombre").replace(
        '"@type": "Product", "name"', '"@type": "Product", "titulo"'
    )
    sin_url = _ficha(slug="sin-url").replace('"url": "', '"enlace": "', 1)
    (tmp_path / "a.html").write_text(_ficha(), encoding="utf-8")
    (tmp_path / "b.html").write_text(sin_nombre, encoding="utf-8")
    (tmp_path / "c.html").write_text(sin_url, encoding="utf-8")

    referencias, notas = build_mx_references(sorted(tmp_path.glob("*.html")), CAPTURA)

    assert [ref.sku for ref in referencias] == ["123"]
    assert len(notas) == 2
    assert any("sin-nombre" in nota and "nombre" in nota for nota in notas)
    assert any("sin-nombre" in nota and "nombre" in nota for nota in notas)
    assert any("no publica URL" in nota for nota in notas)


def test_orden_catalog_build(tmp_path: Path) -> None:
    """La CLI regenera México, actualiza el manifiesto y valida el catálogo."""

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "ficha.html").write_text(_ficha(), encoding="utf-8")
    salida = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, salida)
    load_catalog.cache_clear()

    codigo = main(
        [
            "catalog-build",
            "--corpus",
            str(corpus),
            "--captured-at",
            "2026-10-23",
            "--out",
            str(salida),
            "--allow-missing",
        ]
    )

    assert codigo == 0
    manifiesto = json.loads((salida / "manifest.json").read_text(encoding="utf-8"))
    assert manifiesto["sources"]["referencias_mx"]["rows"] == 1
    assert manifiesto["sources"]["referencias_mx"]["captured_at"] == "2026-10-23"


def test_catalog_build_no_crea_un_catalogo_vacio_sin_allow_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un ``--out`` equivocado no debe crear un CSV sin las filas curadas."""

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "ficha.html").write_text(_ficha(), encoding="utf-8")
    salida = tmp_path / "ruta-equivocada"

    codigo = main(
        [
            "catalog-build",
            "--corpus",
            str(corpus),
            "--captured-at",
            "2026-10-23",
            "--out",
            str(salida),
        ]
    )

    assert codigo == 1
    assert not salida.exists()
    assert "No existe el directorio" in capsys.readouterr().out


def test_catalog_build_no_crea_directorio_ni_con_allow_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--allow-missing`` tampoco debe inventar un ``--out`` inexistente."""

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "ficha.html").write_text(_ficha(), encoding="utf-8")
    salida = tmp_path / "ruta-equivocada"

    codigo = main(
        [
            "catalog-build",
            "--corpus",
            str(corpus),
            "--captured-at",
            "2026-10-23",
            "--out",
            str(salida),
            "--allow-missing",
        ]
    )

    assert codigo == 1
    assert not salida.exists()
    assert "No existe el directorio" in capsys.readouterr().out


def test_orden_catalog_build_aborta_si_falta_una_referencia(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """La CLI informa la baja no autorizada y no toca el CSV."""

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "ficha.html").write_text(_ficha(), encoding="utf-8")
    salida = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, salida)
    archivo = salida / "referencias.csv"
    original = archivo.read_text(encoding="utf-8")
    load_catalog.cache_clear()

    codigo = main(
        [
            "catalog-build",
            "--corpus",
            str(corpus),
            "--captured-at",
            "2026-10-23",
            "--out",
            str(salida),
        ]
    )

    assert codigo == 1
    mensaje = capsys.readouterr().out
    assert "No se reescribió el catálogo" in mensaje
    assert "allow-missing" in mensaje
    assert archivo.read_text(encoding="utf-8") == original


def test_ficha_sin_precio_se_anota_y_el_lote_sigue(tmp_path: Path) -> None:
    """Una ficha sin oferta no tumba la regeneración: se anota con su URL."""

    sin_oferta = _ficha(slug="sin-oferta").replace('"offers"', '"sin_ofertas"')
    (tmp_path / "a.html").write_text(_ficha(), encoding="utf-8")
    (tmp_path / "b.html").write_text(sin_oferta, encoding="utf-8")

    referencias, notas = build_mx_references(sorted(tmp_path.glob("*.html")), CAPTURA)

    assert [ref.sku for ref in referencias] == ["123"]
    assert len(notas) == 1
    assert "sin-oferta" in notas[0]
    assert "precio" in notas[0]


def test_temperatura_en_otro_formato_se_anota() -> None:
    """Si la ficha declara temperatura pero no se reconoce, queda un aviso."""

    avisos: list[str] = []
    ficha = _ficha(
        corta=(
            "Marca: CONVERPRO Materiales: PET Temperatura de uso recomendada: -20°C a 40°C "
            "Color: Transparente Cantidad por caja: 800 Unidades"
        )
    )

    referencia = parse_mx_product(ficha, CAPTURA, avisos)

    assert referencia is not None
    assert referencia.temp_min_c is None
    assert avisos == [
        "https://mexico.carvajalempaques.com/producto/vaso-plastico-pet-16_oz/: "
        "temperatura en formato no reconocido; se omite"
    ]


def test_fila_curada_invalida_no_toca_el_archivo(tmp_path: Path) -> None:
    """Si una fila de Colombia es inválida, el CSV original queda intacto."""

    base = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, base)
    archivo = base / "referencias.csv"
    roto = archivo.read_text(encoding="utf-8").replace("FX-VASO-PS-12,", ",", 1)
    archivo.write_text(roto, encoding="utf-8")
    referencia = parse_mx_product(_ficha(), CAPTURA)
    assert referencia is not None

    with pytest.raises(ValueError, match=r"referencias\.csv:6"):
        write_references(base, [referencia])

    assert archivo.read_text(encoding="utf-8") == roto
    assert not list(base.glob(".*.tmp"))


def test_ld_json_malformado_se_anota_con_su_url(tmp_path: Path) -> None:
    """Un bloque ld+json roto no aborta el lote: se anota con la URL de la ficha."""

    (tmp_path / "buena.html").write_text(_ficha(), encoding="utf-8")
    rota = _ficha(slug="ld-roto").replace('{"@graph"', "{roto", 1)
    (tmp_path / "rota.html").write_text(rota, encoding="utf-8")

    referencias, notas = build_mx_references(sorted(tmp_path.glob("*.html")), CAPTURA)

    assert [ref.sku for ref in referencias] == ["123"]
    assert len(notas) == 1
    assert "ld-roto" in notas[0]
    assert "ld+json" in notas[0]


def test_manifest_se_reemplaza_sin_dejar_temporales(tmp_path: Path) -> None:
    """El manifiesto se actualiza de forma atómica y queda JSON válido."""

    (tmp_path / "manifest.json").write_text('{"spec": "036-asistente-empaques"}', encoding="utf-8")

    update_manifest(tmp_path, "referencias_mx", {"rows": 2})

    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert data["sources"]["referencias_mx"]["rows"] == 2
    assert not list(tmp_path.glob(".*.tmp"))
