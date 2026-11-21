"""Caracterización (spec 044) del contrato de las tres entradas del chat.

Fija, antes del refactor, rutas, formatos, códigos, mensajes, cabeceras, cookies y
el orden CSRF → validación → cuota → proveedor del formulario (`POST /chat`), del
stream web (`POST /chat/stream`) y del stream de API (`POST /api/chat/stream`).
Estas expectativas no deben cambiar con la extracción de las reglas compartidas.
"""

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from http.cookies import SimpleCookie
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.identity import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    SignedCookieIdentity,
)
from apps.api.chat.service import SERVICE_UNAVAILABLE, ThreadAccessError
from apps.api.chat.streaming import ChatEvent
from apps.api.main import create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.api.views.chat import (
    CSRF_INVALIDO,
    EMPTY_QUESTION,
    LIMITE_ALCANZADO,
    TOO_LONG_QUESTION,
)
from carvajal.application.ask import Answer, LLMError, Source
from carvajal.application.quota import QuotaResult

SOURCE = Source("historia-1", "historia", "historia", "core", 0.912, "Inició en 1904.")
IDENTITY = SignedCookieIdentity(b"secreto-caracterizacion-044")
DONE = {"conversation_id": "conv-1", "message_id": "msg-1", "elapsed_ms": 12}
CHAT_PATHS = ["/chat", "/chat/stream", "/api/chat/stream"]


@dataclass
class FakeChat:
    """Doble que responde, falla o registra llamadas según la prueba."""

    events: list[ChatEvent] = field(
        default_factory=lambda: [
            ChatEvent("open", {"stream": "chat"}),
            ChatEvent("meta", {"sources": [asdict(SOURCE)]}),
            ChatEvent("token", {"text": "Carvajal "}),
            ChatEvent("token", {"text": "nació en 1904 [1]."}),
            ChatEvent("done", DONE),
        ]
    )
    ask_error: Exception | None = None
    stream_raise: Exception | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def history(self, owner_key: str, thread_id: str | None = None) -> Sequence[Any]:
        return []

    def conversations(self, owner_key: str) -> Sequence[Any]:
        return []

    def new_conversation(self, owner_key: str) -> Any:
        raise NotImplementedError

    def ask(self, owner_key: str, question: str, *, thread_id: str | None = None) -> Answer:
        self.calls.append((owner_key, question))
        if self.ask_error is not None:
            raise self.ask_error
        return Answer(text=f"Respuesta a: {question}", sources=(SOURCE,))

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        self.calls.append((owner_key, question))
        yield from self.events
        if self.stream_raise is not None:
            raise self.stream_raise


def make_client(
    chat: FakeChat | None = None,
    quota: Callable[[str], QuotaResult] | None = None,
) -> tuple[TestClient, FakeChat, list[str]]:
    """Cliente con proveedor, identidad y cuota inyectados; registra los cobros."""

    provider = chat or FakeChat()
    cobros: list[str] = []

    def quota_default(owner: str) -> QuotaResult:
        cobros.append(owner)
        return QuotaResult(allowed=True)

    app = create_app()
    app.state.chat = provider
    app.state.chat_identity = IDENTITY
    app.state.quota = quota or quota_default
    if quota is not None:
        # La cuota inyectada también se registra para probar el orden.
        def registrada(owner: str) -> QuotaResult:
            cobros.append(owner)
            return quota(owner)

        app.state.quota = registrada
    return TestClient(app), provider, cobros


def _csrf(client: TestClient) -> str:
    if CSRF_COOKIE not in client.cookies:
        client.get("/faq")
    return client.cookies[CSRF_COOKIE]


