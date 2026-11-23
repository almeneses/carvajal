"""Pruebas del motor de cotización sobre el contrato publicado (spec 043, US1-US2)."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from carvajal.catalog.loader import load_catalog
from carvajal.commercial import (
    Availability,
    AvailabilityEvidence,
    DeliveryTerms,
    Presentation,
    Seller,
    Site,
    SourceRef,
    StockEvidence,
    build_commercial_book,
)
from carvajal.commercial import models as commercial_models
from carvajal.quotation import quote
from carvajal.quotation.data import (
    Dataset,
    dataset_from_book,
    load_dataset,
)

HOY = date(2026, 11, 27)
MANANA = datetime(2026, 11, 27, 9, 0, tzinfo=ZoneInfo("America/Bogota"))
HISTORIAL = ["cotízame 20 vasos para bebida caliente"]


def test_sin_ciudad_pide_la_ciudad_y_no_cotiza() -> None:
    resultado = quote("cotízame 20 vasos para bebida caliente", today=HOY, now=MANANA)

    assert resultado["kind"] == "aclaracion"
    assert "ciudad" in resultado["reply"]
    assert "primary" not in resultado
    assert "Bogotá" in resultado["cities"]


def test_cotiza_20_vasos_con_presentacion_y_sobrantes() -> None:
    resultado = quote("en Bogotá", questions=HISTORIAL, today=HOY, now=MANANA)

    assert resultado["kind"] == "cotizacion"
    principal = resultado["primary"]
    presentation = principal["presentation"]
    assert principal["sku"] == "7704918100199"
    assert presentation["basis"] == "paquete"
    assert presentation["units"] == 50
    assert presentation["units_requested"] == 20
    assert presentation["count"] == 1
    assert presentation["units_bought"] == 50
    assert presentation["units_surplus"] == 30
    assert presentation["price_per_presentation"] == "3751"
    assert presentation["subtotal"] == "3751"
    assert presentation["unit_price"] == "75.02"
    assert principal["provider"]["name"] == "EDEXA"
    assert principal["seller"] == "EDEXA"
    assert principal["source_url"].startswith("https://")
    assert principal["captured_at"] == "2026-11-18"
    assert "price_simulated" not in principal
    assert principal["stock"]["status"] in {"por_confirmar", "desconocido"}
    assert principal["stock"]["units"] is None


def test_domicilio_sin_envio_respaldado_deja_el_total_pendiente() -> None:
    resultado = quote("con domicilio", questions=[*HISTORIAL, "en Bogotá"], today=HOY, now=MANANA)

    entrega = resultado["delivery"]
    assert entrega["requested"] is True
    assert entrega["covered"] is False
    assert entrega["ship_cost"] is None
    assert resultado["total"] is None
    assert resultado["commercial"]["shipping"]["status"] == "pending"
    assert resultado["primary"]["presentation"]["subtotal"] == "3751"


def test_cantidad_seguida_recalcula_presentaciones() -> None:
    resultado = quote("mejor 100", questions=[*HISTORIAL, "en Bogotá"], today=HOY, now=MANANA)

    principal = resultado["primary"]
    assert principal["presentation"]["count"] == 2
    assert principal["presentation"]["units_bought"] == 100
    assert principal["presentation"]["units_surplus"] == 0
    assert principal["presentation"]["subtotal"] == "7502"


def test_producto_agotado_no_se_ofrece() -> None:
    resultado = quote("cotiza 20 vasos térmicos de 4 oz en Bogotá", today=HOY, now=MANANA)

    if resultado["kind"] == "cotizacion":
        assert resultado["primary"]["sku"] != "CO-VTER-04"


def test_cali_sin_precio_ofrece_las_sedes_del_directorio() -> None:
    """La ciudad sin oferta publicada conserva los contactos y no inventa precio."""

    resultado = quote("cotiza 100 vasos para bebida caliente en Cali", today=HOY, now=MANANA)

    assert resultado["kind"] == "sin_resultados"
    assert "Cali" in resultado["reply"]
    assert len(resultado["providers"]) == 4
    assert {sede["city"] for sede in resultado["providers"]} == {"Cali"}
    assert "primary" not in resultado


def test_pregunta_ajena_al_catalogo_queda_fuera_de_alcance() -> None:
    resultado = quote("¿cómo nació Carvajal?", today=HOY, now=MANANA)

    assert resultado["kind"] == "fuera_de_alcance"
    assert "chat general" in resultado["reply"]


@pytest.mark.parametrize("texto", ["cotiza 0 vasos en Bogotá", "cotiza -5 vasos en Bogotá"])
def test_cantidades_invalidas_no_producen_total(texto: str) -> None:
    resultado = quote(texto, today=HOY, now=MANANA)

    assert resultado["kind"] in {"aclaracion", "sin_resultados"}


def test_los_proveedores_llevan_coordenadas_para_el_mapa() -> None:
    resultado = quote("en Bogotá", questions=HISTORIAL, today=HOY, now=MANANA)

    assert resultado["providers"]
    assert all(sede["lat"] and sede["lon"] for sede in resultado["providers"])
    assert Decimal(resultado["primary"]["presentation"]["subtotal"]) == Decimal("3751")


def test_el_flujo_publico_no_sirve_datos_simulados() -> None:
    dataset = load_dataset(HOY.isoformat())

    assert len(dataset.offers) == 29
    assert all(offer.country == "CO" for offer in dataset.offers)
    assert all(offer.source_url.startswith("https://") for offer in dataset.offers)
    assert all(offer.source_name for offer in dataset.offers)
    assert not hasattr(dataset.offers[0], "price_simulated")
    assert not hasattr(dataset.offers[0], "stock_simulated")


def _dataset_with_evidence(
    valid_until: date | None, *, delivery: DeliveryTerms | None = None
) -> Dataset:
    reference = next(
        item
        for item in load_catalog().references
        if item.country == "CO" and item.family == "vaso" and "bebida_caliente" in item.uses
    )
    source = SourceRef(
        type="vendedor",
        name="Vendedor de prueba",
        url="https://example.com/oferta",
        captured_at=HOY,
    )
    seller = Seller(
        seller_id="CO-VEND-AAAA",
        name="Vendedor de prueba",
        country="CO",
        kind="distribuidor",
        source=SourceRef(
            type="directorio",
            name="Directorio",
            url="https://example.com/directorio",
            captured_at=HOY,
        ),
    )
    site = Site(
        site_id="CO-SEDE-AAAA",
        seller_id=seller.seller_id,
        country="CO",
        name="Sede de prueba",
        city="Bogotá",
        address="Calle 1",
        phone="300 000 0000",
        source=SourceRef(
            type="directorio",
            name="Directorio",
            url="https://example.com/directorio",
            captured_at=HOY,
        ),
    )
    product = commercial_models.Product(
        sku=reference.sku,
        country="CO",
        name=reference.name,
        family=reference.family,
        units_per_box=reference.units_per_box,
        source=SourceRef(
            type="oficial",
            name="Carvajal",
            url=reference.source_url,
            captured_at=reference.captured_at,
        ),
    )
    evidence = AvailabilityEvidence(
        kind="confirmacion_vendedor",
        recorded_by="Ana, operaciones",
        recorded_at=HOY - timedelta(days=2),
        reference="correo de confirmación",
        units=10_000,
        valid_until=valid_until,
    )
    offer = commercial_models.Offer(
        offer_id="CO-OFERTA-AAAA",
        sku=reference.sku,
        country="CO",
        seller_id=seller.seller_id,
        site_id=site.site_id,
        city="Bogotá",
        presentation=Presentation(basis="caja", units_in_basis=reference.units_per_box),
        amount=Decimal("10000"),
        currency="COP",
        tax_status="incluido",
        source=source,
        availability=Availability.verified((evidence,), units=10_000),
        delivery=delivery,
    )
    book = build_commercial_book(
        products=[product],
        sellers=[seller],
        sites=[site],
        offers=[offer],
        evidence=[
            StockEvidence(
                sku=reference.sku,
                country="CO",
                site_id=site.site_id,
                declared_status="disponible",
                evidence=evidence,
            )
        ],
    )
    return dataset_from_book(book, as_of=HOY)


def test_la_evidencia_con_valid_until_no_vencido_es_la_unica_que_confirma_stock() -> None:
    vigente = _dataset_with_evidence(HOY + timedelta(days=3))
    vencida = _dataset_with_evidence(HOY - timedelta(days=1))
    sin_vigencia = _dataset_with_evidence(None)

    assert vigente.offers[0].stock_status == "verificado"
    assert vencida.offers[0].stock_status == "por_confirmar"
    assert sin_vigencia.offers[0].stock_status == "por_confirmar"

    consulta = "cotiza 100 vasos para bebida caliente en Bogotá"
    confirmada = quote(consulta, today=HOY, now=MANANA, dataset=vigente)
    pendiente = quote(consulta, today=HOY, now=MANANA, dataset=sin_vigencia)

    assert confirmada["primary"]["stock"]["status"] == "verificado"
    assert pendiente["primary"]["stock"]["status"] == "por_confirmar"


def test_300_vasos_son_seis_paquetes_sin_sobrante() -> None:
    resultado = quote("cotiza 300 vasos para bebida caliente en Bogotá", today=HOY, now=MANANA)

    presentation = resultado["primary"]["presentation"]
    assert presentation["count"] == 6
    assert presentation["units_bought"] == 300
    assert presentation["units_surplus"] == 0
    assert presentation["price_per_presentation"] == "3751"
    assert presentation["subtotal"] == "22506"


def test_301_vasos_son_siete_paquetes_con_49_de_sobrante() -> None:
    resultado = quote("cotiza 301 vasos para bebida caliente en Bogotá", today=HOY, now=MANANA)

    presentation = resultado["primary"]["presentation"]
    assert presentation["count"] == 7
    assert presentation["units_bought"] == 350
    assert presentation["units_surplus"] == 49
    assert presentation["subtotal"] == "26257"


def test_el_precio_por_presentacion_se_conserva_separado_del_subtotal() -> None:
    resultado = quote("en Bogotá", questions=HISTORIAL, today=HOY, now=MANANA)

    commercial = resultado["commercial"]
    assert commercial["presentation"]["price_per_presentation"] == "3751"
    assert commercial["presentation"]["subtotal"] == "3751"
    assert commercial["subtotal"] == "3751"
    assert Decimal(commercial["total"]) == Decimal(commercial["subtotal"])


def test_sin_vigencia_comercial_no_se_inventa_fecha() -> None:
    resultado = quote("en Bogotá", questions=HISTORIAL, today=HOY, now=MANANA)

    assert resultado["primary"]["commercial_valid_until"] is None
    assert resultado["commercial"]["valid_until"] is None
    assert "Vigencia publicada" not in resultado["reply"]


def test_envio_con_costo_cero_respaldado_cierra_el_total() -> None:
    delivery = DeliveryTerms(
        lead_days_min=1,
        lead_days_max=2,
        ship_cost=Decimal("0"),
        checked_at=HOY - timedelta(days=1),
        valid_until=HOY + timedelta(days=7),
        source_url="https://example.com/envio",
    )
    dataset = _dataset_with_evidence(None, delivery=delivery)

    resultado = quote(
        "cotiza 100 vasos para bebida caliente en Bogotá con domicilio",
        today=HOY,
        now=MANANA,
        dataset=dataset,
    )

    shipping = resultado["commercial"]["shipping"]
    assert shipping["status"] == "confirmed"
    assert shipping["cost"] == "0"
    assert shipping["free"] is True
    assert Decimal(resultado["commercial"]["total"]) == Decimal(resultado["commercial"]["subtotal"])
    assert resultado["delivery"]["covered"] is True


def test_envio_sin_precio_deja_el_total_pendiente() -> None:
    delivery = DeliveryTerms(
        lead_days_min=1,
        lead_days_max=2,
        ship_cost=None,
        checked_at=HOY - timedelta(days=1),
        valid_until=HOY + timedelta(days=7),
        source_url="https://example.com/envio",
    )
    dataset = _dataset_with_evidence(None, delivery=delivery)

    resultado = quote(
        "cotiza 100 vasos para bebida caliente en Bogotá con domicilio",
        today=HOY,
        now=MANANA,
        dataset=dataset,
    )

    shipping = resultado["commercial"]["shipping"]
    assert shipping["status"] == "pending"
    assert shipping["cost"] is None
    assert shipping["source_url"] == "https://example.com/envio"
    assert resultado["commercial"]["total"] is None
    assert resultado["total"] is None


def test_envio_con_evidencia_vencida_no_confirma_costo() -> None:
    delivery = DeliveryTerms(
        lead_days_min=1,
        lead_days_max=2,
        ship_cost=Decimal("12000"),
        checked_at=HOY - timedelta(days=10),
        valid_until=HOY - timedelta(days=1),
        source_url="https://example.com/envio",
    )
    dataset = _dataset_with_evidence(None, delivery=delivery)

    resultado = quote(
        "cotiza 100 vasos para bebida caliente en Bogotá con domicilio",
        today=HOY,
        now=MANANA,
        dataset=dataset,
    )

    assert resultado["commercial"]["shipping"]["status"] == "pending"
    assert resultado["commercial"]["total"] is None


def test_una_solicitud_de_varias_lineas_conserva_las_pendientes() -> None:
    resultado = quote(
        "cotiza 20 vasos y 10 platos para comida caliente en Bogotá",
        today=HOY,
        now=MANANA,
    )

    assert resultado["kind"] == "cotizacion"
    assert [item["family"] for item in resultado["pending_lines"]] == ["plato"]
    assert resultado["pending_lines"][0]["status"] == "pendiente"
    assert "Quedan líneas pendientes" in resultado["reply"]


def test_el_precio_viejo_se_usa_como_normal() -> None:
    from datetime import date as fecha

    resultado = quote(
        "en Bogotá",
        questions=HISTORIAL,
        today=fecha(2027, 4, 1),
        now=datetime(2027, 4, 1, 9, 0, tzinfo=ZoneInfo("America/Bogota")),
    )

    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "7704918100199"
    assert resultado["primary"]["captured_at"] == "2026-11-18"
    assert "stale" not in resultado["primary"]
    assert resultado["primary"]["presentation"]["subtotal"] == "3751"


def test_el_perfil_recuerda_ciudad_domicilio_y_nombre() -> None:
    perfil = {"name": "Ana", "city": "Bogotá", "delivery": True, "preferences": []}

    resultado = quote("cotiza 20 vasos para bebida caliente", today=HOY, now=MANANA, profile=perfil)

    assert resultado["kind"] == "cotizacion"
    assert resultado["city"] == "Bogotá"
    assert resultado["delivery"]["requested"] is True
    assert resultado["reply"].startswith("Listo, Ana.")


def test_la_ciudad_dicha_gana_sobre_la_recordada() -> None:
    perfil = {"name": None, "city": "Bogotá", "delivery": False, "preferences": []}

    resultado = quote("cotiza 20 vasos calientes en Cali", today=HOY, now=MANANA, profile=perfil)

    assert resultado["kind"] == "sin_resultados"
    assert {sede["city"] for sede in resultado["providers"]} == {"Cali"}
    assert resultado["request"]["city"] == "Cali"


def test_la_preferencia_renovable_se_aplica() -> None:
    perfil = {
        "name": None,
        "city": "Bogotá",
        "delivery": False,
        "preferences": ["materiales renovables"],
    }

    resultado = quote("cotiza 20 vasos calientes", today=HOY, now=MANANA, profile=perfil)

    assert resultado["request"]["prefers_renewable"] is True


def test_la_oferta_conserva_su_sede_estable_y_su_vendedor() -> None:
    resultado = quote("en Bogotá", questions=HISTORIAL, today=HOY, now=MANANA)

    assert resultado["primary"]["provider"]["id"].startswith("CO-SEDE-")
    assert "ficha" not in resultado["primary"]["provider"]
    assert all("id" in sede and "name" in sede for sede in resultado["providers"])


def test_un_saludo_no_repite_la_ultima_cotizacion() -> None:
    resultado = quote(
        "hola", questions=["cotiza 20 vasos calientes en Bogotá"], today=HOY, now=MANANA
    )

    assert resultado["kind"] == "aclaracion"
    assert resultado["reply"].startswith("Hola.")
    assert "primary" not in resultado


def test_un_agradecimiento_no_repite_la_cotizacion() -> None:
    resultado = quote(
        "gracias", questions=["cotiza 20 vasos calientes en Bogotá"], today=HOY, now=MANANA
    )

    assert resultado["kind"] == "aclaracion"
    assert "primary" not in resultado


def test_un_seguimiento_sigue_cotizando() -> None:
    for text in ("mejor 100", "con domicilio", "100"):
        resultado = quote(
            text, questions=["cotiza 20 vasos calientes en Bogotá"], today=HOY, now=MANANA
        )
        assert resultado["kind"] == "cotizacion", text

    sin_precio = quote(
        "en Cali", questions=["cotiza 20 vasos calientes en Bogotá"], today=HOY, now=MANANA
    )
    assert sin_precio["kind"] == "sin_resultados"
    assert sin_precio["providers"]
