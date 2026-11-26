"""Pruebas del motor de cotización sobre el contrato publicado (spec 043, US1-US2)."""

from dataclasses import replace
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
from carvajal.quotation import QuoteRequest, quote, quote_request
from carvajal.quotation.data import (
    REFERENCE_SITE_ID,
    Dataset,
    dataset_from_book,
    load_dataset,
)
from carvajal.quotation.engine import OUT_OF_SCOPE

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


def test_cali_sin_precio_publicado_cotiza_el_de_referencia_y_da_las_sedes() -> None:
    """Sin oferta publicada en Cali: precio de referencia sin vendedor, con los contactos."""

    resultado = quote("cotiza 100 vasos para bebida caliente en Cali", today=HOY, now=MANANA)

    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["price_kind"] == "convertido"
    assert resultado["primary"]["provider"]["phone"] is None
    assert len(resultado["providers"]) == 4
    assert {sede["city"] for sede in resultado["providers"]} == {"Cali"}


def test_referencia_nombrada_sin_precio_en_la_ciudad_ofrece_las_sedes() -> None:
    """Una referencia propia sin oferta en Cali no se sustituye ni recibe precio inventado."""

    resultado = quote_request(
        QuoteRequest(sku="CO-VTER-08", quantity=100, city="Cali", delivery=False),
        today=HOY,
        now=MANANA,
    )

    assert resultado["kind"] == "sin_resultados"
    assert resultado["reason"] == "sin_precio"
    assert {sede["city"] for sede in resultado["providers"]} == {"Cali"}
    assert "primary" not in resultado


def test_el_precio_de_referencia_se_cotiza_en_bogota_sin_nombrar_su_origen() -> None:
    """Solo hay precio de referencia: sin vendedor ni sede, con los contactos de Bogotá."""

    resultado = quote("cotiza 2000 cubiertos en Bogotá", today=HOY, now=MANANA)

    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["price_kind"] == "convertido"
    assert resultado["primary"]["provider"]["address"] is None
    assert resultado["primary"]["provider"]["phone"] is None
    assert resultado["primary"]["tax_status"] == "no_indicado"
    assert "Precio de referencia del catálogo" in resultado["reply"]
    assert "México" not in resultado["reply"] and "convertid" not in resultado["reply"]
    assert "México" not in resultado["primary"]["warning"]
    assert {sede["city"] for sede in resultado["providers"]} == {"Bogotá"}


def test_un_precio_publicado_va_antes_que_uno_convertido_mas_barato() -> None:
    """La convertida nunca desplaza a la publicada, aunque su subtotal sea menor."""

    for cantidad in (100, 1000, 20000):
        resultado = quote(
            f"cotiza {cantidad} vasos para bebida caliente en Bogotá", today=HOY, now=MANANA
        )
        assert resultado["primary"]["price_kind"] == "publicado"
        kinds = [item["price_kind"] for item in resultado["alternatives"]]
        assert kinds == sorted(kinds, reverse=True)  # publicado antes que convertido


def test_el_envio_de_un_precio_convertido_queda_pendiente() -> None:
    resultado = quote("cotiza 2000 cubiertos en Bogotá con domicilio", today=HOY, now=MANANA)

    assert resultado["total"] is None
    assert "distribuidor" in resultado["commercial"]["shipping"]["note"]


def test_pregunta_ajena_al_catalogo_queda_fuera_de_alcance() -> None:
    resultado = quote("¿cómo nació Carvajal?", today=HOY, now=MANANA)

    assert resultado["kind"] == "fuera_de_alcance"
    assert resultado["reply"] == OUT_OF_SCOPE


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

    assert len([offer for offer in dataset.offers if not offer.converted]) == 29
    assert len({offer.sku for offer in dataset.offers if offer.converted}) == 98
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

    assert resultado["kind"] == "cotizacion"
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

    en_cali = quote(
        "en Cali", questions=["cotiza 20 vasos calientes en Bogotá"], today=HOY, now=MANANA
    )
    assert en_cali["kind"] == "cotizacion"
    assert en_cali["city"] == "Cali"
    assert en_cali["providers"]


