"""Pruebas del webhook firmado de mensajería (``POST /api/webhooks/whatsapp``).

Sin red ni LLM: el puente usa un doble de ``ChatService`` o el servicio real sobre SQLite con
un runner guionizado. Cubren la seguridad (deshabilitado, firma, ventana del timestamp,
allowlist E.164, tamaño del cuerpo), la idempotencia por ``message_id`` y que el número no
aparezca en claro ni en los logs ni en las respuestas.
"""

import hashlib
import hmac
import json
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from apps.api.chat.service import _stamp_quote
from apps.api.chat.streaming import ChatEvent
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.views import webhooks
from apps.mcp.bridge import (
    LIMIT_REACHED,
    NEW_CONVERSATION,
    PDF_UNAVAILABLE,
    SERVICE_UNAVAILABLE,
    BridgeReply,
    WhatsAppBridge,
    owner_key_for,
)
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.chat import SqlChatRepository
from carvajal.adapters.storage.database import build_session_factory
from carvajal.adapters.storage.deliveries import SqlDeliveryStore
from carvajal.application.quota import QuotaResult
from carvajal.domain.phone import normalize_e164
from carvajal.infrastructure.settings import Settings
from carvajal.quotation import QuoteRequest, quote_request

SECRET = "secreto-del-webhook"
SESSION = b"secreto-de-sesion"
NUMBER = "+573100000000"
DIGITS = "3100000000"
URL = "/api/webhooks/whatsapp"


@dataclass
class FakeChat:
    """Doble de ``ChatService``: registra cada turno y emite un guion fijo."""

    events: list[ChatEvent] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        self.calls.append(
            {
                "owner_key": owner_key,
                "question": question,
                "client_turn_id": client_turn_id,
                "thread_id": thread_id,
            }
        )
        yield from self.events

    def new_conversation(self, owner_key: str) -> Any:
        self.created.append(owner_key)
        return object()


def _events(answer: str = "Hola, soy el asistente.", folios: tuple[str, ...] = ()) -> list[Any]:
    return [
        ChatEvent("open", {"stream": "chat"}),
        ChatEvent("meta", {"sources": []}),
        ChatEvent("token", {"text": answer}),
        ChatEvent("done", {"quote_ids": list(folios)}),
    ]


def _app(
    monkeypatch: pytest.MonkeyPatch,
    bridge: WhatsAppBridge | None,
    *,
    secret: str = SECRET,
    allowed: str = NUMBER,
) -> TestClient:
    monkeypatch.setenv("WEBHOOK_SECRET", secret)
    monkeypatch.setenv("WEBHOOK_ALLOWED_NUMBERS", allowed)
    app = create_app(Handlers(health=lambda: HealthStatus(0, 0)))
    if bridge is not None:
        app.state.webhook_bridge = bridge
    return TestClient(app)


def _sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    """Firma como la calcularía el emisor: ``timestamp + "." + cuerpo crudo``."""

    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + digest.hexdigest()


def _post(
    client: TestClient,
    payload: Mapping[str, Any] | bytes,
    *,
    timestamp: str | None = None,
    signature: str | None = None,
    secret: str = SECRET,
) -> Any:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    ts = timestamp if timestamp is not None else str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Carvajal-Timestamp": ts,
        "X-Carvajal-Signature": signature if signature is not None else _sign(body, ts, secret),
    }
    return client.post(URL, content=body, headers=headers)


def _message(**overrides: Any) -> dict[str, Any]:
    return {"message_id": "wamid.ABC123", "from": NUMBER, "text": "Hola", **overrides}


# --- E.164 y configuración ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+573100000000", NUMBER),
        ("+57 310-000 (0000)", NUMBER),
        ("whatsapp:+573100000000", NUMBER),
        (" +1 (415) 555.2671 ", "+14155552671"),
    ],
)
def test_normalize_e164_acepta_separadores(raw: str, expected: str) -> None:
    # Act
    result = normalize_e164(raw)

    # Assert
    assert result == expected


@pytest.mark.parametrize("raw", ["573100000000", "+0573100000000", "+57abc", "+1234", ""])
def test_normalize_e164_rechaza_sin_revelar_el_numero(raw: str) -> None:
    # Act / Assert
    with pytest.raises(ValueError) as error:
        normalize_e164(raw)
    assert raw.strip() == "" or raw not in str(error.value)


