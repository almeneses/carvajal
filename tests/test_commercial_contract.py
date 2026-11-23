"""Pruebas del contrato comercial compartido (producto, oferta y sede).

Cubren los criterios de aceptación:

- el directorio se consulta por ciudad sin precio ni stock;
- el contrato acepta varias ofertas por SKU y rechaza huérfanas;
- una sede aproximada conserva su precisión y su mapa no finge exactitud;
- una existencia sin evidencia queda «por confirmar» y nunca «verificada»;
- cada consumidor distingue oferta publicada, contacto de directorio y stock verificado;
- México conserva la paridad de las 98 referencias auditadas.
"""

import json
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from carvajal.commercial import (
    Availability,
    AvailabilityEvidence,
    Basis,
    CommercialRecord,
    ContractDocument,
    ContractError,
    Country,
    Currency,
    EvidenceKind,
    GeocodePrecision,
    GeoPoint,
    MapView,
    NormalizedPrice,
    Offer,
    Origin,
    Presentation,
    Product,
    Seller,
    Site,
    SourceRef,
    SourceType,
    StockEvidence,
    build_commercial_book,
    load_commercial_book,
    mexico_parity,
    site_id_for,
)
from carvajal.commercial.loader import CATALOG_DIR, DEMO_DIR, EVIDENCE_PATH, book_from_paths

TODAY = date(2026, 11, 22)
YESTERDAY = TODAY - timedelta(days=1)
EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "contratos" / "comerciales" / "ejemplos"


def _source(
    kind: str = "vendedor",
    *,
    name: str = "Vendedor de prueba",
    url: str | None = "https://example.com/oferta",
    captured: date = TODAY,
) -> SourceRef:
    return SourceRef(type=cast(SourceType, kind), name=name, url=url, captured_at=captured)


def _product(sku: str = "SKU-1", country: str = "CO") -> Product:
    return Product(
        sku=sku,
        country=cast(Country, country),
        name=f"Producto {sku}",
        family="vaso",
        units_per_box=100,
        origin="real",
        source=_source("oficial", name="Carvajal", url="https://example.com/ficha"),
    )


def _seller(seller_id: str = "CO-VEND-AAAA", country: str = "CO") -> Seller:
    return Seller(
        seller_id=seller_id,
        name=f"Vendedor {seller_id}",
        country=cast(Country, country),
        kind="distribuidor",
        source=_source("directorio", name="Directorio", url="https://example.com/directorio"),
    )


def _site(
    site_id: str = "CO-SEDE-AAAA",
    seller_id: str = "CO-VEND-AAAA",
    *,
    city: str = "Bogotá",
    address: str = "Calle 1 No. 2-3",
    precision: str = "direccion",
) -> Site:
    location = GeoPoint(
        latitude=4.6,
        longitude=-74.1,
        precision=cast(GeocodePrecision, precision),
        geocoder="OpenStreetMap Nominatim",
        geocoded_at=TODAY,
    )
    return Site(
        site_id=site_id,
        seller_id=seller_id,
        country="CO",
        name=f"Sede {site_id}",
        city=city,
        address=address,
        phone="300 000 0000",
        source=_source("directorio", name="Directorio", url="https://example.com/directorio"),
        location=location,
    )


def _offer(
    *,
    offer_id: str = "CO-OFERTA-AAAA",
    sku: str = "SKU-1",
    seller_id: str = "CO-VEND-AAAA",
    site_id: str | None = "CO-SEDE-AAAA",
    country: str = "CO",
    currency: str = "COP",
    amount: Decimal = Decimal("10000"),
    basis: str = "caja",
    units: int = 100,
    availability: Availability | None = None,
    origin: str = "publicada",
    source: SourceRef | None = None,
    captured: date = TODAY,
) -> Offer:
    chosen_source = source or _source(captured=captured)
    return Offer(
        offer_id=offer_id,
        sku=sku,
        country=cast(Country, country),
        seller_id=seller_id,
        site_id=site_id,
        city="Bogotá" if site_id else None,
        presentation=Presentation(basis=cast(Basis, basis), units_in_basis=units),
        amount=amount,
        currency=cast(Currency, currency),
        tax_status="incluido",
        origin=cast(Origin, origin),
        source=chosen_source,
        normalized=(
            NormalizedPrice(units_in_basis=units, amount=amount) if basis == "caja" else None
        ),
        availability=availability or Availability.from_claim("disponible"),
    )


