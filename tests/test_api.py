"""Pruebas del shell web con handlers falsos: sin red ni base de datos."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from apps.api.main import Handlers, HealthStatus, create_app
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.application.ask import Answer, LLMError, Source

SOURCE = Source("historia-1", "historia", "historia", "core", 0.912, "Inició operaciones en 1904.")
ANSWER = Answer(text="Carvajal nació en 1904 [1].", sources=(SOURCE,))


def make_client(
    ask: Callable[[str], Answer] | None = None,
    health: Callable[[], HealthStatus] | None = None,
) -> TestClient:
    """Cliente con handlers falsos; por defecto responden bien."""

    return TestClient(
        create_app(
            Handlers(
                ask=ask or (lambda question: ANSWER),
                health=health or (lambda: HealthStatus(documents=12, chunks=34)),
            )
        )
    )


def failing(error: Exception) -> Callable[[str], Answer]:
    """Handler de preguntas que lanza el error indicado."""

    def ask(question: str) -> Answer:
        raise error

    return ask


def unhealthy() -> Callable[[], HealthStatus]:
    def health() -> HealthStatus:
        raise RuntimeError("conexión rechazada")

    return health


def test_health_devuelve_estado_y_conteos() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/health")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "documents": 12, "chunks": 34}


def test_health_degradado_devuelve_503() -> None:
    # Arrange
    client = make_client(health=unhealthy())

    # Act
    response = client.get("/health")

    # Assert
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert "conexión rechazada" in response.text


def test_portada_renderiza_el_formulario() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/")

    # Assert
    assert response.status_code == 200
    assert 'name="question"' in response.text and 'hx-post="/ask"' in response.text


def test_ask_devuelve_respuesta_y_fuentes() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.post("/ask", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 200
    assert "Carvajal nació en 1904" in response.text
    assert "historia-1" in response.text
    assert "0.912" in response.text


def test_pregunta_vacia_devuelve_422() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.post("/ask", data={"question": "   "})

    # Assert
    assert response.status_code == 422
    assert "La pregunta no puede estar vacía." in response.text


def test_value_error_devuelve_422() -> None:
    # Arrange
    client = make_client(ask=failing(ValueError("La pregunta no puede estar vacía.")))

    # Act
    response = client.post("/ask", data={"question": "¿?"})

    # Assert
    assert response.status_code == 422
    assert "La pregunta no puede estar vacía." in response.text


@pytest.mark.parametrize(
    "error",
    [
        EmbeddingError("No se pudo contactar con Ollama."),
        LLMError("El proveedor de LLM devolvió una respuesta vacía."),
    ],
)
def test_fallos_de_proveedor_devuelven_503(error: Exception) -> None:
    # Arrange
    client = make_client(ask=failing(error))

    # Act
    response = client.post("/ask", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 503
    assert str(error) in response.text
    assert "Traceback" not in response.text
