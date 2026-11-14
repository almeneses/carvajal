"""Pruebas del endurecimiento: CSRF por doble cookie, cookies y cabeceras."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.chat.identity import COOKIE_NAME, SignedCookieIdentity
from apps.api.chat.streaming import ChatEvent
from apps.api.main import SERVICE_UNAVAILABLE, Handlers, HealthStatus, create_app
from apps.api.runtime import APP_DIR
from apps.api.security import (
    CABECERAS,
    CSRF_COOKIE,
    CSRF_FIELD,
    HSTS,
    SecurityMiddleware,
    build_csrf_secret,
    valid_token,
)
from carvajal.application.ask import Answer
from carvajal.application.quota import QuotaResult

RESPUESTA = Answer("ok", ())
IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")


class FakeChat:
    """Doble del servicio de chat: solo registra las llamadas."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def history(self, owner_key: str) -> list[Any]:
        return []

    def ask(self, owner_key: str, question: str) -> Answer:
        self.calls.append((owner_key, question))
        return RESPUESTA

    def stream(self, owner_key: str, question: str) -> Iterator[ChatEvent]:
        self.calls.append((owner_key, question))
        yield ChatEvent("done", {})


def _client(*, base_url: str = "http://testserver") -> TestClient:
    app = create_app(Handlers(ask=lambda question: RESPUESTA, health=lambda: HealthStatus(0, 0)))
    app.state.chat_identity = IDENTITY
    return TestClient(app, base_url=base_url)


def _token(client: TestClient) -> str:
    client.get("/chat")
    return client.cookies[CSRF_COOKIE]


def _parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    eventos: list[tuple[str, dict[str, object]]] = []
    for bloque in body.split("\n\n"):
        if not bloque.strip():
            continue
        nombre = "message"
        datos: list[str] = []
        for linea in bloque.splitlines():
            if linea.startswith("event:"):
                nombre = linea.removeprefix("event:").strip()
            elif linea.startswith("data:"):
                datos.append(linea.removeprefix("data:").lstrip())
        eventos.append((nombre, json.loads("\n".join(datos))))
    return eventos


def test_el_chat_incrusta_el_token_en_su_formulario() -> None:
    """La pantalla de chat recibe el token que emite el middleware."""

    # Act
    response = _client().get("/chat")

    # Assert
    token = response.cookies[CSRF_COOKIE]
    assert f'name="csrf_token" value="{token}"' in response.text


def test_la_portada_emite_el_token_aunque_redirija() -> None:
    """El 303 no pierde la cookie: pasar por / sigue habilitando los POST."""

    # Act
    response = _client().get("/", follow_redirects=False)

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == "/chat"
    assert response.cookies[CSRF_COOKIE]
    assert f"{CSRF_COOKIE}=" in response.headers["set-cookie"]


