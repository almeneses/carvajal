"""Flujos de cotización en el servicio de chat (spec 054), con el runner ``tools`` guionado.

Comprueba en el servicio real sobre SQLite lo que las pruebas puras no ven: que las
respuestas cortas no llaman al agente, que el borrador sobrevive entre turnos y que la web
recibe los botones de la pregunta pendiente.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import StaticPool
from tests.test_chat_service_tools import (
    FakeEmbedder,
    FakeLLM,
    FakeToolRunner,
    build_service,
    commercial_final,
    final,
    named,
    quote_payload,
    scripted_events,
    stored_quotes,
    tools_settings,
)

from apps.api.chat.service import ChatService
from apps.api.chat.streaming import ChatEvent
from apps.mcp.bridge import PDF_CONFIRMATION, WhatsAppBridge
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory

OWNER = "dueno-flujos"


@pytest.fixture()
def engine() -> Iterator[Any]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    with motor.begin() as conexion:
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    try:
        yield motor
    finally:
        motor.dispose()


def _send(service: ChatService, message: str) -> list[ChatEvent]:
    return list(service.stream(OWNER, message))


def _draft(engine: Any) -> dict[str, Any]:
    with engine.connect() as connection:
        return connection.execute(select(tables.quote_drafts.c.payload)).scalar_one()


class FakeJudge:
    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.calls: list[tuple[str, str]] = []

    def classify_reply(self, *, awaiting: str, reply: str) -> tuple[str, float]:
        self.calls.append((awaiting, reply))
        return self.choice, 0.95


def test_el_rechazo_con_insulto_responde_la_broma_sin_el_agente(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")

    # Act
    events = _send(service, "Está muy caro, muy ladrones")

    # Assert
    assert named(events, "fallback").data["text"] == (
        "Arrancado, vaya cómprele a su madre entonces 😂"
    )
    assert named(events, "trace").data["route"] == "rechazo"
    assert len(runner.calls) == 1
    assert stored_quotes(engine) == []
    assert _draft(engine)["state"] == "pausada"


def test_el_carrito_se_cierra_con_el_resumen_y_registra_una_sola_vez(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)

    # Act
    added = _send(service, "cotiza 50 vasos en Bogotá")
    summary = _send(service, "no")
    confirmed = _send(service, "sí")

    # Assert
    assert "✅ Agregué **50 Vaso rígido**" in "".join(
        event.data.get("text", "") for event in added if event.name == "token"
    )
    assert named(summary, "fallback").data["text"].startswith("🧾 **Resumen de tu cotización**")
    assert named(summary, "done").data["flow"]["awaiting"] == "confirmar"
    (row,) = stored_quotes(engine)
    assert named(confirmed, "done").data["quote_ids"] == [row["id"]]
    assert len(runner.calls) == 1  # «no» y «sí» no pasan por el modelo


def test_reintentar_el_si_con_el_mismo_turno_no_emite_otro_folio(engine: Any) -> None:
    # Arrange: carrito y resumen; el «sí» llega dos veces con el mismo ``client_turn_id``.
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")
    _send(service, "no")

    # Act
    first = list(service.stream(OWNER, "sí", client_turn_id="turno-si"))
    retry = list(service.stream(OWNER, "sí", client_turn_id="turno-si"))

    # Assert: el reintento reproduce el turno guardado (mismo folio) y hay una sola fila.
    (row,) = stored_quotes(engine)
    assert named(first, "done").data["quote_ids"] == [row["id"]]
    assert named(retry, "done").data["quote_ids"] == [row["id"]]


def test_un_segundo_si_con_otro_turno_responde_que_ya_quedo_registrada(engine: Any) -> None:
    # Arrange: la cotización ya se registró con un «sí».
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")
    _send(service, "no")
    list(service.stream(OWNER, "sí", client_turn_id="turno-1"))

    # Act: otro «sí» (p. ej. un reenvío con otro identificador).
    again = list(service.stream(OWNER, "sí", client_turn_id="turno-2"))

    # Assert: no hay folio nuevo ni otra fila.
    (row,) = stored_quotes(engine)
    assert named(again, "fallback").data["text"].startswith("Esa cotización ya quedó registrada")
    assert f"#{row['id']}" in named(again, "fallback").data["text"]
    assert named(again, "done").data["quote_ids"] == []


@pytest.mark.parametrize("closing", [False, True])
def test_la_confirmacion_del_puente_registra_desde_algo_mas_y_desde_el_resumen(
    engine: Any, closing: bool
) -> None:
    # Arrange: «¿Algo más?» (tras cotizar) o «¿La confirmo?» (tras «no»).
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")
    if closing:
        _send(service, "no")
    assert service.awaiting_confirmation(OWNER)

    # Act
    events = _send(service, PDF_CONFIRMATION)

    # Assert
    (row,) = stored_quotes(engine)
    assert named(events, "done").data["quote_ids"] == [row["id"]]
    assert len(runner.calls) == 1


def test_una_pregunta_de_otro_tema_se_responde_y_recuerda_la_cotizacion(engine: Any) -> None:
    # Arrange: el primer turno cotiza; el segundo responde sobre Carvajal (sin cotización).
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")
    runner.events = scripted_events(final())

    # Act
    events = _send(service, "¿Cuándo se fundó Carvajal?")

    # Assert: la respuesta del agente y, al final, el recordatorio con la pregunta pendiente.
    text_out = "".join(event.data.get("text", "") for event in events if event.name == "token")
    assert text_out.startswith("Carvajal se fundó en 1904")
    assert text_out.endswith("↩️ Seguimos con tu cotización (1 producto · $3.751). ¿Algo más?")
    assert _draft(engine)["awaiting"] == "algo_mas"


def test_cambiar_de_producto_pregunta_antes_de_cancelar(engine: Any) -> None:
    # Arrange
    vaso = quote_payload()
    vaso["primary"]["family"] = "vaso"  # el motor real siempre trae la familia
    runner = FakeToolRunner(
        scripted_events(final(products={"quote": vaso, "quotes": [vaso]}, quotes=(vaso,)))
    )
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")
    plato = quote_payload(100, "7502", sku="PL9", name="Plato pulpa")
    plato["primary"]["family"] = "plato"
    runner.events = scripted_events(
        final(products={"quote": plato, "quotes": [plato]}, quotes=(plato,))
    )

    # Act
    asked = _send(service, "No, mejor cotízame platos")
    kept = _send(service, "consérvalos")

    # Assert: primero la pregunta (sin el agente); al conservar, el pedido llega al agente.
    assert named(asked, "fallback").data["text"].startswith("Llevábamos **Vaso rígido**")
    assert 'data-pregunta="Cancelar"' in named(asked, "products").data["html"]
    assert runner.calls[-1]["question"] == "No, mejor cotízame platos"
    assert "🛒 **Tu cotización · 2 productos**" in "".join(
        event.data.get("text", "") for event in kept if event.name == "token"
    )


def test_cancelar_y_confirmar_nunca_registra_una_cotizacion_vacia(engine: Any) -> None:
    # Arrange: vasos; «cancela todo» deja el borrador sin líneas.
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")
    _send(service, "cancela todo")

    # Act
    events = _send(service, "sí, mándame el PDF")

    # Assert: ningún folio; el «sí» no confirma un borrador cancelado.
    assert stored_quotes(engine) == []
    assert named(events, "done").data["quote_ids"] == []


def test_cancelar_al_cambiar_de_producto_registra_solo_el_nuevo(engine: Any) -> None:
    # Arrange: vasos y luego «mejor platos» → «cancélalos».
    vaso = quote_payload()
    vaso["primary"]["family"] = "vaso"
    runner = FakeToolRunner(
        scripted_events(final(products={"quote": vaso, "quotes": [vaso]}, quotes=(vaso,)))
    )
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")
    plato = quote_payload(100, "7502", sku="PL9", name="Plato pulpa")
    plato["primary"]["family"] = "plato"
    runner.events = scripted_events(
        final(products={"quote": plato, "quotes": [plato]}, quotes=(plato,))
    )
    _send(service, "No, mejor cotízame platos")
    _send(service, "cancélalos")

    # Act
    _send(service, "sí, mándame el PDF")

    # Assert: una cotización con la línea del plato y sin los vasos cancelados.
    (row,) = stored_quotes(engine)
    assert [line["primary"]["sku"] for line in row["payload"]["lines"]] == ["PL9"]


def test_jev_resuelve_una_respuesta_que_las_reglas_no_entienden(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(commercial_final()))
    judge = FakeJudge("cerrar")
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        tool_agent=runner,
        settings=tools_settings(),
        reply_judge=judge,
    )
    _send(service, "cotiza 50 vasos en Bogotá")

    # Act
    events = _send(service, "de una, ciérrala ya mismo")

    # Assert: Jev vio solo la pregunta pendiente y la respuesta; no hubo segundo turno del agente.
    assert judge.calls == [("algo_mas", "de una, ciérrala ya mismo")]
    assert named(events, "fallback").data["text"].startswith("🧾 **Resumen de tu cotización**")
    assert len(runner.calls) == 1


def test_sin_cotizacion_pendiente_awaiting_confirmation_es_falso(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)

    # Act / Assert
    assert service.awaiting_confirmation(OWNER) is False
    _send(service, "cotiza 50 vasos en Bogotá")
    assert service.awaiting_confirmation(OWNER) is True
    _send(service, "No, confírmala")
    assert service.awaiting_confirmation(OWNER) is False


class _RunnerQueOlvida(FakeToolRunner):
    """Runner guionado que registra cada pedido de olvidar el contexto comercial."""

    def __init__(self, events: Any) -> None:
        super().__init__(events)
        self.cleared: list[str] = []

    def clear_commercial_context(self, thread_id: str) -> None:
        self.cleared.append(thread_id)


@pytest.mark.parametrize("mensaje", ["olvida todo", "cancela todo"])
def test_cancelar_todo_olvida_tambien_el_pedido_en_curso(engine: Any, mensaje: str) -> None:
    # Arrange: WhatsApp del 2026-11-25. «olvida todo» cancelaba el borrador, pero el
    # contexto comercial del grafo seguía y «cotiza 100 vasos» heredaba el vaso de papel.
    runner = _RunnerQueOlvida(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")

    # Act
    events = _send(service, mensaje)

    # Assert
    assert named(events, "fallback").data["text"].startswith("Listo, cancelé la cotización")
    assert len(runner.cleared) == 1
    assert len(runner.calls) == 1  # la cancelación no pasa por el modelo


def test_una_respuesta_corta_que_no_cancela_no_olvida_el_pedido(engine: Any) -> None:
    # Arrange
    runner = _RunnerQueOlvida(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")

    # Act
    _send(service, "no")

    # Assert
    assert runner.cleared == []


def test_el_payload_del_flujo_trae_las_fotos_de_lo_agregado(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)

    # Act
    added = _send(service, "cotiza 50 vasos en Bogotá")

    # Assert: el SKU de la línea agregada viaja al canal para su foto.
    flow = named(added, "done").data["flow"]
    assert flow["photos"]
    assert flow["photos"] == [_draft(engine)["lines"][-1]["primary"]["sku"]]


def test_una_abstencion_delante_de_la_plantilla_se_reemplaza_por_la_plantilla(
    engine: Any,
) -> None:
    # Arrange: WhatsApp del 2026-11-25. La compuerta descartó el comentario del modelo y
    # «No tengo un dato verificado…» salía antes de las opciones de la plantilla.
    from apps.api.chat.agent import ToolAgentSources, ToolAgentToken
    from carvajal.agent.toolagent.evidence import NO_DATA_ANSWER

    quote = quote_payload()
    closing = final(
        answer=NO_DATA_ANSWER,
        products={"quote": quote, "quotes": [quote], "cities": ["Bogotá"]},
        quotes=(quote,),
        abstained=True,
    )
    events_in = [
        ToolAgentSources(sources=closing.sources),
        ToolAgentToken(text=NO_DATA_ANSWER),
        closing,
    ]
    runner = FakeToolRunner(events_in)
    service = build_service(engine, runner)

    # Act
    events = _send(service, "cotiza 50 vasos en Bogotá")

    # Assert: la burbuja se reemplaza por la plantilla y el historial guarda solo ella.
    replaced = named(events, "fallback").data["text"]
    assert replaced.startswith("✅ Agregué")
    assert NO_DATA_ANSWER not in replaced
    with engine.connect() as connection:
        answers = connection.execute(
            select(tables.messages.c.content).where(tables.messages.c.role == "assistant")
        ).scalars()
        assert all(NO_DATA_ANSWER not in answer for answer in answers)


def test_cambiar_algo_en_el_resumen_muestra_el_carrito_y_sigue_el_flujo_en_la_web(
    engine: Any,
) -> None:
    # Arrange: vasos y el resumen pendiente de confirmar.
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")
    summary = _send(service, "no")
    (change,) = [
        action
        for action in named(summary, "done").data["flow"]["actions"]
        if action["label"] == "Cambiar algo"
    ]

    # Act: el botón «Cambiar algo» envía su frase; luego se cierra y se confirma.
    edited = _send(service, change["say"])
    closed = _send(service, "eso es todo")
    confirmed = _send(service, "sí")

    # Assert: carrito editable sin el modelo, con los botones de «¿Algo más?», y un solo folio.
    text_out = named(edited, "fallback").data["text"]
    assert text_out.startswith("🛒 **Tu cotización · 1 producto**")
    assert "**quita la 1**" in text_out
    flow = named(edited, "done").data["flow"]
    assert flow["awaiting"] == "algo_mas"
    assert [action["say"] for action in flow["actions"]] == [
        "Agregar otro producto",
        "Eso es todo",
    ]
    assert named(edited, "done").data["quote_ids"] == []
    assert _draft(engine)["lines"]  # el borrador conserva la línea
    assert named(closed, "done").data["flow"]["awaiting"] == "confirmar"
    (row,) = stored_quotes(engine)
    assert named(confirmed, "done").data["quote_ids"] == [row["id"]]
    assert len(runner.calls) == 1  # «no», «Cambiar algo», «eso es todo» y «sí» sin el modelo


@pytest.mark.parametrize("voice", [False, True])
def test_cambiar_algo_por_whatsapp_sale_en_su_formato_y_sin_el_modelo(
    engine: Any, voice: bool
) -> None:
    # Arrange: el mismo servicio detrás del puente de WhatsApp.
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    bridge = WhatsAppBridge(chat=service, secret=b"secreto-de-prueba")
    chat = "+573001112233"
    bridge.respond("cotiza 50 vasos en Bogotá", chat)
    bridge.respond("no", chat)

    # Act
    reply = bridge.respond("quiero cambiar algo", chat, voice=voice)

    # Assert: negrita de WhatsApp, voz corta sin marcas y el flujo sigue en «¿Algo más?».
    assert "🛒 *Tu cotización · 1 producto*" in reply.text
    assert "*quita la 1*" in reply.text and "**" not in reply.text
    assert reply.quote_ids == ()
    spoken = reply.voice().text
    assert "¿Qué cambio?" in spoken and "*" not in spoken and "🛒" not in spoken
    assert _draft(engine)["awaiting"] == "algo_mas"
    assert len(runner.calls) == 1


# --- Nombre ambiguo: la elección entre candidatas no pasa por el modelo -------------------------

_J1 = [
    {
        "sku": "CO-PCOM-J1",
        "name": "Portacomidas espumado J1 Wau (paca x 200)",
        "family": "contenedor",
        "material": "EPS",
        "capacity_oz": None,
    },
    {
        "sku": "7704918105071",
        "name": "Portacomidas pulpa de caña J1 Bioform",
        "family": "contenedor",
        "material": "PULPA",
        "capacity_oz": None,
    },
]


def _ambiguous_final() -> Any:
    """Cierre de «Cotízame 300 portacomidas J1 en Bogotá»: ``cotizar`` pidió cuál."""

    request = {
        "quantity": 300,
        "unit": "unidad",
        "city": "Bogotá",
        "delivery": None,
        "use": None,
        "temperature": None,
        "microwave": None,
    }
    return final(
        products={"reference_choice": {"candidates": _J1, "request": request}},
        results=({"tool": "cotizar", "status": "needs_input", "source_ids": []},),
        gate="no_query",
    )


def test_la_lista_de_candidatas_queda_en_el_borrador_y_no_en_el_historial(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(_ambiguous_final()))
    service = build_service(engine, runner)

    # Act
    _send(service, "Cotízame 300 portacomidas J1 en Bogotá")

    # Assert
    draft = _draft(engine)
    assert draft["awaiting"] == "referencia"
    assert [item["sku"] for item in draft["choices"]] == ["CO-PCOM-J1", "7704918105071"]
    assert draft["choice_request"]["quantity"] == 300
    with engine.connect() as connection:
        stored = connection.execute(select(tables.messages.c.sources)).scalars().all()
    assert "reference_choice" not in repr(stored)


def test_la_2_cotiza_la_candidata_en_la_web_sin_el_modelo(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(_ambiguous_final()))
    service = build_service(engine, runner)
    _send(service, "Cotízame 300 portacomidas J1 en Bogotá")

    # Act
    events = _send(service, "la 2")

    # Assert: el motor cotizó la de pulpa con su SKU y el flujo sigue en «¿Algo más?».
    text = named(events, "fallback").data["text"]
    assert text.startswith("✅ Agregué")
    assert "Portacomidas pulpa de caña J1 Bioform" in text
    assert named(events, "done").data["flow"]["awaiting"] == "algo_mas"
    draft = _draft(engine)
    assert [line["primary"]["sku"] for line in draft["lines"]] == ["7704918105071"]
    assert draft["choices"] == []
    assert len(runner.calls) == 1


def test_un_si_ante_la_lista_la_vuelve_a_mostrar_con_botones(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(_ambiguous_final()))
    service = build_service(engine, runner)
    _send(service, "Cotízame 300 portacomidas J1 en Bogotá")

    # Act
    events = _send(service, "sí")

    # Assert: no se elige ninguna; la web recibe un botón por candidata.
    text = named(events, "fallback").data["text"]
    assert "1. Portacomidas espumado J1 Wau (paca x 200) (SKU CO-PCOM-J1)" in text
    assert "2. Portacomidas pulpa de caña J1 Bioform (SKU 7704918105071)" in text
    flow = named(events, "done").data["flow"]
    assert flow["awaiting"] == "referencia"
    assert [action["say"] for action in flow["actions"]] == ["La 1", "La 2"]
    assert _draft(engine)["lines"] == []
    assert len(runner.calls) == 1


def test_whatsapp_elige_la_candidata_por_el_nombre_sin_el_modelo(engine: Any) -> None:
    # Arrange: el mismo servicio detrás del puente de WhatsApp.
    runner = FakeToolRunner(scripted_events(_ambiguous_final()))
    service = build_service(engine, runner)
    bridge = WhatsAppBridge(chat=service, secret=b"secreto-de-prueba")
    chat = "+573001112233"
    bridge.respond("Cotízame 300 portacomidas J1 en Bogotá", chat)

    # Act
    reply = bridge.respond("el de icopor", chat)

    # Assert
    assert "✅ Agregué *300 Portacomidas espumado J1 Wau (paca x 200)*" in reply.text
    assert "**" not in reply.text
    assert reply.quote_ids == ()
    assert _draft(engine)["awaiting"] == "algo_mas"
    assert len(runner.calls) == 1


def test_los_botones_de_las_candidatas_salen_en_el_turno_que_las_lista(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(_ambiguous_final()))
    service = build_service(engine, runner)

    # Act
    events = _send(service, "Cotízame 300 portacomidas J1 en Bogotá")

    # Assert: el agente listó las candidatas; el flujo solo añade los botones, sin texto.
    flow = named(events, "done").data["flow"]
    assert flow["awaiting"] == "referencia"
    assert flow["text"] == ""
    assert [action["say"] for action in flow["actions"]] == ["La 1", "La 2"]