def _evidence(
    *,
    kind: str = "confirmacion_vendedor",
    recorded: date = TODAY,
    valid_until: date | None = None,
    units: int = 100,
) -> AvailabilityEvidence:
    return AvailabilityEvidence(
        kind=cast(EvidenceKind, kind),
        recorded_by="Ana Pérez, operaciones del vendedor",
        recorded_at=recorded,
        reference="correo del 2026-11-22 09:15",
        units=units,
        valid_until=valid_until,
    )


def _verified_book(*, recorded: date = TODAY, valid_until: date | None = None):
    product = _product()
    seller = _seller()
    site = _site()
    offer = _offer()
    stock = StockEvidence(
        sku=product.sku,
        country=product.country,
        site_id=site.site_id,
        declared_status="disponible",
        evidence=_evidence(recorded=recorded, valid_until=valid_until),
    )
    return build_commercial_book(
        products=[product],
        sellers=[seller],
        sites=[site],
        offers=[offer],
        evidence=[stock],
    )


# --------------------------------------------------------------------- directorio


def test_directorio_por_ciudad_sin_precio_ni_stock() -> None:
    """Criterio: listar el directorio aunque falten precio y existencias."""

    book = build_commercial_book(
        products=[_product()],
        sellers=[_seller()],
        sites=[_site()],
        offers=[],
    )

    directory = book.city_directory("Bogotá")

    assert directory.city == "Bogotá"
    assert directory.counts == {"contacto_directorio": 1}
    assert len(directory.records) == 1
    contact = directory.records[0]
    assert contact.record_type == "contacto_directorio"
    assert contact.sku is None
    assert contact.offer is None
    assert contact.availability is None
    assert contact.site is not None
    assert contact.site.phone == "300 000 0000"


def test_directorio_real_por_ciudad_sin_ofertas_publicadas() -> None:
    """Cali y Neiva tienen sedes aunque no tengan precio publicado."""

    book = load_commercial_book()

    cali = book.city_directory("Cali")
    neiva = book.city_directory("Neiva")

    assert cali.counts["contacto_directorio"] == 4
    assert cali.counts.get("oferta_publicada", 0) == 0
    assert neiva.counts["contacto_directorio"] == 1
    assert neiva.counts.get("oferta_publicada", 0) == 0
    assert len(book.cities()) == 10


def test_ciudad_sin_sedes_devuelve_lista_de_ciudades() -> None:
    book = load_commercial_book()

    empty = book.city_directory("Tuluá")

    assert empty.records == ()
    assert len(empty.cities) == 10
    assert "Bogotá" in empty.cities


def test_ciudad_se_busca_sin_tildes_ni_mayusculas() -> None:
    book = load_commercial_book()

    assert book.city_directory("bogota").counts["contacto_directorio"] == 7


def test_identificador_de_sede_estable_y_sensible_a_la_identidad() -> None:
    first = site_id_for(
        "CO", "CO-VEND-AAAA", "Sideral", "Bogotá", "Calle 1 No. 2-3", "300 000 0000"
    )
    same = site_id_for("CO", "CO-VEND-AAAA", "Sideral", "Bogota", "calle 1 no. 2-3", "300 000 0000")
    other_address = site_id_for(
        "CO", "CO-VEND-AAAA", "Sideral", "Bogotá", "Calle 9 No. 9-9", "300 000 0000"
    )
    other_city = site_id_for(
        "CO", "CO-VEND-AAAA", "Sideral", "Cali", "Calle 1 No. 2-3", "300 000 0000"
    )
    other_seller = site_id_for(
        "CO", "CO-VEND-BBBB", "Sideral", "Bogotá", "Calle 1 No. 2-3", "300 000 0000"
    )
    other_phone = site_id_for(
        "CO", "CO-VEND-AAAA", "Sideral", "Bogotá", "Calle 1 No. 2-3", "311 111 1111"
    )

    assert first == same
    assert first.startswith("CO-SEDE-")
    assert first != other_address
    assert first != other_city
    assert first != other_seller
    # El teléfono no es el ancla cuando hay dirección: cambiarlo no renombra la sede.
    assert first == other_phone


def test_las_sedes_reales_conservan_sus_identificadores_entre_cargas() -> None:
    first = book_from_paths(CATALOG_DIR, DEMO_DIR, EVIDENCE_PATH)
    second = book_from_paths(CATALOG_DIR, DEMO_DIR, EVIDENCE_PATH)

    assert set(first.sites) == set(second.sites)
    assert len(first.sites) == 29
    assert len({site.legacy_provider_id for site in first.sites.values()}) == 29


