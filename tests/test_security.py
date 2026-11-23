"""Pruebas del endurecimiento: CSRF por doble cookie, cookies y cabeceras."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Request
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
    OPERATOR_COOKIE,
    SecurityMiddleware,
    build_csrf_secret,
    issue_operator_cookie,
    operator_authorized,
    politica_permisos,
    valid_operator_cookie,
    valid_token,
)
from carvajal.application.ask import Answer
from carvajal.application.quota import QuotaResult
from carvajal.infrastructure.settings import Settings

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
    app = create_app(Handlers(health=lambda: HealthStatus(0, 0)))
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


def test_las_cabeceras_de_seguridad_van_en_todas_las_respuestas() -> None:
    """Incluidas las de error: el endurecimiento no depende del camino feliz."""

    # Arrange
    client = _client()

    # Act: sin seguir el 303 de la portada, que terminaría en el documento del chat.
    portada = client.get("/", follow_redirects=False)
    rechazo = client.post("/chat", data={"question": "¿?"})

    # Assert
    for response in (portada, rechazo):
        for nombre, valor in CABECERAS.items():
            assert response.headers[nombre] == valor
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def _microfono(politica: str) -> str:
    """Extrae la directiva ``microphone`` de ``Permissions-Policy``."""

    for directiva in politica.split(","):
        nombre, _, valor = directiva.strip().partition("=")
        if nombre == "microphone":
            return valor
    raise AssertionError("la política no declara microphone")


def _politica_microfono(response: Any) -> str:
    return _microfono(response.headers["permissions-policy"])


def test_el_documento_del_chat_permite_su_propio_microfono() -> None:
    """Solo la página HTML del chat puede pedir micrófono (spec 040, T003)."""

    # Act
    response = _client().get("/chat")

    # Assert
    assert response.status_code == 200
    assert _politica_microfono(response) == "(self)"
    politica = response.headers["permissions-policy"]
    assert "geolocation=()" in politica
    assert "camera=()" in politica


@pytest.mark.parametrize(
    ("ruta", "metodo", "esperado"),
    [
        ("/chat", "GET", "(self)"),
        ("/chat", "HEAD", "(self)"),
        ("/chat", "POST", "()"),
        ("/chat/stream", "POST", "()"),
        ("/chat", "PUT", "()"),
        ("/chat/", "GET", "()"),
        ("/faq", "GET", "()"),
        ("/static/chat-voice.js", "GET", "()"),
    ],
)
def test_la_politica_de_microfono_depende_de_ruta_y_metodo(
    ruta: str, metodo: str, esperado: str
) -> None:
    """La excepción no se hereda por método ni por prefijo de ruta."""

    # Act / Assert
    assert _microfono(politica_permisos(ruta, metodo)) == esperado


def test_el_microfono_sigue_denegado_en_las_demas_rutas() -> None:
    """FAQ, portada, POST del chat, SSE y estáticos no heredan la excepción."""

    # Arrange
    client = _client()
    token = _token(client)

    # Act
    faq = client.get("/faq")
    estatico = client.get("/static/logo.svg")
    portada = client.get("/", follow_redirects=False)
    post_chat = client.post("/chat", data={"question": "¿Qué es Carvajal?"})
    sse_sin_token = client.post("/chat/stream", data={"question": "¿Qué es Carvajal?"})
    sse = client.post(
        "/chat/stream",
        data={"question": "¿Qué es Carvajal?", CSRF_FIELD: token},
    )

    # Assert: el SSE válido responde 200 y aun así no recibe el micrófono.
    assert sse.status_code == 200
    assert "text/event-stream" in sse.headers["content-type"]
    for response in (faq, estatico, portada, post_chat, sse_sin_token, sse):
        assert _politica_microfono(response) == "()"


def test_la_excepcion_del_microfono_no_cambia_csp_ni_frame_ancestors() -> None:
    """El permiso acotado no abre scripts, estilos, conexiones ni iframes."""

    # Act
    response = _client().get("/chat")

    # Assert
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert response.headers["x-frame-options"] == "DENY"


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
    app = create_app(Handlers(health=lambda: HealthStatus(0, 0)))
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
    app = create_app(Handlers(health=lambda: HealthStatus(0, 0)))
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
    app = create_app(Handlers(health=lambda: HealthStatus(0, 0)))
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
    def explode() -> HealthStatus:
        raise ValueError("detalle interno")

    app = create_app(Handlers(health=explode))
    client = TestClient(app, raise_server_exceptions=False)

    # Act
    response = client.get("/health")

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


def _request(headers: dict[str, str] | None = None) -> Request:
    """Petición mínima para probar las credenciales de operador."""

    raw = [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


def test_la_cookie_de_operador_caduca_y_se_invalida_al_cambiar_el_token() -> None:
    # Arrange
    settings = Settings(_env_file=None, operator_token="secreto")
    cookie = issue_operator_cookie(settings, now=1_000)

    # Act / Assert
    assert valid_operator_cookie(cookie, settings, now=1_001) is True
    assert valid_operator_cookie(cookie, settings, now=1_000 + 8 * 3600) is False
    assert valid_operator_cookie(cookie, Settings(_env_file=None, operator_token="otro")) is False
    assert "secreto" not in cookie
    with pytest.raises(ValueError):
        issue_operator_cookie(Settings(_env_file=None, operator_token=""))


def test_operator_authorized_acepta_formulario_bearer_basic_y_cookie() -> None:
    # Arrange
    settings = Settings(_env_file=None, operator_token="secreto")
    cookie = issue_operator_cookie(settings)

    # Act / Assert
    assert operator_authorized(_request(), "secreto", settings=settings) is True
    assert (
        operator_authorized(_request({"Authorization": "Bearer secreto"}), settings=settings)
        is True
    )
    assert (
        operator_authorized(
            _request({"Authorization": "Basic b3BlcmFkb3I6c2VjcmV0bw=="}),
            settings=settings,
        )
        is True
    )
    assert (
        operator_authorized(
            _request({"Authorization": "Basic Y3VhbHF1aWVyYTpzZWNyZXRv"}),
            settings=settings,
        )
        is False
    )
    assert (
        operator_authorized(_request({"Cookie": f"{OPERATOR_COOKIE}={cookie}"}), settings=settings)
        is True
    )
    assert operator_authorized(_request({"X-Operator-Token": "secreto"}), settings=settings) is True
    assert operator_authorized(_request(), "otro", settings=settings) is False
    assert (
        operator_authorized(_request({"Authorization": "Basic no-es-base64"}), settings=settings)
        is False
    )
    assert (
        operator_authorized(_request({"Authorization": "Bearer otro"}), settings=settings) is False
    )
    assert operator_authorized(_request(), "secreto", settings=Settings(_env_file=None)) is False
