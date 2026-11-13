"""Pruebas de observabilidad: request_id, logs JSON, tokens y Sentry."""

import re
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage

from apps.api import observability
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.observability import REQUEST_ID_HEADER, init_sentry
from carvajal.adapters.llm._chat import _LangChainChat
from carvajal.application.ask import Answer
from carvajal.infrastructure.settings import Settings

RESPUESTA = Answer("ok", ())


class FakeLogger:
    """Registra eventos y campos en lugar de escribir JSON."""

    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict[str, Any]]] = []

    def info(self, evento: str, **campos: Any) -> None:
        self.eventos.append((evento, campos))

    def warning(self, evento: str, **campos: Any) -> None:
        self.eventos.append((evento, campos))


class FakeChat:
    """Chat con la forma mínima de ``ChatLike``, sin red."""

    model_name = "deepseek-chat"

    def __init__(self, respuesta: AIMessage) -> None:
        self.respuesta = respuesta

    def invoke(self, input: Sequence[BaseMessage]) -> BaseMessage:
        return self.respuesta

    def stream(self, input: Sequence[BaseMessage]) -> Iterator[BaseMessage]:
        return iter([self.respuesta])


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
