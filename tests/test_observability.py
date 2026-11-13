"""Pruebas de observabilidad: request_id, logs JSON, tokens y Sentry."""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
import sentry_sdk
import structlog
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage

from apps.api import observability
from apps.api.chat.identity import SignedCookieIdentity
from apps.api.chat.streaming import ChatEvent
from apps.api.main import SERVICE_UNAVAILABLE, Handlers, HealthStatus, create_app
from apps.api.observability import REQUEST_ID_HEADER, init_sentry
from carvajal.adapters.llm._chat import _LangChainChat
from carvajal.application.ask import Answer
from carvajal.application.quota import QuotaResult
from carvajal.infrastructure.settings import Settings

RESPUESTA = Answer("ok", ())
IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")


class FakeLogger:
    """Registra eventos y campos en lugar de escribir JSON."""

    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict[str, Any]]] = []

    def info(self, evento: str, **campos: Any) -> None:
        self.eventos.append((evento, campos))

    def debug(self, evento: str, **campos: Any) -> None:
        self.eventos.append((evento, campos))

    def warning(self, evento: str, **campos: Any) -> None:
        self.eventos.append((evento, campos))

    def exception(self, evento: str, **campos: Any) -> None:
        self.eventos.append((evento, campos))


@dataclass
class FakeMessage:
    """Mensaje mínimo: ``_text`` y ``_usage`` solo leen estos dos campos."""

    content: str
    usage_metadata: dict[str, int] | None = field(default=None)


class FakeChat:
    """Chat con la forma mínima de ``ChatLike``, sin red."""

    model_name = "deepseek-chat"

    def __init__(self, respuesta: Any, *, chunks: Sequence[Any] = ()) -> None:
        self.respuesta = respuesta
        self.chunks = list(chunks)

    def invoke(self, input: Sequence[BaseMessage]) -> Any:
        return self.respuesta

    def stream(self, input: Sequence[BaseMessage]) -> Iterator[Any]:
        return iter(self.chunks or [self.respuesta])


def _client() -> TestClient:
    return TestClient(
        create_app(
            Handlers(
                ask=lambda question: RESPUESTA,
                health=lambda: HealthStatus(0, 0),
            )
        )
    )


def test_la_respuesta_devuelve_un_request_id() -> None:
    """Toda respuesta lleva un identificador de petición generado si no viene."""

    response = _client().get("/live")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])


def test_respeta_el_request_id_del_cliente() -> None:
    """Si el cliente manda un identificador válido, se propaga tal cual."""

    response = _client().get("/live", headers={REQUEST_ID_HEADER: "peticion-1234"})

    assert response.headers[REQUEST_ID_HEADER] == "peticion-1234"


def test_descarta_un_request_id_invalido() -> None:
    """Un identificador con caracteres raros no se refleja en la respuesta."""

    response = _client().get("/live", headers={REQUEST_ID_HEADER: "no vale!"})

    assert response.headers[REQUEST_ID_HEADER] != "no vale!"
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])


def test_el_acceso_queda_registrado_con_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cada petición deja una línea con método, ruta, estado y duración."""

    # Arrange
    registro = FakeLogger()
    monkeypatch.setattr(observability, "logger", registro)

    # Act
    response = _client().get("/live")

    # Assert
    evento, campos = registro.eventos[-1]
    assert evento == "request.completed"
    assert campos["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert campos["method"] == "GET"
    assert campos["path"] == "/live"
    assert campos["status"] == 200
    assert float(campos["duration_ms"]) >= 0


def test_un_500_lleva_request_id_y_no_filtra_detalles() -> None:
    """La excepción no controlada se convierte en 500 dentro de la pila."""

    # Arrange
    def explode(question: str) -> Answer:
        raise ValueError("detalle interno que no debe salir")

    app = create_app(Handlers(ask=explode, health=lambda: HealthStatus(0, 0)))
    client = TestClient(app, raise_server_exceptions=False)

    # Act
    response = client.post("/ask", data={"question": "¿?"})

    # Assert
    assert response.status_code == 500
    assert REQUEST_ID_HEADER in response.headers
    assert SERVICE_UNAVAILABLE in response.text
    assert "detalle interno" not in response.text


def test_sin_dsn_no_inicializa_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin DSN no se envía nada fuera del proceso."""

    monkeypatch.setattr(observability, "_sentry_iniciado", False)
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: pytest.fail("no debía inicializar"))

    assert init_sentry(Settings(_env_file=None, sentry_dsn="")) is False


def test_con_dsn_inicializa_sentry_sin_datos_personales(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con DSN se inicializa Sentry y se desactiva el envío de PII."""

    # Arrange
    monkeypatch.setattr(observability, "_sentry_iniciado", False)
    llamadas: list[dict[str, object]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: llamadas.append(kwargs))
    settings = Settings(
        _env_file=None,
        sentry_dsn="https://public@example.com/1",
        sentry_environment="test",
        sentry_traces_sample_rate=0.1,
    )

    # Act
    activo = init_sentry(settings)

    # Assert
    assert activo is True
    assert llamadas[0]["dsn"] == "https://public@example.com/1"
    assert llamadas[0]["environment"] == "test"
    assert llamadas[0]["send_default_pii"] is False
    assert llamadas[0]["traces_sample_rate"] == 0.1


def test_el_request_id_es_etiqueta_de_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con Sentry activo, el identificador viaja como tag del evento."""

    # Arrange
    etiquetas: list[tuple[str, str]] = []
    monkeypatch.setattr(observability, "_sentry_iniciado", True)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda key, value: etiquetas.append((key, value)))

    # Act
    response = _client().get("/live")

    # Assert
    assert ("request_id", response.headers[REQUEST_ID_HEADER]) in etiquetas