def test_el_post_sin_token_se_rechaza_antes_de_llamar_al_caso_de_uso() -> None:
    """Sin token CSRF no se ejecuta la pregunta."""

    # Arrange
    preguntas: list[str] = []

    def ask(question: str) -> Answer:
        preguntas.append(question)
        return RESPUESTA

    app = create_app(Handlers(ask=ask, health=lambda: HealthStatus(0, 0)))
    client = TestClient(app)

    # Act
    response = client.post("/ask", data={"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 403
    assert "sesión del formulario expiró" in response.text
    assert response.headers["x-carvajal-error"] == "1"
    assert preguntas == []


@pytest.mark.parametrize("token", ["otro", "ñ", "token-con-ñ"])
def test_el_post_con_un_token_ajeno_o_no_ascii_se_rechaza(token: str) -> None:
    """Un token que no coincide —o que no es ASCII— se rechaza sin lanzar 500."""

    # Arrange
    client = _client()
    client.get("/")

    # Act
    response = client.post(
        "/ask",
        data={"question": "¿Cuándo nació?", CSRF_FIELD: token},
    )

    # Assert
    assert response.status_code == 403


def test_el_post_con_el_token_de_la_cookie_pasa() -> None:
    """El token emitido por la portada habilita el formulario."""

    # Arrange
    client = _client()
    token = _token(client)

    # Act
    response = client.post("/ask", data={"question": "¿Cuándo nació?", CSRF_FIELD: token})

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
    assert con_tls.headers["strict-transport-security"] == HSTS


def test_las_cookies_son_httponly_y_samesite_lax() -> None:
    """Ni el JS puede leer las cookies ni viajan en peticiones de terceros."""

    # Act
    response = _client().get("/chat")
    cookies = {line.split("=")[0]: line.lower() for line in response.headers.get_list("set-cookie")}

    # Assert
    for nombre in (COOKIE_NAME, CSRF_COOKIE):
        cookie = cookies[nombre]
        assert "httponly" in cookie
        assert "samesite=lax" in cookie


def test_la_cookie_csrf_es_secure_sobre_tls() -> None:
    """La cookie del token no viaja en claro."""

    # Act
    response = _client(base_url="https://testserver").get("/chat")

    # Assert
    csrf = next(
        line for line in response.headers.get_list("set-cookie") if line.startswith(CSRF_COOKIE)
    )
    assert "Secure" in csrf


def test_no_hay_swagger_publico() -> None:
    """El esquema OpenAPI no se expone a visitantes anónimos."""

    client = _client()

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_una_cookie_csrf_fijada_por_tercero_se_reemplaza() -> None:
    """El token emitido va firmado: una cookie impuesta no sirve."""

    # Arrange
    client = _client()
    client.cookies.set(CSRF_COOKIE, "token-elegido-por-el-atacante")

    # Act
    response = client.get("/chat")

    # Assert
    token = response.cookies[CSRF_COOKIE]
    assert token != "token-elegido-por-el-atacante"
    assert valid_token(build_csrf_secret(), token)


def test_el_token_csrf_tiene_firma_y_no_es_el_valor_del_formulario_ajeno() -> None:
    """La firma impide que un token arbitrario pase la comparación."""

    # Arrange
    secret = build_csrf_secret()

    # Act / Assert
    assert not valid_token(secret, "sin-firma")
    assert not valid_token(secret, "nonce.firma-inventada")
    assert not valid_token(secret, "")


def test_no_store_sobrescribe_una_cache_publica_previa() -> None:
    """Una respuesta con token nunca conserva un Cache-Control público."""

    # Arrange
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def publica(request: Any) -> PlainTextResponse:
        return PlainTextResponse("ok", headers={"Cache-Control": "public, max-age=60"})

    app = Starlette(routes=[Route("/", publica)])
    app.add_middleware(SecurityMiddleware, secret=build_csrf_secret())
    client = TestClient(app)

    # Act
    response = client.get("/")

    # Assert
    assert response.headers["cache-control"] == "no-store"


def test_las_paginas_con_token_no_se_cachean() -> None:
    """Un proxy no debe servir un token CSRF viejo desde caché."""

    # Arrange
    client = _client()

    # Act
    portada = client.get("/")
    estatico = client.get("/static/logo.svg")

    # Assert
    assert portada.headers["cache-control"] == "no-store"
    assert "no-store" not in estatico.headers.get("cache-control", "")


def test_post_chat_exige_csrf_antes_de_la_cuota_y_del_proveedor() -> None:
    """Sin token no se cobra cupo ni se persiste el turno."""

    # Arrange
    chat = FakeChat()
    cupos: list[str] = []
    app = create_app(Handlers(ask=lambda question: RESPUESTA, health=lambda: HealthStatus(0, 0)))
    app.state.chat = chat
    app.state.chat_identity = IDENTITY
    app.state.quota = lambda owner: cupos.append(owner) or QuotaResult(allowed=True)
    client = TestClient(app)

    # Act
    response = client.post("/chat", data={"question": "¿Qué es Carvajal?"})

    # Assert
    assert response.status_code == 403
    assert response.headers["x-carvajal-error"] == "1"
    assert cupos == []
    assert chat.calls == []


def test_post_chat_stream_exige_csrf_antes_de_la_cuota_y_del_proveedor() -> None:
    """El SSE tampoco se abre sin token."""

    # Arrange
    chat = FakeChat()
    cupos: list[str] = []
    app = create_app(Handlers(ask=lambda question: RESPUESTA, health=lambda: HealthStatus(0, 0)))
    app.state.chat = chat
    app.state.chat_identity = IDENTITY
    app.state.quota = lambda owner: cupos.append(owner) or QuotaResult(allowed=True)
    client = TestClient(app)

    # Act
    response = client.post("/chat/stream", data={"question": "¿Qué es Carvajal?"})

    # Assert
    assert response.status_code == 403
    eventos = _parse_sse(response.text)
    assert eventos[-1][0] == "error"
    assert "sesión del formulario expiró" in str(eventos[-1][1]["message"])
    assert cupos == []
    assert chat.calls == []


def test_post_chat_con_token_valido_pasa_cuota_y_proveedor() -> None:
    """El token correcto deja seguir el flujo normal del chat."""

    # Arrange
    chat = FakeChat()
    cupos: list[str] = []
    app = create_app(Handlers(ask=lambda question: RESPUESTA, health=lambda: HealthStatus(0, 0)))
    app.state.chat = chat
    app.state.chat_identity = IDENTITY
    app.state.quota = lambda owner: cupos.append(owner) or QuotaResult(allowed=True)
    client = TestClient(app)
    token = _token(client)

    # Act
    response = client.post(
        "/chat",
        data={"question": "¿Qué es Carvajal?", CSRF_FIELD: token},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert len(cupos) == 1
    assert len(chat.calls) == 1


def test_el_500_lleva_request_id_cabeceras_y_parcial() -> None:
    """Una excepción no controlada no se escapa del endurecimiento."""

    # Arrange
    def explode(question: str) -> Answer:
        raise ValueError("detalle interno")

    app = create_app(Handlers(ask=explode, health=lambda: HealthStatus(0, 0)))
    client = TestClient(app, raise_server_exceptions=False)
    token = _token(client)

    # Act
    response = client.post("/ask", data={"question": "¿?", CSRF_FIELD: token})

    # Assert
    assert response.status_code == 500
    assert response.headers["x-request-id"]
    assert response.headers["x-carvajal-error"] == "1"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert SERVICE_UNAVAILABLE in response.text
    assert "detalle interno" not in response.text


def test_el_js_pinta_errores_propios_y_envia_el_token_del_stream() -> None:
    """La CSP prohíbe inline; las dos piezas viven en archivos estáticos."""

    # Arrange
    app_js = (APP_DIR / "static" / "app.js").read_text(encoding="utf-8")
    stream_js = (APP_DIR / "static" / "chat-stream.js").read_text(encoding="utf-8")

    # Assert
    assert "x-carvajal-error" in app_js
    assert "response.ok || propio" in app_js
    assert "csrf_token" in stream_js
