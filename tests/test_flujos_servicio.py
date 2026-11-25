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
from apps.mcp.bridge import PDF_CONFIRMATION
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