def test_el_contrato_sin_demo_conserva_publicados_y_paridad_mx() -> None:
    """El cargador sin demo conserva las 29 ofertas CO y la paridad MX."""

    book = book_from_paths(CATALOG_DIR, DEMO_DIR, EVIDENCE_PATH, include_demo=False)
    co = [offer for offer in book.offers if offer.country == "CO"]
    mx = [offer for offer in book.offers if offer.country == "MX"]

    assert len(book.sites) == 29
    assert len(co) == 29
    assert len(mx) == 98
    assert all(offer.origin == "publicada" for offer in book.offers)
    assert {"CO-PPAN-12", "7704918105071", "7704918100816"} <= {offer.sku for offer in co}

    parity = mexico_parity(book=book)
    assert parity.audited_references == 98
    assert parity.missing_skus == ()
    assert parity.amount_mismatches == ()


# ------------------------------------------------------------------------ ofertas


def test_varias_ofertas_para_el_mismo_sku() -> None:
    """Criterio: el contrato admite varias ofertas del mismo producto."""

    product = _product()
    seller_a = _seller("CO-VEND-AAAA")
    seller_b = _seller("CO-VEND-BBBB")
    site_a = _site("CO-SEDE-AAAA", seller_a.seller_id)
    site_b = _site("CO-SEDE-BBBB", seller_b.seller_id)
    offers = [
        _offer(offer_id="CO-OFERTA-AAAA", seller_id=seller_a.seller_id, site_id=site_a.site_id),
        _offer(
            offer_id="CO-OFERTA-BBBB",
            seller_id=seller_b.seller_id,
            site_id=site_b.site_id,
            amount=Decimal("9000"),
            source=_source(name="Otro vendedor", url="https://example.com/otra"),
        ),
    ]

    book = build_commercial_book(
        products=[product],
        sellers=[seller_a, seller_b],
        sites=[site_a, site_b],
        offers=offers,
    )
    records = book.offers_for_sku("SKU-1", "CO")

    assert len(records) == 2
    assert {record.offer.seller_id for record in records if record.offer} == {
        seller_a.seller_id,
        seller_b.seller_id,
    }


def test_oferta_huerfana_falla_con_mensaje_explicito() -> None:
    """Criterio: el contrato rechaza una oferta que no apunta a un producto."""

    with pytest.raises(ContractError, match="oferta huérfana"):
        build_commercial_book(
            products=[_product("SKU-1")],
            sellers=[_seller()],
            sites=[_site()],
            offers=[_offer(sku="SKU-INEXISTENTE")],
        )


def test_evidencia_huerfana_falla() -> None:
    stock = StockEvidence(
        sku="SKU-1",
        country="CO",
        site_id="CO-SEDE-FANTASMA",
        evidence=_evidence(),
    )

    with pytest.raises(ContractError, match="evidencia huérfana"):
        build_commercial_book(
            products=[_product()],
            sellers=[_seller()],
            sites=[_site()],
            offers=[_offer()],
            evidence=[stock],
        )


def test_oferta_con_sede_desconocida_falla() -> None:
    with pytest.raises(ContractError, match="sede CO-SEDE-FANTASMA"):
        build_commercial_book(
            products=[_product()],
            sellers=[_seller()],
            sites=[_site()],
            offers=[_offer(site_id="CO-SEDE-FANTASMA")],
        )


def test_oferta_publicada_sin_url_falla() -> None:
    with pytest.raises(ContractError, match="sin URL de fuente"):
        build_commercial_book(
            products=[_product()],
            sellers=[_seller()],
            sites=[_site()],
            offers=[_offer(source=_source(url=None))],
        )


def test_oferta_exige_moneda_del_pais() -> None:
    with pytest.raises(ValidationError, match="va en COP"):
        _offer(country="CO", currency="MXN")


def test_oferta_conserva_presentacion_unidades_importe_iva_fuente_y_fecha() -> None:
    book = load_commercial_book()
    records = book.offers_for_sku("CO-CTT-08")
    published = next(item for item in records if item.record_type == "oferta_publicada")
    offer = published.offer

    assert offer is not None
    assert offer.sku == "CO-CTT-08"
    assert offer.country == "CO"
    assert offer.seller_id
    assert offer.presentation.basis in {"paquete", "caja"}
    assert offer.presentation.units_in_basis > 0
    assert offer.amount > 0
    assert offer.currency == "COP"
    assert offer.tax_status in {"incluido", "no_incluido", "no_indicado"}
    assert offer.source.name
    assert offer.source.captured_at == date(2026, 11, 18)


