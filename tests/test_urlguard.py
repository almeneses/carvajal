"""Pruebas del guard SSRF de la captura web."""

from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests

from apps.api.urlguard import SafeWebClient, UrlRejected, validate_capture_url
from carvajal.fetching import RobotsDenied


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://8.8.8.8/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://",
        "http://usuario:clave@8.8.8.8/",
    ],
)
def test_rechaza_esquemas_y_formas_invalidas(url: str) -> None:
    # Act / Assert
    with pytest.raises(UrlRejected):
        validate_capture_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.2:8000/",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://192.168.1.10/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
    ],
)
def test_rechaza_direcciones_no_publicas(url: str) -> None:
    # Act / Assert
    with pytest.raises(UrlRejected):
        validate_capture_url(url)


def test_acepta_una_ip_publica_sin_depender_de_dns() -> None:
    # Act
    resultado = validate_capture_url("http://8.8.8.8/")

    # Assert
    assert resultado == "http://8.8.8.8/"


def test_rechaza_puertos_fuera_de_80_y_443() -> None:
    # Act / Assert
    with pytest.raises(UrlRejected):
        validate_capture_url("http://8.8.8.8:8080/")


def test_allowlist_rechaza_un_dominio_ajeno_sin_resolver_dns() -> None:
    # Act / Assert
    with pytest.raises(UrlRejected):
        validate_capture_url("http://example.org/", allowed_domains=("example.com",))


def test_allowlist_acepta_subdominios() -> None:
    # Act
    with patch("apps.api.urlguard._resolve", return_value=["8.8.8.8"]):
        resultado = validate_capture_url(
            "http://sub.example.com/",
            allowed_domains=("example.com",),
            allow_private=True,
        )

    # Assert
    assert resultado == "http://sub.example.com/"


def test_allow_private_solo_se_usa_en_desarrollo() -> None:
    # Act
    resultado = validate_capture_url("http://127.0.0.1:8080/", allow_private=True)

    # Assert
    assert resultado == "http://127.0.0.1:8080/"


def test_safe_web_client_valida_antes_de_pedir_robots() -> None:
    # Arrange
    client = SafeWebClient(respect_robots=False)

    # Act / Assert
    with pytest.raises(RobotsDenied):
        client.can_fetch("http://127.0.0.1/")
    with pytest.raises(UrlRejected):
        client.fetch("http://127.0.0.1/")


def _redirect(location: str):
    """Respuesta falsa de ``requests`` que redirige a ``location``."""

    response = Mock()
    response.is_redirect = True
    response.headers = {"location": location}
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


def test_una_redireccion_a_una_ip_privada_se_rechaza_antes_de_pedirla() -> None:
    # Arrange
    client = SafeWebClient(respect_robots=False)

    # Act / Assert
    with (
        patch.object(requests.Session, "get", return_value=_redirect("http://169.254.169.254/")),
        pytest.raises(UrlRejected),
    ):
        client.fetch("http://8.8.8.8/")


def test_robots_no_sigue_una_redireccion_privada() -> None:
    # Arrange
    client = SafeWebClient(respect_robots=True)

    # Act
    with patch.object(
        requests.Session,
        "get",
        return_value=_redirect("http://169.254.169.254/"),
    ) as llamadas:
        permitido = client.can_fetch("http://8.8.8.8/")

    # Assert
    assert permitido is True
    assert llamadas.call_count == 1
    assert "169.254.169.254" not in str(llamadas.call_args)


def test_allowlist_normaliza_esquema_y_punto_inicial() -> None:
    # Act
    resultado = validate_capture_url(
        "http://8.8.8.8/",
        allowed_domains=("http://.8.8.8.8/",),
    )

    # Assert
    assert resultado == "http://8.8.8.8/"


def test_can_fetch_usa_el_loader_seguro_de_robots() -> None:
    # Arrange
    from urllib.robotparser import RobotFileParser

    client = SafeWebClient(respect_robots=True)
    parser = RobotFileParser()
    parser.parse([])

    # Act
    with patch.object(client, "_load_robots", return_value=parser) as cargar:
        permitido = client.can_fetch("http://8.8.8.8/")

    # Assert
    assert permitido is True
    cargar.assert_called_once()


def test_respect_robots_false_no_consulta_robots() -> None:
    # Arrange
    client = SafeWebClient(respect_robots=False)

    # Act
    with patch.object(client.session, "get") as llamadas:
        permitido = client.can_fetch("http://8.8.8.8/")

    # Assert
    assert permitido is True
    llamadas.assert_not_called()


def test_safe_web_client_conecta_a_la_ip_fijada_y_conserva_el_host() -> None:
    # Arrange
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    hosts: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hosts.append(self.headers.get("Host", ""))
            body = b"<html><body>ok</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    client = SafeWebClient(allow_private=True, respect_robots=False, delay=0)

    try:
        # ``public.example`` no resuelve: si el cliente no usara la IP fijada,
        # la petición fallaría al intentar resolverlo.
        with patch("apps.api.urlguard._resolve", return_value=["127.0.0.1"]):
            html = client.fetch(f"http://public.example:{port}/")
    finally:
        server.shutdown()
        server.server_close()

    # Assert
    assert "ok" in html
    assert hosts == [f"public.example:{port}"]