# Corrección «tinto» (spec 044, T006): las bebidas calientes por nombre implican temperatura.
BEBIDAS_CALIENTES = [
    "2.000 vasos de 7 onzas para tinto, Bogotá",
    "necesito unos 2 mil vasitos para tinto en Bogotá",
    "dos mil vasos para café caliente de 7 oz en Bogotá",
    "vasos para aromática, 300, Cali",
    "chocolate caliente, 200 vasos en Pasto",
]


@pytest.mark.parametrize("texto", BEBIDAS_CALIENTES)
def test_una_bebida_caliente_nunca_cotiza_un_vaso_para_frio(texto: str) -> None:
    catalogo = {item.sku: item for item in load_dataset(HOY.isoformat()).products}

    resultado = quote(texto, today=HOY, now=MANANA)

    assert resultado["kind"] in {"cotizacion", "sin_resultados", "aclaracion"}
    if resultado["kind"] == "cotizacion":
        assert resultado["primary"]["sku"] != "CO-VRP-07"
        assert "bebida_caliente" in catalogo[resultado["primary"]["sku"]].uses
    assert resultado["request"]["temperature"] == "caliente"


def test_el_tinto_de_7_onzas_no_cotiza_el_vaso_de_pp_para_frio() -> None:
    resultado = quote("2.000 vasos de 7 onzas para tinto, Bogotá", today=HOY, now=MANANA)

    assert resultado["kind"] == "sin_resultados"
    assert resultado["request"]["use"] == "bebida_caliente"
    assert "primary" not in resultado


@pytest.mark.parametrize(
    ("texto", "temperatura"),
    [
        ("cotiza 20 vasos para tinto en Bogotá", "caliente"),
        ("cotiza 20 vasos para tintos en Bogotá", "caliente"),
        ("cotiza 20 vasitos para aromáticas en Bogotá", "caliente"),
        ("cotiza 20 vasos para chocolate en Bogotá", "caliente"),
        ("cotiza 20 vasos para cafecito en Bogotá", "caliente"),
        ("cotiza 20 vasos para capuchino en Bogotá", "caliente"),
        ("cotiza 20 vasos para cappuccino en Bogotá", "caliente"),
        ("cotiza 20 vasos para espresso en Bogotá", "caliente"),
        ("cotiza 20 vasos para té de hierbas en Bogotá", "caliente"),
        ("cotiza 20 vasos para té caliente en Bogotá", "caliente"),
        ("cotiza 20 vasos para chocolate frío en Bogotá", "fria"),
        ("cotiza 20 vasos para vino tinto en Bogotá", None),
        ("cotiza 20 vasos para vinos tintos en Bogotá", None),
        ("cotiza 20 vasos para vino  tinto en Bogotá", None),
    ],
)
def test_la_bebida_nombrada_deduce_la_temperatura(texto: str, temperatura: str | None) -> None:
    resultado = quote(texto, today=HOY, now=MANANA)

    assert resultado["request"]["temperature"] == temperatura


def test_el_uso_de_un_turno_intermedio_cambia_la_familia_de_la_cadena() -> None:
    # Arrange: «necesito vasos» → «para sopa» → «500 unidades en Cali» (mejora 4 del tercer
    # ensayo): el turno actual no nombra el uso, pero la sopa del turno intermedio ya cambió
    # la familia; mezclar la cadena no debe volver a «vasos para sopa».
    historial = ["necesito vasos", "para sopa"]

    # Act
    resultado = quote("500 unidades en Cali", questions=historial, today=HOY, now=MANANA)

    # Assert
    solicitud = resultado["request"]
    assert (solicitud["family"], solicitud["use"]) == ("contenedor", "sopas")
    assert solicitud["temperature"] == "caliente"


# Uso declarado (mejora 3 del 2026-11-26): sin uso pedido, la referencia que declara algún
# uso de la temperatura pedida va antes; declarar además otro uso no resta aptitud (spec 054:
# entre igual de aptas, la más económica).


