"""Pruebas del webhook firmado de mensajería (``POST /api/webhooks/whatsapp``).

Sin red ni LLM: el puente usa un doble de ``ChatService`` o el servicio real sobre SQLite con
un runner guionizado. Cubren la seguridad (deshabilitado, firma, ventana del timestamp,
allowlist E.164, tamaño del cuerpo), la idempotencia por ``message_id`` y que el número no
aparezca en claro ni en los logs ni en las respuestas.
"""

import hashlib
import hmac
import json
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from apps.api.chat.streaming import ChatEvent
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.views import webhooks
from apps.mcp.bridge import SERVICE_UNAVAILABLE, BridgeReply, WhatsAppBridge, owner_key_for
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.chat import SqlChatRepository
from carvajal.adapters.storage.database import build_session_factory
from carvajal.application.quota import QuotaResult
from carvajal.domain.phone import normalize_e164
from carvajal.infrastructure.settings import Settings

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


@pytest.mark.parametrize("offset", [-301, 301, -10_000])
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

    return WhatsAppBridge(chat=service, secret=SESSION, quota=quota, turns=turns)


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

    # Act
    first = _post(client, payload)
    second = _post(client, payload)

    # Assert
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()["quote_ids"]) == 2
    assert len(runner.calls) == 1
    assert len(quota_calls) == 1
    assert _count(sqlite_engine, tables.messages) == 2
    assert _count(sqlite_engine, tables.quotes) == 2
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
