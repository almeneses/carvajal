"""El pedido de la spec 056 por WhatsApp: la herramienta MCP y los atajos del puente.

Cubren los huecos del canal:

- la descripción de ``consultar_carvajal`` explica el pedido y su efecto externo (#5);
- «PDF» a mitad del pedido entrega la cotización y recuerda el paso, sin crear el pedido
  ni correr un turno (#6, #13);
- con el pedido confirmado, «PDF» o «mándame el recibo» reenvían el recibo (#14);
- en el folio, la oferta del pedido cierra el mensaje, después del PDF adjunto (#15).

Los flujos de punta a punta usan el ``ChatService`` real sobre SQLite, como
``tests/test_pedidos_recibo.py``; los casos de borde, un doble del servicio.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.test_chat_service_tools import assistant_rows, stored_quotes
from tests.test_pedidos_odoo import FakeOrders, _service
from tests.test_pedidos_recibo import _quote_readers

from apps.api.chat.streaming import ChatEvent
from apps.api.quote_links import link_expiry, order_pdf_url, pdf_url
from apps.mcp.bridge import (
    ATTACH_PDF_PREFIX,
    NO_QUOTE_FOR_PDF,
    PDF_CONFIRMATION,
    WhatsAppBridge,
    _Turn,
    is_receipt_request,
    owner_key_for,
    to_whatsapp,
)
from apps.mcp.server import INSTRUCTIONS, TOOL_DESCRIPTION, TOOL_NAME, build_server
from carvajal.adapters.storage import tables
from carvajal.quotation import render
from carvajal.speech import TEXT_HEADER

SECRET = b"secreto-de-prueba"
CHAT = "+573004445566"
MEDIA = "http://203.0.113.10:8080"
BASE = "https://carvajal.example"
ORDER = "S00001"
QUOTE_PDF = "/cotizaciones/"
RECEIPT_PDF = "/pedidos/"


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


class NamedOrders(FakeOrders):
    """``FakeOrders`` que también responde ``order_name`` (el número del pedido en Odoo)."""

    def __init__(self) -> None:
        super().__init__()
        self.lookups: list[str] = []

    def order_name(self, folio: str) -> str | None:
        self.lookups.append(folio)
        return ORDER if any(request.folio == folio for request in self.requests) else None


def _pdf_attachment(folio: str, owner: str, valid_until: date | None) -> str:
    return pdf_url(MEDIA, folio, owner, link_expiry(valid_until), SECRET)


def _order_attachment(folio: str, owner: str) -> str:
    return order_pdf_url(MEDIA, folio, owner, link_expiry(None), SECRET)


def _whatsapp(engine: Any, orders: Any, **links: Any) -> tuple[WhatsAppBridge, Any, Any]:
    """El puente con adjuntos (como con ``MEDIA_BASE_URL``) sobre el servicio real."""

    service, runner = _service(engine, orders)
    readers = _quote_readers(engine)
    links = links or {"pdf_attachment": _pdf_attachment, "order_attachment": _order_attachment}
    bridge = WhatsAppBridge(
        chat=service,
        secret=SECRET,
        quotes=readers["quotes"],
        latest_quote=readers["latest"],
        **links,
    )
    return bridge, service, runner


# Mensajes hasta cada paso del pedido (tras la cotización registrada con el «sí»).
QUOTED = ("cotiza 50 vasos en Bogotá", "no", "sí")
STEPS = {
    "pedido": QUOTED,
    "datos_pedido": (*QUOTED, "hagamos el pedido"),
    "confirmar_pedido": (*QUOTED, "hagamos el pedido", "Ana Pérez, ana@correo.com"),
}
PLACED = (*STEPS["confirmar_pedido"], "sí")


def _say_all(bridge: WhatsAppBridge, messages: tuple[str, ...]) -> None:
    for message in messages:
        bridge.respond(message, CHAT)


def _attached(reply_text: str) -> list[str]:
    return [
        line.removeprefix(ATTACH_PDF_PREFIX)
        for line in reply_text.splitlines()
        if line.startswith(ATTACH_PDF_PREFIX)
    ]


# --- #5: la herramienta MCP explica el pedido -----------------------------------------------


def test_la_descripcion_de_la_herramienta_explica_el_pedido() -> None:
    # Assert: pedir nombre y correo es legítimo, se pasan tal cual y el recibo es un adjunto.
    for needle in ("pedido", "nombre", "correo", "¿Lo confirmo?", "tal cual", "ADJUNTAR_PDF"):
        assert needle in TOOL_DESCRIPTION
    assert "nunca confirmes por el cliente" in TOOL_DESCRIPTION
    assert "«sí, pero…» no es «sí»" in TOOL_DESCRIPTION


def test_las_instrucciones_del_servidor_cubren_el_pedido_y_el_adjunto() -> None:
    # Assert
    assert "nombre" in INSTRUCTIONS and "correo" in INSTRUCTIONS
    assert "nunca confirmes por el cliente" in INSTRUCTIONS
    assert "ADJUNTAR_PDF" in INSTRUCTIONS


@pytest.mark.anyio
async def test_la_herramienta_declara_su_efecto_externo() -> None:
    # Arrange
    server = build_server(WhatsAppBridge(chat=SilentChat(), secret=SECRET))

    # Act
    (tool,) = await server.list_tools()

    # Assert: crea el pedido en Odoo, que envía el correo: no es un sistema cerrado.
    assert tool.name == TOOL_NAME
    assert tool.annotations is not None
    assert tool.annotations.open_world_hint is True
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False


# --- #6 y #13: «PDF» a mitad del pedido ---------------------------------------------------


@pytest.mark.parametrize("step", list(STEPS))
@pytest.mark.parametrize("message", ["PDF", "sí, mándame el PDF"])
def test_pdf_a_mitad_del_pedido_entrega_la_cotizacion_y_recuerda_el_paso(
    engine: Any, step: str, message: str
) -> None:
    # Arrange
    orders = NamedOrders()
    bridge, service, runner = _whatsapp(engine, orders)
    _say_all(bridge, STEPS[step])
    owner = owner_key_for(CHAT, SECRET)
    rows_before = len(assistant_rows(engine))
    calls_before = len(runner.calls)

    # Act
    reply = bridge.respond(message, CHAT)

    # Assert: el PDF de la cotización y, al final, la pregunta pendiente del pedido.
    (row,) = stored_quotes(engine)
    reminder = render.order_reminder(step)
    assert reminder is not None
    assert f"📄 Te envío tu cotización #{row['id']} en PDF" in reply.text
    (url,) = _attached(reply.text)
    assert url.startswith(f"{MEDIA}{QUOTE_PDF}{row['id']}.pdf?t=")
    assert reply.text.endswith(to_whatsapp(reminder.text))
    # Sin turno ni pedido: el borrador sigue en el mismo paso.
    assert orders.requests == []
    assert len(assistant_rows(engine)) == rows_before
    assert len(runner.calls) == calls_before
    assert service.pending_order_step(owner) == step


def test_tras_el_pdf_el_si_de_la_confirmacion_sigue_creando_el_pedido(engine: Any) -> None:
    # Arrange
    orders = NamedOrders()
    bridge, _, _ = _whatsapp(engine, orders)
    _say_all(bridge, STEPS["confirmar_pedido"])
    bridge.respond("sí, mándame el PDF", CHAT)

    # Act
    placed = bridge.respond("sí", CHAT)

    # Assert
    assert f"*Pedido {ORDER} confirmado*" in placed.text
    assert len(orders.requests) == 1


def test_pdf_por_voz_a_mitad_del_pedido_lee_el_recordatorio_y_envia_el_adjunto(
    engine: Any,
) -> None:
    # Arrange
    orders = NamedOrders()
    bridge, _, _ = _whatsapp(engine, orders)
    _say_all(bridge, STEPS["confirmar_pedido"])

    # Act: «pe de efe» es como el STT suele oír «PDF».
    reply = bridge.respond("sí, mándame el pe de efe", CHAT, voice=True)
    said = reply.voice_message()

    # Assert: la voz dice el recordatorio y la línea del adjunto llega por escrito.
    reminder = render.order_reminder("confirmar_pedido")
    assert reminder is not None
    spoken, _, written = said.partition(TEXT_HEADER)
    assert reminder.spoken in spoken
    assert ATTACH_PDF_PREFIX not in spoken
    assert len(_attached(written)) == 1
    assert orders.requests == []


@dataclass
class SilentChat:
    """Doble del servicio: registra los turnos y responde los pasos del pedido a pedido."""

    step: str | None = None
    awaiting: bool = False
    order: str | None = None
    step_error: Exception | None = None
    calls: list[str] = field(default_factory=list)
    lookups: list[str] = field(default_factory=list)

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        self.calls.append(question)
        yield ChatEvent("token", {"text": "Respuesta del turno."})
        yield ChatEvent("done", {"quote_ids": []})

    def new_conversation(self, owner_key: str) -> Any:
        return object()

    def pending_order_step(self, owner_key: str) -> str | None:
        if self.step_error is not None:
            raise self.step_error
        return self.step

    def awaiting_confirmation(self, owner_key: str) -> bool:
        return self.awaiting

    def order_of(self, folio: str) -> str | None:
        self.lookups.append(folio)
        return self.order


LATEST = {
    "kind": "cotizacion",
    "primary": {"sku": "V-12", "name": "Vaso 12 oz"},
    "quote": {"folio": "Q-ORDER1", "valid_until": "2026-12-26"},
}


def _double_bridge(chat: SilentChat, **links: Any) -> WhatsAppBridge:
    links = links or {
        "pdf_attachment": lambda folio, owner, until: f"{MEDIA}{QUOTE_PDF}{folio}.pdf",
        "order_attachment": lambda folio, owner: f"{MEDIA}{RECEIPT_PDF}{folio}.pdf",
    }
    return WhatsAppBridge(chat=chat, secret=SECRET, latest_quote=lambda owner: LATEST, **links)


def test_sin_pedido_pendiente_el_pdf_con_cotizacion_por_confirmar_la_confirma() -> None:
    # Arrange: spec 054 sin cambios: en «confirmar», el PDF es el «sí» de la cotización.
    chat = SilentChat(step=None, awaiting=True)

    # Act
    _double_bridge(chat).respond("sí, mándame el PDF", CHAT)

    # Assert
    assert chat.calls == [PDF_CONFIRMATION]
    assert chat.lookups == []


def test_si_no_se_puede_leer_el_paso_el_pdf_sigue_como_antes() -> None:
    # Arrange
    chat = SilentChat(step_error=RuntimeError("base caída"))

    # Act
    reply = _double_bridge(chat).respond("PDF", CHAT)

    # Assert
    assert chat.calls == []
    assert "↩️" not in reply.text
    assert _attached(reply.text) == [f"{MEDIA}{QUOTE_PDF}Q-ORDER1.pdf"]


def test_el_recordatorio_va_aunque_el_pdf_no_este_disponible() -> None:
    # Arrange: sin cotización legible, el paso pendiente igual se recuerda.
    chat = SilentChat(step="datos_pedido")
    bridge = WhatsAppBridge(
        chat=chat,
        secret=SECRET,
        latest_quote=lambda owner: None,
        pdf_attachment=lambda folio, owner, until: f"{MEDIA}{QUOTE_PDF}{folio}.pdf",
    )

    # Act
    reply = bridge.respond("PDF", CHAT)

    # Assert
    reminder = render.order_reminder("datos_pedido")
    assert reminder is not None
    assert reply.text.startswith(NO_QUOTE_FOR_PDF)
    assert reply.text.endswith(to_whatsapp(reminder.text))
    assert chat.calls == []


# --- #14: el recibo se puede volver a pedir -----------------------------------------------


@pytest.mark.parametrize("message", ["PDF", "mándame el recibo", "el PDF del pedido"])
def test_tras_el_pedido_el_pdf_o_el_recibo_reenvian_el_recibo(engine: Any, message: str) -> None:
    # Arrange
    orders = NamedOrders()
    bridge, _, runner = _whatsapp(engine, orders)
    _say_all(bridge, PLACED)
    calls_before = len(runner.calls)

    # Act
    reply = bridge.respond(message, CHAT)

    # Assert: el recibo del pedido (no la cotización), sin turno ni otro pedido.
    (row,) = stored_quotes(engine)
    folio = row["id"]
    assert reply.text.startswith(f"📄 Te envío el recibo de tu pedido {ORDER} en PDF.")
    (url,) = _attached(reply.text)
    assert url.startswith(f"{MEDIA}{RECEIPT_PDF}{folio}.pdf?t=")
    assert QUOTE_PDF not in reply.text
    assert orders.lookups[-1] == folio
    assert len(orders.requests) == 1
    assert len(runner.calls) == calls_before


def test_tras_el_pedido_el_pdf_de_la_cotizacion_sigue_siendo_la_cotizacion(engine: Any) -> None:
    # Arrange
    orders = NamedOrders()
    bridge, _, _ = _whatsapp(engine, orders)
    _say_all(bridge, PLACED)

    # Act
    reply = bridge.respond("el PDF de la cotización", CHAT)

    # Assert
    (row,) = stored_quotes(engine)
    (url,) = _attached(reply.text)
    assert url.startswith(f"{MEDIA}{QUOTE_PDF}{row['id']}.pdf?t=")
    assert "recibo" not in reply.text


def test_sin_numero_de_pedido_en_el_crm_el_pdf_es_la_cotizacion(engine: Any) -> None:
    # Arrange: un CRM sin ``order_name`` (o sin pedido) no cambia el «PDF» de siempre.
    orders = FakeOrders()
    bridge, _, _ = _whatsapp(engine, orders)
    _say_all(bridge, PLACED)

    # Act
    reply = bridge.respond("PDF", CHAT)

    # Assert
    (row,) = stored_quotes(engine)
    (url,) = _attached(reply.text)
    assert url.startswith(f"{MEDIA}{QUOTE_PDF}{row['id']}.pdf?t=")


def test_sin_adjuntos_el_recibo_se_reenvia_como_enlace() -> None:
    # Arrange
    chat = SilentChat(order=ORDER)
    bridge = _double_bridge(
        chat,
        pdf_link=lambda folio, owner, until: f"{BASE}{QUOTE_PDF}{folio}.pdf",
        order_link=lambda folio, owner: f"{BASE}{RECEIPT_PDF}{folio}.pdf?t=1.firma",
    )

    # Act
    reply = bridge.respond("PDF", CHAT, voice=True)

    # Assert: el enlace va solo en su línea y la voz lo manda aparte.
    url = f"{BASE}{RECEIPT_PDF}Q-ORDER1.pdf?t=1.firma"
    assert reply.text == f"📄 Recibo de tu pedido {ORDER} en PDF. Ábrelo aquí:\n\n{url}"
    assert [link.url for link in reply.voice().links] == [url]
    assert chat.lookups == ["Q-ORDER1"]
    assert chat.calls == []


def test_si_el_recibo_no_se_puede_firmar_el_pdf_es_la_cotizacion() -> None:
    # Arrange
    def broken(folio: str, owner: str) -> str:
        raise RuntimeError("sin secreto")

    chat = SilentChat(order=ORDER)
    bridge = _double_bridge(
        chat,
        pdf_attachment=lambda folio, owner, until: f"{MEDIA}{QUOTE_PDF}{folio}.pdf",
        order_attachment=broken,
    )

    # Act
    reply = bridge.respond("PDF", CHAT)

    # Assert
    assert _attached(reply.text) == [f"{MEDIA}{QUOTE_PDF}Q-ORDER1.pdf"]


def test_el_recibo_sin_pedido_sigue_al_turno_del_chat() -> None:
    # Arrange: «mándame el recibo» sin pedido confirmado no es un atajo.
    chat = SilentChat(order=None)

    # Act
    reply = _double_bridge(chat).respond("mándame el recibo", CHAT)

    # Assert
    assert chat.calls == ["mándame el recibo"]
    assert reply.text == "Respuesta del turno."


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("mándame el recibo", True),
        ("el recibo de mi compra por favor", True),
        ("el PDF del pedido", True),
        ("Recibo", True),
        ("¿cuándo recibo el pedido?", False),
        ("PDF", False),
        ("¿el recibo trae el IVA?", False),
        ("quiero hacer el pedido", False),
    ],
)
def test_is_receipt_request(message: str, expected: bool) -> None:
    # Act / Assert
    assert is_receipt_request(message) is expected


# --- #15: la oferta del pedido cierra el mensaje del folio ---------------------------------


def test_el_folio_por_whatsapp_termina_en_la_oferta_del_pedido(engine: Any) -> None:
    # Arrange
    bridge, _, _ = _whatsapp(engine, NamedOrders())
    _say_all(bridge, QUOTED[:-1])

    # Act
    reply = bridge.respond("sí", CHAT)

    # Assert: primero el PDF adjunto y al final la pregunta pendiente, una sola vez.
    offer = to_whatsapp(render.ORDER_OFFER)
    assert reply.quote_ids
    assert reply.text.endswith(f"\n\n{offer}")
    assert reply.text.count(offer) == 1
    assert reply.text.index(ATTACH_PDF_PREFIX) < reply.text.index(offer)
    assert "Te envío el PDF." not in reply.text


def test_sin_crm_el_folio_por_whatsapp_termina_en_el_pdf(engine: Any) -> None:
    # Arrange
    bridge, _, _ = _whatsapp(engine, None)
    _say_all(bridge, QUOTED[:-1])

    # Act
    reply = bridge.respond("sí", CHAT)

    # Assert
    assert to_whatsapp(render.ORDER_OFFER) not in reply.text
    assert reply.text.splitlines()[-1].startswith(ATTACH_PDF_PREFIX)


def test_la_oferta_va_despues_de_las_fotos_aunque_no_haya_pdf() -> None:
    # Arrange: sin enlaces del PDF y con foto, la oferta sigue siendo lo último.
    template = f"Cotización registrada #F1.\n📄 Te envío el PDF.\n{render.ORDER_OFFER}"
    flow = {"text": template, "spoken": "Listo.", "photos": ["V-12"], "order_offer": True}
    bridge = WhatsAppBridge(
        chat=SilentChat(), secret=SECRET, photo_url=lambda sku: f"{BASE}/fotos/{sku}.jpg"
    )

    # Act
    reply = bridge._render_flow(_Turn(template, [], ["F1"], None, flow), "wa_dueno")

    # Assert
    offer = to_whatsapp(render.ORDER_OFFER)
    assert reply.text.endswith(f"📷 Foto: {BASE}/fotos/V-12.jpg\n\n{offer}")
    assert "Te envío el PDF." not in reply.text
