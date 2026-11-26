"""Redirección a HTTPS de las navegaciones que llegan por la IP y el puerto publicados."""

import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from apps.api.chat.identity import COOKIE_NAME, VISITOR_COOKIE, SignedCookieIdentity
from apps.api.https_redirect import (
    TRANSFER_PARAM,
    HttpsRedirectMiddleware,
    public_origin,
)
from apps.api.main import Handlers, HealthStatus, create_app

PUBLIC = "https://144-217-80-136.sslip.io"
PUBLIC_HOST = "144-217-80-136.sslip.io"
IP_HOST = "144.217.80.136:8080"
HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")
VISITOR = "ab" * 32
OWNER = "cd" * 16


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    public_base_url: str = PUBLIC,
    enabled: str = "true",
    base_url: str = f"http://{IP_HOST}",
) -> TestClient:
    monkeypatch.setenv("PUBLIC_BASE_URL", public_base_url)
    monkeypatch.setenv("HTTPS_REDIRECT", enabled)
    app = create_app(Handlers(health=lambda: HealthStatus(0, 0)))
    app.state.chat_identity = IDENTITY
    return TestClient(app, base_url=base_url, follow_redirects=False)


def _transfer_token(location: str) -> str:
    return parse_qs(urlsplit(location).query)[TRANSFER_PARAM][0]


