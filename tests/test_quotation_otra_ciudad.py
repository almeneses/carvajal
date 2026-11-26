"""Precio publicado en otra ciudad (mejora 5 del ensayo, 2026-11-26).

Si la referencia no tiene oferta en la ciudad pedida pero un vendedor publica su precio en
otra ciudad de Colombia, el motor cotiza ese precio publicado antes que un precio convertido,
rotulado con su vendedor y su ciudad, y deja las sedes de la ciudad pedida como dónde
consultarla. Las pruebas usan un dataset sintético: fichas reales del catálogo con ofertas y
sedes inventadas para la prueba.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fpdf import FPDF

from apps.api.quote_pdf import build_quote_pdf
from apps.api.quote_view import QuoteLine, quote_view
from apps.mcp.bridge import spoken_quote, whatsapp_quote
from carvajal.commercial import DeliveryTerms
from carvajal.quotation import QuoteRequest, quote_request, render
from carvajal.quotation.data import REFERENCE_SITE_ID, Dataset, Offer, Provider, load_dataset
from carvajal.quotation.draft import QuoteDraft, add_line
from carvajal.quotation.render import option_rows

HOY = date(2026, 11, 27)
MANANA = datetime(2026, 11, 27, 9, 0, tzinfo=ZoneInfo("America/Bogota"))
VASO_9 = "7704918100199"  # vaso rígido traslúcido 9 oz, apto para caliente
VASO_14 = "CO-VRG-14"  # vaso rígido traslúcido 14 oz, apto para caliente
VASO_MX = "CO-MX-2864632247"  # copia de México con precio convertido


def _provider(provider_id: str, name: str, city: str) -> Provider:
    return Provider(
        provider_id=provider_id,
        name=name,
        city=city,
        address=f"Calle 1 # 2-3, {city}",
        phone="300 000 0000",
        latitude=4.6,
        longitude=-74.0,
        precision="direccion",
        source_url="https://example.com/directorio",
        captured_at=HOY,
    )


BOGOTA = _provider("CO-SEDE-BOG", "Vendedor Capital", "Bogotá")
CALI = _provider("CO-SEDE-CAL", "Distribuidora del Valle", "Cali")
CALI_2 = _provider("CO-SEDE-CAL2", "Empaques Pacífico", "Cali")
MEDELLIN = _provider("CO-SEDE-MED", "Antioquia Empaques", "Medellín")


def _offer(sku: str, site: Provider, amount: str, *, basis: str = "paquete", units: int = 50):
    return Offer(
        sku=sku,
        country="CO",
        site_id=site.provider_id,
        provider=site.name,
        city=site.city,
        basis=basis,
        units_in_basis=units,
        amount=Decimal(amount),
        currency="COP",
        tax_status="incluido",
        tax_rate=None,
        stock_status="por_confirmar",
        stock_units=None,
        source_name=site.name,
        source_url=f"https://example.com/{sku}",
        source_valid_until=None,
        captured_at=HOY,
    )


def _converted(sku: str, city: str, amount: str) -> Offer:
    return replace(
        _offer(sku, BOGOTA, amount, basis="caja", units=1000),
        site_id=REFERENCE_SITE_ID,
        provider="Precio de referencia del catálogo",
        source_name="Precio de referencia del catálogo",
        city=city,
        tax_status="no_indicado",
        stock_status="desconocido",
    )


def _dataset(*offers: Offer) -> Dataset:
    real = load_dataset(HOY.isoformat())
    products = tuple(
        product for product in real.products if product.sku in {VASO_9, VASO_14, VASO_MX}
    )
    return Dataset(products=products, offers=offers, providers=(BOGOTA, CALI, CALI_2, MEDELLIN))


def _cotizar(dataset: Dataset, *, city: str, delivery: bool | None = False) -> dict[str, Any]:
    """100 vasos para bebida caliente en ``city``."""

    request = QuoteRequest(
        family="vaso",
        use="bebida_caliente",
        temperature="caliente",
        quantity=100,
        city=city,
        delivery=delivery,
    )
    return quote_request(request, today=HOY, now=MANANA, dataset=dataset)


def test_sin_precio_en_la_ciudad_cotiza_el_publicado_en_otra_antes_que_el_convertido() -> None:
    # Arrange: en Cali solo hay un precio convertido, más barato; en Bogotá, uno publicado.
    dataset = _dataset(
        _offer(VASO_9, BOGOTA, "4000"),
        _converted(VASO_MX, "Cali", "1000"),
    )

    # Act
    resultado = _cotizar(dataset, city="Cali")

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["city"] == "Cali"
    principal = resultado["primary"]
    assert principal["sku"] == VASO_9
    assert principal["price_kind"] == "publicado"
    assert principal["price_city"] == "Bogotá"
    assert principal["seller"] == "Vendedor Capital"
    assert principal["provider"]["city"] == "Bogotá"
    assert principal["price_origin"] == (
        "Precio publicado por Vendedor Capital en Bogotá; en Cali no hay precio publicado de "
        "esta referencia."
    )
    assert principal["price_origin"] in principal["warning"]
    [alternativa] = resultado["alternatives"]
    assert alternativa["price_kind"] == "convertido"
    assert alternativa["price_origin"] is None
    # Dónde consultarla: las sedes de la ciudad pedida, no la del vendedor.
    assert [sede["id"] for sede in resultado["providers"]] == ["CO-SEDE-CAL", "CO-SEDE-CAL2"]
    assert "Precio publicado por Vendedor Capital en Bogotá" in resultado["reply"]
    assert "sedes de Cali" in resultado["reply"]
    # No es la más económica (la convertida es más barata): se recomienda, no se dice eso.
    assert "La más económica" not in resultado["reply"]
    assert "Te recomiendo" in resultado["reply"]


def test_un_precio_publicado_en_la_ciudad_va_antes_que_uno_de_otra_ciudad() -> None:
    # Arrange: el de 14 oz tiene precio en Cali; el de 9 oz, más barato, solo en Bogotá.
    dataset = _dataset(
        _offer(VASO_9, BOGOTA, "1000"),
        _offer(VASO_14, CALI, "9000", units=25),
    )

    # Act
    resultado = _cotizar(dataset, city="Cali")

    # Assert
    assert resultado["primary"]["sku"] == VASO_14
    assert resultado["primary"]["price_city"] == "Cali"
    assert resultado["primary"]["price_origin"] is None
    assert [item["sku"] for item in resultado["alternatives"]] == [VASO_9]
    assert resultado["alternatives"][0]["price_city"] == "Bogotá"
    assert {sede["id"] for sede in resultado["providers"]} == {"CO-SEDE-CAL"}
    assert "Te recomiendo" in resultado["reply"]
    assert "Precio publicado por" not in resultado["reply"]


def test_entre_varias_ciudades_se_usa_el_precio_publicado_mas_barato() -> None:
    # Arrange
    dataset = _dataset(
        _offer(VASO_9, BOGOTA, "5000"),
        _offer(VASO_9, MEDELLIN, "4500"),
    )

    # Act
    resultado = _cotizar(dataset, city="Cali")

    # Assert
    assert resultado["primary"]["price_city"] == "Medellín"
    assert resultado["primary"]["seller"] == "Antioquia Empaques"
    assert resultado["primary"]["presentation"]["subtotal"] == "9000"


def test_el_envio_desde_otra_ciudad_queda_pendiente_aunque_el_vendedor_publique_costo() -> None:
    # Arrange: el vendedor de Bogotá publica envío gratis, pero nada dice que llegue a Cali.
    envio = DeliveryTerms(
        lead_days_min=1,
        lead_days_max=2,
        ship_cost=Decimal("0"),
        checked_at=HOY - timedelta(days=1),
        valid_until=HOY + timedelta(days=7),
        source_url="https://example.com/envio",
    )
    dataset = _dataset(replace(_offer(VASO_9, BOGOTA, "4000"), delivery=envio))

    # Act
    resultado = _cotizar(dataset, city="Cali", delivery=True)

    # Assert
    envio_cotizado = resultado["commercial"]["shipping"]
    assert envio_cotizado["status"] == "pending"
    assert envio_cotizado["cost"] is None
    assert envio_cotizado["note"] == (
        "Vendedor Capital publica el precio en Bogotá y no publica envío a Cali: el envío lo "
        "confirma el vendedor."
    )
    assert resultado["total"] is None
    assert resultado["delivery"]["covered"] is False


def test_sin_domicilio_definido_el_envio_no_entra_en_la_linea() -> None:
    # Arrange: spec 054, decisión 4: el envío solo va en el resumen.
    dataset = _dataset(_offer(VASO_9, BOGOTA, "4000"))

    # Act
    resultado = _cotizar(dataset, city="Cali", delivery=None)

    # Assert
    assert resultado["commercial"]["shipping"]["status"] == "pending"
    assert "sin envío; el envío va en el resumen" in resultado["reply"]
    assert "envío a Cali" not in resultado["reply"]


def test_una_ciudad_sin_sedes_no_recibe_precios_de_otra() -> None:
    # Arrange
    dataset = _dataset(_offer(VASO_9, BOGOTA, "4000"))

    # Act
    resultado = _cotizar(dataset, city="Leticia")

    # Assert
    assert resultado["kind"] == "sin_resultados"
    assert "primary" not in resultado


def test_un_precio_convertido_de_otra_ciudad_nunca_se_usa() -> None:
    # Arrange: solo hay precio de referencia en Bogotá.
    dataset = _dataset(_converted(VASO_MX, "Bogotá", "1000"))

    # Act
    resultado = _cotizar(dataset, city="Cali")

    # Assert
    assert resultado["kind"] == "sin_resultados"
    assert {sede["city"] for sede in resultado["providers"]} == {"Cali"}


def test_la_referencia_nombrada_se_cotiza_con_el_precio_de_otra_ciudad_sin_sustituirla() -> None:
    # Arrange
    dataset = _dataset(
        _offer(VASO_9, BOGOTA, "4000"),
        _offer(VASO_14, CALI, "1000", units=25),
    )

    # Act
    resultado = quote_request(
        QuoteRequest(sku=VASO_9, quantity=100, city="Cali", delivery=False),
        today=HOY,
        now=MANANA,
        dataset=dataset,
    )

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == VASO_9
    assert resultado["primary"]["price_city"] == "Bogotá"
    assert resultado["alternatives"] == []
    assert "Cotizo la referencia que pediste" in resultado["reply"]


def test_las_plantillas_dicen_quien_publica_el_precio_y_donde() -> None:
    # Arrange
    dataset = _dataset(
        _offer(VASO_9, BOGOTA, "4000"),
        _offer(VASO_14, BOGOTA, "9000", units=25),
    )
    resultado = _cotizar(dataset, city="Cali")
    draft, _ = add_line(QuoteDraft(), resultado)

    # Act
    opciones = render.options(resultado)
    agregado = render.added(draft, [draft.lines[-1]])
    resumen = render.summary(draft)

    # Assert
    assert "📍 Precio publicado por Vendedor Capital en Bogotá" in opciones.text
    assert "(precio de Bogotá)" in opciones.text  # la alternativa también
    assert "Es el precio publicado en Bogotá." in opciones.spoken
    assert "📍 Cali · Vendedor Capital (precio de Bogotá)" in agregado.text
    assert "📍 Cali · Vendedor Capital (precio de Bogotá)" in resumen.text
    assert len(opciones.spoken.split()) <= 45


def _pdf_texts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Textos que el PDF escribe con ``multi_cell`` (la fuente embebida no deja leerlos)."""

    texts: list[str] = []
    original = FPDF.multi_cell

    def spy(self: FPDF, w: float, h: float | None = None, text: str = "", *args, **kwargs):
        texts.append(text)
        return original(self, w, h, text, *args, **kwargs)

    monkeypatch.setattr(FPDF, "multi_cell", spy)
    return texts


