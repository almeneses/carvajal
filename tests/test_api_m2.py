"""Pruebas del contrato HTTP/SSE del frontend propio del M2."""

import json
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.chat.identity import SignedCookieIdentity
from apps.api.chat.service import SERVICE_UNAVAILABLE
from apps.api.chat.streaming import ChatEvent
from apps.api.main import Handlers, HealthStatus, create_app
from carvajal.adapters.storage.chat import ChatMessage, Conversation
from carvajal.application.ask import Answer
from carvajal.application.quota import QuotaResult

IDENTITY = SignedCookieIdentity(b"secreto-m2")
RESPUESTA = Answer("ok", ())
CSRF = "csrf_token"
SOURCE = {
    "chunk_id": "historia:2",
    "slug": "historia",
    "section": "historia",
    "layer": "core",
    "score": 0.91,
    "excerpt": "Cali, 1904.",
    "url": "https://www.carvajal.com/historia/",
    "title": "Historia",
    "source_type": "oficial",
}


@dataclass
class FakeChat:
    events: list[ChatEvent] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    created: list[str] = field(default_factory=list)

    def history(self, owner_key: str) -> Sequence[ChatMessage]:
        return self.messages

    def ask(self, owner_key: str, question: str) -> Answer:
        return RESPUESTA

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        self.calls.append(
            {"owner": owner_key, "question": question, "turn": client_turn_id, "thread": thread_id}
        )
        yield from self.events

    def new_conversation(self, owner_key: str) -> Conversation:
        thread = f"hilo-{len(self.created) + 1}"
        self.created.append(thread)
        now = datetime.now(UTC)
        return Conversation(thread, owner_key, now, now)


def make_client(chat: FakeChat | None = None, quota: Any = None) -> tuple[TestClient, FakeChat]:
    provider = chat or FakeChat(
        events=[
            ChatEvent("open", {"stream": "chat"}),
            ChatEvent("trace", {"thread_id": "hilo-1", "route": "rag", "retrieved": 1}),
            ChatEvent("meta", {"sources": [SOURCE]}),
            ChatEvent("token", {"text": "Cali"}),
            ChatEvent("done", {"conversation_id": "hilo-1", "message_id": "m-1"}),
        ]
    )
    app = create_app(Handlers(ask=lambda question: RESPUESTA, health=lambda: HealthStatus(1, 2)))
    app.state.chat = provider
    app.state.chat_identity = IDENTITY
    app.state.quota = quota or (lambda owner: QuotaResult(allowed=True))
    return TestClient(app), provider


def token(client: TestClient) -> str:
    return client.get("/api/csrf").json()["token"]


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        data = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data.append(line.removeprefix("data:").lstrip())
        events.append((name, json.loads("\n".join(data))))
    return events


def test_csrf_entrega_token_y_cookie() -> None:
    # Arrange
    client, _ = make_client()

    # Act
    response = client.get("/api/csrf")

    # Assert
    assert response.status_code == 200
    assert response.json()["token"]
    assert "carvajal_csrf" in response.headers["set-cookie"]


def test_historial_sin_cookie_es_vacio_y_no_crea_hilo() -> None:
    client, _ = make_client()

    response = client.get("/api/chat/history")

    assert response.status_code == 200
    assert response.json() == {"thread_id": None, "messages": []}


def test_historial_con_identidad_devuelve_mensajes_y_estado() -> None:
    # Arrange
    client, chat = make_client()
    client.get("/api/csrf")
    cookie = IDENTITY.issue("dueno-1")
    chat.messages = [
        ChatMessage(
            "m-1",
            "hilo-1",
            "user",
            "¿Dónde nació?",
            None,
            datetime.now(UTC),
        ),
        ChatMessage(
            "m-2",
            "hilo-1",
            "assistant",
            "Cali",
            [SOURCE],
            datetime.now(UTC),
            status="incomplete",
        ),
    ]

    # Act
    response = client.get("/api/chat/history", cookies={"carvajal_sesion": cookie})

    # Assert
    assert response.status_code == 200
    assert response.json()["thread_id"] == "hilo-1"
    assert response.json()["messages"][1]["status"] == "incomplete"
    assert response.json()["messages"][1]["sources"][0]["url"] == SOURCE["url"]


def test_nuevo_hilo_exige_csrf_y_devuelve_thread() -> None:
    # Arrange
    client, chat = make_client()
    malo = client.post("/api/chat/new", data={CSRF: "ajeno"})
    assert malo.status_code == 403

    # Act
    response = client.post("/api/chat/new", data={CSRF: token(client)})

    # Assert
    assert response.status_code == 200
    assert response.json()["thread_id"] == "hilo-1"
    assert chat.created == ["hilo-1"]


def test_stream_api_emite_trace_tokens_y_done() -> None:
    # Arrange
    client, chat = make_client()
    csrf = token(client)

    # Act
    response = client.post(
        "/api/chat/stream",
        data={
            "question": "¿Dónde nació?",
            CSRF: csrf,
            "client_turn_id": "turno-1",
            "thread_id": "hilo-1",
        },
    )

    # Assert
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["open", "trace", "meta", "token", "done"]
    assert events[1][1]["route"] == "rag"
    assert events[2][1]["sources"][0]["url"] == SOURCE["url"]
    assert chat.calls[0]["turn"] == "turno-1"
    assert chat.calls[0]["thread"] == "hilo-1"