def test_settings_lee_la_allowlist_separada_por_comas(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("WEBHOOK_ALLOWED_NUMBERS", " +57 310 000 0000 , ,+14155552671")

    # Act
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Assert
    assert settings.webhook_allowed_numbers == ("+57 310 000 0000", "+14155552671")
    assert webhooks.allowed_numbers(settings) == frozenset({NUMBER, "+14155552671"})


def test_la_allowlist_ignora_entradas_invalidas_y_las_cuenta() -> None:
    # Arrange
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        webhook_allowed_numbers=("+573100000000", "3100000000", "basura"),
    )

    # Act
    allowed = webhooks.allowed_numbers(settings)
    invalid = webhooks.invalid_allowlist_entries(settings)

    # Assert
    assert allowed == frozenset({NUMBER})
    assert invalid == 2


def test_settings_lee_la_allowlist_como_lista_json(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: el formato JSON no se parte por comas dentro de cada número.
    monkeypatch.setenv("WEBHOOK_ALLOWED_NUMBERS", ' ["+573100000000", "+1 415 555 2671"] ')

    # Act
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Assert
    assert settings.webhook_allowed_numbers == ("+573100000000", "+1 415 555 2671")
    assert webhooks.allowed_numbers(settings) == frozenset({NUMBER, "+14155552671"})
    assert webhooks.invalid_allowlist_entries(settings) == 0


def test_settings_lee_los_dominios_de_captura_como_lista_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv("CAPTURE_ALLOWED_DOMAINS", '["carvajal.com", "larepublica.co"]')

    # Act
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Assert
    assert settings.capture_allowed_domains == ("carvajal.com", "larepublica.co")


def test_el_secreto_del_webhook_no_aparece_en_el_repr() -> None:
    # Arrange
    settings = Settings(_env_file=None, webhook_secret=SECRET)  # type: ignore[call-arg]

    # Act
    rendered = repr(settings)

    # Assert
    assert SECRET not in rendered


# --- seguridad ------------------------------------------------------------------------------


def test_sin_secreto_el_webhook_no_existe(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION), secret="")

    # Act
    response = _post(client, _message(), secret="cualquiera")

    # Assert
    assert response.status_code == 404
    assert chat.calls == []


def test_firma_invalida_responde_401_sin_procesar(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))

    # Act
    response = _post(client, _message(), secret="otro-secreto")

    # Assert
    assert response.status_code == 401
    assert chat.calls == []


@pytest.mark.parametrize("signature", ["", "sha256=", "md5=abc", "sha256=" + "0" * 64, "zz"])
def test_firma_ausente_o_malformada_responde_401(
    monkeypatch: pytest.MonkeyPatch, signature: str
) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))

    # Act
    response = _post(client, _message(), signature=signature)

    # Assert
    assert response.status_code == 401
    assert chat.calls == []


def test_la_firma_cubre_el_cuerpo_crudo(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: se firma un cuerpo y se envía otro (mismo JSON, distinto texto).
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))
    ts = str(int(time.time()))
    signed = json.dumps(_message()).encode()
    tampered = json.dumps(_message(text="Otra cosa")).encode()

    # Act
    response = _post(client, tampered, timestamp=ts, signature=_sign(signed, ts))

    # Assert
    assert response.status_code == 401
    assert chat.calls == []


# ±303 s y no ±301: el timestamp de la prueba se trunca a segundos y el servidor compara con
# la hora con decimales un instante después; a +301 la diferencia real podía quedar en 299,x
# y la petición pasaba (prueba intermitente en la CI).
@pytest.mark.parametrize("offset", [-303, 303, -10_000])
def test_timestamp_fuera_de_la_ventana_responde_401(
    monkeypatch: pytest.MonkeyPatch, offset: int
) -> None:
    # Arrange: la firma es válida para ese timestamp, pero está fuera de los 5 minutos.
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))

    # Act
    response = _post(client, _message(), timestamp=str(int(time.time()) + offset))

    # Assert
    assert response.status_code == 401
    assert chat.calls == []


@pytest.mark.parametrize("timestamp", ["", "abc", "1.5", "-1", "1e9", "9" * 13])
def test_timestamp_invalido_responde_401(monkeypatch: pytest.MonkeyPatch, timestamp: str) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))

    # Act
    response = _post(client, _message(), timestamp=timestamp)

    # Assert
    assert response.status_code == 401
    assert chat.calls == []


