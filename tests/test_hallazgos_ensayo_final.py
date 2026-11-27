"""Hallazgos del ensayo contra producción del 2026-11-27.

- El modelo escribió una tarjeta del carrito («✅ Agregué *1 producto*… Total parcial») sin
  haber agregado nada: se quita del texto y las del historial viajan resumidas.
- Entraba al carrito una tapa que no le servía al vaso del mismo turno.
- En la web, «mándame el recibo» tras el pedido recibía la respuesta genérica.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from tests.test_flujos_cotizacion import make_quote
from tests.test_pedidos_odoo import FakeOrders, _registered, _say, _service
from tests.test_pedidos_seguimiento import ORDER, engine  # noqa: F401 (fixture)

from apps.api.chat.agent import ToolAgentToken, _TurnStream
from apps.mcp.bridge import is_receipt_request
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.graph import history_messages
from carvajal.quotation import render
from carvajal.quotation.cards import compact_cards, strip_imitated_cards
from carvajal.quotation.draft import QuoteDraft, line_label
from carvajal.quotation.engine import QuoteRequest, quote_request
from carvajal.quotation.flow import after_turn
from carvajal.quotation.lids import lid_brand, lid_capacities, lid_fits, lid_fits_cup
from carvajal.quotation.order import asks_order_receipt

AHORA = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)

# Texto del modelo en el ensayo (WhatsApp, guion «varios_productos»).
IMITADA = """Para «platos hondos» la herramienta trajo el Plato espumado pando 12 cm Wau [1].

¿Te sirve el hondo de 12 oz o prefieres el pando de 12 cm?

✅ Agregué *1 producto*
Usé la recomendada; si quieres ver alternativas, dímelo.

🛒 *Tu cotización · 2 productos*
1. Vaso rígido traslúcido 9 oz Wau \u00d7 500 · $37.510
*Total parcial: $54.990* (IVA incl.)

¿Algo más?

