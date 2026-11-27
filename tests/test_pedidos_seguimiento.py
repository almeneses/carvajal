"""Después y alrededor del pedido en Odoo (spec 056): seguimiento, máscara y consultas.

Cubre las respuestas fijas tras el pedido creado («cancela el pedido», «¿cómo va mi
pedido?»), el correo enmascarado sin caracteres de Markdown, la oferta del pedido marcada
en el ``flow`` del folio y las dos consultas que usan los canales: ``pending_order_step``
(el paso del pedido del hilo activo) y ``order_of`` (el número del pedido de un folio).
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.test_chat_service_tools import named, stored_messages
from tests.test_pedidos_odoo import (
    OWNER,
    FakeOdoo,
    FakeOrders,
    _line,
    _orders,
    _registered,
    _say,
    _service,
)

from apps.api.chat.service import _without_buyer
from apps.api.rendering import render_answer
from carvajal.adapters.storage import tables
from carvajal.quotation import flow, render
from carvajal.quotation.draft import QuoteDraft
from carvajal.quotation.order import (
    asks_order_status,
    mask_email,
    spoken_address,
    wants_order_cancel,
)

NOW = datetime.fromisoformat("2026-11-26T10:00:00-05:00")
ORDER = "S00001"


def _ordered() -> QuoteDraft:
    """Cotización registrada con el pedido ya creado en Odoo."""

    return QuoteDraft(lines=(_line(),), state="registrada", folio="COT-1", order=ORDER)


# --- puro: tras el pedido creado -----------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    ["cancela el pedido", "Quiero anular la compra", "anúlalo", "ya no quiero el pedido"],
)
def test_tras_el_pedido_cancelarlo_responde_que_lo_gestiona_el_asesor(message: str) -> None:
    # Act
    decision = flow.before_turn(_ordered(), message, orders=True, now=NOW)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.route == "pedido"
    assert decision.message == render.order_cancel_request(ORDER)
    assert f"**{ORDER}**" in decision.message.text
    assert "no se cancela desde el chat" in decision.message.text
    assert decision.draft.order == ORDER  # el pedido no se pierde


@pytest.mark.parametrize(
    "message",
    ["¿Cómo va mi pedido?", "estado del pedido", "¿cuándo llega mi pedido?", "muéstrame el pedido"],
)
def test_tras_el_pedido_su_estado_lo_da_el_asesor(message: str) -> None:
    # Act
    decision = flow.before_turn(_ordered(), message, orders=True, now=NOW)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.route == "pedido"
    assert decision.message == render.order_status(ORDER)
    assert ORDER in decision.message.text and "asesor" in decision.message.text
    assert decision.draft.order == ORDER


@pytest.mark.parametrize(
    "message", ["cancela el vaso", "quiero hacer el pedido", "gracias", "¿cuánto cuesta?"]
)
def test_lo_que_no_es_cancelar_ni_preguntar_por_el_pedido(message: str) -> None:
    assert not wants_order_cancel(message)
    assert not asks_order_status(message)


# --- máscara del correo ------------------------------------------------------------------


def test_el_correo_enmascarado_no_rompe_la_negrita() -> None:
    # Arrange
    masked = mask_email("lucia.r@empresa.co")

    # Act
    html = str(render_answer(f"- Correo: **{masked}**"))

    # Assert
    assert masked == "l…@empresa.co"
    assert "*" not in masked
    assert f"<strong>{masked}</strong>" in html


def test_el_ejemplo_fijo_de_los_datos_no_se_enmascara() -> None:
    # Arrange
    asking = render.ask_buyer().text

    # Act
    stored = _without_buyer(f"{asking} Escribí otra@empresa.co", [None])

    # Assert
    assert render.EXAMPLE_EMAIL in stored
    assert "o…@empresa.co" in stored and "otra@empresa.co" not in stored


# --- servicio: oferta, voz y consultas -----------------------------------------------------


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


def test_el_folio_marca_la_oferta_del_pedido_al_final(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, FakeOrders())
    list(service.stream(OWNER, "cotiza 50 vasos en Bogotá"))
    _say(service, "no")

    # Act
    events = list(service.stream(OWNER, "sí"))

    # Assert
    done = named(events, "done").data["flow"]
    assert done["order_offer"] is True
    assert done["text"].rstrip().endswith(render.ORDER_OFFER)
    assert done["text"].index(render.PDF_PROMISE) < done["text"].index(render.ORDER_OFFER)


def test_sin_crm_el_folio_no_marca_la_oferta(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, None)
    list(service.stream(OWNER, "cotiza 50 vasos en Bogotá"))
    _say(service, "no")

    # Act
    events = list(service.stream(OWNER, "sí"))

    # Assert
    assert "order_offer" not in named(events, "done").data["flow"]


def test_la_confirmacion_se_oye_con_los_datos_y_se_guarda_sin_ellos(
    engine: Any,
) -> None:
    # Arrange
    service, _ = _service(engine, FakeOrders())
    _registered(service)
    _say(service, "sí")

    # Act
    events = list(service.stream(OWNER, "Lucía Restrepo, lucia.r@empresa.co"))

    # Assert: la persona oye su correo en el turno visible…
    spoken = named(events, "done").data["flow"]["spoken"]
    assert spoken_address("lucia.r@empresa.co") in spoken and "Lucía Restrepo" in spoken
    # …pero el flow guardado no lo trae.
    stored = repr(stored_messages(engine))
    assert "lucia.r@empresa.co" not in stored and "Lucía Restrepo" not in stored
    assert spoken_address("lucia.r@empresa.co") not in stored
    assert "el correo que escribiste" in stored


def test_el_paso_pendiente_del_pedido_sigue_cada_turno(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, FakeOrders())

    # Act
    before = service.pending_order_step(OWNER)
    _registered(service)
    offered = service.pending_order_step(OWNER)
    _say(service, "sí")
    asking = service.pending_order_step(OWNER)
    _say(service, "Ana Pérez, ana@correo.com")
    confirming = service.pending_order_step(OWNER)
    _say(service, "sí")
    placed = service.pending_order_step(OWNER)

    # Assert
    assert before is None
    assert (offered, asking, confirming) == ("pedido", "datos_pedido", "confirmar_pedido")
    assert placed is None


def test_el_paso_pendiente_nunca_lanza(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, FakeOrders())

    def broken() -> Any:
        raise RuntimeError("base caída")

    service._session_factory = cast("Any", broken)

    # Act
    step = service.pending_order_step(OWNER)

    # Assert
    assert step is None


class LookupOrders(FakeOrders):
    def __init__(self, *, names: dict[str, str] | None = None, broken: bool = False) -> None:
        super().__init__()
        self.names = names or {}
        self.broken = broken

    def order_name(self, folio: str) -> str | None:
        if self.broken:
            raise RuntimeError("Odoo no responde")
        return self.names.get(folio)


def test_el_numero_del_pedido_de_un_folio(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, LookupOrders(names={"COT-1": ORDER}))

    # Act
    found = service.order_of("COT-1")
    missing = service.order_of("COT-2")

    # Assert
    assert (found, missing) == (ORDER, None)


@pytest.mark.parametrize("orders", [None, FakeOrders(), LookupOrders(broken=True)])
def test_sin_crm_o_con_el_crm_caido_no_hay_numero(engine: Any, orders: Any) -> None:
    # Arrange
    service, _ = _service(engine, orders)

    # Act
    found = service.order_of("COT-1")

    # Assert
    assert found is None


def test_odoo_da_el_numero_solo_del_pedido_confirmado() -> None:
    # Arrange
    confirmed = _orders(FakeOdoo(order_state="sale"))
    none = _orders(FakeOdoo())

    # Act
    found = confirmed.order_name("COT-1")
    missing = none.order_name("COT-1")

    # Assert
    assert (found, missing) == ("S00003", None)


def test_tras_el_pedido_cancelar_no_pasa_por_el_modelo(engine: Any) -> None:
    # Arrange
    service, runner = _service(engine, FakeOrders())
    _registered(service)
    _say(service, "sí")
    _say(service, "Ana Pérez, ana@correo.com")
    _say(service, "sí")
    calls = len(runner.calls)

    # Act
    cancel = _say(service, "cancela el pedido")
    status = _say(service, "¿cómo va mi pedido?")

    # Assert
    assert cancel == render.order_cancel_request(ORDER).text
    assert status == render.order_status(ORDER).text
    assert len(runner.calls) == calls