def test_redirige_la_navegacion_por_ip_al_dominio_publico(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    client = _client(monkeypatch)

    # Act
    response = client.get("/chat?hilo=a%20b&x=1", headers=HTML)

    # Assert
    assert response.status_code == 307
    assert response.headers["location"] == f"{PUBLIC}/chat?hilo=a%20b&x=1"
    assert "x-request-id" in response.headers


def test_redirige_head_y_conserva_la_ruta_de_la_base_publica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: la base pública puede traer barra final o un puerto https propio.
    client = _client(monkeypatch, public_base_url="https://carva.example:8443/")

    # Act
    response = client.head("/cotizador", headers=HTML)

    # Assert
    assert response.status_code == 307
    assert response.headers["location"] == "https://carva.example:8443/cotizador"


def test_redirige_el_dominio_publico_por_el_puerto_http_publicado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: el dominio sslip resuelve a la IP, así que también llega por :8080.
    client = _client(monkeypatch, base_url="http://144-217-80-136.sslip.io:8080")

    # Act
    response = client.get("/chat", headers=HTML)

    # Assert
    assert response.status_code == 307
    assert response.headers["location"] == f"{PUBLIC}/chat"


@pytest.mark.parametrize("host", ["144-217-80-136.sslip.io", "144-217-80-136.SSLIP.io:443"])
def test_no_redirige_el_host_publico_aunque_el_esquema_llegue_como_http(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    # Arrange: nginx manda el Host público; si X-Forwarded-Proto falla, no hay bucle.
    client = _client(monkeypatch, base_url=f"http://{host}")

    # Act
    response = client.get("/chat", headers=HTML)

    # Assert
    assert response.status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1:8080", "localhost:8000", "[::1]:8080"])
def test_no_redirige_el_loopback_del_tunel_ni_los_healthchecks(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    # Arrange: el ensayo entra por túnel SSH a 127.0.0.1:8080; localhost ya es seguro.
    client = _client(monkeypatch, base_url=f"http://{host}")

    # Act
    response = client.get("/chat", headers=HTML)

    # Assert
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/live",
        "/health",
        "/api",
        "/api/webhooks/whatsapp",
        "/mcp",
        "/static/app.css",
        "/cotizaciones/COT-1.pdf?t=1.abc",
        "/catalogo/imagenes/sku.jpg",
    ],
)
def test_no_redirige_sondas_api_webhooks_mcp_estaticos_ni_recursos(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    # Arrange
    client = _client(monkeypatch)

    # Act
    response = client.get(path, headers=HTML)

    # Assert
    assert response.status_code != 307


def test_no_redirige_peticiones_que_no_aceptan_html(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: curl, httpx y los clientes internos mandan ``*/*`` o JSON.
    client = _client(monkeypatch)

    # Act
    responses = [
        client.get("/chat"),
        client.get("/chat", headers={"Accept": "*/*"}),
        client.get("/chat", headers={"Accept": "application/json"}),
    ]

    # Assert
    assert all(response.status_code != 307 for response in responses)


def test_no_redirige_htmx_ni_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    client = _client(monkeypatch)

    # Act
    htmx = client.get("/chat", headers={**HTML, "HX-Request": "true"})
    fetch = client.get("/chat", headers={**HTML, "Sec-Fetch-Mode": "cors"})
    navigate = client.get("/chat", headers={**HTML, "Sec-Fetch-Mode": "navigate"})

    # Assert
    # (sin base, el parcial de htmx responde 503; lo que importa es que no hay 307)
    assert htmx.status_code != 307
    assert fetch.status_code != 307
    assert navigate.status_code == 307


def test_no_redirige_post(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    client = _client(monkeypatch)

    # Act
    response = client.post("/chat", headers=HTML, data={"pregunta": "hola"})

    # Assert
    assert response.status_code != 307


def test_no_redirige_si_el_esquema_ya_es_https(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    client = _client(monkeypatch, base_url=f"https://{IP_HOST}")

    # Act
    response = client.get("/chat", headers=HTML)

    # Assert
    assert response.status_code == 200


@pytest.mark.parametrize("public_base_url", ["", "http://144.217.80.136:8080", "https://", "x"])
def test_sin_base_publica_https_no_hace_nada(
    monkeypatch: pytest.MonkeyPatch, public_base_url: str
) -> None:
    # Arrange
    client = _client(monkeypatch, public_base_url=public_base_url)

    # Act
    response = client.get("/chat", headers=HTML)

    # Assert
    assert response.status_code == 200


def test_https_redirect_false_lo_apaga(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    client = _client(monkeypatch, enabled="false")

    # Act
    response = client.get("/chat", headers=HTML)

    # Assert
    assert response.status_code == 200


def test_public_origin_normaliza_host_y_puerto() -> None:
    # Arrange / Act
    origin = public_origin(" https://Carva.Example/ ")
    invalid_port = public_origin("https://carva.example:99999")

    # Assert
    assert origin is not None
    assert (origin.base, origin.hostname, origin.port) == (
        "https://Carva.Example",
        "carva.example",
        443,
    )
    assert invalid_port is None


def test_traslada_la_identidad_anonima_al_host_publico(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: quien ya chateaba por la IP tiene sus hilos colgados de estas cookies.
    ip = _client(monkeypatch)
    ip.cookies.set(VISITOR_COOKIE, VISITOR)
    ip.cookies.set(COOKIE_NAME, IDENTITY.issue(OWNER))
    public = _client(monkeypatch, base_url=f"https://{PUBLIC_HOST}")

    # Act
    first = ip.get("/chat?hilo=7", headers=HTML)
    token = _transfer_token(first.headers["location"])
    second = public.get(f"/chat?hilo=7&{TRANSFER_PARAM}={token}", headers=HTML)

    # Assert: el host público fija las mismas cookies y limpia la URL.
    assert first.status_code == 307
    assert first.headers["location"].startswith(f"{PUBLIC}/chat?hilo=7&{TRANSFER_PARAM}=")
    assert second.status_code == 307
    assert second.headers["location"] == f"{PUBLIC}/chat?hilo=7"
    assert public.cookies.get(VISITOR_COOKIE) == VISITOR
    assert IDENTITY.verify(public.cookies.get(COOKIE_NAME) or "") == OWNER
    set_cookies = second.headers.get_list("set-cookie")
    visitor_cookie = next(c for c in set_cookies if c.startswith(f"{VISITOR_COOKIE}="))
    session_cookie = next(c for c in set_cookies if c.startswith(f"{COOKIE_NAME}="))
    assert "secure" in visitor_cookie.lower()
    assert "httponly" not in visitor_cookie.lower()
    assert "httponly" in session_cookie.lower()


def test_sin_identidad_previa_no_agrega_traslado(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: una firma que no cuadra tampoco cuenta como identidad.
    client = _client(monkeypatch)
    client.cookies.set(COOKIE_NAME, f"{OWNER}.firma-falsa")

    # Act
    response = client.get("/chat", headers=HTML)

    # Assert
    assert response.headers["location"] == f"{PUBLIC}/chat"


def test_el_traslado_no_pisa_la_identidad_del_host_publico(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    ip = _client(monkeypatch)
    ip.cookies.set(VISITOR_COOKIE, VISITOR)
    token = _transfer_token(ip.get("/chat", headers=HTML).headers["location"])
    public = _client(monkeypatch, base_url=f"https://{PUBLIC_HOST}")
    existing = "ef" * 32
    public.cookies.set(VISITOR_COOKIE, existing)

    # Act
    response = public.get(f"/chat?{TRANSFER_PARAM}={token}", headers=HTML)

    # Assert
    assert response.status_code == 307
    assert response.headers["location"] == f"{PUBLIC}/chat"
    set_cookies = response.headers.get_list("set-cookie")
    assert not any(c.startswith(f"{VISITOR_COOKIE}=") for c in set_cookies)
    assert public.cookies.get(VISITOR_COOKIE) == existing


@pytest.mark.parametrize("kind", ["falsificado", "caducado"])
def test_un_traslado_falso_o_caducado_solo_limpia_la_url(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    # Arrange
    if kind == "falsificado":
        token = f"traslado-9999999999-{VISITOR}-.{'0' * 64}"
    else:
        token = IDENTITY.issue(f"traslado-1-{VISITOR}-")
    public = _client(monkeypatch, base_url=f"https://{PUBLIC_HOST}")

    # Act
    response = public.get(f"/chat?x=1&{TRANSFER_PARAM}={token}", headers=HTML)

    # Assert
    assert response.status_code == 307
    assert response.headers["location"] == f"{PUBLIC}/chat?x=1"
    assert VISITOR_COOKIE not in public.cookies


def test_el_traslado_solo_se_acepta_en_el_host_publico(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: por loopback el parámetro se ignora y la página responde normal.
    token = IDENTITY.issue(f"traslado-9999999999-{VISITOR}-")
    client = _client(monkeypatch, base_url="http://127.0.0.1:8080")

    # Act
    response = client.get(f"/chat?{TRANSFER_PARAM}={token}", headers=HTML)

    # Assert
    assert response.status_code == 200
    assert VISITOR_COOKIE not in client.cookies


def _run_raw(raw_path: bytes, path: str) -> list[Message]:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    origin = public_origin(PUBLIC)
    assert origin is not None
    middleware = HttpsRedirectMiddleware(app, origin=origin)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": raw_path,
        "query_string": b"",
        "headers": [(b"host", IP_HOST.encode()), (b"accept", b"text/html")],
    }
    asyncio.run(middleware(scope, receive, send))
    return sent


def test_no_arma_location_con_un_destino_en_forma_absoluta() -> None:
    # Arrange / Act: ``GET http://evil.com/x HTTP/1.1`` deja así ``path`` y ``raw_path``.
    sent = _run_raw(b"http://evil.com/x", "http://evil.com/x")

    # Assert: no se redirige a «…sslip.iohttp://evil.com/x»; pasa a la app.
    assert sent[0]["status"] == 200


def test_location_usa_la_ruta_cruda_con_su_codificacion() -> None:
    # Arrange / Act
    sent = _run_raw(b"/chat/a%2Fb", "/chat/a/b")

    # Assert
    headers = dict(sent[0]["headers"])
    assert sent[0]["status"] == 307
    assert headers[b"location"] == f"{PUBLIC}/chat/a%2Fb".encode()
