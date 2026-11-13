"""Pruebas del endurecimiento: CSRF por doble cookie, cookies y cabeceras."""

from fastapi.testclient import TestClient

from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.security import CABECERAS, CSRF_COOKIE
from apps.api.session import COOKIE_NAME
from carvajal.application.ask import Answer

RESPUESTA = Answer("ok", ())


def _client(*, base_url: str = "http://testserver") -> TestClient:
    return TestClient(
        create_app(
            Handlers(
                ask=lambda question: RESPUESTA,
                health=lambda: HealthStatus(0, 0),
            )
        ),
        base_url=base_url,
    )


def test_la_portada_emite_el_token_y_lo_incrusta_en_el_formulario() -> None:
    """El token de la cookie viaja en el campo oculto del formulario."""

    # Act
    response = _client().get("/")

    # Assert
    token = response.cookies[CSRF_COOKIE]
    assert f'name="csrf_token" value="{token}"' in response.text


def test_el_post_sin_token_se_rechaza_antes_de_llamar_al_caso_de_uso() -> None:
    """Sin token CSRF no se ejecuta la pregunta."""

    # Arrange
    preguntas: list[str] = []

    def ask(question: str) -> Answer:
        preguntas.append(question)
        return RESPUESTA

    client = TestClient(create_app(Handlers(ask=ask, health=lambda: HealthStatus(0, 0))))

    # Act
    response = client.post("/ask", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 403
    assert "sesión del formulario expiró" in response.text
    assert preguntas == []


def test_el_post_con_un_token_ajeno_se_rechaza() -> None:
    """Un token que no coincide con la cookie se rechaza."""

    # Arrange
    client = _client()
    client.get("/")

    # Act
    response = client.post("/ask", data={"question": "¿Cuándo nació?", "csrf_token": "otro"})

    # Assert
    assert response.status_code == 403


def test_el_post_con_el_token_de_la_cookie_pasa() -> None:
    """El token emitido por la portada habilita el formulario."""

    # Arrange
    client = _client()
    client.get("/")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post("/ask", data={"question": "¿Cuándo nació?", "csrf_token": token})

    # Assert
    assert response.status_code == 200
    assert "ok" in response.text


def test_las_cabeceras_de_seguridad_van_en_todas_las_respuestas() -> None:
    """Incluidas las de error: el endurecimiento no depende del camino feliz."""

    # Arrange
    client = _client()

    # Act
    portada = client.get("/")
    rechazo = client.post("/ask", data={"question": "¿?"})

    # Assert
    for response in (portada, rechazo):
        for nombre, valor in CABECERAS.items():
            assert response.headers[nombre] == valor
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_hsts_solo_sobre_https() -> None:
    """HSTS se anuncia cuando la petición llega por TLS, no antes."""

    # Arrange
    http = _client()
    https = _client(base_url="https://testserver")

    # Act
    sin_tls = http.get("/")
    con_tls = https.get("/")

    # Assert
    assert "strict-transport-security" not in sin_tls.headers
    assert "max-age=31536000" in con_tls.headers["strict-transport-security"]


def test_las_cookies_son_httponly_y_samesite_lax() -> None:
    """Ni el JS puede leer las cookies ni viajan en peticiones de terceros."""

    # Act
    response = _client().get("/")
    cookies = {line.split("=")[0]: line.lower() for line in response.headers.get_list("set-cookie")}

    # Assert
    for nombre in (COOKIE_NAME, CSRF_COOKIE):
        cookie = cookies[nombre]
        assert "httponly" in cookie
        assert "samesite=lax" in cookie


def test_no_hay_swagger_publico() -> None:
    """El esquema OpenAPI no se expone a visitantes anónimos."""

    client = _client()

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
