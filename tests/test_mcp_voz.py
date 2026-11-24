"""Pruebas de las respuestas por voz del canal de WhatsApp (``voz=true``).

Sin red, sin Postgres y sin LLM: el servicio de chat es un doble que emite los mismos
``ChatEvent`` que ``ChatService.stream``. Cubren:

- la herramienta MCP con ``voz=true`` y con ``voz=false`` (igual que antes);
- el turno con cotización: versión corta (~15 s) con las cifras exactas, importes y
  unidades en palabras, fuentes en una frase, oferta del PDF hablada y foto como enlace
  aparte;
- el pedido «PDF» por voz: solo el final del folio en voz, el enlace firmado nunca va en el
  texto hablado, y las formas en que un STT oye «PDF» («impedefe», «pe de efe») cuentan.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from apps.api.chat.streaming import ChatEvent
from apps.api.quote_view import quote_view
from apps.mcp.bridge import (
    LIMIT_REACHED,
    PDF_OFFER,
    PDF_OFFER_SPOKEN,
    WhatsAppBridge,
    spoken_quote,
)
from apps.mcp.server import TOOL_NAME, build_server
from carvajal.application.quota import QuotaResult
from carvajal.speech import LINKS_HEADER, split_voice_reply

SECRET = b"secreto-de-prueba"
CHAT = "+573100000000"
PDF_URL = "https://carvajal.example/cotizaciones/a3f09c12b7e4.pdf?e=1&t=firma"
PHOTO_URL = "https://carvajal.example/catalogo/imagenes/VP12.jpg"

QUOTE: dict[str, Any] = {
    "kind": "cotizacion",
    "city": "Cali",
    "primary": {
        "name": "Vaso de papel 12 oz",
        "sku": "VP12",
        "currency": "COP",
        "seller": "Distribuidora del Valle",
        "presentation": {
            "basis": "Caja",
            "units": 1000,
            "count": 2,
            "units_requested": 1800,
            "units_bought": 2000,
            "units_surplus": 200,
            "price_per_presentation": 185000,
            "subtotal": 370000,
        },
        "source": {"name": "Carvajal Empaques", "captured_at": "2026-11-18"},
    },
    "commercial": {"tax": {"status": "incluido", "rate": 0.19}},
    "total": 370000,
    "quote": {"folio": "a3f09c12b7e4", "valid_until": "2026-12-01"},
}
SOURCES = [{"title": "Vasos de papel", "url": "https://colombia.carvajalempaques.com/vasos"}]
MODEL_TEXT = (
    "Te comparto la cotización de **2 cajas** del *Vaso de papel 12 oz* [1]:\n"
    "- Caja x 1.000 und\n"
    "- Precio aprox. $185.000 por caja"
)


@dataclass
class FakeChat:
    events: list[ChatEvent] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def stream(self, owner_key: str, question: str, **_: Any) -> Iterator[ChatEvent]:
        self.calls.append(question)
        yield from self.events

    def new_conversation(self, owner_key: str) -> Any:
        return object()


def _quote_turn() -> list[ChatEvent]:
    return [
        ChatEvent("meta", {"sources": SOURCES}),
        ChatEvent("token", {"text": MODEL_TEXT}),
        ChatEvent("done", {"quote_ids": ["a3f09c12b7e4"]}),
    ]


def _pdf_link(folio: str, _owner: str, _valid_until: date | None) -> str:
    return f"https://carvajal.example/cotizaciones/{folio}.pdf?e=1&t=firma"


def _bridge(chat: FakeChat, **kwargs: Any) -> WhatsAppBridge:
    defaults: dict[str, Any] = {
        "quotes": lambda _folio, _owner: QUOTE,
        "latest_quote": lambda _owner: QUOTE,
        "pdf_link": _pdf_link,
        "photo_url": lambda sku: PHOTO_URL if sku == "VP12" else None,
    }
    return WhatsAppBridge(chat=chat, secret=SECRET, **{**defaults, **kwargs})


# --- turno con cotización ------------------------------------------------------------------


def test_cotizacion_por_voz_sin_marcas_ni_url_y_con_enlaces_aparte() -> None:
    # Arrange
    bridge = _bridge(FakeChat(events=_quote_turn()))

    # Act
    answer = bridge.consult("Cotiza 1800 vasos en Cali", CHAT, voice=True)
    spoken, links = split_voice_reply(answer)

    # Assert
    for mark in ("http", "*", "_", "[1]", "🧾", "💰", "📷", "#", " oz", "$", "COP"):
        assert mark not in spoken, mark
    assert (
        "Son 2 cajas de 1.000 unidades de Vaso de papel 12 onzas en Cali, por un total de "
        "370.000 pesos con IVA incluido. El envío se confirma con el vendedor."
    ) in spoken
    assert "a3f09c12b7e4" not in spoken and "Subtotal" not in spoken
    assert "Según el sitio de Carvajal Empaques." in spoken
    assert spoken.endswith(PDF_OFFER_SPOKEN)
    assert links == [
        f"Foto de Vaso de papel 12 oz: {PHOTO_URL}",
        "Vasos de papel: https://colombia.carvajalempaques.com/vasos",
    ]


def test_cotizacion_sin_voz_no_cambia() -> None:
    # Arrange
    bridge = _bridge(FakeChat(events=_quote_turn()))

    # Act
    answer = bridge.consult("Cotiza 1800 vasos en Cali", CHAT)

    # Assert
    assert answer == bridge.respond("Cotiza 1800 vasos en Cali", CHAT).text
    assert "🧾 *Cotización #a3f09c12b7e4*" in answer
    assert "*Fuentes:*" in answer
    assert answer.endswith(PDF_OFFER)
    assert LINKS_HEADER not in answer


def test_spoken_quote_lee_las_cifras_del_payload() -> None:
    # Arrange
    view = quote_view(QUOTE)
    assert view is not None

    # Act
    spoken = spoken_quote(view, PHOTO_URL)

    # Assert
    assert spoken.text == (
        "Son 2 cajas de 1.000 unidades de Vaso de papel 12 onzas en Cali, por un total de "
        "370.000 pesos con IVA incluido. El envío se confirma con el vendedor."
    )
    assert [link.url for link in spoken.links] == [PHOTO_URL]


def test_spoken_quote_dura_unos_quince_segundos_con_la_oferta_del_pdf() -> None:
    # Arrange
    view = quote_view(QUOTE)
    assert view is not None

    # Act
    words = f"{spoken_quote(view).text} {PDF_OFFER_SPOKEN}".split()

    # Assert: unas 150 palabras por minuto; 45 palabras son unos 18 s.
    assert len(words) <= 45


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {"commercial": {"tax": {"status": "no_incluido"}}},
            "por un total de 370.000 pesos sin incluir IVA.",
        ),
        ({"delivery": {"free": True}}, "El envío es gratis."),
        ({"commercial": {"shipping": {"cost": 12000}}}, "El envío cuesta 12.000 pesos."),
    ],
)
def test_spoken_quote_dice_el_iva_y_el_envio(changes: dict[str, Any], expected: str) -> None:
    # Arrange
    view = quote_view({**QUOTE, **changes})
    assert view is not None

    # Act
    spoken = spoken_quote(view)

    # Assert
    assert expected in spoken.text


def test_el_envio_de_una_cotizacion_en_mxn_se_dice_en_su_moneda() -> None:
    # Arrange
    primary = {**QUOTE["primary"], "currency": "MXN"}
    view = quote_view({**QUOTE, "primary": primary, "commercial": {"shipping": {"cost": 150}}})
    assert view is not None

    # Act
    spoken = spoken_quote(view)

    # Assert: el total y el envío, los dos en pesos mexicanos.
    assert "por un total de 370.000 pesos mexicanos" in spoken.text
    assert "El envío cuesta 150 pesos mexicanos." in spoken.text


def test_cotizacion_con_total_pendiente_lo_dice() -> None:
    # Arrange
    payload = {**QUOTE, "total": None, "pending_lines": ["Falta el costo de envío"]}
    view = quote_view(payload)
    assert view is not None

    # Act
    spoken = spoken_quote(view)

    # Assert
    assert "El total está pendiente: Falta el costo de envío." in spoken.text
    assert spoken.links == ()


# --- pedido del PDF ------------------------------------------------------------------------


def test_pdf_por_voz_separa_el_enlace() -> None:
    # Arrange
    chat = FakeChat()
    bridge = _bridge(chat)

    # Act
    answer = bridge.consult("PDF", CHAT, voice=True)
    spoken, links = split_voice_reply(answer)

    # Assert
    assert chat.calls == []
    assert spoken == "Te mando por escrito el PDF de tu cotización terminada en B 7 E 4."
    assert links == [f"Cotización a3f09c12b7e4 en PDF: {PDF_URL}"]


@pytest.mark.parametrize(
    "message",
    ["impedefe", "MPDF", "pe de efe", "Pé de éfe", "sí, mándame el pe de fe", "el pe de e\u0444e"],
)
def test_pdf_oido_por_el_stt_se_atiende_sin_llm(message: str) -> None:
    # Arrange
    chat = FakeChat()
    bridge = _bridge(chat)

    # Act
    spoken, links = split_voice_reply(bridge.consult(message, CHAT, voice=True))

    # Assert
    assert chat.calls == []
    assert spoken.endswith("terminada en B 7 E 4.")
    assert links == [f"Cotización a3f09c12b7e4 en PDF: {PDF_URL}"]


def test_pdf_oido_por_el_stt_por_escrito_va_al_agente() -> None:
    # Arrange: sin voz, «impedefe» no es un pedido del PDF.
    chat = FakeChat(events=[ChatEvent("token", {"text": "¿Me lo repites?"}), ChatEvent("done", {})])

    # Act
    answer = _bridge(chat).consult("impedefe", CHAT)

    # Assert
    assert chat.calls == ["impedefe"]
    assert answer == "¿Me lo repites?"


def test_pdf_sin_voz_no_cambia() -> None:
    # Act
    answer = _bridge(FakeChat()).consult("PDF", CHAT)

    # Assert
    assert answer == (
        "📄 Aquí está tu cotización #a3f09c12b7e4 en PDF:\n"
        f"{PDF_URL}\n"
        "_El enlace funciona hasta el 1 dic 2026._"
    )


def test_respuestas_cortas_tambien_salen_aptas_para_voz() -> None:
    # Arrange
    def quota(_owner: str) -> QuotaResult:
        return QuotaResult(allowed=False)

    # Act
    answer = _bridge(FakeChat(), quota=quota).consult("hola", CHAT, voice=True)

    # Assert
    assert answer == LIMIT_REACHED


def test_turno_sin_texto_hablable_no_deja_la_voz_vacia() -> None:
    # Arrange
    chat = FakeChat(
        events=[
            ChatEvent("token", {"text": "https://colombia.carvajalempaques.com/vasos"}),
            ChatEvent("done", {}),
        ]
    )

    # Act
    spoken, links = split_voice_reply(_bridge(chat).consult("catálogo", CHAT, voice=True))

    # Assert
    assert spoken == "Te envío la información por escrito."
    assert links == ["https://colombia.carvajalempaques.com/vasos"]


# --- herramienta MCP -----------------------------------------------------------------------


@pytest.mark.anyio
async def test_la_herramienta_acepta_voz_opcional() -> None:
    # Arrange
    server = build_server(_bridge(FakeChat()))

    # Act
    tools = await server.list_tools()

    # Assert
    schema = tools[0].input_schema
    assert set(schema["required"]) == {"mensaje", "conversacion"}
    assert schema["properties"]["voz"]["default"] is False


@pytest.mark.anyio
async def test_la_herramienta_con_voz_devuelve_texto_hablado_y_enlaces() -> None:
    # Arrange
    chat = FakeChat(events=_quote_turn())
    server = build_server(_bridge(chat))

    # Act
    result = await server.call_tool(
        TOOL_NAME, {"mensaje": "Cotiza 1800 vasos", "conversacion": CHAT, "voz": True}
    )

    # Assert
    assert isinstance(result, CallToolResult)
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    assert len(texts) == 1
    spoken, links = split_voice_reply(texts[0])
    assert "http" not in spoken and "por un total de 370.000 pesos" in spoken
    assert len(links) == 2
    assert result.structured_content == {"result": texts[0]}


@pytest.mark.anyio
async def test_la_herramienta_sin_voz_devuelve_lo_mismo_que_antes() -> None:
    # Arrange
    chat = FakeChat(events=_quote_turn())
    expected = _bridge(FakeChat(events=_quote_turn())).consult("Cotiza 1800 vasos", CHAT)
    server = build_server(_bridge(chat))

    # Act
    implicit = await server.call_tool(
        TOOL_NAME, {"mensaje": "Cotiza 1800 vasos", "conversacion": CHAT}
    )
    explicit = await server.call_tool(
        TOOL_NAME, {"mensaje": "Cotiza 1800 vasos", "conversacion": CHAT, "voz": False}
    )

    # Assert
    for result in (implicit, explicit):
        assert isinstance(result, CallToolResult)
        texts = [block.text for block in result.content if isinstance(block, TextContent)]
        assert texts == [expected]
