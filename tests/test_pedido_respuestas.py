"""Respuestas a mitad del pedido y «ahora no» suelto (ensayo y WhatsApp real del 2026-11-27).

Tras la PR #283, los guiones de exploración contra producción mostraron tres respuestas
flojas, sin bloquear la entrega:

- «¿aceptan Nequi?» con el pedido pendiente: el agente se abstenía y el flujo cambiaba la
  abstención por el recordatorio «↩️» solo; la pregunta quedaba sin respuesta.
- «¿cuánto tarda el envío?»: el modelo cerraba con su propia pregunta del paso, a veces con
  el nombre enmascarado del historial («¿confirmo con (tu nombre)…?»), y debajo salía el
  recordatorio: dos veces lo mismo.
- «Ahora no» a una propuesta del agente («¿La agrego a tu cotización?») sin pregunta
  pendiente del flujo: el agente respondía «No tengo un dato verificado…».

La prueba por WhatsApp real (pedidos S00008 y S00009) mostró otra: el correo iba en
negrita, OpenClaw la convierte en énfasis (``_…_``) y WhatsApp metía el ``_`` en el enlace
(``mailto:_ana@…``). Tocar el correo abría una dirección equivocada.
"""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.test_chat_service_tools import final
from tests.test_pedidos_odoo import FakeOrders, _registered, _say, _service
from tests.test_pedidos_odoo_flujo import EMAIL, NAME, _ask, _stored_draft

from apps.api.chat.agent import ToolAgentEvent, ToolAgentSources, ToolAgentToken
from carvajal.adapters.storage import tables
from carvajal.agent.toolagent.evidence import NO_DATA_ANSWER
from carvajal.quotation import flow, render
from carvajal.quotation.draft import QuoteDraft

DELIVERY = "El plazo de entrega no aparece en las fuentes que revisé. Lo coordina un asesor."
REPEATED = "¿Confirmo el pedido con tu nombre y correo?"


@pytest.fixture
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


def _agent_says(answer: str, *, abstained: bool = False) -> list[ToolAgentEvent]:
    closing = final(answer=answer, sources=(), abstained=abstained, gate="no_query", results=())
    return [ToolAgentSources(sources=()), ToolAgentToken(text=answer), closing]


def _until_confirmation(service: Any) -> None:
    _registered(service)
    _say(service, "sí")
    _say(service, f"{NAME}, {EMAIL}")


# --- La pregunta del paso que el modelo repite -------------------------------------------


@pytest.mark.parametrize(
    "closing",
    [
        REPEATED,
        "Seguimos con el pedido: ¿confirmo con **(tu nombre)** y **a…@example.com**?",
        "¿Te confirmo los datos del pedido?",
    ],
)
def test_quita_la_pregunta_del_paso_que_repite_el_modelo(closing: str) -> None:
    # Act
    trimmed = flow.without_step_question(f"{DELIVERY}\n\n{closing}")

    # Assert
    assert trimmed == DELIVERY


@pytest.mark.parametrize(
    "answer",
    [
        DELIVERY,
        REPEATED,  # un solo párrafo: nunca queda vacío
        f"{DELIVERY}\n\n¿Quieres que te cotice también las tapas?",  # no es del pedido
    ],
)
def test_no_toca_una_respuesta_sin_la_pregunta_del_paso(answer: str) -> None:
    # Act / Assert
    assert flow.without_step_question(answer) == answer


def test_a_mitad_del_pedido_no_sale_dos_veces_la_pregunta_del_paso(engine: Any) -> None:
    # Arrange
    service, runner = _service(engine, FakeOrders())
    _until_confirmation(service)
    runner.events = _agent_says(f"{DELIVERY}\n\n{REPEATED}")
    reminder = render.order_reminder("confirmar_pedido")
    assert reminder is not None

    # Act
    answer = _ask(service, "¿cuánto tarda el envío?")

    # Assert: la respuesta y el recordatorio, sin la pregunta del modelo; el paso sigue.
    assert answer == f"{DELIVERY}\n\n{reminder.text}"
    assert "(tu nombre)" not in answer
    assert _stored_draft(engine).awaiting == "confirmar_pedido"


def test_una_pregunta_sin_dato_a_mitad_del_pedido_recibe_respuesta(engine: Any) -> None:
    # Arrange
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    _until_confirmation(service)
    runner.events = _agent_says(NO_DATA_ANSWER, abstained=True)
    reminder = render.order_reminder("confirmar_pedido")
    assert reminder is not None

    # Act
    answer = _ask(service, "¿aceptan Nequi?")

    # Assert: antes salía solo el recordatorio; ahora una línea fija y el recordatorio.
    assert answer == f"{render.ORDER_ASIDE}\n\n{reminder.text}"
    assert "Nequi" not in render.ORDER_ASIDE  # no inventa un medio de pago
    assert orders.requests == []
    assert _stored_draft(engine).awaiting == "confirmar_pedido"


def test_fuera_del_pedido_la_abstencion_no_cambia(engine: Any) -> None:
    # Arrange: sin pedido pendiente no hay recordatorio del pedido.
    service, runner = _service(engine, FakeOrders())
    runner.events = _agent_says(NO_DATA_ANSWER, abstained=True)

    # Act
    answer = _ask(service, "¿aceptan Nequi?")

    # Assert
    assert render.ORDER_ASIDE not in answer


# --- «Ahora no» sin pregunta pendiente del flujo -----------------------------------------


@pytest.mark.parametrize(
    "message", ["ahora no", "Ahora no", "no, gracias", "Ahora no, gracias.", "así está bien"]
)
def test_ahora_no_sin_pregunta_pendiente_tiene_respuesta_fija(message: str) -> None:
    # Act
    decision = flow.before_turn(QuoteDraft(), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.message == render.not_now()
    assert decision.draft == QuoteDraft()


def test_ahora_no_con_pregunta_pendiente_lo_decide_su_paso() -> None:
    # Arrange: «¿Algo más?» con una línea en el carrito.
    draft = QuoteDraft(
        state="carrito",
        awaiting="algo_mas",
        lines=({"kind": "cotizacion", "primary": {"sku": "X", "name": "Vaso"}},),
    )

    # Act
    decision = flow.before_turn(draft, "ahora no")

    # Assert: no es la respuesta fija del «ahora no» suelto.
    assert not (isinstance(decision, flow.Reply) and decision.message == render.not_now())


def test_ahora_no_tras_una_propuesta_del_agente_no_llama_al_modelo(engine: Any) -> None:
    # Arrange
    service, runner = _service(engine, FakeOrders())
    calls = len(runner.calls)

    # Act
    answer = _ask(service, "ahora no")

    # Assert
    assert answer == render.not_now().text
    assert len(runner.calls) == calls


# --- El correo sin énfasis (WhatsApp lo enlaza) -------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        render.confirm_buyer("Ana Pérez", "ana_p@correo.com"),
        render.order_placed("S00001", "ana_p@correo.com", "COT-1"),
    ],
)
def test_el_correo_va_sin_enfasis_para_que_whatsapp_lo_enlace_bien(message: render.Message) -> None:
    # Assert: ningún «*» ni «_» pegado al correo.
    assert "ana_p@correo.com" in message.text
    assert re.search(r"[*_]ana_p@correo\.com|ana_p@correo\.com[*_]", message.text) is None