def test_whatsapp_y_el_pdf_rotulan_la_ciudad_del_precio(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    dataset = _dataset(_offer(VASO_9, BOGOTA, "4000"))
    resultado = _cotizar(dataset, city="Cali")
    textos = _pdf_texts(monkeypatch)

    # Act
    view = quote_view(resultado)
    assert view is not None
    bloque = whatsapp_quote(view)
    documento = build_quote_pdf(view)

    # Assert
    assert view.city == "Cali"
    assert view.price_city == "Bogotá"
    assert view.price_origin.startswith("Precio publicado por Vendedor Capital en Bogotá")
    assert "📍 Cali · Vendedor Capital (precio de Bogotá)" in bloque
    assert documento.startswith(b"%PDF-")
    assert "Bogotá (precio publicado allí)" in textos


def test_el_pdf_de_varios_productos_dice_la_ciudad_de_cada_precio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    dataset = _dataset(_offer(VASO_9, BOGOTA, "4000"))
    view = quote_view(_cotizar(dataset, city="Cali"))
    assert view is not None
    lineas = (
        QuoteLine(product="Vaso 9 oz", seller="Vendedor Capital", price_city="Bogotá",
                  price_origin="Precio publicado por Vendedor Capital en Bogotá."),
        QuoteLine(product="Vaso 14 oz", seller="Distribuidora del Valle"),
    )  # fmt: skip
    textos = _pdf_texts(monkeypatch)

    # Act
    documento = build_quote_pdf(replace(view, lines=lineas))

    # Assert
    assert documento.startswith(b"%PDF-")
    assert "   Vendedor Capital en Bogotá" in textos
    assert "   Distribuidora del Valle" in textos


def test_la_voz_dice_la_ciudad_del_precio() -> None:
    # Arrange: la advertencia con el rótulo es larga y la voz no la lee.
    dataset = _dataset(_offer(VASO_9, BOGOTA, "4000"))
    view = quote_view(_cotizar(dataset, city="Cali"))
    assert view is not None

    # Act
    voz = spoken_quote(view).text

    # Assert
    assert "en Cali" in voz
    assert "Es el precio que publica Vendedor Capital en Bogotá." in voz


def test_el_comparativo_de_cantidades_dice_la_ciudad_del_precio() -> None:
    # Arrange
    dataset = _dataset(_offer(VASO_9, BOGOTA, "4000"))
    cotizaciones = [
        quote_request(
            QuoteRequest(sku=VASO_9, quantity=cantidad, city="Cali", delivery=False),
            today=HOY,
            now=MANANA,
            dataset=dataset,
        )
        for cantidad in (100, 500)
    ]

    # Act
    mensaje = render.comparison(cotizaciones)

    # Assert
    assert "📍 Precio publicado por Vendedor Capital en Bogotá" in mensaje.text
    assert mensaje.text.count("📍") == 1
    assert "Son precios publicados en Bogotá." in mensaje.spoken


def test_la_opcion_mas_economica_dice_la_ciudad_del_precio() -> None:
    # Arrange
    dataset = _dataset(
        _offer(VASO_9, BOGOTA, "4000"),
        _offer(VASO_14, BOGOTA, "9000", units=25),
    )
    filas = option_rows(_cotizar(dataset, city="Cali"))

    # Act
    mensaje = render.cheaper_offer(filas[1])

    # Assert
    assert ", precio publicado en Bogotá." in mensaje.text
    assert ", precio publicado en Bogotá." in mensaje.spoken


def test_la_voz_del_carrito_y_del_resumen_dice_la_ciudad_del_precio() -> None:
    # Arrange
    dataset = _dataset(
        _offer(VASO_9, BOGOTA, "4000"),
        _offer(VASO_14, CALI, "9000", units=25),
    )
    remota = quote_request(
        QuoteRequest(sku=VASO_9, quantity=100, city="Cali", delivery=False),
        today=HOY,
        now=MANANA,
        dataset=dataset,
    )
    local = quote_request(
        QuoteRequest(sku=VASO_14, quantity=100, city="Cali", delivery=False),
        today=HOY,
        now=MANANA,
        dataset=dataset,
    )
    uno, _ = add_line(QuoteDraft(), remota)
    dos, _ = add_line(uno, local)

    # Act
    agregado = render.added(uno, [uno.lines[-1]])
    resumen_uno = render.summary(uno)
    agregado_dos = render.added(dos, [dos.lines[-1]])
    resumen_dos = render.summary(dos)

    # Assert
    assert "Es el precio publicado en Bogotá." in agregado.spoken
    assert "Es el precio publicado en Bogotá." in resumen_uno.spoken
    # La línea local no se rotula; el carrito escrito marca la de Bogotá.
    assert "Bogotá" not in agregado_dos.spoken
    assert "(precio de Bogotá)" in agregado_dos.text
    assert "Incluye precios publicados en Bogotá." in resumen_dos.spoken


def test_un_precio_agotado_en_la_ciudad_no_se_cambia_por_el_de_otra() -> None:
    # Arrange: Bogotá publica precio pero agotado; decir «no hay precio publicado» es falso.
    dataset = _dataset(
        replace(_offer(VASO_9, BOGOTA, "4000"), stock_status="no_disponible"),
        _offer(VASO_9, MEDELLIN, "5000"),
    )

    # Act
    resultado = _cotizar(dataset, city="Bogotá")

    # Assert
    assert resultado.get("primary", {}).get("price_city") != "Medellín"
    assert "no hay precio publicado" not in resultado["reply"]
    assert "Antioquia Empaques" not in resultado["reply"]


def test_una_cotizacion_con_precio_de_la_ciudad_no_cambia_su_rotulo() -> None:
    # Arrange
    dataset = _dataset(_offer(VASO_9, BOGOTA, "4000"))
    resultado = _cotizar(dataset, city="Bogotá")

    # Act
    view = quote_view(resultado)

    # Assert
    assert resultado["primary"]["price_city"] == "Bogotá"
    assert resultado["primary"]["price_origin"] is None
    assert view is not None and view.price_city == "" and view.price_origin == ""
    assert "📍 Bogotá · Vendedor Capital\n" in whatsapp_quote(view)


def test_el_resumen_dice_que_el_envio_de_un_precio_de_otra_ciudad_se_confirma() -> None:
    # Arrange
    dataset = _dataset(
        _offer(VASO_9, BOGOTA, "4000"),
        _offer(VASO_14, CALI, "9000", units=25),
    )
    remota = quote_request(
        QuoteRequest(sku=VASO_9, quantity=100, city="Cali", delivery=False),
        today=HOY,
        now=MANANA,
        dataset=dataset,
    )
    local = quote_request(
        QuoteRequest(sku=VASO_14, quantity=100, city="Cali", delivery=False),
        today=HOY,
        now=MANANA,
        dataset=dataset,
    )
    con_remota, _ = add_line(QuoteDraft(), remota)
    solo_local, _ = add_line(QuoteDraft(), local)

    # Act
    resumen_remoto = render.summary(con_remota)
    resumen_local = render.summary(solo_local)

    # Assert: la decisión 4 deja el envío en el resumen; sin cobertura declarada, se confirma.
    assert (
        "🚚 Precios publicados en Bogotá: el envío a Cali se confirma con el vendedor."
        in resumen_remoto.text
    )
    assert "🚚" not in resumen_local.text
