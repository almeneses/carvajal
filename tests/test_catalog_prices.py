"""Pruebas de las ofertas por fuente y la regeneración de precios (spec 039).

Cubren T003-T008 (modelo y carga), T018-T021 (normalización, idempotencia y diferencias),
T024-T027 (cotización con IVA, fuente y avisos), T029 (no conversión) y T035-T036
(manifiesto y escritura atómica).
"""

import csv
import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from carvajal.agent.commercial import STALE_DAYS, quote_offer
from carvajal.agent.commercial_flow import commercial_sources, run_commercial
from carvajal.catalog.loader import PRICES, CatalogError, load_catalog
from carvajal.catalog.models import Reference, ReferencePrice
from carvajal.catalog.prices import RawPrice, build_prices, normalize, write_prices

FIXTURE = Path(__file__).parent / "fixtures" / "catalog"
HOY = date(2026, 11, 18)
CAMPOS = (
    "sku",
    "country",
    "amount",
    "currency",
    "tax_status",
    "source_type",
    "source_name",
    "source_city",
    "valid_until",
    "source_url",
    "captured_at",
)


@pytest.fixture(autouse=True)
def _cache_limpia() -> None:
    load_catalog.cache_clear()


def _catalogo(tmp_path: Path) -> Path:
    destino = tmp_path / "catalogo"
    shutil.copytree(FIXTURE, destino)
    return destino


def _oferta(**extra: object) -> dict[str, str]:
    datos = {
        "sku": "FX-VASO-PS-12",
        "country": "CO",
        "amount": "54000",
        "currency": "COP",
        "tax_status": "incluido",
        "source_type": "tercero",
        "source_name": "Distribuidora Ejemplo Uno",
        "source_city": "Bogotá",
        "valid_until": "",
        "source_url": "https://ejemplo.test/tienda/vaso-12",
        "captured_at": "2026-11-18",
    }
    datos.update({key: str(value) for key, value in extra.items()})
    return datos


def _escribir_precios(directorio: Path, filas: list[dict[str, str]]) -> None:
    with (directorio / PRICES).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CAMPOS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(filas)


def _crudo(**extra: object) -> dict[str, str]:
    datos = {
        "sku": "FX-VASO-PS-12",
        "country": "CO",
        "amount": "900",
        "currency": "COP",
        "basis": "paquete",
        "units_in_basis": "15",
        "tax_status": "incluido",
        "source_type": "tercero",
        "source_name": "Distribuidora Ejemplo Uno",
        "source_city": "Bogotá",
        "source_url": "https://ejemplo.test/tienda/vaso-12",
        "captured_at": "2026-11-18",
        "valid_until": "",
    }
    datos.update({key: str(value) for key, value in extra.items()})
    return datos


