"""Hallazgos del ensayo contra producción del 2026-11-27.

- El modelo escribió una tarjeta del carrito («✅ Agregué *1 producto*… Total parcial») sin
  haber agregado nada: se quita del texto y las del historial viajan resumidas.
- Entraba al carrito una tapa que no le servía al vaso del mismo turno.
- En la web, «mándame el recibo» tras el pedido recibía la respuesta genérica.
"""

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from PIL import Image
from tests.test_flujos_cotizacion import make_quote
from tests.test_pedidos_odoo import FakeOrders, _registered, _say, _service
from tests.test_pedidos_seguimiento import ORDER, engine  # noqa: F401 (fixture)

from apps.api.chat.agent import CARD_ONLY_ANSWER, ToolAgentToken, _TurnStream
from apps.mcp.bridge import is_receipt_request, read_inbound_photo
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.graph import history_messages
from carvajal.food.photo import PhotoRejected, prepare_photo
from carvajal.quotation import render
from carvajal.quotation.cards import CARD_NOTE, compact_cards, strip_imitated_cards
from carvajal.quotation.draft import QuoteDraft, line_label
from carvajal.quotation.engine import QuoteRequest, quote_request
from carvajal.quotation.flow import Reply, after_turn, before_turn
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


# --- Revisión adversarial de la PR -----------------------------------------------------------


@pytest.mark.parametrize(
    "card",
    [
        "✅ Agregué **500 Vaso**\n**$37.510** ($75 c/u)\n📍 Bogotá\n\n"
        "¿Algo más para tu cotización?",
        "✅ Agregué **500 Vaso**\n10 bolsas x 50 · **$37.510**\n📍 Bogotá",
    ],
)
def test_las_tarjetas_sin_presentacion_o_en_bolsas_se_quitan_enteras(card: str) -> None:
    # Act
    clean, removed = strip_imitated_cards(f"El vaso sirve [1].\n\n{card}")

    # Assert
    assert (clean, removed > 0) == ("El vaso sirve [1].", True)


def test_la_tarjeta_termina_en_su_pregunta_y_no_se_lleva_lo_que_sigue() -> None:
    # Arrange
    text = (
        "🔎 Opciones · 1.000 und\n\n⭐ Recomendada: Vaso\n   PP\n\nAlternativas\n1. Vaso B\n\n"
        "¿Cuál te cotizo? Di esa o un número.\n\n1. Para frío usa el de PP.\n2. Para caliente."
    )

    # Act
    clean, _ = strip_imitated_cards(text)

    # Assert
    assert clean == "1. Para frío usa el de PP.\n2. Para caliente."
    assert strip_imitated_cards("Total parcial aparte, sirve para frío.")[1] == 0


def test_un_texto_que_solo_era_la_tarjeta_no_deja_el_turno_vacio() -> None:
    # Arrange
    stream = _TurnStream("agrega 500 vasos")
    stream.values = {
        "results": [
            {"tool": "cotizar", "args": {}, "status": "ok", "source_ids": [1], "elapsed_ms": 1}
        ],
    }
    card = "✅ Agregué *1 producto*\n🛒 *Tu cotización · 1 producto*\n1. Vaso x 500 · $37.510"

    # Act
    events = stream.on_update({"agent": {"messages": [AIMessage(content=card)]}})

    # Assert: una frase neutra; el flujo agrega debajo la tarjeta real.
    emitted = "".join(event.text for event in events if isinstance(event, ToolAgentToken))
    assert emitted == CARD_ONLY_ANSWER


def test_sin_compuerta_la_tarjeta_que_empieza_en_vivo_tampoco_sale() -> None:
    # Arrange: compuerta apagada, así que el texto se libera tras RETAIN_CHARS.
    stream = _TurnStream("agrega 200 platos hondos")
    stream.values = {
        "results": [
            {"tool": "cotizar", "args": {}, "status": "ok", "source_ids": [1], "elapsed_ms": 1}
        ],
    }
    pieces = IMITADA.split("\n")
    metadata = {"langgraph_node": "agent"}
    events: list[Any] = []

    # Act
    for line in pieces:
        events += stream.on_message(AIMessageChunk(content=line + "\n"), metadata)
    events += stream.on_update({"agent": {"messages": [AIMessage(content="\n".join(pieces))]}})

    # Assert
    emitted = "".join(event.text for event in events if isinstance(event, ToolAgentToken))
    assert emitted.startswith("Para «platos hondos»")
    assert "Agregué" not in emitted and "Total parcial" not in emitted and "🛒" not in emitted