def test_cambiar_el_timestamp_invalida_la_firma(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: una petición capturada no se puede refrescar con un timestamp nuevo.
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))
    body = json.dumps(_message()).encode()
    old = str(int(time.time()) - 600)

    # Act
    response = _post(client, body, timestamp=str(int(time.time())), signature=_sign(body, old))

    # Assert
    assert response.status_code == 401
    assert chat.calls == []


def test_numero_fuera_de_la_allowlist_responde_403_sin_procesar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION), allowed="+14155552671")

    # Act
    with capture_logs() as logs:
        response = _post(client, _message())

    # Assert
    assert response.status_code == 403
    assert chat.calls == []
    assert DIGITS not in response.text
    assert DIGITS not in repr(logs)
    assert any(log.get("reason") == "sender_not_allowed" for log in logs)


def test_allowlist_vacia_rechaza_todo(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION), allowed="")

    # Act
    response = _post(client, _message())

    # Assert
    assert response.status_code == 403
    assert chat.calls == []


def test_modo_abierto_acepta_cualquier_remitente_e164(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION), allowed="*")

    # Act
    response = _post(client, _message(**{"from": "+14155552671"}))

    # Assert
    assert response.status_code == 200
    assert len(chat.calls) == 1


def test_modo_abierto_sigue_exigiendo_la_firma(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION), allowed="*")
    body = json.dumps(_message()).encode()
    timestamp = str(int(time.time()))

    # Act
    response = client.post(
        "/api/webhooks/whatsapp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Carvajal-Timestamp": timestamp,
            "X-Carvajal-Signature": _sign(body, timestamp, secret="otro-secreto"),
        },
    )

    # Assert
    assert response.status_code == 401
    assert chat.calls == []


def test_la_allowlist_compara_numeros_normalizados(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(
        monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION), allowed="+57 (310) 000-0000"
    )

    # Act
    response = _post(client, _message(**{"from": "+57 310 000 0000"}))

    # Assert
    assert response.status_code == 200
    assert chat.calls[0]["owner_key"] == owner_key_for(NUMBER, SESSION)


@pytest.mark.parametrize(
    "payload",
    [
        b"no es json",
        json.dumps({"from": NUMBER, "text": "Hola"}).encode(),
        json.dumps(_message(**{"from": "3100000000"})).encode(),
        json.dumps(_message(message_id="")).encode(),
        json.dumps(_message(text="x" * 2001)).encode(),
        json.dumps(_message(nueva="tal vez")).encode(),
    ],
)
def test_cuerpo_invalido_responde_422_sin_eco(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))

    # Act
    response = _post(client, payload)

    # Assert
    assert response.status_code == 422
    assert chat.calls == []
    assert DIGITS not in response.text


def test_cuerpo_demasiado_grande_responde_413(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))
    body = json.dumps(_message(text="x" * (webhooks.MAX_BODY_BYTES + 1))).encode()

    # Act
    response = _post(client, body)

    # Assert
    assert response.status_code == 413
    assert chat.calls == []


# --- turno ---------------------------------------------------------------------------------


def test_exito_devuelve_la_respuesta_y_los_folios(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events("Te cuento: **sí**.", ("Q-1", "Q-2")))
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))

    # Act
    with capture_logs() as logs:
        response = _post(client, _message(text="¿Tienen vasos?"))

    # Assert
    assert response.status_code == 200
    assert response.json() == {"reply": "Te cuento: *sí*.", "quote_ids": ["Q-1", "Q-2"]}
    call = chat.calls[0]
    assert call["question"] == "¿Tienen vasos?"
    assert call["owner_key"] == owner_key_for(NUMBER, SESSION)
    assert call["client_turn_id"] == webhooks.turn_id_for("wamid.ABC123")
    assert DIGITS not in call["owner_key"]
    assert DIGITS not in repr(logs)


def test_el_client_turn_id_es_estable_acotado_y_opaco() -> None:
    # Arrange
    long_id = "wamid." + "A" * 190

    # Act
    first = webhooks.turn_id_for(long_id)
    second = webhooks.turn_id_for(long_id)

    # Assert
    assert first == second
    assert first.startswith("wh_")
    assert len(first) <= 64
    assert "AAAA" not in first
    assert first != webhooks.turn_id_for("wamid.otro")


