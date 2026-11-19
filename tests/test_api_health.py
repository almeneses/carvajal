"""Pruebas del bloque de monitoreo en ``/health`` (spec 038, T014)."""

import os
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api.main import Handlers, HealthStatus, create_app, force_global_tracing_off


def _handlers(salud: Callable[[], HealthStatus] | None = None) -> Handlers:
    return Handlers(health=salud or (lambda: HealthStatus(1, 2)))


def _app(monkeypatch: pytest.MonkeyPatch, **entorno: str) -> TestClient:
    for nombre, valor in entorno.items():
        monkeypatch.setenv(nombre, valor)
    app = create_app(_handlers())
    return TestClient(app)


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Cada prueba parte de un monitoreo apagado y sin llave."""

    for nombre in (
        "MONITORING_ENABLED",
        "LANGSMITH_API_KEY",
        "LANGSMITH_ENDPOINT",
        "MONITORING_SAMPLE_RATE",
        "MONITORING_INCLUDE_CONTENT",
    ):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setenv("MONITORING_ENABLED", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    yield


def test_health_sin_monitoreo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apagado por defecto: el bloque existe y declara lo que hay (FR-011)."""

    cliente = _app(monkeypatch)

    salud = cliente.get("/health").json()["monitoring"]

    assert salud["enabled"] is False
    assert salud["content"] == "metadata"
    assert salud["sample_rate"] == pytest.approx(0.2)


def test_health_con_monitoreo_activo_no_expone_credenciales(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Activo muestra enabled, muestreo y modo; nunca la llave ni el endpoint."""

    cliente = _app(
        monkeypatch,
        MONITORING_ENABLED="true",
        LANGSMITH_API_KEY="lsv2_pt_secreta",
        LANGSMITH_ENDPOINT="https://langsmith.interno",
        MONITORING_SAMPLE_RATE="0.5",
        MONITORING_INCLUDE_CONTENT="true",
    )

    response = cliente.get("/health")

    assert response.status_code == 200
    assert response.json()["monitoring"] == {
        "enabled": True,
        "sample_rate": 0.5,
        "content": "full",
    }
    texto = response.text
    assert "lsv2_pt_secreta" not in texto
    assert "langsmith.interno" not in texto


def test_health_sin_llave_no_anuncia_monitoreo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Encendido sin credencial es un error de despliegue, no un monitoreo activo."""

    cliente = _app(monkeypatch, MONITORING_ENABLED="true", LANGSMITH_API_KEY="")

    assert cliente.get("/health").json()["monitoring"]["enabled"] is False


def test_health_degradado_tambien_publica_el_monitoreo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un 503 por base caída sigue informando el estado del monitoreo."""

    def caida() -> HealthStatus:
        raise SQLAlchemyError("sin base")

    app = create_app(_handlers(caida))

    response = TestClient(app).get("/health")

    assert response.status_code == 503
    assert response.json()["monitoring"] == {
        "enabled": False,
        "sample_rate": pytest.approx(0.2),
        "content": "metadata",
    }


def test_el_arranque_fuerza_las_globales_a_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una variable heredada no enciende el tracing global del proceso (FR-001)."""

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "1")

    create_app(_handlers())

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_forzar_las_globales_es_idempotente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Llamar dos veces deja el mismo estado y no falla si no estaban definidas."""

    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    force_global_tracing_off()
    force_global_tracing_off()

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
