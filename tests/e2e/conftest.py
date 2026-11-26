"""Fixtures del ensayo general (docs/ensayo.md).

Todo el paquete lleva ``e2e`` y ``ensayo``: ``make check`` y la CI lo excluyen. Cada bloque
se salta, con el motivo, si falta su configuración (SSH, token, secreto o Firefox); así
``make ensayo`` informa lo que pudo comprobar en vez de fallar por credenciales.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.e2e.clientes import Config, Mcp, Secreto, Ssh, Web

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(str(item.fspath)).is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.e2e)
            item.add_marker(pytest.mark.ensayo)


@pytest.fixture(autouse=True)
def _jev_del_servidor() -> Iterator[None]:
    # tests/conftest.py apaga Jev y bloquea TypeSafe en el proceso de pruebas; aquí no
    # aplica porque el ensayo habla por HTTP con una app ya desplegada.
    yield


@pytest.fixture(scope="session")
def config() -> Config:
    return Config.desde_entorno()


@pytest.fixture(scope="session")
def salud(config: Config) -> dict[str, Any]:
    """``/health`` una sola vez por sesión; sin app viva se salta todo lo que la usa."""

    try:
        httpx.get(f"{config.base_url}/live", timeout=10).raise_for_status()
        respuesta = httpx.get(f"{config.base_url}/health", timeout=30)
    except httpx.HTTPError as error:
        pytest.skip(f"la app no responde en {config.base_url}: {error}")
    return respuesta.json()


@pytest.fixture(scope="session")
def ssh_app(config: Config) -> Ssh:
    if not config.app_ssh:
        pytest.skip("sin ENSAYO_APP_SSH: no se revisa el servidor de la app")
    return Ssh(config.app_ssh, config.app_ssh_port)


@pytest.fixture(scope="session")
def ssh_openclaw(config: Config) -> Ssh:
    if not config.openclaw_ssh:
        pytest.skip("sin ENSAYO_OPENCLAW_SSH: no se revisa OpenClaw")
    return Ssh(config.openclaw_ssh)


@pytest.fixture(scope="session")
def mcp(config: Config) -> Iterator[Mcp]:
    """Cliente MCP como OpenClaw: en producción por un túnel SSH al loopback del servidor."""

    if not config.prod:
        token = config.mcp_token
        try:
            httpx.get(config.mcp_url.removesuffix("/mcp") + "/live", timeout=5).raise_for_status()
        except httpx.HTTPError:
            pytest.skip('sin MCP local en :8765 (make up PERFILES="full whatsapp", docs/ensayo.md)')
        yield Mcp(config.mcp_url, token)
        return
    if not config.app_ssh:
        pytest.skip("producción sin ENSAYO_APP_SSH: el MCP solo escucha en el loopback")
    ssh = Ssh(config.app_ssh, config.app_ssh_port)
    token = config.mcp_token or ssh.secreto(f"{config.app_dir}/.env", "MCP_TOKEN")
    if not token:
        pytest.skip("no se pudo leer MCP_TOKEN del servidor")
    with ssh.tunel(8765) as puerto:
        yield Mcp(f"http://127.0.0.1:{puerto}/mcp", token)


@pytest.fixture(scope="session")
def webhook_secreto(config: Config) -> Secreto:
    if config.webhook_secret:
        return config.webhook_secret
    if config.prod and config.app_ssh:
        secreto = Ssh(config.app_ssh, config.app_ssh_port).secreto(
            f"{config.app_dir}/.env", "WEBHOOK_SECRET"
        )
    else:
        archivo = Path(".env")
        lineas = archivo.read_text().splitlines() if archivo.exists() else []
        secreto = next(
            (
                linea.split("=", 1)[1].strip().strip("'\"")
                for linea in lineas
                if linea.startswith("WEBHOOK_SECRET=")
            ),
            "",
        )
    if not secreto:
        pytest.skip("sin WEBHOOK_SECRET: el webhook está apagado (404)")
    return Secreto(secreto)


@pytest.fixture
def web(config: Config, salud: dict[str, Any], request: pytest.FixtureRequest) -> Iterator[Web]:
    """Un visitante nuevo por prueba: memoria, cuota y hilos propios."""

    cliente = Web(config.base_url, Web.visitante_para(f"{config.run_id}:{request.node.nodeid}"))
    yield cliente
    cliente.cerrar()


@pytest.fixture
def conversacion(config: Config, request: pytest.FixtureRequest) -> str:
    """Alias de chat de WhatsApp propio de la prueba (no es un número: nadie lo recibe)."""

    nombre = request.node.name.removeprefix("test_").replace("_", "-")[:40]
    return f"e2e-{config.run_id}-{nombre}"


@pytest.fixture(scope="session")
def foto_ajiaco() -> bytes:
    return (FIXTURES / "ajiaco.jpg").read_bytes()