def test_logistica_del_cotizador_se_conserva_sin_volverse_real() -> None:
    book = load_commercial_book()
    published = next(
        offer
        for offer in book.offers
        if offer.country == "CO" and offer.published and offer.delivery is not None
    )
    demo = next(offer for offer in book.offers if offer.origin == "demo")

    assert published.delivery is not None
    assert published.delivery.lead_days_max >= published.delivery.lead_days_min
    assert published.delivery.simulated is True
    assert published.tax_simulated is False
    assert demo.delivery is not None
    assert demo.delivery.simulated is True
    assert demo.tax_simulated is True


def test_presentacion_publicada_es_la_primaria_y_la_caja_es_derivada() -> None:
    book = load_commercial_book()
    published = next(
        item for item in book.offers if item.country == "CO" and item.origin == "publicada"
    )

    assert published.presentation.basis in {"unidad", "paquete", "caja"}
    assert published.amount > 0
    assert published.normalized is not None
    assert published.normalized.basis == "caja"
    factor = published.normalized.units_in_basis // published.presentation.units_in_basis
    assert published.normalized.amount == published.amount * factor


# ------------------------------------------------------------------------- sedes


def test_precision_de_geocodificacion_decide_el_mapa() -> None:
    book = load_commercial_book()
    sites = list(book.sites.values())

    address = next(site for site in sites if site.name.startswith("Comercializadora"))
    street = next(site for site in sites if site.name == "Megamarcas SAS")
    city = next(site for site in sites if site.name == "EDEXA")
    no_address = next(site for site in sites if site.name == "PAAK" and site.city == "Medellín")

    assert address.location is not None and address.location.precision == "direccion"
    assert address.map_view.mode == "marcador"
    assert address.map_view.approximate is False
    assert street.location is not None and street.location.precision == "via"
    assert street.map_view.mode == "marcador"
    assert street.map_view.approximate is True
    assert city.location is not None and city.location.precision == "ciudad"
    assert city.map_view.mode == "zona"
    assert city.map_view.zoom == 11
    assert city.map_view.approximate is True
    assert no_address.address is None
    assert no_address.location is not None
    assert no_address.location.precision == "ciudad"
    assert "zona aproximada" in no_address.map_view.note


def test_sede_sin_geocodificar_no_inventa_mapa() -> None:
    site = Site(
        site_id="CO-SEDE-AAAA",
        seller_id="CO-VEND-AAAA",
        country="CO",
        name="Sede sin mapa",
        city="Bogotá",
        address=None,
        phone="300 000 0000",
        source=_source("directorio", url="https://example.com/directorio"),
        location=None,
    )

    assert site.map_view.available is False
    assert site.map_view.mode == "sin_mapa"
    assert site.map_view.approximate is False


# --------------------------------------------------------------- disponibilidad


def test_sin_mapa_no_se_marca_aproximado() -> None:
    with pytest.raises(ValidationError, match="sin_mapa"):
        MapView(available=False, mode="sin_mapa", approximate=True)


def test_afirmacion_sin_evidencia_queda_por_confirmar() -> None:
    """Criterio: disponibilidad sin evidencia no se presenta como verificada."""

    available = Availability.from_claim("disponible")
    exhausted = Availability.from_claim("agotado")
    unknown = Availability.from_claim("desconocido")

    assert available.status == "por_confirmar"
    assert exhausted.status == "por_confirmar"
    assert unknown.status == "desconocido"
    assert available.evidence == ()


def test_lo_simulado_nunca_es_verificado() -> None:
    simulated = Availability.from_claim("disponible", simulated=True)

    assert simulated.status == "por_confirmar"
    assert simulated.simulated is True
    assert simulated.evidence == ()


def test_verificado_sin_evidencia_se_rechaza() -> None:
    with pytest.raises(ValidationError, match="exige evidencia"):
        Availability(status="verificado", declared_status="disponible")


def test_agotado_sin_evidencia_no_se_afirma() -> None:
    with pytest.raises(ValidationError, match="exige evidencia"):
        Availability(status="no_disponible", declared_status="agotado")


