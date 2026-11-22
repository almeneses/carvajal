"""Invariantes del conjunto de demostración del Cotizador (spec 042).

La demo mezcla datos publicados y simulados; estas pruebas aseguran que la marca de
simulación no se pierda, que las monedas no se crucen y que ninguna oferta apunte a un
producto inexistente.
"""

import csv
import json
from decimal import Decimal
from pathlib import Path

from carvajal.catalog.loader import load_catalog

DEMO = Path(__file__).resolve().parents[1] / "data" / "demo" / "cotizador"
CATALOG = Path(__file__).resolve().parents[1] / "data" / "catalog" / "empaques"


def _rows(name: str) -> list[dict[str, str]]:
    with (DEMO / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _products() -> list[dict[str, str]]:
    return _rows("productos.csv")


def test_colombia_llega_a_150_con_el_catalogo_y_48_simulados() -> None:
    extra = _products()
    catalog = [ref for ref in load_catalog().references if ref.country == "CO"]

    assert len(catalog) == 102
    assert len(extra) == 48
    assert {row["origin"] for row in extra} == {"simulada"}
    assert len({ref.sku for ref in catalog} | {row["sku"] for row in extra}) == 150


def test_los_simulados_declaran_su_origen_y_no_citan_ficha() -> None:
    for row in _products():
        assert row["origin"] == "simulada"
        assert row["source_url"] == ""
        assert "demostración" in row["note"]


def test_ofertas_apuntan_al_catalogo_y_respetan_la_moneda() -> None:
    catalog = {(ref.sku, ref.country) for ref in load_catalog().references}
    extra = {(row["sku"], row["country"]) for row in _products()}
    offers = _rows("ofertas.csv")
    currencies = {"CO": "COP", "MX": "MXN"}

    assert all((offer["sku"], offer["country"]) in catalog | extra for offer in offers)
    assert all(offer["currency"] == currencies[offer["country"]] for offer in offers)
    assert {offer["sku"] for offer in offers if offer["country"] == "CO"} == {
        sku for sku, country in catalog if country == "CO"
    } | {sku for sku, _ in extra}


def test_stock_simulado_es_coherente_y_esta_marcado() -> None:
    valid = {"disponible", "agotado", "insuficiente", "desconocido"}
    for offer in _rows("ofertas.csv"):
        if offer["country"] != "CO":
            continue
        assert {"stock", "lead_days", "ship_cost"} <= set(offer["simulated"].split("|"))
        assert offer["stock_status"] in valid
        if offer["stock_status"] == "agotado":
            assert offer["stock_units"] == "0"
        if offer["stock_status"] == "desconocido":
            assert offer["stock_units"] == ""
        if offer["stock_status"] == "insuficiente":
            assert int(offer["stock_units"]) < int(offer["units_in_basis"])


def test_precio_real_de_edexa_se_copia_sin_cambios() -> None:
    with (CATALOG / "fuentes" / "precios_co.csv").open(encoding="utf-8", newline="") as handle:
        published = {row["sku"]: row for row in csv.DictReader(handle)}

    offers = [row for row in _rows("ofertas.csv") if row["provider"] == "EDEXA"]
    assert offers
    for offer in offers:
        source = published[offer["sku"]]
        assert Decimal(offer["amount"]) == Decimal(source["amount"])
        assert offer["basis"] == source["basis"]
        assert offer["units_in_basis"] == source["units_in_basis"]
        assert "price" not in offer["simulated"].split("|")


def test_sedes_tienen_coordenadas_y_precision_declarada() -> None:
    precision = {"direccion", "via", "ciudad", "sin_coordenadas"}
    for row in _rows("proveedores.csv"):
        assert row["precision"] in precision
        assert 0 < float(row["latitude"]) < 13
        assert -80 < float(row["longitude"]) < -66


def test_cada_oferta_apunta_a_una_sede_del_directorio() -> None:
    sedes = {row["provider_id"]: row for row in _rows("proveedores.csv")}

    for offer in _rows("ofertas.csv"):
        if offer["country"] != "CO":
            assert offer["provider_id"] == ""
            continue
        sede = sedes[offer["provider_id"]]
        assert (sede["name"], sede["city"]) == (offer["provider"], offer["city"])


def test_manifiesto_cuenta_lo_escrito() -> None:
    manifest = json.loads((DEMO / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["counts"]["productos_co"] == 150
    assert manifest["counts"]["productos_co_reales"] == 102
    assert manifest["counts"]["productos_co_simulados"] == 48
    assert manifest["counts"]["productos_mx"] == 98
    assert manifest["counts"]["sedes"] == len(_rows("proveedores.csv"))
    assert manifest["counts"]["ofertas_co"] == len(
        [row for row in _rows("ofertas.csv") if row["country"] == "CO"]
    )
