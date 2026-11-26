"""Pruebas de las respuestas por voz del canal de WhatsApp (``voz=true``).

Sin red, sin Postgres y sin LLM: el servicio de chat es un doble que emite los mismos
``ChatEvent`` que ``ChatService.stream``. Cubren:

- la herramienta MCP con ``voz=true`` y con ``voz=false`` (igual que antes);
- el turno con cotización: versión corta (~15 s) con las cifras exactas, importes y
  unidades en palabras, fuentes en una frase, oferta del PDF hablada y foto como enlace
  aparte;
- el pedido «PDF» por voz: solo el final del folio en voz, el enlace firmado nunca va en el
  texto hablado, y las formas en que un STT oye «PDF» («impedefe», «pe de efe») cuentan;
- el resumen hablado (spec 048): una respuesta larga se resume y la respuesta escrita
  completa va tras ``TEXT_HEADER``; una corta se lee entera, con los enlaces aparte como
  antes; la transcripción llega con los miles al estilo colombiano.
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
    PDF_CONFIRMATION,
    PDF_OFFER,
    PDF_OFFER_SPOKEN,
    SERVICE_UNAVAILABLE,
    WhatsAppBridge,
    spoken_quote,
)
from apps.mcp.server import TOOL_NAME, build_server
from carvajal.application.quota import QuotaResult
from carvajal.speech import LINKS_HEADER, TEXT_HEADER, parse_voice_reply, split_voice_reply

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


def test_cotizacion_por_voz_sin_marcas_ni_url_y_con_el_texto_escrito_aparte() -> None:
    # Arrange
    bridge = _bridge(FakeChat(events=_quote_turn()))

    # Act
    answer = bridge.consult("Cotiza 1800 vasos en Cali", CHAT, voice=True)
    parts = parse_voice_reply(answer)
    spoken = parts.spoken

    # Assert
    for mark in ("http", "*", "_", "[1]", "🧾", "💰", "📷", "#", " oz", "$", "COP"):
        assert mark not in spoken, mark
    assert (
        "Son 2 cajas de 1.000 unidades de Vaso de papel 12 onzas en Cali, por un total de "
        "370.000 pesos con IVA incluido. El envío se confirma con el vendedor."
    ) in spoken
    assert "a3f09c12b7e4" not in spoken and "Subtotal" not in spoken
    assert spoken.startswith("Son 2 cajas"), "la introducción del agente no se repite en voz"
    assert len(spoken.split()) <= 50
    assert "Según el sitio de Carvajal Empaques." in spoken
    assert spoken.endswith(PDF_OFFER_SPOKEN)
    # La cotización completa (folio, subtotal, foto y fuentes) va por escrito, tal cual.
    assert parts.written == bridge.respond("Cotiza 1800 vasos en Cali", CHAT).text
    assert PHOTO_URL in parts.written and "#a3f09c12b7e4" in parts.written
    assert parts.links == (), "los enlaces ya van en el texto escrito"


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
        "📄 Tu cotización #a3f09c12b7e4 en PDF (válida hasta el 1 dic 2026). Ábrela aquí:\n\n"
        f"{PDF_URL}"
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
    parts = parse_voice_reply(texts[0])
    assert "http" not in parts.spoken and "por un total de 370.000 pesos" in parts.spoken
    assert PHOTO_URL in parts.written and "*Fuentes:*" in parts.written
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


# --- resumen hablado (spec 048) --------------------------------------------------------------

RAG_TEXT = (
    "Para bebidas calientes, Carvajal Empaques ofrece vasos de papel de pared sencilla y "
    "doble pared [1]. Son aptos para café, té y chocolate, y se venden en cajas de 1.000 "
    "unidades.\n\n"
    "Estas son las opciones:\n"
    "- **Vaso de papel 8 oz**: pared sencilla, caja x 1.000 und, ideal para tinto.\n"
    "- **Vaso de papel 12 oz**: doble pared, caja x 500 und, aprox. $185.000 por caja.\n"
    "- **Vaso de papel 16 oz** — doble pared, para capuchino y bebidas grandes.\n\n"
    "Todos se pueden combinar con tapas de 80 mm y portavasos.\n\n"
    "¿Cuántas unidades necesitas y en qué ciudad?"
)


def _rag_turn(text: str = RAG_TEXT) -> list[ChatEvent]:
    return [
        ChatEvent("meta", {"sources": SOURCES}),
        ChatEvent("token", {"text": text}),
        ChatEvent("done", {}),
    ]


def test_una_respuesta_larga_se_resume_y_va_completa_por_escrito() -> None:
    # Arrange
    bridge = _bridge(FakeChat(events=_rag_turn()))
    written = _bridge(FakeChat(events=_rag_turn())).consult("vasos para café", CHAT)

    # Act
    answer = bridge.consult("vasos para café", CHAT, voice=True)
    parts = parse_voice_reply(answer)

    # Assert
    assert parts.spoken == (
        "Para bebidas calientes, Carvajal Empaques ofrece vasos de papel de pared sencilla y "
        "doble pared. Son aptos para café, té y chocolate, y se venden en cajas de 1.000 "
        "unidades. Te dejo tres opciones por escrito.\n\n"
        "Según el sitio de Carvajal Empaques.\n\n"
        "Te dejo el detalle por escrito.\n\n"
        "¿Cuántas unidades necesitas y en qué ciudad?"
    )
    assert parts.written == written
    assert "*Fuentes:*" in parts.written and "https://" in parts.written
    assert parts.links == ()
    assert answer.index(TEXT_HEADER) > 0 and LINKS_HEADER not in answer
    spoken, links = split_voice_reply(answer)
    assert (spoken, links) == (parts.spoken, []), "split_voice_reply ignora lo escrito"


def test_una_respuesta_corta_por_voz_no_trae_texto_escrito() -> None:
    # Arrange
    chat = FakeChat(events=_rag_turn("Sí, tenemos vasos de 12 oz para café [1]."))

    # Act
    answer = _bridge(chat).consult("¿tienen vasos?", CHAT, voice=True)
    parts = parse_voice_reply(answer)

    # Assert
    assert TEXT_HEADER not in answer
    assert parts.spoken == (
        "Sí, tenemos vasos de 12 onzas para café.\n\nSegún el sitio de Carvajal Empaques."
    )
    assert parts.links == ("Vasos de papel: https://colombia.carvajalempaques.com/vasos",)


def test_la_transcripcion_por_voz_llega_con_miles_colombianos() -> None:
    # Arrange
    voice_chat = FakeChat(events=_rag_turn("Listo."))
    text_chat = FakeChat(events=_rag_turn("Listo."))

    # Act
    _bridge(voice_chat).consult("Cotiza 1,800 vasos de 12,5 onzas", CHAT, voice=True)
    _bridge(text_chat).consult("Cotiza 1,800 vasos de 12,5 onzas", CHAT)

    # Assert: solo la nota de voz se normaliza; lo escrito por el cliente no se toca.
    assert voice_chat.calls == ["Cotiza 1.800 vasos de 12,5 onzas"]
    assert text_chat.calls == ["Cotiza 1,800 vasos de 12,5 onzas"]


def test_respuesta_larga_sin_voz_no_cambia() -> None:
    # Arrange
    bridge = _bridge(FakeChat(events=_rag_turn()))

    # Act
    answer = bridge.consult("vasos para café", CHAT)

    # Assert
    assert TEXT_HEADER not in answer and LINKS_HEADER not in answer
    assert answer.startswith("Para bebidas calientes, Carvajal Empaques ofrece vasos")
    assert "- *Vaso de papel 8 oz*: pared sencilla" in answer


# --- Spec 054: flujo de cotización por WhatsApp --------------------------------------------

FLOW_TEXT = (
    "✅ **Cotización #a3f09c12b7e4 registrada**\n"
    "2 productos · Total **$281.000** · válida hasta 2 dic\n"
    "📄 Te envío el PDF."
)


@dataclass
class AwaitingChat(FakeChat):
    awaiting: bool = True

    def awaiting_confirmation(self, owner_key: str) -> bool:
        return self.awaiting


def _flow_turn(text: str, *, folios: list[str], spoken: str, prose: str = "") -> list[ChatEvent]:
    body = f"{prose}\n\n{text}" if prose else text
    return [
        ChatEvent("meta", {"sources": []}),
        ChatEvent("fallback", {"text": body}),
        ChatEvent(
            "done",
            {"quote_ids": folios, "flow": {"text": text, "spoken": spoken, "awaiting": None}},
        ),
    ]


def test_la_plantilla_del_flujo_sale_en_formato_whatsapp_y_con_su_voz() -> None:
    # Arrange
    cart = (
        "🛒 **Tu cotización · 2 productos**\n**Total parcial: $281.000** (IVA incl.)\n\n¿Algo más?"
    )
    chat = FakeChat(
        _flow_turn(
            cart,
            folios=[],
            spoken="Llevas 2 productos. ¿Algo más?",
            prose="Te sirve para bebida caliente. El total es $281.000.",
        )
    )

    # Act
    reply = _bridge(chat).respond("sí, 500 vasos", "+573001112233")

    # Assert: las cifras del agente no se repiten; la voz es la de la plantilla.
    assert "🛒 *Tu cotización · 2 productos*" in reply.text
    assert "El total es" not in reply.text
    assert reply.text.endswith("¿Algo más?")
    assert reply.voice().text.endswith("Llevas 2 productos. ¿Algo más?")


def _flow_with_photos(photos: list[str]) -> list[ChatEvent]:
    text = "✅ Agregué **100 Vaso de papel 8 oz**\n1 caja x 1.000 · **$193.459**"
    return [
        ChatEvent("meta", {"sources": []}),
        ChatEvent("fallback", {"text": text}),
        ChatEvent(
            "done",
            {
                "quote_ids": [],
                "flow": {"text": text, "spoken": "Agregué.", "awaiting": None, "photos": photos},
            },
        ),
    ]


def test_el_mensaje_del_flujo_lleva_la_foto_del_producto() -> None:
    # Arrange: 2026-11-25, la cotización por WhatsApp había dejado de traer las fotos.
    chat = FakeChat(_flow_with_photos(["CO-MX-2864632114"]))

    def photo(sku: str) -> str | None:
        return f"https://carvajal.test/catalogo/imagenes/{sku}.jpg"

    # Act
    reply = _bridge(chat, photo_url=photo).respond("2", "+573001112233")

    # Assert: por escrito y como enlace aparte de la nota de voz (la foto no se lee).
    url = "https://carvajal.test/catalogo/imagenes/CO-MX-2864632114.jpg"
    assert f"📷 Foto: {url}" in reply.text
    assert url in [link.url for link in reply.voice().links]
    assert url not in reply.voice().text


def test_sin_foto_en_el_catalogo_el_mensaje_del_flujo_no_cambia() -> None:
    # Arrange: un SKU sin foto y un mensaje guardado antes del cambio (sin «photos»).
    chat = FakeChat(_flow_with_photos(["SIN-FOTO"]))
    old = FakeChat(_flow_turn(FLOW_TEXT, folios=[], spoken="Listo."))

    # Act
    reply = _bridge(chat, photo_url=lambda sku: None).respond("2", "+573001112233")
    previous = _bridge(old, photo_url=lambda sku: "https://x.test/f.jpg").respond(
        "2", "+573001112233"
    )

    # Assert
    assert "📷" not in reply.text
    assert "📷" not in previous.text


def test_el_fallback_reemplaza_lo_recibido_como_en_la_web() -> None:
    # Arrange: el servicio reemplaza una abstención por la plantilla con «fallback».
    text = "✅ Agregué **100 Vaso**"
    chat = FakeChat(
        [
            ChatEvent("meta", {"sources": []}),
            ChatEvent("token", {"text": "No tengo un dato verificado para responder eso."}),
            ChatEvent("fallback", {"text": text}),
            ChatEvent("done", {"quote_ids": [], "flow": {"text": text, "spoken": "Agregué."}}),
        ]
    )

    # Act
    reply = _bridge(chat).respond("2", "+573001112233")

    # Assert
    assert "No tengo un dato verificado" not in reply.text
    assert "✅ Agregué *100 Vaso*" in reply.text


def test_al_registrar_el_pdf_se_adjunta_sin_pedirlo() -> None:
    # Arrange
    chat = FakeChat(_flow_turn(FLOW_TEXT, folios=["a3f09c12b7e4"], spoken="Listo."))

    # Act
    reply = _bridge(chat).respond("sí", "+573001112233")

    # Assert
    assert reply.quote_ids == ("a3f09c12b7e4",)
    assert "📄 Tu cotización #a3f09c12b7e4 en PDF" in reply.text
    assert PDF_URL in reply.text
    assert "Te envío el PDF." not in reply.text
    assert PDF_OFFER not in reply.text


def test_si_mandame_el_pdf_confirma_la_cotizacion_pendiente() -> None:
    # Arrange
    chat = AwaitingChat(_flow_turn(FLOW_TEXT, folios=["a3f09c12b7e4"], spoken="Listo."))

    # Act
    reply = _bridge(chat).respond("sí, mándame el PDF", "+573001112233")

    # Assert: va al turno (que registra) y el PDF sale una sola vez.
    assert chat.calls == [PDF_CONFIRMATION]
    assert reply.text.count(PDF_URL) == 1


@pytest.mark.parametrize(
    ("message", "voice"),
    [
        ("envíame la cotización en pdf por favor", False),  # el flujo no la lee como «sí»
        ("quiero el pdf de la cotización por favor", False),  # ni como «agregar»
        ("sí, mándame el pe de efe", True),  # PDF dictado
    ],
)
def test_todo_pedido_de_pdf_con_cotizacion_pendiente_llega_como_confirmacion(
    message: str, voice: bool
) -> None:
    # Arrange
    chat = AwaitingChat(_flow_turn(FLOW_TEXT, folios=["a3f09c12b7e4"], spoken="Listo."))

    # Act
    reply = _bridge(chat).respond(message, "+573001112233", voice=voice)

    # Assert: el turno recibe la forma que el flujo confirma y el PDF sale una vez.
    assert chat.calls == [PDF_CONFIRMATION]
    assert reply.text.count(PDF_URL) == 1


@pytest.mark.parametrize(
    "message", ["¿el PDF trae IVA?", "mejor 800", "sí pero sin el pdf", "mejor 800 y el pdf"]
)
def test_una_pregunta_o_un_cambio_llega_tal_cual_aunque_haya_cotizacion_pendiente(
    message: str,
) -> None:
    # Arrange
    chat = AwaitingChat(_flow_turn(FLOW_TEXT, folios=[], spoken="Listo."))

    # Act
    _bridge(chat).respond(message, "+573001112233")

    # Assert: solo un pedido del PDF se vuelve confirmación; lo demás no se toca.
    assert chat.calls and chat.calls[0] != PDF_CONFIRMATION


def test_si_el_turno_falla_el_pdf_pendiente_no_manda_una_cotizacion_anterior() -> None:
    # Arrange: hay una cotización por confirmar y el servicio falla en el turno.
    chat = AwaitingChat([ChatEvent("error", {"message": SERVICE_UNAVAILABLE})])

    # Act
    reply = _bridge(chat).respond("PDF", "+573001112233")

    # Assert: error transitorio (el relé reintenta el mismo mensaje), sin el PDF viejo.
    assert reply.text == SERVICE_UNAVAILABLE
    assert PDF_URL not in reply.text


def test_pdf_sin_cotizacion_pendiente_sigue_sin_llm() -> None:
    # Arrange
    chat = AwaitingChat(_quote_turn(), awaiting=False)

    # Act
    reply = _bridge(chat).respond("PDF", "+573001112233")

    # Assert
    assert chat.calls == []
    assert PDF_URL in reply.text
