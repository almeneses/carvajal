"""Rediseño 051 de las tarjetas comerciales: fecha local, foto, plural y «Cambiar»."""

from pathlib import Path
from typing import Any

import pytest

from apps.api import runtime
from apps.api.chat.products import render_products

BASE = Path(__file__).resolve().parents[1] / "apps" / "api" / "templates" / "base.html"


def _cotizacion(**cambios: Any) -> dict[str, Any]:
    presentacion = {
        "basis": "paquete",
        "units": 50,
        "count": 60,
        "units_bought": 3000,
        "units_surplus": 0,
        "price_per_presentation": "3751",
        "subtotal": "225060",
    }
    presentacion.update(cambios)
    cotizacion = {
        "kind": "cotizacion",
        "city": "Bogotá",
        "primary": {"sku": "7704918100199", "units_requested": 3000, "presentation": presentacion},
        "commercial": {"shipping": {"requested": False}},
        "total": "225060",
        "pending_lines": [],
        "quote": {"folio": "abc123", "id": "abc123", "issued_at": "2026-11-25T01:30:00+00:00"},
    }
    return {"quote": cotizacion}


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        pytest.param("2026-11-25T01:30:00+00:00", "2026-11-24", id="noche-en-bogota"),
        pytest.param("2026-11-25T15:00:00+00:00", "2026-11-25", id="mismo-dia"),
        pytest.param("2026-11-25T01:30:00", "2026-11-24", id="sin-zona-es-utc"),
        pytest.param("no-es-fecha", "no-es-fech", id="texto-raro-se-corta"),
        pytest.param(None, "", id="vacio"),
    ],
)
def test_la_fecha_local_usa_la_hora_de_bogota(valor: object, esperado: str) -> None:
    # Arrange / Act
    fecha = runtime._local_date(valor)

    # Assert
    assert fecha == esperado


def test_la_cotizacion_emitida_de_noche_muestra_el_dia_de_bogota() -> None:
    # Arrange
    resultado = _cotizacion()

    # Act
    html = render_products(resultado)

    # Assert
    assert "emitida el 2026-11-24" in html
    assert "emitida el 2026-11-25" not in html


@pytest.mark.parametrize(
    ("cantidad", "texto"),
    [
        pytest.param(60, "60 paquetes · 3000 unidades", id="plural"),
        pytest.param(1, "1 paquete · 3000 unidades", id="singular"),
    ],
)
def test_la_presentacion_de_la_cotizacion_concuerda_en_numero(cantidad: int, texto: str) -> None:
    # Arrange
    resultado = _cotizacion(count=cantidad)

    # Act
    html = render_products(resultado)

    # Assert
    assert texto in " ".join(html.split())


def test_la_foto_del_catalogo_solo_se_enlaza_si_existe(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    import carvajal.catalog.images as imagenes

    monkeypatch.setattr(imagenes, "catalog_image", lambda sku: object() if sku == "CON" else None)

    # Act
    con_foto = runtime._catalog_photo("CON")
    sin_foto = runtime._catalog_photo("SIN")
    larga = runtime._catalog_photo("X" * 65)

    # Assert
    assert con_foto == "/catalogo/imagenes/CON.jpg"
    assert sin_foto == ""
    assert larga == ""


def test_la_solicitud_ofrece_cambiar_cada_dato_oculto_sin_javascript() -> None:
    # Arrange
    resultado = {
        "request": {"family": "vaso", "use": "bebida_caliente", "quantity": 3000, "city": "Cali"}
    }

    # Act
    html = render_products(resultado)

    # Assert
    assert html.count("data-chat-cambiar") == 4
    assert html.count("hidden>Cambiar") == 4  # chat-stream.js los muestra
    assert 'data-entendi="cantidad"' in html
    assert "3.000" in html


def test_la_pagina_apaga_los_estilos_en_linea_de_htmx() -> None:
    # Arrange / Act
    plantilla = BASE.read_text(encoding="utf-8")

    # Assert: la CSP (style-src 'self') bloquearía el <style> que htmx inserta.
    assert '"includeIndicatorStyles": false' in plantilla
    assert plantilla.index('name="htmx-config"') < plantilla.index("htmx.min.js")