def test_el_adaptador_registra_los_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el proveedor publica el uso, queda una línea con los tokens."""

    # Arrange
    registro = FakeLogger()
    monkeypatch.setattr("carvajal.adapters.llm._chat.logger", registro)
    mensaje = AIMessage(
        content="hola",
        usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    )
    adaptador = _LangChainChat(FakeChat(mensaje))

    # Act
    texto = adaptador.complete(system="sistema", prompt="pregunta")

    # Assert
    assert texto == "hola"
    assert registro.eventos == [
        ("llm.completed", {"model": "deepseek-chat", "tokens_in": 12, "tokens_out": 3})
    ]


def test_el_stream_mergea_el_uso_de_varios_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """El uso puede venir repartido: no se pierde si no está en el último chunk."""

    # Arrange
    registro = FakeLogger()
    monkeypatch.setattr("carvajal.adapters.llm._chat.logger", registro)
    chunks = [
        FakeMessage(content="", usage_metadata={"input_tokens": 12}),
        FakeMessage(content="hola", usage_metadata={"output_tokens": 3, "total_tokens": 15}),
        FakeMessage(content=""),
    ]
    adaptador = _LangChainChat(FakeChat(FakeMessage(content=""), chunks=chunks))

    # Act
    texto = "".join(adaptador.stream(system="sistema", prompt="pregunta"))

    # Assert
    assert texto == "hola"
    assert registro.eventos == [
        ("llm.completed", {"model": "deepseek-chat", "tokens_in": 12, "tokens_out": 3})
    ]


def test_sin_uso_no_inventa_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un proveedor que no publica consumo no genera una línea falsa."""

    # Arrange
    registro = FakeLogger()
    monkeypatch.setattr("carvajal.adapters.llm._chat.logger", registro)
    adaptador = _LangChainChat(FakeChat(AIMessage(content="hola")))

    # Act
    adaptador.complete(system="sistema", prompt="pregunta")

    # Assert
    assert registro.eventos == []


class _ChatDoble:
    """Chat mínimo que inspecciona el contexto al ejecutar el caso de uso."""

    def __init__(self) -> None:
        self.contextos: list[dict[str, Any]] = []

    def history(self, owner_key: str) -> list[Any]:
        return []

    def ask(self, owner_key: str, question: str) -> Answer:
        self.contextos.append(dict(structlog.contextvars.get_contextvars()))
        return RESPUESTA

    def stream(self, owner_key: str, question: str) -> Iterator[ChatEvent]:
        self.contextos.append(dict(structlog.contextvars.get_contextvars()))
        yield ChatEvent("done", {})


def test_ask_vincula_owner_key_al_contexto() -> None:
    """Los tokens del LLM en /ask quedan asociados al visitante anónimo."""

    # Arrange
    contextos: list[dict[str, Any]] = []

    def ask(question: str) -> Answer:
        contextos.append(dict(structlog.contextvars.get_contextvars()))
        return RESPUESTA

    app = create_app(Handlers(ask=ask, health=lambda: HealthStatus(0, 0)))
    app.state.chat_identity = IDENTITY

    # Act
    response = TestClient(app).post("/ask", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 200
    assert contextos[0]["owner_key"]
    assert contextos[0]["request_id"]


def test_chat_vincula_owner_key_al_contexto() -> None:
    """Los tokens del LLM en POST /chat quedan asociados al visitante anónimo."""

    # Arrange
    provider = _ChatDoble()
    app = create_app(Handlers(ask=lambda question: RESPUESTA, health=lambda: HealthStatus(0, 0)))
    app.state.chat = provider
    app.state.chat_identity = IDENTITY
    app.state.quota = lambda owner: QuotaResult(allowed=True)

    # Act
    response = TestClient(app).post(
        "/chat", data={"question": "¿Cuándo nació?"}, follow_redirects=False
    )

    # Assert
    assert response.status_code == 303
    assert provider.contextos[0]["owner_key"]
    assert provider.contextos[0]["request_id"]


def test_chat_stream_vincula_owner_key_al_contexto() -> None:
    """Los tokens del stream también quedan asociados al visitante anónimo."""

    # Arrange
    provider = _ChatDoble()
    app = create_app(Handlers(ask=lambda question: RESPUESTA, health=lambda: HealthStatus(0, 0)))
    app.state.chat = provider
    app.state.chat_identity = IDENTITY
    app.state.quota = lambda owner: QuotaResult(allowed=True)

    # Act
    response = TestClient(app).post("/chat/stream", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 200
    assert provider.contextos[0]["owner_key"]
    assert provider.contextos[0]["request_id"]
