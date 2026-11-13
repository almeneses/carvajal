"""Pruebas HTTP del chat con un proveedor falso: sin red ni base de datos."""

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.identity import COOKIE_MAX_AGE, COOKIE_NAME, SignedCookieIdentity
from apps.api.main import create_app
from apps.api.views.chat import (
    EMPTY_QUESTION,
    SERVICE_UNAVAILABLE,
    TOO_LONG_QUESTION,
)
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage.chat import ChatMessage
from carvajal.application.ask import Answer, LLMError, Source
from carvajal.application.quota import QuotaResult

SOURCE = Source("historia-1", "historia", "historia", "core", 0.912, "Inició en 1904.")
IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")
WHEN = datetime(2026, 11, 12, tzinfo=UTC)


@dataclass
class FakeChat:
    """Doble en memoria del servicio; solo persiste si la respuesta termina bien."""

    by_owner: dict[str, list[ChatMessage]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    history_calls: list[str] = field(default_factory=list)
    error: Exception | None = None
    history_error: Exception | None = None

    def history(self, owner_key: str) -> Sequence[ChatMessage]:
        self.history_calls.append(owner_key)
        if self.history_error is not None:
            raise self.history_error
        return list(self.by_owner.get(owner_key, []))

    def ask(self, owner_key: str, question: str) -> Answer:
        self.calls.append((owner_key, question))
        if self.error is not None:
            raise self.error
        answer = Answer(text=f"Respuesta a: {question}", sources=(SOURCE,))
        self.by_owner.setdefault(owner_key, []).extend(
            [
                _message("user", question, sources=None),
                _message("assistant", answer.text, sources=[asdict(SOURCE)]),
            ]
        )
        return answer


@dataclass
class FakeIdentity:
    """Identidad fija para probar la inyección sin pasar por cookies reales."""

    owner_key: str = "dueno-fijo"

    def read(self, request: object) -> str:
        return self.owner_key

    def issue(self, owner_key: str) -> str:
        return f"firma::{owner_key}"

    def new_owner_key(self) -> str:
        return "dueno-nuevo"


def _message(role: str, content: str, *, sources: list[dict[str, object]] | None) -> ChatMessage:
    return ChatMessage(
        id=f"{role}-{content}",
        conversation_id="conversacion",
        role=role,
        content=content,
        sources=sources,
        created_at=WHEN,
    )


def make_client(
    chat: FakeChat | None = None,
    identity: SignedCookieIdentity | FakeIdentity | None = None,
    quota: Callable[[str], QuotaResult] | None = None,
    *,
    base_url: str = "http://testserver",
) -> tuple[TestClient, FakeChat]:
    """Cliente con proveedor, identidad y cuota inyectados; por defecto responden bien."""

    provider = chat or FakeChat()
    app = create_app()
    app.state.chat = provider
    app.state.chat_identity = identity or IDENTITY
    app.state.quota = quota or (lambda owner: QuotaResult(allowed=True))
    return TestClient(app, base_url=base_url), provider


def _cookie(owner_key: str) -> str:
    return IDENTITY.issue(owner_key)


def test_get_sin_cookie_muestra_el_estado_vacio_y_emite_cookie() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert "Todavía no hay mensajes" in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.history_calls == []


def test_la_cookie_http_es_httponly_lax_y_sin_secure() -> None:
    # Arrange
    client, _ = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert f"{COOKIE_NAME}=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert f"Max-Age={COOKIE_MAX_AGE}" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie


def test_la_cookie_https_marca_secure() -> None:
    # Arrange
    client, _ = make_client(base_url="https://testserver")

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_get_con_cookie_muestra_el_historial_en_orden() -> None:
    # Arrange
    client, chat = make_client()
    chat.by_owner["dueno-a"] = [
        _message("user", "primera pregunta", sources=None),
        _message("assistant", "primera respuesta", sources=[asdict(SOURCE)]),
        _message("user", "segunda pregunta", sources=None),
    ]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert chat.history_calls == ["dueno-a"]
    primera = response.text.index("primera pregunta")
    respuesta = response.text.index("primera respuesta")
    segunda = response.text.index("segunda pregunta")
    assert primera < respuesta < segunda
    assert "Fuentes citadas" in response.text
    assert "<strong>historia</strong>" in response.text


def test_get_con_cookie_manipulada_regenera_sin_romper() -> None:
    # Arrange
    client, chat = make_client()
    client.cookies.set(COOKIE_NAME, "dueno-a.firma-falsa")

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert "Todavía no hay mensajes" in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.history_calls == []


def test_post_vacio_devuelve_422_sin_persistir() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = client.post("/chat", data={"question": "   "})

    # Assert
    assert response.status_code == 422
    assert "<!doctype html>" in response.text
    assert "<header" in response.text
    assert "<form" in response.text
    assert 'id="pregunta"' in response.text
    assert EMPTY_QUESTION in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.calls == []
    assert chat.by_owner == {}


def test_post_demasiado_largo_devuelve_422_sin_persistir() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = client.post("/chat", data={"question": "x" * 501})

    # Assert
    assert response.status_code == 422
    assert "<form" in response.text
    assert 'id="pregunta"' in response.text
    assert TOO_LONG_QUESTION in response.text
    assert "500" in response.text
    assert 'value="' + "x" * 501 + '"' in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.calls == []
    assert chat.by_owner == {}


def test_post_ok_redirige_emite_cookie_y_conserva_el_historial() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = client.post(
        "/chat",
        data={"question": "¿Cuándo nació?"},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == "/chat"
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert len(chat.calls) == 1
    assert chat.calls[0][1] == "¿Cuándo nació?"

    # Act: la recarga con la cookie que dejó el POST conserva la conversación.
    reloaded = client.get("/chat")

    # Assert
    assert reloaded.status_code == 200
    assert "¿Cuándo nació?" in reloaded.text
    assert "Respuesta a: ¿Cuándo nació?" in reloaded.text
    assert reloaded.text.index("¿Cuándo nació?") < reloaded.text.index("Respuesta a:")


def test_post_usa_el_dueno_de_la_cookie_valida() -> None:
    # Arrange
    client, chat = make_client()
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.post(
        "/chat",
        data={"question": "¿Qué es Carvajal?"},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert chat.calls == [("dueno-a", "¿Qué es Carvajal?")]


def test_post_con_cookie_invalida_regenera_la_identidad() -> None:
    # Arrange
    client, chat = make_client()
    client.cookies.set(COOKIE_NAME, "dueno-manipulado.firma-falsa")

    # Act
    response = client.post(
        "/chat",
        data={"question": "¿Qué es Carvajal?"},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert len(chat.calls) == 1
    assert chat.calls[0][0] != "dueno-manipulado"


@pytest.mark.parametrize(
    "error",
    [
        LLMError("detalle-interno-llm"),
        EmbeddingError("detalle-interno-embedder"),
        SQLAlchemyError("detalle-interno-base"),
    ],
)
def test_fallos_del_proveedor_devuelven_503_sin_filtrar_ni_persistir(error: Exception) -> None:
    # Arrange
    client, chat = make_client(chat=FakeChat(error=error))

    # Act
    response = client.post("/chat", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 503
    assert "<form" in response.text
    assert 'id="pregunta"' in response.text
    assert 'value="¿Cuándo nació?"' in response.text
    assert SERVICE_UNAVAILABLE in response.text
    assert str(error) not in response.text
    assert "Traceback" not in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.by_owner == {}


def test_el_error_503_conserva_el_historial_previo() -> None:
    # Arrange
    client, chat = make_client(chat=FakeChat(error=LLMError("caído")))
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))
    chat.by_owner["dueno-a"] = [_message("user", "pregunta previa", sources=None)]

    # Act
    response = client.post("/chat", data={"question": "pregunta nueva"})

    # Assert
    assert response.status_code == 503
    assert "pregunta previa" in response.text
    assert 'value="pregunta nueva"' in response.text
    assert chat.calls == [("dueno-a", "pregunta nueva")]
    assert [message.content for message in chat.by_owner["dueno-a"]] == ["pregunta previa"]


def test_fallo_de_base_al_leer_el_historial_devuelve_503_generico() -> None:
    # Arrange
    client, _ = make_client(
        chat=FakeChat(history_error=SQLAlchemyError("127.0.0.1:5432 rechazó la conexión"))
    )
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 503
    assert SERVICE_UNAVAILABLE in response.text
    assert "127.0.0.1" not in response.text
    assert "5432" not in response.text


def test_el_historial_escapa_html() -> None:
    # Arrange
    peligro = "<script>alert(1)</script>"
    client, chat = make_client()
    chat.by_owner["dueno-a"] = [_message("assistant", peligro, sources=None)]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert peligro not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_la_identidad_se_puede_inyectar_sin_cookies_reales() -> None:
    # Arrange
    client, chat = make_client(identity=FakeIdentity(owner_key="dueno-fijo"))

    # Act
    response = client.post(
        "/chat",
        data={"question": "¿Qué es Carvajal?"},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert "firma::dueno-fijo" in response.headers["set-cookie"]
    assert chat.calls == [("dueno-fijo", "¿Qué es Carvajal?")]


def test_post_chat_agota_la_cuota_antes_de_llamar_al_proveedor() -> None:
    """El cupo también frena el chat normal, no solo /ask."""

    # Arrange
    client, chat = make_client(quota=lambda owner: QuotaResult(False, 120))

    # Act
    response = client.post("/chat", data={"question": "¿Qué es Carvajal?"})

    # Assert
    assert response.status_code == 429
    assert response.headers["retry-after"] == "120"
    assert "límite diario" in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.calls == []


def test_fallo_de_bd_en_la_cuota_del_chat_devuelve_503() -> None:
    """Un fallo al consultar la cuota no se convierte en 500 ni llama al proveedor."""

    # Arrange
    def quota(owner: str) -> QuotaResult:
        raise SQLAlchemyError("base caída")

    client, chat = make_client(quota=quota)

    # Act
    response = client.post("/chat", data={"question": "¿Qué es Carvajal?"})

    # Assert
    assert response.status_code == 503
    assert SERVICE_UNAVAILABLE in response.text
    assert chat.calls == []
