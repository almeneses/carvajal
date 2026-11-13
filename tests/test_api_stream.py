"""Pruebas HTTP del stream SSE con un proveedor falso: sin red ni base de datos."""

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from http.cookies import SimpleCookie
from time import perf_counter, sleep

import pytest
from fastapi.testclient import TestClient

from apps.api.chat.identity import COOKIE_NAME, SignedCookieIdentity
from apps.api.chat.streaming import ChatEvent
from apps.api.main import create_app
from apps.api.views.chat import EMPTY_QUESTION, TOO_LONG_QUESTION
from carvajal.application.ask import Source

SOURCE = Source("historia-1", "historia", "historia", "core", 0.912, "Inició en 1904.")
IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")
DONE = {"conversation_id": "conv-1", "message_id": "msg-1", "elapsed_ms": 12}


@dataclass
class FakeChat:
    """Doble del servicio: emite los eventos dados y cuenta el cierre."""

    events: list[ChatEvent] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    persisted: int = 0

    def stream(self, owner_key: str, question: str) -> Iterator[ChatEvent]:
        self.calls.append((owner_key, question))
        for event in self.events:
            if event.name == "done":
                self.persisted += 1
            yield event


class SlowTokenChat(FakeChat):
    """Retrasa el primer token para comprobar que se mide el token, no ``open``."""

    def stream(self, owner_key: str, question: str) -> Iterator[ChatEvent]:
        self.calls.append((owner_key, question))
        yield ChatEvent("open", {"stream": "chat"})
        yield ChatEvent("meta", {"sources": []})
        sleep(0.1)
        yield ChatEvent("token", {"text": "Carvajal"})
        yield ChatEvent("done", DONE)
        self.persisted += 1


def ok_events() -> list[ChatEvent]:
    """Secuencia feliz: apertura, fuentes, tokens y cierre."""

    return [
        ChatEvent("open", {"stream": "chat"}),
        ChatEvent("meta", {"sources": [asdict(SOURCE)]}),
        ChatEvent("token", {"text": "Carvajal "}),
        ChatEvent("token", {"text": "nació en 1904 [1]."}),
        ChatEvent("done", DONE),
    ]


def make_client(chat: FakeChat | None = None) -> tuple[TestClient, FakeChat]:
    """Cliente con proveedor e identidad inyectados, sin tocar la base."""

    provider = chat or FakeChat(events=ok_events())
    app = create_app()
    app.state.chat = provider
    app.state.chat_identity = IDENTITY
    return TestClient(app), provider


def parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    """Extrae ``(evento, datos)`` de un cuerpo SSE terminado en línea en blanco."""

    events: list[tuple[str, dict[str, object]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data.append(line.removeprefix("data:").lstrip())
        events.append((name, json.loads("\n".join(data))))
    return events


def test_stream_emite_open_meta_tokens_y_done_en_orden() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = client.post("/chat/stream", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["open", "meta", "token", "token", "done"]
    assert events[0][1] == {"stream": "chat"}
    assert events[1][1] == {"sources": [asdict(SOURCE)]}
    assert [events[2][1]["text"], events[3][1]["text"]] == [
        "Carvajal ",
        "nació en 1904 [1].",
    ]
    assert events[4][1] == DONE
    assert len(chat.calls) == 1
    assert chat.calls[0][1] == "¿Cuándo nació?"
    assert chat.persisted == 1


def test_stream_emite_cookie_si_el_visitante_no_tenia_identidad() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = client.post("/chat/stream", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 200
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    owner_key = IDENTITY.verify(cookie[COOKIE_NAME].value)
    assert owner_key is not None
    assert chat.calls[0][0] == owner_key
    assert COOKIE_NAME in response.headers["set-cookie"]


def test_fallback_antes_del_primer_token_se_emite_y_cierra() -> None:
    # Arrange
    events = [
        ChatEvent("open", {"stream": "chat"}),
        ChatEvent("meta", {"sources": [asdict(SOURCE)]}),
        ChatEvent("fallback", {"text": "Respuesta completa [1].", "sources": [asdict(SOURCE)]}),
        ChatEvent("done", DONE),
    ]
    client, chat = make_client(FakeChat(events=events))

    # Act
    response = client.post("/chat/stream", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 200
    parsed = parse_sse(response.text)
    assert [name for name, _ in parsed] == ["open", "meta", "fallback", "done"]
    assert parsed[2][1] == {
        "text": "Respuesta completa [1].",
        "sources": [asdict(SOURCE)],
    }
    assert chat.persisted == 1
    assert "token" not in [name for name, _ in parsed]


def test_error_tras_un_token_marca_incompleto_y_no_emite_done() -> None:
    # Arrange
    events = [
        ChatEvent("open", {"stream": "chat"}),
        ChatEvent("meta", {"sources": []}),
        ChatEvent("token", {"text": "Carvajal"}),
        ChatEvent(
            "error",
            {
                "message": "El servicio de chat no está disponible en este momento.",
                "incomplete": True,
            },
        ),
    ]
    client, chat = make_client(FakeChat(events=events))

    # Act
    response = client.post("/chat/stream", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 200
    parsed = parse_sse(response.text)
    assert [name for name, _ in parsed] == ["open", "meta", "token", "error"]
    assert parsed[-1][1]["incomplete"] is True
    assert chat.persisted == 0


@pytest.mark.parametrize(
    ("question", "message"),
    [
        ("   ", EMPTY_QUESTION),
        ("x" * 501, TOO_LONG_QUESTION),
    ],
)
def test_pregunta_invalida_devuelve_422_sse_sin_llamar_al_proveedor(
    question: str,
    message: str,
) -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = client.post("/chat/stream", data={"question": question})

    # Assert
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/event-stream")
    parsed = parse_sse(response.text)
    assert parsed == [("error", {"message": message, "incomplete": False})]
    assert chat.calls == []


def test_open_es_el_primer_evento_y_llega_en_menos_de_dos_segundos() -> None:
    # Arrange: el doble responde de inmediato; la medición es una cota superior.
    client, _ = make_client()

    # Act
    inicio = perf_counter()
    primer_evento: tuple[str, float] | None = None
    with client.stream("POST", "/chat/stream", data={"question": "¿Cuándo nació?"}) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event:"):
                nombre = line.removeprefix("event:").strip()
                primer_evento = (nombre, perf_counter() - inicio)
                break

    # Assert
    assert primer_evento is not None, "el stream no emitió ningún evento"
    assert primer_evento[0] == "open"
    assert primer_evento[1] < 2.0, f"open tardó {primer_evento[1]:.3f} s"


def test_el_primer_token_llega_en_menos_de_dos_segundos() -> None:
    # Arrange: open/meta salen de inmediato y el token se retrasa 100 ms; medir
    # open por error daría una latencia menor y rompería la cota inferior.
    client, _ = make_client(SlowTokenChat())

    # Act
    inicio = perf_counter()
    primer_token: float | None = None
    with client.stream("POST", "/chat/stream", data={"question": "¿Cuándo nació?"}) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.strip() == "event: token":
                primer_token = perf_counter() - inicio
                break

    # Assert
    assert primer_token is not None, "el stream no emitió ningún token"
    assert 0.05 <= primer_token < 2.0, f"el primer token tardó {primer_token:.3f} s"


def test_get_no_puede_disparar_el_stream() -> None:
    # Arrange: la pregunta no debe viajar en la URL ni dispararse con un GET.
    client, chat = make_client()

    # Act
    response = client.get("/chat/stream", params={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 405
    assert chat.calls == []