def _dataset_de_usos(*filas: tuple[str, str, str, Decimal, bool]) -> Dataset:
    """Vasos sintéticos de 9 oz en Bogotá: ``(sku, usos, material, precio, convertido)``.

    Copian la ficha y la oferta publicada del vaso de PP de 9 oz; el precio es el del
    paquete de 50 y ``convertido`` la vuelve un precio de referencia sin vendedor.
    """

    real = load_dataset(HOY.isoformat())
    base = real.product("7704918100199")
    assert base is not None
    [oferta] = [item for item in real.offers_for(base.sku) if item.city == "Bogotá"]
    products, offers = [], []
    for sku, usos, material, precio, convertido in filas:
        products.append(base.model_copy(update={"sku": sku, "use": usos, "material": material}))
        offers.append(
            replace(
                oferta,
                sku=sku,
                amount=precio,
                site_id=REFERENCE_SITE_ID if convertido else oferta.site_id,
            )
        )
    return Dataset(products=tuple(products), offers=tuple(offers), providers=real.providers)


def _cotiza_vasos(dataset: Dataset, **campos: object) -> dict:
    solicitud = QuoteRequest(family="vaso", quantity=1000, city="Bogotá", delivery=False)
    return quote_request(replace(solicitud, **campos), today=HOY, now=MANANA, dataset=dataset)


def test_declarar_tambien_bebida_fria_no_resta_aptitud_para_cafe() -> None:
    # Arrange: las dos declaran bebida caliente; la que declara además fría es más barata.
    dataset = _dataset_de_usos(
        ("T-VERSATIL", "bebida_fria|bebida_caliente", "PP", Decimal("3000"), False),
        ("T-CALIENTE", "bebida_caliente|en_tu_negocio", "PP", Decimal("4000"), False),
    )

    # Act
    resultado = _cotiza_vasos(dataset, use="bebida_caliente", temperature="caliente")

    # Assert: igual de aptas, decide el precio.
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "T-VERSATIL"
    assert [item["sku"] for item in resultado["alternatives"]] == ["T-CALIENTE"]
    assert "Por ser la más apta para el uso" not in resultado["reply"]


def test_declarar_tambien_comida_caliente_no_resta_aptitud_para_frio() -> None:
    # Arrange: el térmico (fría y comida caliente) es más barato que el de solo fría.
    dataset = _dataset_de_usos(
        ("T-TERMICO", "bebida_fria|comida_caliente", "PP", Decimal("3000"), False),
        ("T-FRIA", "bebida_fria|fiestas_y_eventos", "PP", Decimal("4000"), False),
    )

    # Act
    resultado = _cotiza_vasos(dataset, use="bebida_fria", temperature="fria")

    # Assert
    assert resultado["primary"]["sku"] == "T-TERMICO"
    assert [item["sku"] for item in resultado["alternatives"]] == ["T-FRIA"]


def test_sin_uso_la_temperatura_pedida_ordena_por_el_uso_declarado() -> None:
    # Arrange: sin uso, solo la temperatura; la que no declara ninguna bebida es más barata.
    dataset = _dataset_de_usos(
        ("T-SIN-USO", "en_tu_negocio", "PP", Decimal("2000"), False),
        ("T-VERSATIL", "bebida_fria|bebida_caliente", "PP", Decimal("3000"), False),
        ("T-CALIENTE", "bebida_caliente", "PP", Decimal("4000"), False),
    )

    # Act
    resultado = _cotiza_vasos(dataset, temperature="caliente")

    # Assert: primero las que declaran un uso caliente, por precio; al final la que no.
    orden = [resultado["primary"]["sku"], *(item["sku"] for item in resultado["alternatives"])]
    assert orden == ["T-VERSATIL", "T-CALIENTE", "T-SIN-USO"]


