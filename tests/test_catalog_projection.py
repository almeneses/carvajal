"""Precios proyectados para las referencias propias de Colombia (decisión del 2026-11-26)."""

import csv
from decimal import Decimal

from carvajal.catalog.loader import DEFAULT_DIR, load_catalog
from carvajal.catalog.models import Reference, ReferencePrice
from carvajal.catalog.projection import PROJECTION_FILE, project, project_prices


def _catalogo():
    return load_catalog()


def _precios_no_proyectados() -> list[ReferencePrice]:
    """Los precios publicados y los convertidos de las copias: la base de la proyección."""

    catalog = _catalogo()
    propias = {ref.sku for ref in catalog.references if ref.country == "CO" and not ref.is_adapted}
    return [
        offer
        for offers in catalog.prices.values()
        for offer in offers
        if not (offer.source_type == "sistema" and offer.sku in propias)
    ]


def _unidad(offer: ReferencePrice, reference: Reference) -> Decimal:
    return offer.amount / Decimal(reference.units_per_box)


def test_ninguna_referencia_de_colombia_queda_sin_precio() -> None:
    catalog = _catalogo()

    sin_precio = [r.sku for r in catalog.references if r.country == "CO" and not catalog.offers(r)]

    assert sin_precio == []


def test_los_archivos_versionados_coinciden_con_la_proyeccion() -> None:
    catalog = _catalogo()
    proyecciones = project(catalog.references, _precios_no_proyectados())
    ciudades = {item.city for item in catalog.distributors}
    esperados = project_prices(proyecciones, ciudades, _fecha())

    proyectados = {ref.sku for ref in catalog.references if ref.country == "CO"} & {
        item.reference.sku for item in proyecciones
    }
    versionados = [offer for sku in proyectados for offer in catalog.prices.get((sku, "CO"), ())]

    def clave(item: ReferencePrice) -> tuple[str, str]:
        return (item.sku, item.source_city or "")

    assert len(proyecciones) == 73
    assert sorted(versionados, key=clave) == sorted(esperados, key=clave)
    with (DEFAULT_DIR / PROJECTION_FILE).open(encoding="utf-8", newline="") as handle:
        sustento = {row["sku"]: row for row in csv.DictReader(handle)}
    assert set(sustento) == proyectados
    for item in proyecciones:
        fila = sustento[item.reference.sku]
        assert fila["base_sku"] == item.base.sku
        assert int(fila["precio_caja"]) == item.box


def _fecha():
    from carvajal.catalog.conversion import load_rate

    return load_rate(DEFAULT_DIR).date


def test_el_precio_por_unidad_queda_en_el_rango_de_su_familia() -> None:
    catalog = _catalogo()
    by_sku = {ref.sku: ref for ref in catalog.references}
    base = _precios_no_proyectados()
    rangos: dict[str, list[Decimal]] = {}
    for offer in base:
        reference = by_sku[offer.sku]
        if offer.country == "CO":
            rangos.setdefault(reference.family, []).append(_unidad(offer, reference))

    for item in project(catalog.references, base):
        familia = rangos[item.reference.family]
        assert min(familia) <= item.unit <= max(familia), item.reference.sku


def test_la_proyeccion_no_toca_a_las_que_ya_tenian_precio() -> None:
    catalog = _catalogo()
    con_precio = {offer.sku for offer in _precios_no_proyectados() if offer.country == "CO"}

    proyectados = {
        item.reference.sku for item in project(catalog.references, _precios_no_proyectados())
    }

    assert not proyectados & con_precio
    assert all(not ref.is_adapted for ref in catalog.references if ref.sku in proyectados)


def test_cada_precio_proyectado_cita_la_ficha_de_su_base_y_va_en_las_10_ciudades() -> None:
    catalog = _catalogo()
    ciudades = {item.city for item in catalog.distributors}

    for item in project(catalog.references, _precios_no_proyectados()):
        ofertas = catalog.prices[(item.reference.sku, "CO")]
        assert {offer.source_city for offer in ofertas} == ciudades
        assert all(offer.source_type == "sistema" for offer in ofertas)
        assert all(offer.tax_status == "no_indicado" for offer in ofertas)
        assert all(offer.source_url == item.base.source_url for offer in ofertas)


def test_dentro_de_una_linea_la_capacidad_mayor_no_es_mas_barata() -> None:
    # Vasos térmicos, platos hondos, vasos rígidos y Gold: el escalado por capacidad.
    catalog = _catalogo()
    por_sku = {
        item.reference.sku: item.unit
        for item in project(catalog.references, _precios_no_proyectados())
    }

    for linea in (
        ["CO-VTER-04", "CO-VTER-06", "CO-VTER-08", "CO-VTER-10"],
        ["CO-PHON-05", "CO-PHON-16", "CO-PHON-25", "CO-PHON-30"],
        ["CO-VRM-10", "CO-VRM-12"],
        ["CO-VGOLD-09", "CO-VGOLD-12", "CO-VGOLD-16"],
    ):
        precios = [por_sku[sku] for sku in linea]
        assert precios == sorted(precios), linea