def test_nueva_conversacion_sin_texto_solo_confirma(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))

    # Act
    response = _post(client, _message(text="", nueva=True))

    # Assert
    assert response.status_code == 200
    assert response.json()["quote_ids"] == []
    assert chat.created == [owner_key_for(NUMBER, SESSION)]
    assert chat.calls == []


def test_sin_session_secret_responde_503(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: sin puente inyectado, el real exige SESSION_SECRET.
    from apps.mcp import server

    server.default_bridge.cache_clear()
    monkeypatch.setenv("SESSION_SECRET", "")
    client = _app(monkeypatch, None)

    # Act
    response = _post(client, _message())

    # Assert
    assert response.status_code == 503
    assert response.json() == {"detail": SERVICE_UNAVAILABLE}
    server.default_bridge.cache_clear()


# --- puente: reintentos ----------------------------------------------------------------------


def test_respond_reemite_el_turno_guardado_sin_cupo_ni_hilo_nuevo() -> None:
    # Arrange
    chat = FakeChat(events=_events("Respuesta guardada", ("Q-1",)))
    quota_calls: list[str] = []

    def quota(owner: str) -> QuotaResult:
        quota_calls.append(owner)
        return QuotaResult(allowed=True)

    bridge = WhatsAppBridge(
        chat=chat, secret=SESSION, quota=quota, turns=lambda owner, turn: "hilo-1"
    )

    # Act
    reply = bridge.respond("Hola", NUMBER, new_conversation=True, client_turn_id="wh_x")

    # Assert
    assert reply == BridgeReply("Respuesta guardada", ("Q-1",))
    assert quota_calls == []
    assert chat.created == []
    assert chat.calls[0]["thread_id"] == "hilo-1"
    assert chat.calls[0]["client_turn_id"] == "wh_x"


def test_respond_falla_la_consulta_del_turno_y_responde_legible() -> None:
    # Arrange
    def broken(owner: str, turn: str) -> str | None:
        raise RuntimeError("la base no responde")

    chat = FakeChat(events=_events())
    bridge = WhatsAppBridge(chat=chat, secret=SESSION, turns=broken)

    # Act
    reply = bridge.respond("Hola", NUMBER, client_turn_id="wh_x")

    # Assert
    assert reply == BridgeReply(SERVICE_UNAVAILABLE)
    assert chat.calls == []


def test_consult_sigue_devolviendo_texto() -> None:
    # Arrange
    chat = FakeChat(events=_events("Hola"))
    bridge = WhatsAppBridge(chat=chat, secret=SESSION)

    # Act
    answer = bridge.consult("hola", NUMBER)

    # Assert
    assert answer == "Hola"
    assert chat.calls[0]["client_turn_id"] is None


# --- extremo a extremo: idempotencia con el servicio real ----------------------------------


@pytest.fixture()
def sqlite_engine() -> Iterator[Any]:
    """SQLite en memoria con el esquema real (como ``tests/test_mcp_whatsapp.py``)."""

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


def _real_bridge(engine: Any, runner: Any, quota_calls: list[str]) -> WhatsAppBridge:
    from tests.test_chat_service_tools import build_service

    service = build_service(engine, runner)
    factory = build_session_factory(engine)

    def quota(owner: str) -> QuotaResult:
        quota_calls.append(owner)
        return QuotaResult(allowed=True)

    def turns(owner: str, client_turn_id: str) -> str | None:
        with factory() as session:
            return SqlChatRepository(session).find_turn_thread(owner, client_turn_id)

    return WhatsAppBridge(
        chat=service,
        secret=SESSION,
        quota=quota,
        turns=turns,
        deliveries=SqlDeliveryStore(factory),
    )


def _count(engine: Any, table: Any) -> int:
    with engine.connect() as connection:
        return int(connection.scalar(select(func.count()).select_from(table)) or 0)


def test_reintento_con_el_mismo_message_id_no_duplica_turnos_ni_folios(
    monkeypatch: pytest.MonkeyPatch, sqlite_engine: Any
) -> None:
    # Arrange
    from tests.test_chat_service_tools import FakeToolRunner, scripted_events, two_quotes_final

    runner = FakeToolRunner(scripted_events(two_quotes_final()))
    quota_calls: list[str] = []
    client = _app(monkeypatch, _real_bridge(sqlite_engine, runner, quota_calls))
    payload = _message(text="Cotiza 50 y 100 vasos en Bogotá", nueva=True)
    confirmation = _message(message_id="wamid.SI", text="No, confírmala")

    # Act: el carrito y el cierre con «sí» (spec 054), cada uno reintentado.
    proposal = _post(client, payload)
    proposal_again = _post(client, payload)
    first = _post(client, confirmation)
    second = _post(client, confirmation)

    # Assert
    assert proposal.json() == proposal_again.json() and proposal.json()["quote_ids"] == []
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()["quote_ids"]) == 1  # un folio con las dos líneas
    assert len(runner.calls) == 1
    assert len(quota_calls) == 2
    assert _count(sqlite_engine, tables.messages) == 4
    assert _count(sqlite_engine, tables.quotes) == 1
    assert _count(sqlite_engine, tables.conversations) == 1