def test_el_uso_declarado_no_salta_la_regla_del_precio_publicado() -> None:
    # Arrange: la que declara el uso caliente solo tiene precio convertido; la publicada no
    # declara ninguno.
    dataset = _dataset_de_usos(
        ("T-SIN-USO", "en_tu_negocio", "PP", Decimal("4000"), False),
        ("T-CALIENTE-REF", "bebida_caliente", "PAPEL", Decimal("2000"), True),
        ("T-SIN-USO-REF", "en_tu_negocio", "PP", Decimal("1000"), True),
    )

    # Act
    resultado = _cotiza_vasos(dataset, temperature="caliente")

    # Assert: publicada primero; entre las convertidas, la que declara el uso.
    assert resultado["primary"]["sku"] == "T-SIN-USO"
    assert resultado["primary"]["price_kind"] == "publicado"
    assert [item["sku"] for item in resultado["alternatives"]] == [
        "T-CALIENTE-REF",
        "T-SIN-USO-REF",
    ]


def test_sin_uso_ni_temperatura_manda_el_precio() -> None:
    # Arrange: con «caliente» la de comida caliente iría primero; sin uso ni temperatura, no.
    dataset = _dataset_de_usos(
        ("T-SIN-USO", "en_tu_negocio", "PP", Decimal("3000"), False),
        ("T-COMIDA", "comida_caliente|en_tu_negocio", "PP", Decimal("4000"), False),
    )

    # Act
    sin_temperatura = _cotiza_vasos(dataset)
    caliente = _cotiza_vasos(dataset, temperature="caliente")

    # Assert
    assert sin_temperatura["primary"]["sku"] == "T-SIN-USO"
    assert caliente["primary"]["sku"] == "T-COMIDA"


def test_cafe_en_bogota_con_el_catalogo_real_recomienda_el_pp_de_9_oz_publicado() -> None:
    """Los vasos de papel «Bebida Caliente» solo tienen precio convertido en Bogotá.

    El vaso de PP traslúcido de 9 oz declara bebida fría y caliente y tiene precio
    publicado: sigue de recomendado (regla del 2026-11-25) y los de papel van de
    alternativa con precio de referencia.
    """

    # Arrange
    catalogo = {item.sku: item for item in load_dataset(HOY.isoformat()).products}

    # Act
    resultado = quote("necesito 1000 vasos para café en Bogotá", today=HOY, now=MANANA)

    # Assert
    assert resultado["primary"]["sku"] == "7704918100199"
    assert resultado["primary"]["price_kind"] == "publicado"
    papel = [i for i in resultado["alternatives"] if catalogo[i["sku"]].material == "PAPEL"]
    assert papel
    assert all(item["price_kind"] == "convertido" for item in papel)


@pytest.mark.parametrize(
    ("texto", "sku"),
    [
        # Térmicos (bebida fría y comida caliente) más baratos que el PS de 2 oz «Bio».
        ("necesito 1000 vasos para jugo en Barranquilla", "CO-MX-2864108000"),
        ("necesito 1000 vasos para bebida fría en Pereira", "CO-MX-2864112370"),
        ("necesito 1000 vasos de 12 oz para bebida fría en Bogotá", "CO-MX-2864112370"),
    ],
)
def test_vasos_frios_con_el_catalogo_real_recomiendan_el_mas_barato_apto(
    texto: str, sku: str
) -> None:
    # Act
    resultado = quote(texto, today=HOY, now=MANANA)

    # Assert: declarar también comida caliente no lo baja de recomendado.
    assert resultado["primary"]["sku"] == sku
    assert "Por ser la más apta para el uso" not in resultado["reply"]


def test_cinco_cajas_de_vasos_frios_recomiendan_el_pp_de_9_oz() -> None:
    # Arrange: sin capacidad; el Gold de 14 oz solo declara bebida fría y cuesta unas cinco
    # veces más por unidad.
    solicitud = QuoteRequest(
        family="vaso", temperature="fria", unit="caja", quantity=5, city="Bogotá"
    )

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["primary"]["sku"] == "7704918100199"
    assert "CO-VGOLD-14" in [item["sku"] for item in resultado["alternatives"]]
