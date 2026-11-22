"""Evidencia de los criterios de éxito de la spec 036 sobre el catálogo real (T044).

SC-001, SC-004 y SC-005 se verifican en ``test_routing_eval_set.py``; aquí van los que
dependen de cada referencia del catálogo: cajas y totales (SC-002) y precios completos
(SC-003).
"""

import math
from datetime import date
from decimal import Decimal

import pytest

from apps.api.chat.products import render_products
from carvajal.agent.commercial import NoPriceError, boxes_for, quote
from carvajal.agent.commercial_flow import run_commercial
from carvajal.agent.commercial_intent import COMMERCIAL_KEYS
from carvajal.catalog.loader import load_catalog
from carvajal.catalog.models import Reference

CATALOGO = load_catalog()
CANTIDADES = (1, 999, 1000, 1001, 5000, 123_457)


@pytest.mark.parametrize("referencia", CATALOGO.references, ids=lambda ref: ref.sku)
def test_sc002_cajas_y_totales_coinciden_con_el_calculo_esperado(referencia: Reference) -> None:
    for unidades in CANTIDADES:
        cajas = boxes_for(referencia, unidades)

        assert cajas.boxes == math.ceil(unidades / referencia.units_per_box)
        assert cajas.surplus == cajas.boxes * referencia.units_per_box - unidades
        assert 0 <= cajas.surplus < referencia.units_per_box
        if referencia.price is not None:
            total = quote(referencia, cajas.boxes, today=date(2026, 11, 1)).total
            assert Decimal(total) == referencia.price * cajas.boxes


@pytest.mark.parametrize("referencia", CATALOGO.references, ids=lambda ref: ref.sku)
def test_sc003_todo_precio_lleva_moneda_impuestos_y_fecha(referencia: Reference) -> None:
    try:
        cotizacion = quote(referencia, 1, today=date(2026, 11, 1))
    except NoPriceError:
        assert referencia.price is None  # sin precio se declara, no se estima (FR-006)
        return

    assert cotizacion.currency in {"MXN", "COP"}
    assert ("con impuestos incluidos" in cotizacion.notice) or (
        "sin impuestos" in cotizacion.notice
    )
    assert referencia.captured_at.isoformat() in cotizacion.notice
    assert "no confirma existencias" in cotizacion.notice


def test_sc003_las_tarjetas_muestran_moneda_aviso_y_fecha_de_cada_precio() -> None:
    pedido = dict.fromkeys(COMMERCIAL_KEYS)
    pedido.update(prefers_renewable=False, temperature="caliente", country="MX", quantity=1000)

    resultado = run_commercial(pedido)
    html = render_products(resultado)

    con_precio = [opcion for opcion in resultado["options"] if "total" in opcion]
    assert con_precio
    for opcion in con_precio:
        assert opcion["total"] in html
        assert opcion["price_notice"] in html
        assert f"capturada el {opcion['captured_at']}" in html