def _data(client: TestClient, question: str = "¿Cuándo nació?", **extra: str) -> dict[str, str]:
    return {
        "question": question,
        CSRF_FIELD: _csrf(client),
        "thread_id": "",
        "client_turn_id": "",
        **extra,
    }


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Extrae ``(evento, datos)`` de un cuerpo SSE terminado en línea en blanco."""

    events: list[tuple[str, dict[str, Any]]] = []
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


def _cookies(response: Any) -> dict[str, str]:
    """Cookies emitidas por la respuesta, por nombre, sin perder ninguna."""

    emitidas: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(header)
        for name, morsel in cookie.items():
            emitidas[name] = morsel.value
    return emitidas


# ---------------------------------------------------------------------------
# Rutas, formatos y paridad web/API (SC-001, SC-002)
# ---------------------------------------------------------------------------


def test_el_formulario_responde_303_html_sin_cuerpo() -> None:
    # Arrange
    client, chat, _ = make_client()

    # Act
    response = client.post("/chat", data=_data(client), follow_redirects=False)

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == "/chat"
    assert response.text == ""
    assert chat.calls and chat.calls[0][1] == "¿Cuándo nació?"


@pytest.mark.parametrize("path", CHAT_PATHS[1:])
def test_los_streams_responden_el_mismo_contrato_sse(path: str) -> None:
    # Arrange
    client, _, _ = make_client()

    # Act
    response = client.post(path, data=_data(client))

    # Assert
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    parsed = parse_sse(response.text)
    assert [name for name, _ in parsed] == ["open", "meta", "token", "token", "done"]
    assert parsed[-1][1] == DONE


def test_web_y_api_emiten_la_misma_secuencia_observable() -> None:
    # Arrange
    web, _, _ = make_client()
    api, _, _ = make_client()

    # Act
    respuesta_web = web.post("/chat/stream", data=_data(web))
    respuesta_api = api.post("/api/chat/stream", data=_data(api))

    # Assert
    assert parse_sse(respuesta_web.text) == parse_sse(respuesta_api.text)


@pytest.mark.parametrize("path", CHAT_PATHS[1:])
def test_get_no_dispara_el_chat(path: str) -> None:
    # Arrange
    client, chat, _ = make_client()

    # Act
    response = client.get(path)

    # Assert
    assert response.status_code == 405
    assert chat.calls == []


# ---------------------------------------------------------------------------
# Orden CSRF → validación → cuota → proveedor (FR-003)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", CHAT_PATHS)
def test_csrf_invalido_va_primero_y_no_cobra_ni_llama(path: str) -> None:
    # Arrange: la cuota negaría y el proveedor fallaría; no deben ejecutarse.
    client, chat, cobros = make_client(quota=lambda owner: QuotaResult(False, 99))

    # Act
    response = client.post(path, data={"question": "¿Cuándo nació?", CSRF_FIELD: "ajeno"})

    # Assert
    assert response.status_code == 403
    assert response.headers["x-carvajal-error"] == "1"
    assert cobros == []
    assert chat.calls == []
    if path == "/chat":
        assert "<!doctype html>" in response.text
        assert CSRF_INVALIDO in response.text
    else:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert parse_sse(response.text) == [
            ("error", {"message": CSRF_INVALIDO, "incomplete": False})
        ]


@pytest.mark.parametrize("path", CHAT_PATHS)
@pytest.mark.parametrize(
    ("question", "message"),
    [("   ", EMPTY_QUESTION), ("x" * 501, TOO_LONG_QUESTION)],
)
def test_validacion_va_antes_de_la_cuota(path: str, question: str, message: str) -> None:
    # Arrange: la cuota negaría; la validación debe responder antes.
    client, chat, cobros = make_client(quota=lambda owner: QuotaResult(False, 99))

    # Act
    response = client.post(path, data=_data(client, question=question))

    # Assert
    assert response.status_code == 422
    assert response.headers["x-carvajal-error"] == "1"
    assert cobros == []
    assert chat.calls == []
    if path == "/chat":
        assert message in response.text
        assert 'id="pregunta"' in response.text
    else:
        assert parse_sse(response.text) == [("error", {"message": message, "incomplete": False})]


@pytest.mark.parametrize("path", CHAT_PATHS)
def test_la_cuota_se_cobra_despues_de_validar_y_antes_del_proveedor(path: str) -> None:
    # Arrange
    client, chat, cobros = make_client()

    # Act
    client.post(path, data=_data(client), follow_redirects=False)

    # Assert
    assert len(cobros) == 1
    assert len(chat.calls) == 1


# ---------------------------------------------------------------------------
# Errores controlados: 422, 429, 503 y 404 (FR-004, FR-006)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", CHAT_PATHS[1:])
def test_cuota_agotada_429_con_retry_after(path: str) -> None:
    # Arrange
    client, chat, _ = make_client(quota=lambda owner: QuotaResult(False, 120))

    # Act
    response = client.post(path, data=_data(client))

    # Assert
    assert response.status_code == 429
    assert response.headers["retry-after"] == "120"
    assert chat.calls == []
    eventos = parse_sse(response.text)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["message"] == LIMITE_ALCANZADO


def test_cuota_agotada_en_el_formulario_429_html() -> None:
    # Arrange
    client, chat, _ = make_client(quota=lambda owner: QuotaResult(False, 120))

    # Act
    response = client.post("/chat", data=_data(client))

    # Assert
    assert response.status_code == 429
    assert response.headers["retry-after"] == "120"
    assert LIMITE_ALCANZADO in response.text
    assert 'id="pregunta"' in response.text
    assert chat.calls == []


@pytest.mark.parametrize("path", CHAT_PATHS)
def test_fallo_de_bd_en_la_cuota_503_sin_detalle(path: str) -> None:
    # Arrange
    def caida(owner: str) -> QuotaResult:
        raise SQLAlchemyError("127.0.0.1:5432 rechazó la conexión")

    client, chat, _ = make_client(quota=caida)

    # Act
    response = client.post(path, data=_data(client))

    # Assert
    assert response.status_code == 503
    assert "127.0.0.1" not in response.text
    assert "5432" not in response.text
    assert chat.calls == []
    if path == "/chat":
        assert SERVICE_UNAVAILABLE in response.text
    else:
        assert parse_sse(response.text)[-1] == (
            "error",
            {"message": SERVICE_UNAVAILABLE, "incomplete": False},
        )


@pytest.mark.parametrize(
    "error",
    [LLMError("detalle-interno-llm"), SQLAlchemyError("detalle-interno-base")],
)
def test_formulario_503_conserva_pregunta_y_oculta_detalle(error: Exception) -> None:
    # Arrange
    client, _, _ = make_client(chat=FakeChat(ask_error=error))

    # Act
    response = client.post("/chat", data=_data(client), follow_redirects=False)

    # Assert
    assert response.status_code == 503
    assert SERVICE_UNAVAILABLE in response.text
    assert 'value="¿Cuándo nació?"' in response.text
    assert str(error) not in response.text


@pytest.mark.parametrize("path", CHAT_PATHS[1:])
def test_stream_error_tras_token_marca_incompleto_y_no_emite_done(path: str) -> None:
    # Arrange: los eventos llegan y después el proveedor falla.
    events = [
        ChatEvent("open", {"stream": "chat"}),
        ChatEvent("token", {"text": "Carvajal"}),
    ]
    client, _, _ = make_client(chat=FakeChat(events=events, stream_raise=LLMError("interno")))

    # Act
    response = client.post(path, data=_data(client))

    # Assert
    assert response.status_code == 200
    parsed = parse_sse(response.text)
    assert [name for name, _ in parsed] == ["open", "token", "error"]
    assert parsed[-1][1]["incomplete"] is True
    assert "interno" not in response.text


def test_hilo_ajeno_en_el_formulario_404() -> None:
    # Arrange
    client, _, _ = make_client(chat=FakeChat(ask_error=ThreadAccessError("de otro dueño")))

    # Act
    response = client.post(
        "/chat",
        data=_data(client, thread_id="hilo-ajeno"),
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 404
    assert "de otro dueño" not in response.text


# ---------------------------------------------------------------------------
# Cookies e identidad (FR-005)
# ---------------------------------------------------------------------------


def test_el_formulario_emite_la_cookie_de_identidad_del_visitante_nuevo() -> None:
    # Arrange
    client, _, _ = make_client()

    # Act
    response = client.post("/chat", data=_data(client), follow_redirects=False)

    # Assert
    cookies = _cookies(response)
    assert COOKIE_NAME in cookies
    assert IDENTITY.verify(cookies[COOKIE_NAME]) is not None
    header = next(item for item in response.headers.get_list("set-cookie") if COOKIE_NAME in item)
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert f"Max-Age={COOKIE_MAX_AGE}" in header
    assert "Path=/" in header
    assert "Secure" not in header


@pytest.mark.parametrize("path", CHAT_PATHS[1:])
def test_los_streams_emiten_cookie_de_identidad_si_el_turno_arranca(path: str) -> None:
    # Arrange
    client, _, _ = make_client()

    # Act
    response = client.post(path, data=_data(client))

    # Assert
    assert response.status_code == 200
    assert IDENTITY.verify(_cookies(response)[COOKIE_NAME]) is not None


@pytest.mark.parametrize("path", CHAT_PATHS[1:])
def test_el_403_de_los_streams_no_emite_cookie_de_identidad(path: str) -> None:
    # Arrange
    client, _, _ = make_client()
    _csrf(client)  # fija la cookie CSRF para aislar la de identidad

    # Act
    response = client.post(path, data={"question": "x", CSRF_FIELD: "ajeno"})

    # Assert
    assert response.status_code == 403
    assert COOKIE_NAME not in _cookies(response)


@pytest.mark.parametrize("path", CHAT_PATHS[1:])
def test_el_422_de_los_streams_no_emite_cookie_de_identidad(path: str) -> None:
    # Arrange
    client, _, _ = make_client()

    # Act
    response = client.post(path, data=_data(client, question="   "))

    # Assert
    assert response.status_code == 422
    assert COOKIE_NAME not in _cookies(response)


def test_el_formulario_si_emite_cookie_de_identidad_en_un_403() -> None:
    # Arrange: la identidad se resuelve antes del CSRF en esta ruta.
    client, _, _ = make_client()
    _csrf(client)

    # Act
    response = client.post("/chat", data={"question": "x", CSRF_FIELD: "ajeno"})

    # Assert
    assert response.status_code == 403
    assert COOKIE_NAME in _cookies(response)


@pytest.mark.parametrize("path", CHAT_PATHS[1:])
def test_la_cuota_agotada_emite_cookie_si_el_visitante_es_nuevo(path: str) -> None:
    # Arrange
    client, _, _ = make_client(quota=lambda owner: QuotaResult(False, 30))

    # Act
    response = client.post(path, data=_data(client))

    # Assert
    assert response.status_code == 429
    assert COOKIE_NAME in _cookies(response)


def test_la_cookie_valida_conserva_el_dueno_en_los_tres_caminos() -> None:
    # Arrange
    client, chat, cobros = make_client()
    client.cookies.set(COOKIE_NAME, IDENTITY.issue("dueno-fijo"))

    # Act: formulario, stream web y stream de API con el mismo dueño.
    client.post("/chat", data=_data(client), follow_redirects=False)
    client.post("/chat/stream", data=_data(client))
    client.post("/api/chat/stream", data=_data(client))

    # Assert
    assert [owner for owner, _ in chat.calls] == ["dueno-fijo"] * 3
    assert cobros == ["dueno-fijo"] * 3
