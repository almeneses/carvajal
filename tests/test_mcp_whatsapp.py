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
from apps.api.quote_view import quote_view
from apps.mcp import bridge as bridge_module
from apps.mcp.bridge import (
    EMPTY_MESSAGE,
    LIMIT_REACHED,
    NEW_CONVERSATION,
    SERVICE_UNAVAILABLE,
    TOO_LONG_MESSAGE,
    WhatsAppBridge,
    owner_key_for,
    quote_remarks,
    to_whatsapp,
    whatsapp_quote,
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
    chat = FakeChat(
        events=_turn("## Historia\n**Carvajal** nació en 1904 [1]; informe [3].", sources=sources)
    )

    answer = _bridge(chat).consult("¿Cuándo nació Carvajal?", "+573100000000")

    # Las citas se renumeran desde 1: «[3]» sin «[2]» pasa a «[2]».
    assert answer.startswith("*Historia*\n*Carvajal* nació en 1904 [1]; informe [2].")
    assert "*Fuentes:*" in answer
    assert "[1] Historia de Carvajal — https://www.carvajal.com/historia" in answer
    assert answer.count("carvajal.com/historia") == 1
    assert "[2] Sostenibilidad" in answer
    assert chat.calls == [(owner_key_for("+573100000000", SECRET), "¿Cuándo nació Carvajal?")]


def test_cotizacion_se_redacta_desde_el_folio_guardado() -> None:
    chat = FakeChat(events=_turn("Te comparto la cotización [1].", folios=["Q-ABC123"]))
    looked: list[tuple[str, str]] = []

    def quotes(folio: str, owner: str) -> Mapping[str, Any] | None:
        looked.append((folio, owner))
        return QUOTE

    answer = _bridge(chat, quotes=quotes).consult("2000 vasos en Cali", "+573100000000")

    assert looked == [("Q-ABC123", owner_key_for("+573100000000", SECRET))]
    assert "🧾 *Cotización #Q-ABC123*" in answer
    assert "*Vaso de papel 12 oz*" in answer
    assert "💰 *Total: $370.000 COP*" in answer


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


@pytest.mark.parametrize(
    "texto", ["/new", "/New", "/nuevo", "Nueva conversación", "empecemos de nuevo."]
)
def test_el_comando_de_reinicio_abre_un_hilo_nuevo_sin_nueva_true(texto: str) -> None:
    # Arrange: OpenClaw reenvía «/new» como texto, sin ``nueva=true`` (2026-11-25).
    chat = FakeChat(events=_turn("Hola"))

    # Act
    answer = _bridge(chat).consult(texto, "+57300")

    # Assert: hilo nuevo, sin turno ni cupo, y la confirmación de siempre.
    assert answer == NEW_CONVERSATION
    assert chat.created == [owner_key_for("+57300", SECRET)]
    assert chat.calls == []


@pytest.mark.parametrize(
    "texto", ["empecemos de nuevo, quiero 100 vasos", "nuevo", "vasos nuevos", "/precios"]
)
def test_un_pedido_que_menciona_nuevo_no_reinicia(texto: str) -> None:
    # Arrange
    chat = FakeChat(events=_turn("Respuesta"))

    # Act
    _bridge(chat).consult(texto, "+57300")

    # Assert
    assert chat.created == []
    assert chat.calls == [(owner_key_for("+57300", SECRET), texto)]


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


def test_health_del_mcp_exige_bearer_y_publica_la_compuerta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: WhatsApp corre el agente en este proceso; sus contadores son propios.
    from carvajal.agent.gatekeeper import stats as stats_module

    stats = stats_module.ClaimsStats()
    stats.record("supported", p=0.9)
    stats.record("unsupported", p=0.2, substituted=True)
    monkeypatch.setattr(stats_module, "CLAIMS_STATS", stats)
    app = build_app(build_server(_bridge(FakeChat())), "token-correcto")

    # Act
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        anonymous = client.get("/health")
        response = client.get("/health", headers={"Authorization": "Bearer token-correcto"})

    # Assert
    assert anonymous.status_code == 401
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert (body["claims"]["judged"], body["claims"]["substituted"]) == (2, 1)
    assert body["claims"]["substitution_rate"] == 0.5


def test_mcp_rechaza_host_que_no_es_loopback() -> None:
    app = build_app(build_server(_bridge(FakeChat())), "token")
    headers = {
        "Authorization": "Bearer token",
        "Accept": "application/json, text/event-stream",
    }

    with TestClient(app, base_url="http://evil.example") as client:
        response = client.post("/mcp", json={}, headers=headers)

    assert response.status_code in {400, 403, 421}


@pytest.mark.parametrize(
    ("base_url", "origin", "status"),
    [
        pytest.param("http://evil.example.com", "", 421, id="host-ajeno"),
        pytest.param("http://127.0.0.1:8765", "http://evil.example.com", 403, id="origin-ajeno"),
    ],
)
def test_health_del_mcp_rechaza_host_u_origin_que_no_es_loopback(
    base_url: str, origin: str, status: int
) -> None:
    # Arrange: la defensa contra DNS rebinding cubre también ``/health``, no solo ``/mcp``.
    app = build_app(build_server(_bridge(FakeChat())), "token")
    headers = {"Authorization": "Bearer token"}
    if origin:
        headers["Origin"] = origin

    # Act
    with TestClient(app, base_url=base_url) as client:
        response = client.get("/health", headers=headers)

    # Assert
    assert response.status_code == status
    assert "claims" not in response.text


def test_health_del_mcp_publica_las_metricas_del_proceso_sin_texto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec 055: la página de métricas de la app lee estos contadores por la red interna."""

    # Arrange
    from carvajal.infrastructure import metrics

    eventos = metrics.EventCounts()
    monkeypatch.setattr(bridge_module, "WHATSAPP_EVENTS", eventos)
    monkeypatch.setattr("apps.mcp.server.WHATSAPP_EVENTS", eventos)
    bridge = _bridge(FakeChat(events=_turn("Hola, soy Carva")))
    bridge.consult("hola, mi número es 3100000000", "+573100000000")
    app = build_app(build_server(bridge), "token-correcto")

    # Act
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.get("/health", headers={"Authorization": "Bearer otro"})
        response = client.get("/health", headers={"Authorization": "Bearer token-correcto"})

    # Assert
    assert response.status_code == 200
    metricas = response.json()["metrics"]
    assert metricas["whatsapp"]["counts"] == {"mensajes": 1, "no_autorizados": 1}
    assert set(metricas) == {"whatsapp", "jev", "deepseek"}
    assert "3100000000" not in response.text
    assert "wa_" not in response.text


def test_health_del_mcp_acepta_el_host_del_servicio_interno_y_mcp_no() -> None:
    # Arrange: la app llega por http://mcp:8765/health en la red de compose.
    app = build_app(build_server(_bridge(FakeChat())), "token")
    headers = {"Authorization": "Bearer token", "Accept": "application/json, text/event-stream"}

    # Act
    with TestClient(app, base_url="http://mcp:8765") as client:
        health = client.get("/health", headers=headers)
        mcp = client.post("/mcp", json={}, headers=headers)

    # Assert
    assert health.status_code == 200
    assert mcp.status_code in {400, 403, 421}


def test_el_bearer_de_health_abre_solo_health_y_no_mcp() -> None:
    """Spec 055: la app web lleva MCP_HEALTH_TOKEN; con él no puede llamar a /mcp."""

    # Arrange
    app = build_app(build_server(_bridge(FakeChat())), "token-mcp", health_token="token-health")
    headers = {
        "Authorization": "Bearer token-health",
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

    # Act
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        health = client.get("/health", headers=headers)
        mcp = client.post("/mcp", json=initialize, headers=headers)
        full = client.get("/health", headers={"Authorization": "Bearer token-mcp"})

    # Assert
    assert health.status_code == 200
    assert mcp.status_code == 401
    assert full.status_code == 200


def test_un_bearer_de_health_igual_a_mcp_token_se_ignora() -> None:
    # Arrange: si fueran iguales, la app web tendría /mcp; el MCP descarta el de /health.
    app = build_app(build_server(_bridge(FakeChat())), "mismo", health_token=" mismo ")

    # Act
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        health = client.get("/health", headers={"Authorization": "Bearer mismo"})
        other = client.get("/health", headers={"Authorization": "Bearer otro"})

    # Assert: MCP_TOKEN sigue abriendo /health y nada más cambia.
    assert health.status_code == 200
    assert other.status_code == 401


def test_un_reintento_de_openclaw_no_cuenta_como_mensaje_nuevo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec 055: «mensajes» del MCP cuadra con la base aunque OpenClaw reintente el turno."""

    # Arrange: el almacén de entregas por defecto y uno de turnos que ya guardó «t-2».
    from carvajal.infrastructure import metrics

    eventos = metrics.EventCounts()
    monkeypatch.setattr(bridge_module, "WHATSAPP_EVENTS", eventos)
    bridge = _bridge(
        FakeChat(events=_turn("Hola, soy Carva")),
        turns=lambda _owner, turn_id: "hilo-1" if turn_id == "t-2" else None,
    )

    # Act
    bridge.respond("hola", "+573100000000", client_turn_id="t-1")
    bridge.respond("hola", "+573100000000", client_turn_id="t-1")
    bridge.respond("hola", "+573100000000", client_turn_id="t-2", voice=True)

    # Assert
    assert eventos.snapshot()["counts"] == {"mensajes": 1, "reintentos": 2}


def test_el_puente_cuenta_errores_y_fotos_sin_datos_de_la_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    from carvajal.infrastructure import metrics

    eventos = metrics.EventCounts()
    monkeypatch.setattr(bridge_module, "WHATSAPP_EVENTS", eventos)

    class ChatQueFalla(FakeChat):
        def stream(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("caído")

    bridge = _bridge(ChatQueFalla())

    # Act
    bridge.consult("hola", "+573100000000")
    bridge.consult("", "+573100000000", photo="media://inbound/foto.jpg")
    snapshot = eventos.snapshot()

    # Assert
    assert snapshot["counts"] == {
        "errores": 1,
        "fotos": 1,
        "fotos_no_disponibles": 1,
        "mensajes": 2,
    }
    assert snapshot["last_at"] is not None


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

    bridge = WhatsAppBridge(chat=service, secret=SECRET, quotes=quotes)
    proposal = bridge.consult("Cotiza 50 vasos y 100 platos en Bogotá", "+573100000000")
    answer = bridge.consult("No, confírmala", "+573100000000")

    # Spec 054: primero el carrito con el total parcial, sin folio; el cierre lo registra.
    assert "🛒 *Tu cotización · 2 productos*" in proposal
    assert "*Total parcial: $11.253* (IVA incl.)" in proposal
    assert "#" not in proposal.split("*Fuentes")[0]
    assert proposal.endswith("¿Algo más?")
    assert "✅ *Cotización #" in answer and "registrada*" in answer
    assert "2 productos · Total *$11.253*" in answer
    assert "📄 Te envío el PDF" not in answer  # sin URL pública no se promete el PDF


def test_extremo_a_extremo_la_compuerta_de_jev_entrega_solo_el_sustituto(
    sqlite_engine: Any,
) -> None:
    """Spec 046 (T033): por WhatsApp llega el texto neutro, no la afirmación sin fuente."""

    from tests.test_chat_service_tools import jev_service

    from carvajal.prompts import NO_CONTEXT_ANSWER

    service, _ = jev_service(sqlite_engine)

    answer = WhatsAppBridge(chat=service, secret=SECRET).consult(
        "¿Cuándo se fundó Carvajal?", "+573100000000"
    )

    assert "más usada" not in answer
    assert NO_CONTEXT_ANSWER.split(".")[0] in answer


def test_fuentes_sin_titulo_no_muestran_el_hash() -> None:
    sources = [
        {"slug": "f710dd593a8687c3dfdcd8d726051983e0f6af26a35e70bf3bd9ad87a10e8258", "url": ""},
        {"slug": "a" * 64, "url": "https://www.carvajal.com/gobierno"},
        {"slug": "b" * 64, "section": "Junta directiva", "url": ""},
    ]
    chat = FakeChat(events=_turn("Pedro Felipe Carvajal Cabal [1][2][3].", sources=sources))

    answer = _bridge(chat).consult("¿Quién es el presidente?", "+57300")

    assert "f710dd" not in answer and "aaaa" not in answer
    assert "[1] Documento del corpus de Carvajal" in answer
    assert "[2] carvajal.com — https://www.carvajal.com/gobierno" in answer
    assert "[3] Junta directiva" in answer


DIRECTORIO = "https://colombia.carvajalempaques.com/distribuidores"


def test_las_fuentes_listadas_son_las_que_cita_el_texto() -> None:
    # Arrange: dos sedes con la misma URL del directorio y citas altas, como en el ensayo
    # (antes: las cinco primeras sin repetir URL; la [2] y la [7] quedaban sin fuente).
    sources = [
        {"title": "Sede Dispapeles (Pereira)", "url": DIRECTORIO, "n": 1},
        {"title": "Sede Kive (Pereira)", "url": DIRECTORIO, "n": 2},
        *(
            {"title": f"Documento {n}", "url": f"https://carvajal.com/{n}", "n": n}
            for n in range(3, 9)
        ),
    ]
    chat = FakeChat(events=_turn("Dos sedes [1][2]; nace en 1904 [7].", sources=sources))

    # Act
    answer = _bridge(chat).consult("¿Dónde compro en Pereira?", "+57300")

    # Assert
    block = answer.split("*Fuentes:*", 1)[1]
    assert f"[1] Sede Dispapeles (Pereira) — {DIRECTORIO}" in block
    assert f"[2] Sede Kive (Pereira) — {DIRECTORIO}" in block
    # Renumeradas desde 1 (prueba de cotización del 2026-11-27): la [7] pasa a [3].
    assert "[3] Documento 7 — https://carvajal.com/7" in block
    assert "Documento 3" not in block and "[4]" not in block
    assert "nace en 1904 [3]." in answer


def test_una_cita_sin_fuente_se_quita_del_texto() -> None:
    # Arrange
    sources = [{"title": "Plato hondo", "url": "https://carvajal.com/plato", "n": 2}]
    chat = FakeChat(events=_turn("Precio de EDEXA [2][11].", sources=sources))

    # Act
    answer = _bridge(chat).consult("platos", "+57300")

    # Assert
    assert "Precio de EDEXA [1]." in answer  # la única cita, renumerada desde 1
    assert "[1] Plato hondo" in answer
    assert "[11]" not in answer and "[2]" not in answer


def test_dos_citas_de_la_misma_fuente_van_juntas() -> None:
    # Arrange
    sources = [
        {"title": "Sede Bonniplast (Cali)", "url": DIRECTORIO, "n": 1},
        {"title": "Sede Bonniplast (Cali)", "url": DIRECTORIO, "n": 2},
    ]
    chat = FakeChat(events=_turn("Dirección [1] y teléfono [2].", sources=sources))

    # Act
    answer = _bridge(chat).consult("Cali", "+57300")

    # Assert
    assert f"[1][2] Sede Bonniplast (Cali) — {DIRECTORIO}" in answer


AGENT_QUOTE_TEXT = (
    "Cotización para 500 unidades en Bogotá [1]:\n\n"
    "- Vaso rígido traslúcido 9 oz Wau (PP), SKU 7704918100199 [1]\n"
    "- 500 unidades = 10 paquetes de 50 unidades [1]\n"
    "- Precio por paquete: $3.751 [1]\n"
    "- Subtotal: $37.510, IVA incluido [1]\n"
    "- Total: pendiente hasta conocer el envío [1]\n\n"
    "Sigue siendo PP, no la opción renovable que preferías [1]. "
    "¿Te busco una alternativa de papel o pulpa de 9 oz?"
)


# La cotización que respalda ``AGENT_QUOTE_TEXT``: 500 vasos de 9 oz en paquetes de 50.
QUOTE_9OZ = {
    "kind": "cotizacion",
    "city": "Bogotá",
    "primary": {
        "name": "Vaso rígido traslúcido 9 oz Wau",
        "sku": "7704918100199",
        "material": "PP",
        "currency": "COP",
        "presentation": {
            "basis": "paquete",
            "count": 10,
            "units": 50,
            "units_requested": 500,
            "units_bought": 500,
            "price_per_presentation": 3751,
            "subtotal": 37510,
        },
    },
    "commercial": {"tax": {"status": "incluido"}},
    "total": 37510,
    "quote": {"folio": "Q-ABC123"},
}

_VIEW_9OZ = quote_view(QUOTE_9OZ)
assert _VIEW_9OZ is not None
# Lo que ``_render`` le pasa a ``quote_remarks``: el bloque más SKU, marca y material.
QUOTED_BLOCK = (
    f"{whatsapp_quote(_VIEW_9OZ)}\n{_VIEW_9OZ.sku} {_VIEW_9OZ.brand} {_VIEW_9OZ.material}"
)


def test_quote_remarks_deja_solo_los_comentarios() -> None:
    assert quote_remarks(AGENT_QUOTE_TEXT, QUOTED_BLOCK) == (
        "Sigue siendo PP, no la opción renovable que preferías [1]. "
        "¿Te busco una alternativa de papel o pulpa de 9 oz?"
    )
    assert quote_remarks("Te comparto la cotización: total $37.510 [1].", QUOTED_BLOCK) == ""
    assert quote_remarks("", QUOTED_BLOCK) == ""


def test_quote_remarks_quita_la_frase_con_importe_y_no_el_parrafo() -> None:
    text = "El total queda en $37.510 COP [1]. Sigue siendo PP. ¿Te busco una opción de papel?"

    assert quote_remarks(text, QUOTED_BLOCK) == "Sigue siendo PP. ¿Te busco una opción de papel?"


def test_quote_remarks_conserva_las_preguntas_con_importe() -> None:
    text = "Son $37.510 en total. ¿Te confirmo por $37.510?"

    assert quote_remarks(text, QUOTED_BLOCK) == "¿Te confirmo por $37.510?"


def test_quote_remarks_conserva_cifras_que_no_estan_en_el_bloque() -> None:
    text = (
        "El precio por paquete es $3.751. El montaje cuesta $12.000 aparte. "
        "El envío llega en 3 a 5 días hábiles."
    )

    assert quote_remarks(text, QUOTED_BLOCK) == (
        "El montaje cuesta $12.000 aparte. El envío llega en 3 a 5 días hábiles."
    )


def test_quote_remarks_conserva_las_vinetas_que_no_repiten_el_bloque() -> None:
    text = (
        "Condiciones [1]:\n"
        "- Total: $37.510 [1]\n"
        "- No incluye IVA\n"
        "- Entrega en 5 días\n"
        "1. Alternativa: vaso de papel 9 oz a $4.100 el paquete"
    )

    assert quote_remarks(text, QUOTED_BLOCK) == (
        "Condiciones [1]:\n"
        "- No incluye IVA\n"
        "- Entrega en 5 días\n"
        "1. Alternativa: vaso de papel 9 oz a $4.100 el paquete"
    )


def test_quote_remarks_conserva_frases_que_dicen_algo_nuevo_con_cifras_del_bloque() -> None:
    text = (
        "El total queda en $37.510 COP [1]. "
        "Recuerda que el precio de $3.751 sube en enero. "
        "El mínimo es de 10 paquetes (500 unidades). "
        "El total de $37.510 no incluye el envío."
    )

    assert quote_remarks(text, QUOTED_BLOCK) == (
        "Recuerda que el precio de $3.751 sube en enero. "
        "El mínimo es de 10 paquetes (500 unidades). "
        "El total de $37.510 no incluye el envío."
    )


def test_quote_remarks_conserva_lineas_del_bloque_con_cifras_nuevas() -> None:
    text = (
        "- Total: pendiente hasta conocer el envío [1]\n"
        "- Total con envío: $49.510\n"
        "- Precio de la opción de papel: $4.100"
    )

    assert quote_remarks(text, QUOTED_BLOCK) == (
        "- Total con envío: $49.510\n- Precio de la opción de papel: $4.100"
    )


def test_quote_remarks_pasa_las_vinetas_por_el_filtro_de_frases() -> None:
    text = "- Subtotal: $37.510\n- ¿Te confirmo por $37.510?\n- No incluye envío"

    assert quote_remarks(text, QUOTED_BLOCK) == "- ¿Te confirmo por $37.510?\n- No incluye envío"


def test_quote_remarks_quita_el_encabezado_si_no_queda_nada_debajo() -> None:
    text = "Resumen [1]:\n- Subtotal: $37.510\n- 500 unidades = 10 paquetes de 50 unidades"

    assert quote_remarks(text, QUOTED_BLOCK) == ""


def test_con_cotizacion_no_se_repiten_las_cifras_del_agente() -> None:
    chat = FakeChat(events=_turn(AGENT_QUOTE_TEXT, folios=["Q-ABC123"]))

    answer = _bridge(chat, quotes=lambda folio, owner: QUOTE_9OZ).consult(
        "Cotiza 500 vasos de 9 oz en Bogotá", "+57300"
    )

    assert answer.startswith("🧾 *Cotización #Q-ABC123*")
    for repeated in ("Precio por paquete", "500 unidades =", "SKU 7704918100199", "- Subtotal"):
        assert repeated not in answer
    assert "¿Te busco una alternativa de papel o pulpa de 9 oz?" in answer
    assert answer.index("🧾") < answer.index("Sigue siendo PP")


def test_sin_cotizacion_el_texto_del_agente_queda_completo() -> None:
    chat = FakeChat(events=_turn(AGENT_QUOTE_TEXT))

    answer = _bridge(chat).consult("hola", "+57300")

    assert "- Precio por paquete: $3.751 [1]" in answer


def test_sedes_que_comparten_el_directorio_no_toman_el_nombre_de_la_primera() -> None:
    # Arrange: 2026-11-26, la sede de EDEXA salía citada como «Sede … Sideral».
    from apps.mcp.bridge import _listed_sources, _sources_block

    url = "https://colombia.carvajalempaques.com/distribuidores"
    sedes = [
        {
            "n": 1,
            "url": url,
            "title": "Sede Sideral (Bogotá)",
            "section": "directorio_distribuidores",
        },
        {
            "n": 2,
            "url": url,
            "title": "Sede EDEXA (Bogotá)",
            "section": "directorio_distribuidores",
        },
    ]

    # Act
    block = _sources_block(sedes)

    # Assert: cada sede conserva su número y su nombre aunque compartan la URL.
    assert f"[1] Sede Sideral (Bogotá) — {url}" in block
    assert f"[2] Sede EDEXA (Bogotá) — {url}" in block
    assert _listed_sources(sedes) == [
        ("Sede Sideral (Bogotá)", url),
        ("Sede EDEXA (Bogotá)", url),
    ]


def test_quitar_una_cita_sin_fuente_no_deja_espacios_sueltos() -> None:
    # Arrange
    sources = [{"title": "Sede", "url": "https://x", "n": 1}]
    chat = FakeChat(events=_turn("Dos sedes [1] [4], y teléfonos [9].", sources=sources))

    # Act
    answer = _bridge(chat).consult("sedes", "+57300")

    # Assert
    assert answer.startswith("Dos sedes [1], y teléfonos.")


def test_una_cita_que_no_empieza_en_1_se_renumera() -> None:
    # Arrange: «200 vasos en Pasto» (prueba de cotización del 2026-11-27): el texto citaba
    # solo la sede [2] y WhatsApp mostraba «[2]» sin un «[1]».
    sources = [
        {"title": "EDEXA: Vaso rígido traslúcido 9 oz Wau", "url": "https://edexa.co/v", "n": 1},
        {"title": "Sede Representaciones Lastra Pasto (Pasto)", "url": DIRECTORIO, "n": 2},
    ]
    chat = FakeChat(events=_turn("En Pasto hay una sede del directorio [2].", sources=sources))

    # Act
    answer = _bridge(chat).consult("chocolate caliente, 200 vasos en Pasto", "+57300")

    # Assert
    assert "En Pasto hay una sede del directorio [1]." in answer
    assert f"[1] Sede Representaciones Lastra Pasto (Pasto) — {DIRECTORIO}" in answer
    assert "[2]" not in answer and "EDEXA" not in answer


def test_la_cita_de_otro_producto_pasa_a_la_ficha_del_sku_nombrado() -> None:
    # Arrange: prueba 06 (2026-11-27): el modelo citó [1] y [2] (fuentes del vaso de 9 oz)
    # en frases sobre el vaso de 7 oz (sin fuente) y el térmico de 6 oz (fuente [15]).
    sources = [
        {
            "title": "Vaso rígido traslúcido 9 oz Wau",
            "url": "https://c.co/9",
            "n": 1,
            "chunk_id": "catalogo:7704918100199",
        },
        {
            "title": "EDEXA: Vaso rígido traslúcido 9 oz Wau",
            "url": "https://e.co/9",
            "n": 2,
            "chunk_id": "precio:7704918100199:1",
        },
        {
            "title": "Vaso térmico 6 oz Wau",
            "url": "https://c.co/termico",
            "n": 15,
            "chunk_id": "catalogo:CO-VTER-06",
        },
    ]
    text = (
        "El único de 7 oz es el Vaso rígido 7 oz Wau (SKU CO-VRP-07), para bebida fría [1].\n\n"
        "Lo más cercano es el Vaso térmico 6 oz Wau (SKU CO-VTER-06), de EPS [2]."
    )
    chat = FakeChat(events=_turn(text, sources=sources))

    # Act
    answer = _bridge(chat).consult("dos mil vasos para café caliente de 7 oz", "+57300")

    # Assert
    assert "(SKU CO-VRP-07), para bebida fría." in answer
    assert "(SKU CO-VTER-06), de EPS [1]." in answer
    assert "[1] Vaso térmico 6 oz Wau — https://c.co/termico" in answer
    assert "9 oz" not in answer.split("*Fuentes:*", 1)[1]


def test_una_frase_sin_sku_conserva_su_cita() -> None:
    # Arrange
    sources = [
        {"title": "Vaso 9 oz", "url": "https://c.co/9", "n": 1, "chunk_id": "catalogo:V9"},
        {"title": "Vaso 6 oz", "url": "https://c.co/6", "n": 2, "chunk_id": "catalogo:V6"},
    ]
    chat = FakeChat(
        events=_turn("El vaso de 9 oz es de PP [1]. Y el de 6 oz, EPS [2].", sources=sources)
    )

    # Act
    answer = _bridge(chat).consult("vasos", "+57300")

    # Assert
    assert "El vaso de 9 oz es de PP [1]. Y el de 6 oz, EPS [2]." in answer


def test_una_frase_con_dos_productos_conserva_sus_citas() -> None:
    # Arrange: revisión de la PR #297: la cita de la tapa no pasa a la fuente del vaso.
    sources = [
        {"title": "Vaso 9 oz", "url": "https://c.co/v", "n": 1, "chunk_id": "catalogo:CO-VRP-09"},
        {
            "title": "Precio de referencia del catálogo: Tapa viajera 9 a 12 oz para vaso Bioform",
            "url": "https://c.co/t",
            "n": 2,
            "chunk_id": "catalogo:CO-TVIA-0912",
        },
    ]
    text = "El vaso (SKU CO-VRP-09) [1] va con la tapa viajera 9 a 12 oz [2]."
    chat = FakeChat(events=_turn(text, sources=sources))

    # Act
    answer = _bridge(chat).consult("vaso y tapa", "+57300")

    # Assert
    assert text in answer


def test_la_cita_del_producto_nombrado_por_su_nombre_no_cambia() -> None:
    # Arrange: la frase dice el SKU del vaso y nombra la tapa, con una sola cita (la tapa).
    sources = [
        {"title": "Vaso 9 oz", "url": "https://c.co/v", "n": 1, "chunk_id": "catalogo:CO-VRP-09"},
        {
            "title": "Tapa viajera 9 a 12 oz para vaso Bioform",
            "url": "https://c.co/t",
            "n": 2,
            "chunk_id": "catalogo:CO-TVIA-0912",
        },
    ]
    text = "Para el vaso (SKU CO-VRP-09) sirve la Tapa viajera 9 a 12 oz [2]."
    chat = FakeChat(events=_turn(text, sources=sources))

    # Act
    answer = _bridge(chat).consult("tapa", "+57300")

    # Assert
    assert "sirve la Tapa viajera 9 a 12 oz [1]." in answer  # la [2] de la tapa, renumerada
    assert "[1] Tapa viajera 9 a 12 oz para vaso Bioform" in answer