def test_evidencia_sin_estado_declarado_queda_desconocida() -> None:
    stock = StockEvidence(
        sku="SKU-1",
        country="CO",
        site_id="CO-SEDE-AAAA",
        evidence=_evidence(kind="simulacion"),
    )
    book = build_commercial_book(
        products=[_product()],
        sellers=[_seller()],
        sites=[_site()],
        offers=[_offer()],
        evidence=[stock],
    )

    record = book.offer_record(book.offers[0], as_of=TODAY)

    assert record.availability is not None
    assert record.availability.status == "desconocido"


def test_evidencia_admisible_permite_stock_verificado_en_la_sede() -> None:
    book = _verified_book(valid_until=TODAY + timedelta(days=3))

    links = book.sites_for_sku("SKU-1", as_of=TODAY)
    directory = book.city_directory("Bogotá", as_of=TODAY)

    assert len(links) == 1
    assert links[0].relationship == "stock_verificado"
    assert links[0].availability.status == "verificado"
    assert directory.counts["stock_verificado"] == 1
    stock = next(item for item in directory.records if item.record_type == "stock_verificado")
    assert stock.sku == "SKU-1"
    assert stock.site is not None
    assert stock.availability is not None
    assert stock.availability.evidence[0].recorded_by.startswith("Ana")


def test_evidencia_de_agotado_cambia_la_oferta_a_no_disponible() -> None:
    stock = StockEvidence(
        sku="SKU-1",
        country="CO",
        site_id="CO-SEDE-AAAA",
        declared_status="agotado",
        evidence=_evidence(units=0),
    )
    book = build_commercial_book(
        products=[_product()],
        sellers=[_seller()],
        sites=[_site()],
        offers=[_offer()],
        evidence=[stock],
    )

    record = book.offer_record(book.offers[0], as_of=TODAY)
    link = book.sites_for_sku("SKU-1", as_of=TODAY)[0]

    assert record.availability is not None
    assert record.availability.status == "no_disponible"
    assert link.availability.status == "no_disponible"
    assert book.city_directory("Bogotá", as_of=TODAY).counts.get("stock_verificado", 0) == 0


def test_consulta_por_defecto_no_confia_en_evidencia_vieja() -> None:
    book = _verified_book(recorded=date.today() - timedelta(days=30))

    directory = book.city_directory("Bogotá")

    assert directory.counts.get("stock_verificado", 0) == 0


def test_evidencia_vencida_degrada_a_por_confirmar() -> None:
    book = _verified_book(recorded=YESTERDAY - timedelta(days=10))

    directory = book.city_directory("Bogotá", as_of=TODAY)
    links = book.sites_for_sku("SKU-1", as_of=TODAY)

    assert directory.counts.get("stock_verificado", 0) == 0
    assert links[0].relationship == "oferta_publicada"
    assert links[0].availability.status == "por_confirmar"


def test_evidencia_de_simulacion_no_habilita_stock_verificado() -> None:
    simulated = Availability.from_claim("disponible", simulated=True)
    offer = _offer(availability=simulated)
    stock = StockEvidence(
        sku="SKU-1",
        country="CO",
        site_id="CO-SEDE-AAAA",
        evidence=_evidence(kind="simulacion"),
    )
    book = build_commercial_book(
        products=[_product()],
        sellers=[_seller()],
        sites=[_site()],
        offers=[offer],
        evidence=[stock],
    )

    directory = book.city_directory("Bogotá")

    assert directory.counts.get("stock_verificado", 0) == 0


# ----------------------------------------------------------- discriminador común


def test_los_mismos_campos_distinguen_contacto_oferta_y_stock() -> None:
    """Criterio: un consumidor distingue los tres registros con el mismo contrato."""

    book = load_commercial_book()
    bogota = book.city_directory("Bogotá")
    contact = bogota.of_type("contacto_directorio")[0]
    published = bogota.of_type("oferta_publicada")[0]
    demo = bogota.of_type("oferta_demo")[0]

    fields = set(CommercialRecord.model_fields)
    assert set(contact.model_dump()) == fields
    assert set(published.model_dump()) == fields
    assert set(demo.model_dump()) == fields

    assert contact.record_type == "contacto_directorio"
    assert contact.sku is None and contact.offer is None and contact.availability is None
    assert published.record_type == "oferta_publicada"
    assert published.offer is not None and published.availability is not None
    assert demo.record_type == "oferta_demo"
    assert demo.offer is not None and demo.offer.origin == "demo"

    verified = _verified_book().city_directory("Bogotá", as_of=TODAY).of_type("stock_verificado")[0]
    assert set(verified.model_dump()) == fields
    assert verified.record_type == "stock_verificado"
    assert verified.availability is not None
    assert verified.availability.status == "verificado"


