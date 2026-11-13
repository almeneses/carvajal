"""Pruebas de la identidad anónima: cookie firmada, sin autenticación."""

from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient

from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.session import COOKIE_NAME, owner_key
from carvajal.application.ask import Answer

RESPUESTA = Answer("respuesta", ())
HANDLERS = Handlers(ask=lambda question: RESPUESTA, health=lambda: HealthStatus(0, 0))


def _client() -> TestClient:
    return TestClient(create_app(HANDLERS))


def test_la_portada_crea_una_cookie_firmada() -> None:
    """La primera visita recibe una cookie opaca, firmada y no accesible a JS."""

    # Act
    response = _client().get("/")

    # Assert
    cookie = response.headers["set-cookie"].lower()
    assert COOKIE_NAME in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "." in response.cookies[COOKIE_NAME]


def test_la_cookie_no_se_reemite_si_la_firma_cuadra() -> None:
    """La segunda petición reutiliza el identificador y no vuelve a emitir la cookie."""

    # Arrange
    client = _client()

    # Act
    client.get("/")
    segunda = client.get("/")

    # Assert
    assert COOKIE_NAME not in segunda.headers.get("set-cookie", "")


def test_una_cookie_manipulada_se_renueva() -> None:
    """Si la firma no cuadra, se emite un identificador nuevo."""

    # Arrange
    client = _client()
    client.cookies.set(COOKIE_NAME, "inventada.firma-falsa")

    # Act
    response = client.get("/")

    # Assert
    assert COOKIE_NAME in response.cookies
    assert response.cookies[COOKIE_NAME].split(".")[0] != "inventada"


def test_los_estaticos_no_emiten_cookie() -> None:
    """Servir un estático no crea identidad."""

    # Act
    response = _client().get("/static/app.css")

    # Assert
    assert response.status_code == 200
    assert COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_la_dependencia_expone_el_mismo_identificador() -> None:
    """``owner_key`` devuelve el identificador estable que fijó el middleware."""

    # Arrange
    app = create_app(HANDLERS)

    @app.get("/quien")
    def quien(valor: Annotated[str, Depends(owner_key)]) -> dict[str, str]:
        return {"owner": valor}

    client = TestClient(app)

    # Act
    uno = client.get("/quien").json()["owner"]
    dos = client.get("/quien").json()["owner"]

    # Assert
    assert uno == dos
