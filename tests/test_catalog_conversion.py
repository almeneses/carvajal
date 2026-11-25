"""Ampliación del catálogo de Colombia con las referencias de México (2026-11-25)."""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from carvajal.agent.commercial import quote_offer
from carvajal.agent.toolagent.tools.builtin import _offer_source_type
from carvajal.catalog.conversion import (
    SOURCE_NAME,
    ExchangeRate,
    adapt_all,
    adapted_reference,
    adapted_sku,
    convert_price,
    converted_price,
    load_rate,
)
from carvajal.catalog.images import catalog_image
from carvajal.catalog.loader import DEFAULT_DIR, load_catalog
from carvajal.commercial import load_commercial_book
from carvajal.commercial.models import price_origin, seller_source_type
from carvajal.quotation import load_dataset
from carvajal.quotation.data import REFERENCE_SITE_ID

TASA = ExchangeRate(date(2026, 11, 25), Decimal("3592.03"), Decimal("19.7"))


def _mexico():
    return [ref for ref in load_catalog().references if ref.country == "MX"]


def _copias():
    return [ref for ref in load_catalog().references if ref.is_adapted]


def test_la_tasa_versionada_es_el_cruce_publicado() -> None:
    rate = load_rate(DEFAULT_DIR)

    assert rate == TASA
    assert rate.cop_per_mxn == Decimal("182.3365")


def test_el_importe_se_convierte_y_se_redondea_al_peso() -> None:
    assert convert_price(Decimal("482.00"), TASA) == Decimal("87886")


def test_una_referencia_que_no_es_de_mexico_no_se_copia() -> None:
    colombiana = next(
        ref for ref in load_catalog().references if ref.country == "CO" and not ref.is_adapted
    )

    with pytest.raises(ValueError, match="no es una referencia de México"):
        adapted_reference(colombiana)


def test_sin_precio_en_mexico_no_hay_precio_convertido() -> None:
    sin_precio = _mexico()[0].model_copy(update={"price": None, "currency": None})

    assert converted_price(sin_precio, TASA) is None


def test_todo_el_catalogo_de_mexico_esta_copiado_con_los_mismos_datos() -> None:
    campos = {"sku", "country", "price", "currency", "tax_included", "price_valid_until"}
    copias = {ref.sku: ref for ref in _copias()}

    assert len(copias) == len(_mexico()) == 98
    for original in _mexico():
        copia = copias[adapted_sku(original.sku)]
        assert copia.model_dump(exclude=campos) == original.model_dump(exclude=campos)
        assert copia.price is None


def test_los_archivos_versionados_coinciden_con_lo_que_genera_el_script() -> None:
    catalog = load_catalog()
    copias, precios = adapt_all(catalog.references, load_rate(DEFAULT_DIR))

    assert copias == sorted(_copias(), key=lambda ref: ref.sku)
    publicados = [oferta for ref in _copias() for oferta in catalog.offers(ref)]
    assert sorted(publicados, key=lambda item: item.sku) == precios


def test_el_precio_convertido_nunca_figura_como_publicado_en_colombia() -> None:
    catalog = load_catalog()
    for ref in _copias():
        [oferta] = catalog.offers(ref)
        assert oferta.source_type == "sistema"
        assert oferta.tax_status == "no_indicado"
        assert oferta.valid_until is None
        assert oferta.source_city == "Bogotá"
        aviso = quote_offer(oferta, 1, today=date(2026, 11, 25)).notice
        assert "precio de referencia del catálogo" in aviso
        assert "México" not in aviso


def test_las_referencias_propias_de_colombia_no_reciben_precio_convertido() -> None:
    catalog = load_catalog()
    propias = [ref for ref in catalog.references if ref.country == "CO" and not ref.is_adapted]

    assert len(propias) == 102
    assert all(oferta.source_type == "tercero" for ref in propias for oferta in catalog.offers(ref))


def test_la_oferta_convertida_no_se_atribuye_a_una_sede() -> None:
    book = load_commercial_book()
    convertidas = [offer for offer in book.offers if offer.source.type == "sistema"]

    assert len(convertidas) == 98
    for offer in convertidas:
        assert offer.site_id is None
        vendedor = book.seller(offer.seller_id)
        assert vendedor is not None
        assert (vendedor.kind, vendedor.name) == ("sistema", SOURCE_NAME)
        assert book.sites_for_sku(offer.sku, "CO") == ()
    assert SOURCE_NAME not in {site.name for site in book.sites.values()}


def test_el_precio_convertido_solo_se_cotiza_en_bogota() -> None:
    dataset = load_dataset("2026-11-25")

    ofertas = dataset.offers_for("CO-MX-2864108000")

    assert [offer.city for offer in ofertas] == ["Bogotá"]
    assert all(offer.converted for offer in ofertas)
    assert dataset.provider(ofertas[0].site_id) is None


def test_la_procedencia_distingue_publicado_de_convertido() -> None:
    assert seller_source_type("distribuidor") == "tercero"
    assert seller_source_type("fabricante") == "oficial"
    assert seller_source_type("sistema") == "sistema"
    assert price_origin("tercero", "EDEXA") == "publicado por EDEXA"
    # El origen queda en la fuente para sustentarlo si alguien lo pregunta.
    assert "México" in price_origin("sistema", SOURCE_NAME)


def test_cada_copia_tiene_su_foto_con_procedencia() -> None:
    for ref in _copias():
        image = catalog_image(ref.sku, (DEFAULT_DIR,))
        assert image is not None, ref.sku
        assert image.source_url == ref.source_url
        assert image.source_type == "oficial"
        # La referencia de México es el mismo producto y usa el mismo archivo.
        original = catalog_image(ref.sku.removeprefix("CO-MX-"), (DEFAULT_DIR,))
        assert original is not None and original.path == image.path


def test_regenerar_los_precios_publicados_conserva_los_convertidos(tmp_path: Path) -> None:
    import shutil

    from carvajal.catalog.prices import build_prices, write_prices

    directorio = tmp_path / "empaques"
    shutil.copytree(DEFAULT_DIR, directorio, ignore=shutil.ignore_patterns("imagenes"))
    antes = (directorio / "precios.csv").read_text(encoding="utf-8")

    build = build_prices(directorio, "CO")
    write_prices(directorio, build)

    assert (directorio / "precios.csv").read_text(encoding="utf-8") == antes
    assert build.removed == []
    with (directorio / "precios.csv").open(encoding="utf-8", newline="") as handle:
        assert sum(row["source_type"] == "sistema" for row in csv.DictReader(handle)) == 98
    load_catalog.cache_clear()


def test_el_precio_convertido_tiene_la_misma_procedencia_en_todas_las_rutas() -> None:
    # Arrange: una copia de México, como la ven el cotizador, la ruta comercial y el catálogo.

    sku = "CO-MX-2864108000"
    book = load_commercial_book()
    [offer] = [item for item in book.offers if item.sku == sku and item.country == "CO"]
    seller = book.seller(offer.seller_id)

    # Act
    cotizador = _offer_source_type(book, sku, REFERENCE_SITE_ID, offer.source.url or "")
    comercial = seller_source_type(seller.kind) if seller is not None else None
    [reference] = [ref for ref in load_catalog().references if ref.sku == sku]
    catalogo = {price.source_type for price in load_catalog().offers(reference)}

    # Assert: «sistema» en las tres; nunca «oficial» ni «tercero».
    assert cotizador == comercial == "sistema"
    assert catalogo == {"sistema"}