*Fuentes:*
[1] EDEXA: Plato espumado pando 12 cm Wau"""


# --- Tarjetas imitadas -----------------------------------------------------------------------


def test_la_tarjeta_imitada_por_el_modelo_se_quita_y_queda_su_texto() -> None:
    # Act
    clean, removed = strip_imitated_cards(IMITADA)

    # Assert
    assert removed > 0
    assert "Agregué" not in clean and "Total parcial" not in clean
    assert "Tu cotización" not in clean and "¿Algo más?" not in clean
    assert clean.startswith("Para «platos hondos»")
    assert "¿Te sirve el hondo de 12 oz" in clean
    assert clean.endswith("[1] EDEXA: Plato espumado pando 12 cm Wau")


@pytest.mark.parametrize(
    "text",
    [
        "Hay 7 platos hondos.\n\n1. Plato A (SKU X)\n2. Plato B (SKU Y)\n\n¿Cuál quieres?",
        "El vaso de 7 oz sirve para bebidas frías [1].",
        "El total parcial lo calcula el sistema cuando confirmes.",
    ],
)
def test_un_texto_sin_tarjetas_no_cambia(text: str) -> None:
    # Act
    clean, removed = strip_imitated_cards(text)

    # Assert
    assert (clean, removed) == (text, 0)


def test_las_tarjetas_del_historial_viajan_resumidas_sin_su_formato() -> None:
    # Arrange
    answer = (
        "El vaso sirve para frío [1].\n\n🔎 **Opciones · 1.000 und · Bogotá**\n\n"
        "⭐ **Recomendada: Vaso 7 oz** · $53.060\n   PP, 7 oz\n\n**Alternativas**\n"
        "1. Vaso 3,5 oz · $55.880\n\n¿Cuál te cotizo? Di **esa** o un número."
    )

    # Act
    turn = Turn(question="1000 vasos", answer=answer, incomplete=False)
    messages = history_messages([turn], limit=4)

    # Assert
    assert isinstance(messages[0], HumanMessage) and isinstance(messages[1], AIMessage)
    sent = str(messages[1].content)
    assert sent.startswith("El vaso sirve para frío [1].")
    assert "🔎" not in sent and "⭐" not in sent and "**" not in sent
    assert "Recomendada: Vaso 7 oz · $53.060" in sent and "Vaso 3,5 oz · $55.880" in sent
    assert compact_cards("Sin tarjeta.") == "Sin tarjeta."


def test_el_agente_no_emite_la_tarjeta_que_imita() -> None:
    # Arrange: turno que cotizó (camino con evidencia) y cuyo texto final trae la tarjeta.
    stream = _TurnStream("agrega 200 platos hondos")
    stream.values = {
        "results": [
            {"tool": "cotizar", "args": {}, "status": "ok", "source_ids": [1], "elapsed_ms": 1}
        ],
    }

    # Act
    events = stream.on_update({"agent": {"messages": [AIMessage(content=IMITADA)]}})

    # Assert
    emitted = "".join(event.text for event in events if isinstance(event, ToolAgentToken))
    assert emitted.startswith("Para «platos hondos»")
    assert "Agregué" not in emitted and "Total parcial" not in emitted


# --- Tapas -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Tapa traslúcida para vaso rígido 3,5 a 7 oz Wau", (3.5, 7.0)),
        ("Tapa viajera 8 y 12 oz para vaso Bioform", frozenset({8.0, 12.0})),
        ("Tapa viajera 16 oz para vaso Bioform", frozenset({16.0})),
        ("Domo perforado PET para vaso Gold Wau Maestro", None),
    ],
)
def test_la_capacidad_de_la_tapa_sale_de_su_nombre(name: str, expected: Any) -> None:
    assert lid_capacities(name) == expected


def test_una_tapa_sirve_segun_su_rango_y_su_linea_de_vasos() -> None:
    # Arrange
    small = "Tapa traslúcida para vaso rígido 3,5 a 7 oz Wau"
    bioform = "Tapa viajera 9 a 12 oz para vaso Bioform"
    wau_9 = ("Vaso rígido traslúcido 9 oz Wau", 9.0)

    # Act / Assert
    assert lid_fits(small, 9.0) is False and lid_fits(small, 7.0) is True
    assert lid_fits("Domo para vaso Gold", 9.0) is None
    assert (lid_brand(bioform), lid_brand(small)) == ("Bioform", None)
    assert not lid_fits_cup(small, [wau_9])
    assert not lid_fits_cup(bioform, [wau_9])  # 9 oz, pero para el vaso Bioform
    assert lid_fits_cup(bioform, [("Vaso Bioform 9 oz", 9.0)])
    assert lid_fits_cup(small, [])


def test_el_motor_encuentra_tapas_por_la_capacidad_del_vaso() -> None:
    # Act: antes, con capacidad pedida, ninguna tapa calzaba (no traen ``capacity_oz``).
    result = quote_request(QuoteRequest(family="tapa", capacity_oz=9, quantity=300, city="Bogotá"))

    # Assert
    name = str((result.get("primary") or {}).get("name") or "")
    assert name.startswith("Tapa") and lid_fits(name, 9.0) is True


def _with_capacity(quote: dict[str, Any], capacity: float | None) -> dict[str, Any]:
    quote["primary"]["capacity_oz"] = capacity
    return quote


def _cup(sku: str, name: str, capacity: float, subtotal: int = 37510) -> dict[str, Any]:
    spec = {"sku": sku, "nombre": name, "unidades": 500, "subtotal": subtotal}
    return _with_capacity(make_quote(spec), capacity)


def _lid(name: str = "Tapa traslúcida para vaso rígido 3,5 a 7 oz Wau") -> dict[str, Any]:
    spec = {"sku": "T37", "nombre": name, "unidades": 300, "subtotal": 28950, "familia": "tapa"}
    return make_quote(spec)


def test_la_tapa_que_no_le_sirve_al_vaso_no_entra_al_carrito() -> None:
    # Arrange: «500 vasos de 9 oz y 300 tapas para esos vasos» (ensayo del 2026-11-27).
    vaso = _cup("V9", "Vaso rígido traslúcido 9 oz Wau", 9.0)
    tapa = _lid()
    text = "Los vasos quedaron cotizados. La tapa no corresponde a vasos de 9 oz. ¿La busco?"

    # Act
    outcome = after_turn(QuoteDraft(), [vaso, tapa], text, now=AHORA, asks=True)

    # Assert
    assert [line_label(line) for line in outcome.draft.lines] == [line_label(vaso)]


def test_la_tapa_que_si_le_sirve_al_vaso_del_carrito_entra() -> None:
    # Arrange: el vaso de 7 oz ya está en el carrito; luego «300 tapas para esos vasos».
    vaso = _cup("V7", "Vaso rígido traslúcido 7 oz Wau", 7.0, 30000)
    tapa = _lid()
    cart = after_turn(QuoteDraft(), [vaso], "Listo.", now=AHORA).draft

    # Act
    outcome = after_turn(cart, [tapa], "La tapa le sirve a tus vasos [1].", now=AHORA)

    # Assert
    assert len(outcome.draft.lines) == 2


def test_la_tapa_nombrada_por_la_persona_entra_aunque_no_cuadre() -> None:
    # Arrange
    vaso = _cup("V9", "Vaso rígido traslúcido 9 oz Wau", 9.0)
    tapa = _lid()
    tapa["reference_origin"] = "persona"

    # Act
    outcome = after_turn(QuoteDraft(), [vaso, tapa], "Listo.", now=AHORA)

    # Assert
    assert len(outcome.draft.lines) == 2


# --- Recibo en la web ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("mándame el recibo", True),
        ("¿Me mandas el recibo?", True),
        ("el PDF del pedido", True),
        ("recibo de mi compra", True),
        ("reenvíame el recibo por favor", True),
        ("¿cuándo recibo el pedido?", False),
        ("el PDF de la cotización", False),
        ("PDF", False),
        ("quiero 500 vasos y el recibo", False),
    ],
)
def test_pedir_el_recibo_se_reconoce_igual_en_los_dos_canales(message: str, expected: bool) -> None:
    # Act / Assert: el puente de WhatsApp acepta todo lo que la regla común acepta.
    assert asks_order_receipt(message) is expected
    if expected:
        assert is_receipt_request(message)


def test_en_la_web_el_recibo_del_pedido_se_reenvia_sin_el_modelo(engine: Any) -> None:  # noqa: F811
    # Arrange: folio → «sí» → datos → «sí» (pedido creado) y un turno ajeno.
    service, runner = _service(engine, FakeOrders())
    _registered(service)
    _say(service, "sí")
    _say(service, "Ana Pérez, ana@correo.com")
    _say(service, "sí")
    _say(service, "¿cómo va mi pedido?")
    calls = len(runner.calls)

    # Act
    text = _say(service, "mándame el recibo")

    # Assert: la cabecera del pedido y la promesa que la web cambia por el enlace del PDF.
    assert text.startswith(f"🧾 **Pedido {ORDER} confirmado** · cotización #")
    assert text.endswith(render.RECEIPT_PROMISE)
    linked = render.receipt_offer(text, lambda folio: f"https://app/pedidos/{folio}.pdf")
    assert f"[Descarga el recibo de tu pedido {ORDER}](https://app/pedidos/" in linked
    assert len(runner.calls) == calls


def test_en_la_web_sin_pedido_el_recibo_tiene_respuesta_fija(engine: Any) -> None:  # noqa: F811
    # Arrange: un hilo sin cotización ni pedido.
    service, runner = _service(engine, FakeOrders())
    calls = len(runner.calls)

    # Act
    text = _say(service, "mándame el recibo")

    # Assert: respuesta fija, sin que el modelo invente un recibo.
    assert text == render.NO_ORDER_RECEIPT
    assert len(runner.calls) == calls