def test_contacto_del_directorio_no_se_confunde_con_sede_que_vende() -> None:
    book = load_commercial_book()

    links = book.sites_for_sku("CO-CTT-08", "CO")
    linked_names = {site.name for link in links if (site := book.site(link.site_id)) is not None}

    assert "EDEXA" in linked_names
    assert "Comercializadora de Desechables Sideral" not in linked_names
    assert all(link.relationship in {"oferta_publicada", "oferta_demo"} for link in links)


def test_oferta_demo_no_se_presenta_como_publicada() -> None:
    book = load_commercial_book()
    demo = next(item for item in book.offers if item.origin == "demo")
    record = book.offer_record(demo)

    assert record.record_type == "oferta_demo"
    assert demo.source.type == "demo"
    assert demo.source.url is None
    assert demo.availability.status in {"por_confirmar", "desconocido"}
    assert demo.availability.status != "verificado"


def test_numeros_de_la_auditoria_coinciden() -> None:
    book = load_commercial_book()

    summary = book.summary()

    assert summary["sedes"] == 29
    assert summary["ciudades"] == 10
    assert summary["ofertas_co"] == 160
    assert summary["ofertas_mx"] == 98
    assert summary["ofertas_publicadas"] == 127
    assert summary["ofertas_demo"] == 131


def test_disponibilidad_real_del_catalogo_es_por_confirmar_o_desconocida() -> None:
    """Hoy no hay ninguna evidencia real: 145 por confirmar y 15 desconocidas."""

    book = load_commercial_book()
    co = [offer for offer in book.offers if offer.country == "CO"]
    statuses = Counter(offer.availability.status for offer in co)

    assert statuses == {"por_confirmar": 145, "desconocido": 15}
    assert all(offer.availability.status not in {"verificado", "no_disponible"} for offer in co)
    for city in book.cities():
        assert book.city_directory(city).counts.get("stock_verificado", 0) == 0


# -------------------------------------------------------------------------- México


def test_mexico_conserva_cobertura_moneda_y_presentacion() -> None:
    """Criterio: comparar las 98 referencias antes de retirar la lectura inline."""

    report = mexico_parity()

    assert report.ok
    assert report.audited_references == 98
    assert report.inline_offers == 98
    assert report.contract_offers == 98
    assert report.missing_skus == ()
    assert report.extra_skus == ()
    assert report.duplicate_skus == ()
    assert report.currency_mismatches == ()
    assert report.presentation_mismatches == ()
    assert report.amount_mismatches == ()
    assert report.tax_mismatches == ()
    assert report.source_mismatches == ()
    assert report.date_mismatches == ()
    assert report.valid_until_mismatches == ()


def test_oferta_mexicana_es_caja_en_mxn_con_iva_declarado() -> None:
    book = load_commercial_book()
    offer = next(item for item in book.offers if item.country == "MX")

    assert offer.presentation.basis == "caja"
    assert offer.currency == "MXN"
    assert offer.tax_status == "incluido"
    assert offer.source.type == "oficial"
    assert offer.source.url is not None
    assert offer.published
    assert offer.normalized is None
    assert offer.presentation.units_in_basis > 0


def test_mexico_sin_precio_no_se_convierte_desde_colombia() -> None:
    book = load_commercial_book()
    mx = [item for item in book.offers if item.country == "MX"]

    assert {item.currency for item in mx} == {"MXN"}
    assert all(item.country == "MX" for item in mx)


def test_el_esquema_solo_admite_normalizacion_a_caja() -> None:
    basis = NormalizedPrice.model_json_schema()["properties"]["basis"]
    assert basis.get("const") == "caja"


def test_ejemplos_validan_contra_el_contrato() -> None:
    """Los archivos de ejemplo son instancias reales de ContractDocument."""

    files = sorted(EXAMPLES.glob("*.json"))
    assert files
    for path in files:
        ContractDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_el_registro_marca_igual_que_la_oferta_si_el_stock_es_simulado() -> None:
    book = load_commercial_book()

    for offer in book.offers:
        record = book.offer_record(offer, as_of=TODAY)
        assert record.availability is not None
        assert record.availability.simulated == offer.availability.simulated