def test_message_id_distinto_es_un_turno_nuevo(
    monkeypatch: pytest.MonkeyPatch, sqlite_engine: Any
) -> None:
    # Arrange
    from tests.test_chat_service_tools import FakeToolRunner, scripted_events

    runner = FakeToolRunner(scripted_events())
    client = _app(monkeypatch, _real_bridge(sqlite_engine, runner, []))

    # Act
    _post(client, _message(message_id="wamid.1"))
    _post(client, _message(message_id="wamid.2"))

    # Assert
    assert len(runner.calls) == 2
    assert _count(sqlite_engine, tables.messages) == 4


def test_find_turn_thread_solo_ve_los_hilos_del_dueno(sqlite_engine: Any) -> None:
    # Arrange
    factory = build_session_factory(sqlite_engine)
    with factory() as session:
        repository = SqlChatRepository(session)
        mine = repository.create("dueno-a")
        repository.append_turn(mine.id, question="Hola", answer="Respuesta", client_turn_id="wh_1")
        session.commit()

    # Act
    with factory() as session:
        repository = SqlChatRepository(session)
        found = repository.find_turn_thread("dueno-a", "wh_1")
        foreign = repository.find_turn_thread("dueno-b", "wh_1")
        missing = repository.find_turn_thread("dueno-a", "wh_2")

    # Assert
    assert found == mine.id
    assert foreign is None
    assert missing is None


def test_timestamp_con_digitos_no_ascii_no_es_valido() -> None:
    # Arrange: Starlette decodifica las cabeceras en latin-1; «²» pasa ``isdigit``.
    now = time.time()

    # Act
    fresh = webhooks._fresh(f"{int(now)}²", now)

    # Assert
    assert fresh is False


# --- arranque ------------------------------------------------------------------------------