@pytest.mark.parametrize(
    ("name", "capacity", "expected"),
    [
        ("Tapas de plástico PET 12 oz a 24 oz - Caja con 800 pzs", 16.0, True),
        ("Tapas de plástico 12 oz a 20 oz PS", 24.0, False),
    ],
)
def test_los_rangos_con_la_unidad_repetida_se_leen(
    name: str, capacity: float, expected: bool
) -> None:
    assert lid_fits(name, capacity) is expected


def test_una_tapa_de_cazuela_no_es_tapa_de_vaso() -> None:
    # Arrange
    cazuela = "Tapa traslúcida para cazuela 12 y 16 oz Wau Maestro"

    # Act
    result = quote_request(QuoteRequest(family="tapa", capacity_oz=16, quantity=300, city="Cali"))

    # Assert
    assert not lid_fits_cup(cazuela, [("Vaso rígido 16 oz", 16.0)])
    assert str((result.get("primary") or {}).get("name") or "") != cazuela


@pytest.mark.parametrize("message", ["sí, lo recibo", "vale, recibo", "ok recibo el pedido"])
def test_recibo_como_verbo_no_pide_el_recibo(message: str) -> None:
    assert not asks_order_receipt(message)
    assert not is_receipt_request(message)


def test_con_el_pedido_en_el_borrador_el_recibo_gana_al_estado() -> None:
    # Arrange: «ver mi pedido en pdf» casa también con el estado del pedido.
    draft = QuoteDraft(state="registrada", folio="COT-1", order=ORDER).touch(AHORA)

    # Act
    decision = before_turn(draft, "ver mi pedido en pdf", now=AHORA, orders=True)

    # Assert
    assert isinstance(decision, Reply)
    assert decision.message.text == render.order_receipt(ORDER, "COT-1").text


def test_la_tapa_se_mide_contra_el_vaso_del_mismo_turno() -> None:
    # Arrange: el carrito ya tiene un vaso de 9 oz; el turno trae un vaso de 7 oz y su tapa.
    cart = after_turn(
        QuoteDraft(), [_cup("V9", "Vaso rígido traslúcido 9 oz Wau", 9.0)], "Listo.", now=AHORA
    ).draft
    vaso = _cup("V7", "Vaso rígido traslúcido 7 oz Wau", 7.0, 30000)

    # Act
    outcome = after_turn(cart, [vaso, _lid()], "Listo.", now=AHORA)

    # Assert: entra la tapa, que sí le sirve al vaso de 7 oz del turno.
    assert len(outcome.draft.lines) == 3


def test_la_tapa_pedida_con_su_capacidad_entra_aunque_el_carrito_tenga_otro_vaso() -> None:
    # Arrange: carrito con vasos de 9 oz y luego «300 tapas de 7 oz».
    cart = after_turn(
        QuoteDraft(), [_cup("V9", "Vaso rígido traslúcido 9 oz Wau", 9.0)], "Listo.", now=AHORA
    ).draft
    tapa = _lid()
    tapa["request"]["capacity_oz"] = 7

    # Act
    outcome = after_turn(cart, [tapa], "Listo.", now=AHORA)

    # Assert
    assert len(outcome.draft.lines) == 2


def test_el_flujo_avisa_que_tapa_no_agrego() -> None:
    # Arrange
    vaso = _cup("V9", "Vaso rígido traslúcido 9 oz Wau", 9.0)

    # Act
    text = "Los vasos quedaron cotizados. La tapa no corresponde a vasos de 9 oz. ¿La busco?"
    outcome = after_turn(QuoteDraft(), [vaso, _lid()], text, now=AHORA, asks=True)
    only_lid = after_turn(
        after_turn(QuoteDraft(), [vaso], "Listo.", now=AHORA).draft, [_lid()], "Ok.", now=AHORA
    )

    # Assert: el aviso va antes de la tarjeta del vaso, y solo, si el turno no agregó nada.
    assert outcome.message is not None and only_lid.message is not None
    assert outcome.message.text.startswith("No agregué **Tapa traslúcida para vaso rígido")
    assert "Agregué **500 Vaso rígido traslúcido 9 oz Wau" in outcome.message.text
    assert only_lid.message.text.startswith("No agregué **Tapa")
    assert len(only_lid.draft.lines) == 1


