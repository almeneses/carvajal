"""Pruebas del shell web con handlers falsos: sin red ni base de datos."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api import main
from apps.api.main import (
    DB_UNAVAILABLE,
    SERVICE_UNAVAILABLE,
    Handlers,
    HealthStatus,
    create_app,
)
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.application.ask import Answer, LLMError, Source

SOURCE = Source("historia-1", "historia", "historia", "core", 0.912, "Inició operaciones en 1904.")
ANSWER = Answer(text="Carvajal nació en 1904 [1].", sources=(SOURCE,))


def make_client(
    ask: Callable[[str], Answer] | None = None,
    health: Callable[[], HealthStatus] | None = None,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    """Cliente con handlers falsos; por defecto responden bien."""

    return TestClient(
        create_app(
            Handlers(
                ask=ask or (lambda question: ANSWER),
                health=health or (lambda: HealthStatus(documents=12, chunks=34)),
            )
        ),
        raise_server_exceptions=raise_server_exceptions,
    )


def failing(error: Exception) -> Callable[[str], Answer]:
    """Handler de preguntas que lanza el error indicado."""

    def ask(question: str) -> Answer:
        raise error

    return ask


def unhealthy() -> Callable[[], HealthStatus]:
    def health() -> HealthStatus:
        raise SQLAlchemyError("connection to 127.0.0.1:5432 as user carvajal failed")

    return health


def test_health_devuelve_estado_y_conteos() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/health")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "documents": 12, "chunks": 34}


def test_health_degradado_no_filtra_detalles() -> None:
    # Arrange
    client = make_client(health=unhealthy())

    # Act
    response = client.get("/health")

    # Assert
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "detail": DB_UNAVAILABLE}
    assert "127.0.0.1" not in response.text and "carvajal" not in response.text


def test_portada_renderiza_el_formulario() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/")

    # Assert
    assert response.status_code == 200
    assert 'name="question"' in response.text
    assert 'for="pregunta"' in response.text and 'id="pregunta"' in response.text


def test_portada_aplica_el_tema_y_la_accesibilidad_basica() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/")

    # Assert
    assert response.status_code == 200
    assert 'lang="es"' in response.text
    assert "<title>Asistente Carvajal</title>" in response.text
    assert "Saltar al contenido" in response.text
    assert 'alt="Carvajal"' in response.text
    assert 'aria-live="polite"' in response.text and 'id="respuesta"' in response.text
    assert "corpus público de Carvajal" in response.text


def test_la_navegacion_enlaza_la_portada_y_las_pantallas() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/")

    # Assert
    assert 'href="/#preguntar"' in response.text and 'id="preguntar"' in response.text
    assert 'href="/corpus"' in response.text
    assert 'href="/chat"' in response.text
    assert 'href="#respuesta"' not in response.text


def test_el_logo_se_sirve_como_svg() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/static/logo.svg")

    # Assert
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert len(response.content) > 1000
    assert "<svg" in response.text


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


def test_error_interno_no_filtra_detalles() -> None:
    # Arrange: un ValueError interno (p. ej. dimensión del vector) no es Culpa
    # del cliente y su mensaje no debe llegar al HTML.
    client = make_client(
        ask=failing(ValueError("El vector debe tener 768 dimensiones; recibidas 1024.")),
        raise_server_exceptions=False,
    )

    # Act
    response = client.post("/ask", data={"question": "¿?"})

    # Assert
    assert response.status_code == 500
    assert "1024" not in response.text
    assert "dimensiones" not in response.text


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
    assert SERVICE_UNAVAILABLE in response.text
    assert str(error) not in response.text
    assert "Traceback" not in response.text


def test_fallo_inesperado_no_se_convierte_en_503() -> None:
    # Arrange
    client = make_client(
        ask=failing(RuntimeError("detalle interno")), raise_server_exceptions=False
    )

    # Act
    response = client.post("/ask", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 500
    assert SERVICE_UNAVAILABLE not in response.text


def test_portada_carga_htmx_local() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/")

    # Assert
    assert response.status_code == 200
    assert "/static/htmx.min.js" in response.text
    assert (main.APP_DIR / "static" / "htmx.min.js").is_file()


def test_la_salida_escapa_html() -> None:
    # Arrange
    peligro = "<script>alert(1)</script>"
    source = Source("x-1", "doc", "seccion", "core", 0.5, peligro)
    client = make_client(ask=lambda question: Answer(text=peligro, sources=(source,)))

    # Act
    response = client.post("/ask", data={"question": "¿?"})

    # Assert
    assert response.status_code == 200
    assert peligro not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_create_app_difiere_los_handlers_a_la_primera_peticion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    calls: list[int] = []

    def fake_handlers() -> Handlers:
        calls.append(1)
        return Handlers(ask=lambda question: ANSWER, health=lambda: HealthStatus(1, 2))

    monkeypatch.setattr(main, "_default_handlers", fake_handlers)
    app = create_app()
    assert calls == []

    # Act
    response = TestClient(app).get("/health")

    # Assert
    assert response.status_code == 200
    assert calls == [1]
    assert response.json() == {"status": "ok", "documents": 1, "chunks": 2}