def _escribir_crudos(directorio: Path, filas: list[dict[str, str]]) -> None:
    fuentes = directorio / "fuentes"
    fuentes.mkdir(exist_ok=True)
    with (fuentes / "precios_co.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(filas[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(filas)


# --- Modelo -------------------------------------------------------------------------


def test_un_precio_de_colombia_va_en_pesos_colombianos() -> None:
    with pytest.raises(ValueError, match="no se convierte"):
        ReferencePrice.model_validate(_oferta(currency="MXN"))


def test_el_iva_no_indicado_queda_como_desconocido() -> None:
    oferta = ReferencePrice.model_validate(_oferta(tax_status="no_indicado"))

    assert oferta.tax_included is None


def test_una_oferta_sin_fuente_o_fecha_se_rechaza() -> None:
    with pytest.raises(ValueError):
        ReferencePrice.model_validate(_oferta(source_url="sin-url"))
    with pytest.raises(ValueError):
        ReferencePrice.model_validate(_oferta(captured_at=""))


def test_un_precio_negativo_se_rechaza() -> None:
    with pytest.raises(ValueError):
        ReferencePrice.model_validate(_oferta(amount="-1"))


# --- Carga --------------------------------------------------------------------------


def test_sin_precios_csv_el_catalogo_carga_igual(tmp_path: Path) -> None:
    catalogo = load_catalog(str(_catalogo(tmp_path)))

    assert catalogo.prices == {}


def test_carga_las_ofertas_por_referencia_y_pais(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_precios(directorio, [_oferta()])

    catalogo = load_catalog(str(directorio))
    vaso = next(ref for ref in catalogo.references if ref.sku == "FX-VASO-PS-12")

    ofertas = catalogo.offers(vaso)
    assert len(ofertas) == 1
    assert ofertas[0].amount == Decimal("54000")
    assert ofertas[0].source_name == "Distribuidora Ejemplo Uno"


def test_el_precio_de_la_ficha_es_la_oferta_oficial(tmp_path: Path) -> None:
    catalogo = load_catalog(str(_catalogo(tmp_path)))
    vaso = next(ref for ref in catalogo.references if ref.sku == "FX-VASO-PAPEL-12")

    (oferta,) = catalogo.offers(vaso)
    assert oferta.source_type == "oficial"
    assert oferta.currency == "MXN"
    assert oferta.tax_status == "incluido"


def test_una_oferta_oficial_de_precios_csv_reemplaza_la_de_la_ficha(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_precios(
        directorio,
        [
            _oferta(
                sku="FX-VASO-PAPEL-12",
                country="MX",
                currency="MXN",
                amount="1500",
                source_type="oficial",
                source_name="Carvajal Empaques México",
                source_city="",
                source_url="https://ejemplo.test/mx/vaso-papel-12",
            )
        ],
    )

    catalogo = load_catalog(str(directorio))
    vaso = next(ref for ref in catalogo.references if ref.sku == "FX-VASO-PAPEL-12")

    (oferta,) = catalogo.offers(vaso)
    assert oferta.amount == Decimal("1500")


def test_un_precio_huerfano_detiene_la_carga(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_precios(directorio, [_oferta(sku="NO-EXISTE")])

    with pytest.raises(CatalogError, match=r"precios\.csv:2: precio huérfano"):
        load_catalog(str(directorio))


def test_un_precio_de_otro_pais_no_se_cuelga_de_la_referencia(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    # FX-VASO-PAPEL-12 solo existe en México: un precio CO para ese SKU es huérfano.
    _escribir_precios(directorio, [_oferta(sku="FX-VASO-PAPEL-12")])

    with pytest.raises(CatalogError, match="huérfano"):
        load_catalog(str(directorio))


def test_la_misma_fuente_no_publica_dos_precios(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_precios(directorio, [_oferta(), _oferta(amount="60000")])

    with pytest.raises(CatalogError, match=r"precios\.csv:3: precio repetido"):
        load_catalog(str(directorio))


def test_un_manifiesto_roto_detiene_la_carga(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    (directorio / "manifest.json").write_text("{roto", encoding="utf-8")

    with pytest.raises(CatalogError, match=r"manifest\.json: JSON inválido"):
        load_catalog(str(directorio))


def test_el_manifiesto_exige_las_fuentes(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    (directorio / "manifest.json").write_text('{"spec": "039"}', encoding="utf-8")

    with pytest.raises(CatalogError, match="sources"):
        load_catalog(str(directorio))


def test_cada_referencia_de_mexico_con_precio_tiene_una_oferta_oficial() -> None:
    catalogo = load_catalog()
    con_precio = [r for r in catalogo.references if r.country == "MX" and r.price is not None]

    assert con_precio
    for referencia in con_precio:
        oficiales = [o for o in catalogo.offers(referencia) if o.source_type == "oficial"]
        assert len(oficiales) == 1, referencia.sku
        assert oficiales[0].currency == "MXN"


# --- Normalización ------------------------------------------------------------------


def _vaso_co() -> Reference:
    return load_catalog(str(FIXTURE)).references[4]


def test_normaliza_un_precio_por_paquete_a_caja() -> None:
    oferta = normalize(RawPrice.model_validate(_crudo()), _vaso_co())

    assert isinstance(oferta, ReferencePrice)
    assert oferta.amount == Decimal("54000")  # 900 x 60 paquetes de 15


def test_normaliza_un_precio_por_unidad() -> None:
    crudo = RawPrice.model_validate(_crudo(basis="unidad", units_in_basis=1, amount="70"))

    oferta = normalize(crudo, _vaso_co())

    assert isinstance(oferta, ReferencePrice)
    assert oferta.amount == Decimal("63000")


def test_descarta_un_paquete_que_no_cabe_entero_en_la_caja() -> None:
    motivo = normalize(RawPrice.model_validate(_crudo(units_in_basis=7)), _vaso_co())

    assert isinstance(motivo, str)
    assert "número entero" in motivo


def test_descarta_una_caja_de_otro_tamano() -> None:
    crudo = RawPrice.model_validate(_crudo(basis="caja", units_in_basis=1000, amount="60000"))

    motivo = normalize(crudo, _vaso_co())

    assert isinstance(motivo, str)
    assert "1000" in motivo and "900" in motivo


# --- Regeneración -------------------------------------------------------------------


def test_la_regeneracion_es_idempotente(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_crudos(directorio, [_crudo()])

    write_prices(directorio, build_prices(directorio, "CO"))
    primero = (directorio / PRICES).read_bytes()
    segunda = build_prices(directorio, "CO")
    write_prices(directorio, segunda)

    assert (directorio / PRICES).read_bytes() == primero
    assert segunda.added == []
    assert segunda.changed == []


def test_un_precio_cambiado_deja_el_antes_y_el_despues(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_crudos(directorio, [_crudo()])
    write_prices(directorio, build_prices(directorio, "CO"))
    _escribir_crudos(directorio, [_crudo(amount="1000")])

    build = build_prices(directorio, "CO")
    write_prices(directorio, build)

    (cambio,) = build.changed
    assert cambio["before"].startswith("54000 COP")
    assert cambio["after"].startswith("60000 COP")
    manifiesto = json.loads((directorio / "manifest.json").read_text(encoding="utf-8"))
    assert manifiesto["sources"]["precios_co"]["changes"]["changed"] == build.changed


def test_los_descartes_quedan_en_el_manifiesto(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_crudos(
        directorio, [_crudo(), _crudo(sku="NO-EXISTE", source_url="https://ejemplo.test/x")]
    )

    build = build_prices(directorio, "CO")
    write_prices(directorio, build)

    manifiesto = json.loads((directorio / "manifest.json").read_text(encoding="utf-8"))
    (descarte,) = manifiesto["sources"]["precios_co"]["discarded"]
    assert "NO-EXISTE" in descarte["row"]


def test_varias_fuentes_se_conservan_sin_promediar(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_crudos(
        directorio,
        [
            _crudo(),
            _crudo(
                amount="1000",
                source_name="Comercio Ejemplo",
                source_url="https://ejemplo.test/otra",
            ),
        ],
    )
    write_prices(directorio, build_prices(directorio, "CO"))

    catalogo = load_catalog(str(directorio))
    vaso = next(ref for ref in catalogo.references if ref.sku == "FX-VASO-PS-12")

    assert sorted(oferta.amount for oferta in catalogo.offers(vaso)) == [
        Decimal("54000"),
        Decimal("60000"),
    ]


def test_conserva_las_ofertas_de_otros_paises(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    mexico = _oferta(
        sku="FX-VASO-PET-16",
        country="MX",
        currency="MXN",
        amount="990",
        source_name="Comercio MX",
        source_city="",
        source_url="https://ejemplo.test/mx/otra",
    )
    _escribir_precios(directorio, [mexico])
    _escribir_crudos(directorio, [_crudo()])

    write_prices(directorio, build_prices(directorio, "CO"))

    with (directorio / PRICES).open(encoding="utf-8") as handle:
        paises = sorted(row["country"] for row in csv.DictReader(handle))
    assert paises == ["CO", "MX"]


def test_si_el_catalogo_no_valida_no_se_escribe_nada(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_crudos(directorio, [_crudo()])
    build = build_prices(directorio, "CO")
    # Se rompe el catálogo después de calcular: la validación posterior debe deshacerlo.
    referencias = directorio / "referencias.csv"
    referencias.write_text(
        referencias.read_text(encoding="utf-8").replace("FX-VASO-PS-12", "OTRO"), encoding="utf-8"
    )

    with pytest.raises(CatalogError):
        write_prices(directorio, build)

    assert not (directorio / PRICES).exists()
    assert not list(directorio.glob(".*.tmp"))
    assert not list(directorio.glob(".*.bak"))


# --- Cotización ---------------------------------------------------------------------


def test_cotiza_una_oferta_de_tercero_con_su_fuente() -> None:
    oferta = ReferencePrice.model_validate(_oferta())

    cotizacion = quote_offer(oferta, 3, today=HOY)

    assert cotizacion.total == "162000"
    assert cotizacion.currency == "COP"
    assert "Distribuidora Ejemplo Uno (tercero, no es un precio de Carvajal)" in cotizacion.notice
    assert "con impuestos incluidos" in cotizacion.notice


@pytest.mark.parametrize(
    ("estado", "texto"),
    [
        ("incluido", "con impuestos incluidos"),
        ("no_incluido", "sin impuestos"),
        ("no_indicado", "sin que la fuente indique si incluye impuestos"),
    ],
)
def test_la_nota_de_iva_sigue_a_la_fuente(estado: str, texto: str) -> None:
    oferta = ReferencePrice.model_validate(_oferta(tax_status=estado))

    assert texto in quote_offer(oferta, 1, today=HOY).notice


def test_un_precio_viejo_avisa_que_puede_estar_desactualizado() -> None:
    oferta = ReferencePrice.model_validate(_oferta(captured_at="2026-01-02"))

    aviso = quote_offer(oferta, 1, today=HOY).notice

    assert f"más de {STALE_DAYS} días" in aviso


def test_un_precio_reciente_no_avisa_desactualizacion() -> None:
    oferta = ReferencePrice.model_validate(_oferta())

    assert "desactualizado" not in quote_offer(oferta, 1, today=HOY).notice


# --- Ruta comercial -----------------------------------------------------------------


def _extra(**valores: object) -> dict[str, object]:
    datos: dict[str, object] = {
        "use": "bebida_fria",
        "temperature": "fria",
        "microwave": None,
        "capacity_oz": 12,
        "family": "vaso",
        "quantity": 1800,
        "country": "CO",
        "city": "Bogotá",
        "prefers_renewable": False,
    }
    datos.update(valores)
    return datos


def test_una_solicitud_de_colombia_cotiza_en_pesos_con_la_fuente(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_precios(directorio, [_oferta()])

    resultado = run_commercial(_extra(), catalog=load_catalog(str(directorio)), today=HOY)

    (opcion,) = resultado["options"]
    assert opcion["country"] == "CO"
    assert opcion["boxes"] == 2
    (oferta,) = opcion["offers"]
    assert oferta["total"] == "108000 COP"
    assert oferta["source_name"] == "Distribuidora Ejemplo Uno"
    assert oferta["source_type"] == "tercero"


def test_colombia_nunca_recibe_referencias_ni_precios_de_mexico(tmp_path: Path) -> None:
    resultado = run_commercial(
        _extra(capacity_oz=None, use=None, temperature=None),
        catalog=load_catalog(str(_catalogo(tmp_path))),
        today=HOY,
    )

    assert resultado["options"]
    assert {opcion["country"] for opcion in resultado["options"]} == {"CO"}
    assert "MXN" not in json.dumps(resultado, ensure_ascii=False)


def test_sin_precio_colombiano_no_se_convierte_el_de_mexico(tmp_path: Path) -> None:
    # Hay un vaso MX de 12 oz con precio; el CO de 12 oz no tiene oferta (T029).
    resultado = run_commercial(_extra(), catalog=load_catalog(str(_catalogo(tmp_path))), today=HOY)

    (opcion,) = resultado["options"]
    assert "offers" not in opcion
    assert "no se estima ni se convierte" in opcion["price_note"]
    assert "MXN" not in json.dumps(resultado, ensure_ascii=False)


def test_las_fuentes_citan_al_tercero_que_publica_el_precio(tmp_path: Path) -> None:
    directorio = _catalogo(tmp_path)
    _escribir_precios(directorio, [_oferta()])
    resultado = run_commercial(_extra(), catalog=load_catalog(str(directorio)), today=HOY)

    fuentes = commercial_sources(resultado)

    terceros = [fuente for fuente in fuentes if fuente["source_type"] == "tercero"]
    assert [fuente["url"] for fuente in terceros] == ["https://ejemplo.test/tienda/vaso-12"]
    assert "Distribuidora Ejemplo Uno" in terceros[0]["excerpt"]