def test_el_arranque_avisa_de_la_allowlist_sin_revelar_numeros(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("WEBHOOK_ALLOWED_NUMBERS", f"{DIGITS},basura")

    # Act
    with capture_logs() as logs:
        create_app(Handlers(health=lambda: HealthStatus(0, 0)))

    # Assert
    events = {log["event"]: log for log in logs}
    assert events["webhook.allowlist_invalid_entries"]["invalid"] == 2
    assert "webhook.allowlist_empty" in events
    assert DIGITS not in repr(logs)
    assert SECRET not in repr(logs)


def test_el_arranque_avisa_del_modo_abierto(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("WEBHOOK_ALLOWED_NUMBERS", "*")

    # Act
    with capture_logs() as logs:
        create_app(Handlers(health=lambda: HealthStatus(0, 0)))

    # Assert
    events = {log["event"]: log for log in logs}
    assert events["webhook.open"]["reason"] == "any_sender"
    assert "webhook.allowlist_empty" not in events
    assert "webhook.allowlist_invalid_entries" not in events
    assert SECRET not in repr(logs)


def test_el_arranque_registra_el_webhook_activo(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("WEBHOOK_ALLOWED_NUMBERS", f"{NUMBER},+14155552671")

    # Act
    with capture_logs() as logs:
        create_app(Handlers(health=lambda: HealthStatus(0, 0)))

    # Assert
    enabled = [log for log in logs if log["event"] == "webhook.enabled"]
    assert enabled[0]["allowed_numbers"] == 2
    assert DIGITS not in repr(logs)


def test_sin_secreto_el_arranque_no_menciona_el_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    monkeypatch.setenv("WEBHOOK_ALLOWED_NUMBERS", "")

    # Act
    with capture_logs() as logs:
        create_app(Handlers(health=lambda: HealthStatus(0, 0)))

    # Assert
    assert all(not str(log["event"]).startswith("webhook.") for log in logs)


# --- reintentos concurrentes y repeticiones firmadas --------------------------------------


class ReentrantRunner:
    """Runner que dispara un reintento del relé mientras el primer turno sigue en curso.

    Envuelve un ``FakeToolRunner``: el reintento entra antes de que el primero guarde nada,
    como cuando el turno tarda más que el timeout del relé.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.retry: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        if self.retry is not None:
            retry, self.retry = self.retry, None
            retry()
        yield from self.inner.stream(*args, **kwargs)


def test_reintento_concurrente_con_nueva_no_duplica_hilo_folios_ni_cupo(
    sqlite_engine: Any,
) -> None:
    # Arrange
    from tests.test_chat_service_tools import FakeToolRunner, scripted_events, two_quotes_final

    runner = ReentrantRunner(FakeToolRunner(scripted_events(two_quotes_final())))
    quota_calls: list[str] = []
    bridge = _real_bridge(sqlite_engine, runner, quota_calls)
    turn = webhooks.turn_id_for("wamid.SLOW")
    question = "Cotiza 50 y 100 vasos"
    retried: list[BridgeReply] = []
    runner.retry = lambda: retried.append(
        bridge.respond(question, NUMBER, new_conversation=True, client_turn_id=turn)
    )

    # Act
    first = bridge.respond(question, NUMBER, new_conversation=True, client_turn_id=turn)
    later = bridge.respond(question, NUMBER, new_conversation=True, client_turn_id=turn)

    # Assert: el reintento en vuelo no atiende; el posterior reemite el mismo turno.
    assert retried[0].in_progress is True
    assert retried[0].quote_ids == ()
    assert first.quote_ids == ()  # spec 054: borrador, sin folios hasta el «sí»
    assert "🛒 *Tu cotización · 2 productos*" in first.text
    assert later == first
    assert _count(sqlite_engine, tables.conversations) == 1
    assert _count(sqlite_engine, tables.messages) == 2
    assert _count(sqlite_engine, tables.quotes) == 0
    assert len(quota_calls) == 1
    assert len(runner.calls) == 1


def test_reintento_concurrente_sin_nueva_no_cobra_cupo_ni_llm_dos_veces(
    sqlite_engine: Any,
) -> None:
    # Arrange
    from tests.test_chat_service_tools import FakeToolRunner, scripted_events

    runner = ReentrantRunner(FakeToolRunner(scripted_events()))
    quota_calls: list[str] = []
    bridge = _real_bridge(sqlite_engine, runner, quota_calls)
    turn = webhooks.turn_id_for("wamid.SLOW2")
    retried: list[BridgeReply] = []
    runner.retry = lambda: retried.append(bridge.respond("Hola", NUMBER, client_turn_id=turn))

    # Act
    bridge.respond("Hola", NUMBER, client_turn_id=turn)

    # Assert
    assert retried[0].in_progress is True
    assert len(quota_calls) == 1
    assert len(runner.calls) == 1


@dataclass
class BlockingChat(FakeChat):
    """Doble que se detiene dentro del turno hasta que la prueba lo libera."""

    barrier: threading.Barrier = field(default_factory=lambda: threading.Barrier(2, timeout=5))
    release: threading.Event = field(default_factory=threading.Event)

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        self.barrier.wait()
        self.release.wait(timeout=5)
        yield from super().stream(
            owner_key, question, client_turn_id=client_turn_id, thread_id=thread_id
        )


def test_dos_hilos_con_el_mismo_message_id_atienden_un_solo_turno() -> None:
    # Arrange
    chat = BlockingChat(events=_events("Respuesta única"))
    quota_calls: list[str] = []

    def quota(owner: str) -> QuotaResult:
        quota_calls.append(owner)
        return QuotaResult(allowed=True)

    bridge = WhatsAppBridge(chat=chat, secret=SESSION, quota=quota)
    turn = webhooks.turn_id_for("wamid.HILOS")
    replies: list[BridgeReply] = []
    first = threading.Thread(
        target=lambda: replies.append(
            bridge.respond("Hola", NUMBER, new_conversation=True, client_turn_id=turn)
        )
    )

    # Act: el primer hilo queda dentro del turno; el segundo llega en ese momento.
    first.start()
    chat.barrier.wait()
    second = bridge.respond("Hola", NUMBER, new_conversation=True, client_turn_id=turn)
    chat.release.set()
    first.join(timeout=5)

    # Assert
    assert second.in_progress is True
    assert replies == [BridgeReply("Respuesta única")]
    assert len(chat.calls) == 1
    assert len(chat.created) == 1
    assert len(quota_calls) == 1


def test_turno_en_curso_responde_409_con_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = BlockingChat(events=_events())
    bridge = WhatsAppBridge(chat=chat, secret=SESSION)
    turn = webhooks.turn_id_for("wamid.ABC123")
    assert bridge.deliveries is not None
    bridge.deliveries.claim(bridge.owner_key(NUMBER), turn)  # otro intento lo tiene
    client = _app(monkeypatch, bridge)

    # Act
    response = _post(client, _message())

    # Assert
    assert response.status_code == 409
    assert response.headers["Retry-After"] == str(webhooks.RETRY_AFTER_SECONDS)
    assert response.json() == {"detail": webhooks.TURN_IN_PROGRESS}
    assert chat.calls == []


def test_replay_firmado_de_nueva_sin_texto_abre_un_solo_hilo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: el mismo cuerpo firmado, reenviado tal cual dentro de la ventana.
    chat = FakeChat(events=_events())
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))
    body = json.dumps(_message(text="", nueva=True)).encode()
    timestamp = str(int(time.time()))
    signature = _sign(body, timestamp)

    # Act
    responses = [_post(client, body, timestamp=timestamp, signature=signature) for _ in range(3)]

    # Assert
    assert [response.status_code for response in responses] == [200, 200, 200]
    assert {response.json()["reply"] for response in responses} == {NEW_CONVERSATION}
    assert len(chat.created) == 1


def test_repetir_un_mensaje_sin_cupo_no_vuelve_a_cobrar(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    quota_calls: list[str] = []

    def quota(owner: str) -> QuotaResult:
        quota_calls.append(owner)
        return QuotaResult(allowed=False)

    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION, quota=quota))

    # Act
    first = _post(client, _message())
    second = _post(client, _message())

    # Assert
    assert first.json()["reply"] == second.json()["reply"] == LIMIT_REACHED
    assert len(quota_calls) == 1
    assert chat.calls == []


@dataclass
class FlakyChat(FakeChat):
    """Doble cuyo primer turno falla antes de guardar nada."""

    failures: int = 1

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("el proveedor no responde")
        yield from super().stream(
            owner_key, question, client_turn_id=client_turn_id, thread_id=thread_id
        )


def test_un_fallo_libera_el_message_id_para_el_reintento(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    chat = FlakyChat(events=_events("Ahora sí"))
    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION))

    # Act
    first = _post(client, _message())
    second = _post(client, _message())

    # Assert
    assert first.json()["reply"] == SERVICE_UNAVAILABLE
    assert second.json()["reply"] == "Ahora sí"
    assert len(chat.calls) == 1


def test_el_webhook_entrega_el_pdf_como_el_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    quoted = quote_request(
        QuoteRequest(sku="CO-PCOM-J1", quantity=450, city="Bogotá", delivery=False)
    )
    payload, _record = _stamp_quote(quoted, folio="Q-7F3A2C", moment=datetime.now(UTC))
    chat = FakeChat(events=_events())
    links: list[tuple[str, str]] = []

    def pdf_link(folio: str, owner: str, valid_until: date | None) -> str:
        links.append((folio, owner))
        return f"https://carvajal.example/cotizaciones/{folio}.pdf?firma"

    bridge = WhatsAppBridge(
        chat=chat, secret=SESSION, latest_quote=lambda owner: payload, pdf_link=pdf_link
    )
    client = _app(monkeypatch, bridge)

    # Act
    response = _post(client, _message(text="PDF"))

    # Assert
    assert response.status_code == 200
    assert "https://carvajal.example/cotizaciones/Q-7F3A2C.pdf?firma" in response.json()["reply"]
    assert links == [("Q-7F3A2C", owner_key_for(NUMBER, SESSION))]
    assert chat.calls == []


# --- Mismo hilo por los dos canales y reintentos tras un fallo -------------------------------


@pytest.mark.parametrize(
    "mcp_id",
    [
        NUMBER,
        "+57 310 000 0000",
        "+57.310.000.0000",
        "whatsapp:+573100000000",
        "573100000000",
        "573100000000@s.whatsapp.net",
        "573100000000:12@s.whatsapp.net",
    ],
)
def test_el_mismo_numero_cae_en_el_mismo_hilo_por_webhook_y_por_mcp(
    monkeypatch: pytest.MonkeyPatch, mcp_id: str
) -> None:
    # Arrange
    chat = FakeChat(events=_events())
    bridge = WhatsAppBridge(chat=chat, secret=SESSION)
    client = _app(monkeypatch, bridge)

    # Act: el mismo número por el webhook (E.164) y por la herramienta MCP (número o JID).
    _post(client, _message())
    bridge.consult("Hola otra vez", mcp_id)

    # Assert
    webhook_owner, mcp_owner = (call["owner_key"] for call in chat.calls)
    assert webhook_owner == mcp_owner == owner_key_for(NUMBER, SESSION)


def test_un_grupo_o_alias_conserva_su_propio_hilo() -> None:
    group = owner_key_for("120363025246125486@g.us", SESSION)

    assert group == owner_key_for(" 120363025246125486@G.US ", SESSION)
    assert group != owner_key_for(NUMBER, SESSION)


def test_un_fallo_tras_cobrar_cupo_y_abrir_hilo_no_los_repite_en_el_reintento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: los dos primeros intentos fallan después de cobrar el cupo y abrir el hilo.
    chat = FlakyChat(events=_events("Ahora sí"), failures=2)
    quota_calls: list[str] = []

    def quota(owner: str) -> QuotaResult:
        quota_calls.append(owner)
        return QuotaResult(allowed=True)

    client = _app(monkeypatch, WhatsAppBridge(chat=chat, secret=SESSION, quota=quota))

    # Act: el mismo message_id llega tres veces con ``nueva``.
    replies = [_post(client, _message(nueva=True)).json()["reply"] for _ in range(3)]

    # Assert: un solo cobro y un solo hilo nuevo para un solo mensaje.
    assert replies == [SERVICE_UNAVAILABLE, SERVICE_UNAVAILABLE, "Ahora sí"]
    assert len(quota_calls) == 1
    assert len(chat.created) == 1


def test_el_pdf_sin_url_publica_es_definitivo_y_no_vuelve_a_cobrar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    quoted = quote_request(QuoteRequest(sku="CO-PCOM-J1", quantity=450, city="Bogotá"))
    payload, _record = _stamp_quote(quoted, folio="Q-7F3A2C", moment=datetime.now(UTC))
    quota_calls: list[str] = []

    def quota(owner: str) -> QuotaResult:
        quota_calls.append(owner)
        return QuotaResult(allowed=True)

    bridge = WhatsAppBridge(
        chat=FakeChat(events=_events()),
        secret=SESSION,
        quota=quota,
        latest_quote=lambda owner: payload,
        pdf_link=None,
    )
    client = _app(monkeypatch, bridge)

    # Act
    replies = [_post(client, _message(text="PDF")).json()["reply"] for _ in range(3)]

    # Assert
    assert replies == [PDF_UNAVAILABLE] * 3
    assert len(quota_calls) == 1


def test_el_pdf_con_un_fallo_transitorio_se_reintenta_sin_volver_a_cobrar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: leer la última cotización falla una vez.
    quoted = quote_request(QuoteRequest(sku="CO-PCOM-J1", quantity=450, city="Bogotá"))
    payload, _record = _stamp_quote(quoted, folio="Q-7F3A2C", moment=datetime.now(UTC))
    failures = [RuntimeError("bd caída")]
    quota_calls: list[str] = []

    def latest(owner: str) -> Any:
        if failures:
            raise failures.pop()
        return payload

    def quota(owner: str) -> QuotaResult:
        quota_calls.append(owner)
        return QuotaResult(allowed=True)

    bridge = WhatsAppBridge(
        chat=FakeChat(events=_events()),
        secret=SESSION,
        quota=quota,
        latest_quote=latest,
        pdf_link=lambda folio, owner, valid: f"https://carvajal.example/{folio}.pdf",
    )
    client = _app(monkeypatch, bridge)

    # Act
    first = _post(client, _message(text="PDF")).json()["reply"]
    second = _post(client, _message(text="PDF")).json()["reply"]

    # Assert
    assert first == PDF_UNAVAILABLE
    assert "https://carvajal.example/Q-7F3A2C.pdf" in second
    assert len(quota_calls) == 1