# --- Ensayo tras la PR #288: el modelo copió la nota del historial ----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "La tapa traslúcida Wau cubre vasos de 3,5 a 7 oz [1].\n\n"
        "(El sistema mostró aquí su tarjeta de la cotización: Recomendada: Tapa traslúcida "
        "para vaso rígido 3,5 a 7 oz Wau (paquete x 50) · $28.950; alternativas en PET.)\n\n"
        "*Fuentes:*\n[1] EDEXA: Tapa",
        "El plato que encontré es pando, no hondo [1].\n\n"
        "(El sistema mostró aquí su tarjeta de la cotización: Plato espumado pando 12 cm Wau "
        "x 200 · $17.480, más las alternativas.)",
    ],
)
def test_la_nota_del_historial_copiada_por_el_modelo_se_quita(text: str) -> None:
    # Act
    clean, removed = strip_imitated_cards(text)

    # Assert
    assert removed == 1
    assert "El sistema mostró" not in clean
    assert clean.split("\n")[0] == text.split("\n")[0]


def test_la_nota_del_historial_es_una_tarjeta_para_el_filtro() -> None:
    # Arrange: lo que el modelo ve en el historial es justo lo que el filtro quita.
    note = compact_cards("Listo.\n\n✅ Agregué **500 Vaso**\n1. Vaso x 500 · $37.510")

    # Act
    clean, removed = strip_imitated_cards(note)

    # Assert
    assert note.split("\n\n")[1].startswith(CARD_NOTE)
    assert (clean, removed) == ("Listo.", 1)


# --- WhatsApp real: las fotos llegan en WebP ---------------------------------------------------


def _webp(*, frames: int = 1) -> bytes:
    colors = [(200, 40, 40), (40, 200, 40), (40, 40, 200)]
    images = [Image.new("RGB", (640, 480), colors[index % 3]) for index in range(frames)]
    out = io.BytesIO()
    images[0].save(out, "WEBP", save_all=frames > 1, append_images=images[1:], duration=100, loop=0)
    return out.getvalue()


def test_una_foto_webp_de_whatsapp_se_lee_y_se_prepara(tmp_path: Path) -> None:
    # Arrange: OpenClaw dejó el adjunto con el nombre que le da (UUID + extensión).
    (tmp_path / "3f2a9c1e-foto.webp").write_bytes(_webp())

    # Act
    raw = read_inbound_photo("media://inbound/3f2a9c1e-foto.webp", str(tmp_path))
    photo = prepare_photo(raw)

    # Assert: sale como JPEG, igual que las demás.
    assert photo.data[:3] == b"\xff\xd8\xff"
    assert (photo.width, photo.height) == (640, 480)


def test_un_webp_animado_se_rechaza() -> None:
    with pytest.raises(PhotoRejected):
        prepare_photo(_webp(frames=3))


def test_la_tapa_de_otra_linea_no_entra_aunque_traiga_la_capacidad_del_vaso() -> None:
    # Arrange: «500 vasos de 9 oz y 300 tapas para esos vasos»; el modelo cotiza la tapa con
    # capacity_oz=9 y el motor trae la viajera de 9 a 12 oz para vaso Bioform.
    vaso = _cup("V9", "Vaso rígido traslúcido 9 oz Wau", 9.0)
    tapa = _lid("Tapa viajera 9 a 12 oz para vaso Bioform")
    tapa["request"]["capacity_oz"] = 9
    text = "Listo, cotizadas las dos cosas para Bogotá: el vaso de 9 oz y la tapa [1]."

    # Act
    outcome = after_turn(QuoteDraft(), [vaso, tapa], text, now=AHORA)

    # Assert
    assert [line_label(line) for line in outcome.draft.lines] == [line_label(vaso)]
    assert outcome.message is not None
    assert outcome.message.text.startswith("No agregué **Tapa viajera 9 a 12 oz")
