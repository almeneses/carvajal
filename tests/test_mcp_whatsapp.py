"""Pruebas del servidor MCP para WhatsApp (``apps/mcp``).

Sin red, sin Postgres y sin LLM: el servicio de chat es un doble que emite los mismos
``ChatEvent`` que ``ChatService.stream``. Cubren:

- identidad: ``owner_key`` opaco, estable y normalizado por chat; nunca el número en claro;
- turno: texto, fuentes y cotizaciones por folio, en formato de WhatsApp;
- límites: mensaje vacío o largo, cupo diario agotado, conversación nueva;
- fallos: error antes y después del primer token, excepción del servicio o de la consulta
  de cotizaciones, sin trazas internas hacia el canal;
- servidor: la herramienta expuesta, el bearer obligatorio y ``/live`` sin token.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from apps.api.chat.streaming import ChatEvent
from apps.mcp import bridge as bridge_module
from apps.mcp.bridge import (
    EMPTY_MESSAGE,
    LIMIT_REACHED,
    NEW_CONVERSATION,
    SERVICE_UNAVAILABLE,
    TOO_LONG_MESSAGE,
    WhatsAppBridge,
    owner_key_for,
    to_whatsapp,
)
from apps.mcp.server import TOOL_NAME, build_app, build_server
from carvajal.adapters.storage import tables
from carvajal.application.quota import QuotaResult

SECRET = b"secreto-de-prueba"

QUOTE = {
    "kind": "cotizacion",
    "city": "Cali",
    "primary": {
        "name": "Vaso de papel 12 oz",
        "currency": "COP",
        "presentation": {
            "basis": "Caja",
            "units": 1000,
            "price_per_presentation": 185000,
            "subtotal": 370000,
        },
    },
    "commercial": {"tax": {"status": "incluido"}},
    "total": 370000,
    "quote": {"folio": "Q-ABC123"},
}


@dataclass
class FakeChat:
    """Doble de ``ChatService``: registra las llamadas y emite eventos guionizados."""

    events: list[ChatEvent] = field(default_factory=list)
    error: Exception | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        self.calls.append((owner_key, question))
        if self.error is not None:
            raise self.error
        yield from self.events

    def new_conversation(self, owner_key: str) -> Any:
        self.created.append(owner_key)
        return object()


def _turn(
    text: str, *, sources: list[dict[str, Any]] | None = None, folios: Sequence[str] = ()
) -> list[ChatEvent]:
    return [
        ChatEvent("open", {"stream": "chat"}),
        ChatEvent("meta", {"sources": sources or []}),
        ChatEvent("token", {"text": text}),
        ChatEvent("done", {"quote_ids": list(folios)}),
    ]


def _bridge(chat: FakeChat, **kwargs: Any) -> WhatsAppBridge:
    return WhatsAppBridge(chat=chat, secret=SECRET, **kwargs)


# --- identidad -----------------------------------------------------------------------------


def test_owner_key_es_opaco_estable_y_normalizado() -> None:
    key = owner_key_for("+57 310-000 (0000)", SECRET)

    assert key == owner_key_for("+573100000000", SECRET)
    assert key.startswith("wa_") and len(key) == 35
    assert "3100000000" not in key
    assert key != owner_key_for("+573100000001", SECRET)
    assert key != owner_key_for("+573100000000", b"otro-secreto")


def test_cada_chat_usa_su_propio_owner_key() -> None:
    chat = FakeChat(events=_turn("Hola"))
    bridge = _bridge(chat)

    bridge.consult("hola", "+573100000000")
    bridge.consult("hola", "120363000000000000@g.us")

    assert chat.calls[0][0] != chat.calls[1][0]
    assert all("310" not in owner for owner, _ in chat.calls)


# --- turno ---------------------------------------------------------------------------------


def test_turno_con_fuentes_en_formato_whatsapp() -> None:
    sources = [
        {"title": "Historia de Carvajal", "url": "https://www.carvajal.com/historia"},
        {"title": "Historia de Carvajal", "url": "https://www.carvajal.com/historia"},
        {"title": "Sostenibilidad", "url": ""},
    ]
    chat = FakeChat(events=_turn("## Historia\n**Carvajal** nació en 1904 [1].", sources=sources))

    answer = _bridge(chat).consult("¿Cuándo nació Carvajal?", "+573100000000")

    assert answer.startswith("*Historia*\n*Carvajal* nació en 1904 [1].")
    assert "*Fuentes:*" in answer
    assert "[1] Historia de Carvajal — https://www.carvajal.com/historia" in answer
    assert answer.count("carvajal.com/historia") == 1
    assert "[3] Sostenibilidad" in answer
    assert chat.calls == [(owner_key_for("+573100000000", SECRET), "¿Cuándo nació Carvajal?")]


def test_cotizacion_se_redacta_desde_el_folio_guardado() -> None:
    chat = FakeChat(events=_turn("Te comparto la cotización [1].", folios=["Q-ABC123"]))
    looked: list[tuple[str, str]] = []

    def quotes(folio: str, owner: str) -> Mapping[str, Any] | None:
        looked.append((folio, owner))
        return QUOTE

    answer = _bridge(chat, quotes=quotes).consult("2000 vasos en Cali", "+573100000000")

    assert looked == [("Q-ABC123", owner_key_for("+573100000000", SECRET))]
    assert "Cotización en Cali (Folio #Q-ABC123):" in answer
    assert "- Producto: Vaso de papel 12 oz" in answer
    assert "- Total:" in answer


def test_folio_sin_registro_o_consulta_fallida_no_rompe_el_turno() -> None:
    chat = FakeChat(events=_turn("Listo.", folios=["Q-1", "Q-2"]))

    def quotes(folio: str, _owner: str) -> Mapping[str, Any] | None:
        if folio == "Q-1":
            return None
        raise RuntimeError("base caída")

    assert _bridge(chat, quotes=quotes).consult("cotiza", "+57300") == "Listo."


def test_respaldo_determinista_se_entrega_como_texto() -> None:
    events = [ChatEvent("fallback", {"text": "No pude redactar la respuesta."})]
    chat = FakeChat(events=[*events, ChatEvent("done", {})])

    assert _bridge(chat).consult("hola", "+57300") == "No pude redactar la respuesta."


# --- límites -------------------------------------------------------------------------------


def test_mensaje_vacio_no_llama_al_agente() -> None:
    chat = FakeChat()

    assert _bridge(chat).consult("   ", "+57300") == EMPTY_MESSAGE
    assert chat.calls == []


def test_mensaje_largo_no_llama_al_agente() -> None:
    chat = FakeChat()

    assert _bridge(chat).consult("x" * 501, "+57300") == TOO_LONG_MESSAGE
    assert chat.calls == []


def test_cupo_agotado_no_llama_al_agente() -> None:
    chat = FakeChat(events=_turn("Hola"))
    charged: list[str] = []

    def quota(owner: str) -> QuotaResult:
        charged.append(owner)
        return QuotaResult(allowed=False, retry_after=60)

    assert _bridge(chat, quota=quota).consult("hola", "+57300") == LIMIT_REACHED
    assert charged == [owner_key_for("+57300", SECRET)]
    assert chat.calls == []


def test_conversacion_nueva_sin_mensaje_solo_confirma() -> None:
    chat = FakeChat(events=_turn("Hola"))

    answer = _bridge(chat).consult("", "+57300", new_conversation=True)

    assert answer == NEW_CONVERSATION
    assert chat.created == [owner_key_for("+57300", SECRET)]
    assert chat.calls == []


def test_conversacion_nueva_con_mensaje_responde_en_el_hilo_nuevo() -> None:
    chat = FakeChat(events=_turn("Hola de nuevo"))

    answer = _bridge(chat).consult("hola", "+57300", new_conversation=True)

    assert answer == "Hola de nuevo"
    assert len(chat.created) == 1 and len(chat.calls) == 1


def test_identificador_de_chat_invalido() -> None:
    with pytest.raises(ValueError):
        _bridge(FakeChat()).consult("hola", "   ")
    with pytest.raises(ValueError):
        _bridge(FakeChat()).consult("hola", "x" * 201)


# --- fallos --------------------------------------------------------------------------------


def test_error_antes_del_primer_token() -> None:
    chat = FakeChat(events=[ChatEvent("error", {"message": "detalle interno"})])

    assert _bridge(chat).consult("hola", "+57300") == SERVICE_UNAVAILABLE


def test_error_despues_del_primer_token_avisa_que_quedo_incompleto() -> None:
    chat = FakeChat(
        events=[
            ChatEvent("token", {"text": "Carvajal tiene"}),
            ChatEvent("error", {"message": "x", "incomplete": True}),
        ]
    )

    answer = _bridge(chat).consult("hola", "+57300")

    assert answer.startswith("Carvajal tiene")
    assert "incompleta" in answer


def test_excepcion_del_servicio_no_llega_al_canal() -> None:
    chat = FakeChat(error=RuntimeError("password=secreta"))

    answer = _bridge(chat).consult("hola", "+57300")

    assert answer == SERVICE_UNAVAILABLE
    assert "secreta" not in answer


# --- formato -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("**Total:** $370.000 [2]", "*Total:* $370.000 [2]"),
        ("* uno\n* dos", "- uno\n- dos"),
        ("### Sedes", "*Sedes*"),
        ("Ver [la ficha](https://carvajal.com/x).", "Ver la ficha (https://carvajal.com/x)."),
        ("a\n\n\n\nb\n---\n", "a\n\nb"),
        ("*ya en formato*", "*ya en formato*"),
    ],
)
def test_to_whatsapp(markdown: str, expected: str) -> None:
    assert to_whatsapp(markdown) == expected


# --- servidor ------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_servidor_expone_una_sola_herramienta() -> None:
    server = build_server(_bridge(FakeChat(events=_turn("Hola"))))

    tools = await server.list_tools()

    assert [tool.name for tool in tools] == [TOOL_NAME]
    schema = tools[0].input_schema
    assert set(schema["required"]) == {"mensaje", "conversacion"}
    assert "nueva" in schema["properties"]


@pytest.mark.anyio
async def test_la_herramienta_ejecuta_el_turno() -> None:
    chat = FakeChat(events=_turn("Hola, soy el asistente."))
    server = build_server(_bridge(chat))

    result = await server.call_tool(TOOL_NAME, {"mensaje": "hola", "conversacion": "+57300"})

    assert isinstance(result, CallToolResult)
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    assert texts == ["Hola, soy el asistente."]
    assert chat.calls == [(owner_key_for("+57300", SECRET), "hola")]


def test_app_exige_token_para_arrancar() -> None:
    with pytest.raises(RuntimeError):
        build_app(build_server(_bridge(FakeChat())), "  ")


def test_mcp_exige_bearer_y_live_queda_abierto() -> None:
    app = build_app(build_server(_bridge(FakeChat())), "token-correcto")

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/live").text == "ok"
        assert client.post("/mcp", json={}).status_code == 401
        wrong = {"Authorization": "Bearer otro"}
        assert client.post("/mcp", json={}, headers=wrong).status_code == 401
        good = {
            "Authorization": "Bearer token-correcto",
            "Accept": "application/json, text/event-stream",
        }
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "prueba", "version": "0"},
            },
        }
        response = client.post("/mcp", json=initialize, headers=good)
        assert response.status_code == 200


def test_mcp_rechaza_host_que_no_es_loopback() -> None:
    app = build_app(build_server(_bridge(FakeChat())), "token")
    headers = {
        "Authorization": "Bearer token",
        "Accept": "application/json, text/event-stream",
    }

    with TestClient(app, base_url="http://evil.example") as client:
        response = client.post("/mcp", json={}, headers=headers)

    assert response.status_code in {400, 403, 421}


def test_modulo_no_importa_el_servicio_real() -> None:
    # Importar el puente no debe abrir conexiones ni construir el runtime.
    assert not hasattr(bridge_module, "default_chat_service")


# --- extremo a extremo con el servicio real ------------------------------------------------


@pytest.fixture()
def sqlite_engine() -> Iterator[Any]:
    """SQLite en memoria con el esquema real (como ``tests/test_chat_service_tools.py``)."""

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
        tables.metadata.drop_all(motor)
        motor.dispose()


def test_extremo_a_extremo_con_chat_service_y_cotizaciones_guardadas(sqlite_engine: Any) -> None:
    """``ChatService`` real sobre SQLite: el folio emitido se lee de ``quotes`` y se redacta."""

    from tests.test_chat_service_tools import (
        FakeToolRunner,
        build_service,
        scripted_events,
        two_quotes_final,
    )

    from carvajal.adapters.storage.database import build_session_factory
    from carvajal.adapters.storage.quotes import SqlQuoteRepository

    service = build_service(sqlite_engine, FakeToolRunner(scripted_events(two_quotes_final())))
    factory = build_session_factory(sqlite_engine)

    def quotes(folio: str, owner: str) -> Mapping[str, Any] | None:
        with factory() as session:
            record = SqlQuoteRepository(session).get(folio, owner)
            return None if record is None else dict(record.payload)

    answer = WhatsAppBridge(chat=service, secret=SECRET, quotes=quotes).consult(
        "Cotiza 50 y 100 vasos en Bogotá", "+573100000000"
    )

    assert answer.count("Cotización en Bogotá (Folio #") == 2
    assert "- Producto: Vaso rígido" in answer
    assert "3.751" in answer and "7.502" in answer
    assert "*Fuentes:*" in answer