def test_stream_api_sin_csrf_no_cobra_cuota() -> None:
    # Arrange
    llamadas: list[str] = []
    client, chat = make_client(quota=lambda owner: llamadas.append(owner) or QuotaResult(True))

    # Act
    response = client.post("/api/chat/stream", data={"question": "¿Dónde nació?"})

    # Assert
    assert response.status_code == 403
    assert parse_sse(response.text)[-1][0] == "error"
    assert llamadas == []
    assert chat.calls == []


def test_stream_api_429_llega_como_evento_error() -> None:
    # Arrange
    client, _ = make_client(quota=lambda owner: QuotaResult(allowed=False, retry_after=60))
    csrf = token(client)

    # Act
    response = client.post(
        "/api/chat/stream",
        data={"question": "¿Dónde nació?", CSRF: csrf},
    )

    # Assert
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    name, data = parse_sse(response.text)[-1]
    assert name == "error"
    assert "límite diario" in data["message"]


def test_stream_api_503_por_fallo_del_proveedor_es_sse() -> None:
    # Arrange: el proveedor lanza antes de emitir; la red de seguridad cierra.
    class Broken(FakeChat):
        def stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatEvent]:
            raise RuntimeError("qdrant caído")
            yield ChatEvent("open", {})

    client, _ = make_client(Broken())
    csrf = token(client)

    # Act
    response = client.post(
        "/api/chat/stream",
        data={"question": "¿Dónde nació?", CSRF: csrf},
    )

    # Assert
    assert response.status_code == 200
    name, data = parse_sse(response.text)[-1]
    assert name == "error"
    assert data["message"] == SERVICE_UNAVAILABLE
    assert data["incomplete"] is False


def test_cors_permite_el_origen_del_frontend() -> None:
    # Arrange
    client, _ = make_client()

    # Act
    response = client.options(
        "/api/chat/stream",
        headers={
            "Origin": "http://localhost:8001",
            "Access-Control-Request-Method": "POST",
        },
    )

    # Assert
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8001"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_health_api_separa_indice_y_base(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    from apps.api import runtime

    class FakeSession:
        def scalar(self, statement: Any) -> int:
            return 24

    @contextmanager
    def fake_begin() -> Generator[FakeSession]:
        yield FakeSession()

    class FakeFactory:
        def begin(self) -> Any:
            return fake_begin()

    class FakeQdrant:
        def health(self) -> dict[str, object]:
            return {"status": "ok", "collection": "carvajal_chunks", "points": 125}

    monkeypatch.setattr(runtime, "session_factory", lambda: FakeFactory())
    monkeypatch.setattr(runtime, "qdrant_store", lambda: FakeQdrant())
    client, _ = make_client()

    # Act
    response = client.get("/api/health")

    # Assert
    assert response.status_code == 200
    assert response.json()["index"]["points"] == 125
    assert response.json()["documents"] == 24


def test_metricas_publica_indice_corpus_y_retrieval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange
    from apps.api import runtime
    from apps.api.views import api as api_views

    class FakeSession:
        def __init__(self) -> None:
            self.valores = iter([24, 125])

        def scalar(self, statement: Any) -> int:
            return next(self.valores)

    @contextmanager
    def fake_begin() -> Generator[FakeSession]:
        yield FakeSession()

    class FakeFactory:
        def begin(self) -> Any:
            return fake_begin()

    class FakeQdrant:
        def info(self) -> dict[str, object]:
            return {
                "status": "green",
                "optimizer_status": "ok",
                "collection": "carvajal_chunks",
                "points": 125,
            }

    resumen = tmp_path / "retrieval-summary.json"
    resumen.write_text(
        json.dumps({"k": 10, "recall_at_k": 0.8, "ndcg_at_k": 0.7791}), encoding="utf-8"
    )
    monkeypatch.setattr(runtime, "session_factory", lambda: FakeFactory())
    monkeypatch.setattr(runtime, "qdrant_store", lambda: FakeQdrant())
    monkeypatch.setattr(api_views, "RETRIEVAL_SUMMARY", resumen)
    client, _ = make_client()

    # Act
    response = client.get("/api/metricas")

    # Assert
    datos = response.json()
    assert response.status_code == 200
    assert datos["corpus"] == {"documents": 24, "chunks": 125}
    assert datos["index"]["points"] == 125
    assert datos["retrieval"]["recall_at_k"] == 0.8


def test_metricas_degrada_sin_base_y_sin_resumen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange
    from apps.api import runtime
    from apps.api.views import api as api_views

    class FakeFactory:
        def begin(self) -> Any:
            raise OSError("base no disponible")

    class FakeQdrant:
        def info(self) -> dict[str, object]:
            return {"status": "green", "collection": "carvajal_chunks", "points": 125}

    monkeypatch.setattr(runtime, "session_factory", lambda: FakeFactory())
    monkeypatch.setattr(runtime, "qdrant_store", lambda: FakeQdrant())
    monkeypatch.setattr(api_views, "RETRIEVAL_SUMMARY", tmp_path / "inexistente.json")
    client, _ = make_client()

    # Act
    response = client.get("/api/metricas")

    # Assert
    datos = response.json()
    assert response.status_code == 503
    assert datos["status"] == "degraded"
    assert datos["corpus"] == {"status": "unavailable"}
    assert datos["index"]["points"] == 125
    assert datos["retrieval"] is None
