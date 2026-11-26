"""Pruebas del bloque de monitoreo en ``/health`` (spec 038, T014)."""

import os
from collections.abc import Callable, Iterator
from typing import Any

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
        "MONITORING_TIMEOUT_MS",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setenv("MONITORING_ENABLED", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    # El .env del desarrollador puede tener el monitoreo a mano: estas pruebas
    # fijan el modo por defecto para no depender de la configuración local.
    monkeypatch.setenv("MONITORING_INCLUDE_CONTENT", "false")
    monkeypatch.setenv("MONITORING_SAMPLE_RATE", "0.2")
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

    from langsmith import utils

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    object.__getattribute__(utils.get_env_var, "cache_clear")()
    # El SDK cachea la lectura del entorno: comprobamos el efecto real.
    assert utils.tracing_is_enabled() is True

    create_app(_handlers())

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert utils.tracing_is_enabled() is False


def test_forzar_las_globales_es_idempotente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Llamar dos veces deja el mismo estado y no falla si no estaban definidas."""

    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    force_global_tracing_off()
    force_global_tracing_off()

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


@pytest.mark.parametrize(
    ("entorno", "esperado"),
    [
        (
            {"AGENT_MODE": "tools", "LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "sk-prueba"},
            {
                "mode_configured": "tools",
                "mode_expected": "tools",
                "tools_unavailable_reason": None,
            },
        ),
        (
            {"AGENT_MODE": "tools", "LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": ""},
            {
                "mode_configured": "tools",
                "mode_expected": "routes",
                "tools_unavailable_reason": "missing_deepseek_api_key",
            },
        ),
        (
            {"AGENT_MODE": "tools", "LLM_PROVIDER": "ollama", "DEEPSEEK_API_KEY": "sk-prueba"},
            {
                "mode_configured": "tools",
                "mode_expected": "routes",
                "tools_unavailable_reason": "llm_provider",
            },
        ),
        (
            {"AGENT_MODE": "routes", "LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "sk-prueba"},
            {
                "mode_configured": "routes",
                "mode_expected": "routes",
                "tools_unavailable_reason": None,
            },
        ),
    ],
)
def test_health_publica_el_modo_del_agente_sin_construir_el_runtime(
    monkeypatch: pytest.MonkeyPatch, entorno: dict[str, str], esperado: dict[str, object]
) -> None:
    """Spec 044: ``/health`` dice si el modo ``tools`` arrancará, sin la clave ni el modelo."""

    # Arrange
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    cliente = _app(monkeypatch, **entorno)

    # Act
    response = cliente.get("/health")

    # Assert: el estado no cambia; ``routes`` atiende igual.
    assert response.status_code == 200
    assert response.json()["agent"] == esperado
    assert "sk-prueba" not in response.text
    assert "deepseek-flash" not in response.text


# --- Bloque ``jev`` (spec 046, T046; FR-024) ---------------------------------------------------


def test_health_publica_jev_sin_runtime_ni_clave(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: el runtime no está construido.
    import apps.api.agent_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_runtime", None)

    # Act
    body = _app(monkeypatch).get("/health").json()

    # Assert
    assert body["jev"] == {
        "configured": False,
        "enabled": False,
        "available": False,
        "model": "jev-1.14.0",
        "router": "on",
        "claims": "enforce",
        "calls_today": 0,
        "last_error": None,
    }


def test_health_con_clave_y_encendido_sin_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.api.agent_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_runtime", None)

    body = (
        _app(monkeypatch, JEV_ENABLED="true", TYPESAFE_API_KEY="clave-falsa").get("/health").json()
    )

    assert body["jev"]["configured"] is True
    assert body["jev"]["available"] is True
    assert "clave-falsa" not in str(body)


def test_health_en_modo_routes_no_ofrece_jev(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: runtime construido en ``routes`` (interruptor de emergencia): sin juez.
    import apps.api.agent_runtime as runtime_module

    class Runtime:
        judge = None
        mode = "routes"

    monkeypatch.setattr(runtime_module, "_runtime", Runtime())

    # Act
    body = (
        _app(monkeypatch, JEV_ENABLED="true", TYPESAFE_API_KEY="clave-falsa").get("/health").json()
    )

    # Assert
    assert body["jev"]["available"] is False
    assert body["jev"]["router"] == "off"
    assert body["jev"]["claims"] == "off"


def _runtime_with_judge(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    import apps.api.agent_runtime as runtime_module
    from carvajal.adapters.judge.jev import JevJudge

    class Runtime:
        judge = JevJudge(client)

    monkeypatch.setattr(runtime_module, "_runtime", Runtime())


def test_health_con_circuito_abierto_o_tope_no_esta_disponible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    import httpx

    from carvajal.adapters.judge.jev import JevClient

    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    circuit = JevClient(api_key="x", transport=httpx.MockTransport(transport))
    circuit.open_until = circuit._clock() + 60
    circuit.last_error = "http_5xx"
    capped = JevClient(api_key="x", max_calls_per_day=0, transport=httpx.MockTransport(transport))

    # Act
    _runtime_with_judge(monkeypatch, circuit)
    open_body = _app(monkeypatch, JEV_ENABLED="true", TYPESAFE_API_KEY="x").get("/health").json()
    _runtime_with_judge(monkeypatch, capped)
    cap_body = _app(monkeypatch, JEV_ENABLED="true", TYPESAFE_API_KEY="x").get("/health").json()

    # Assert: sin peticiones a TypeSafe y sin cambiar el estado global.
    assert open_body["jev"]["available"] is False
    assert open_body["jev"]["last_error"] == "http_5xx"
    assert cap_body["jev"]["available"] is False
    assert open_body["status"] == "ok"
    assert requests == []


def test_health_degradado_tambien_publica_jev(monkeypatch: pytest.MonkeyPatch) -> None:
    def caida() -> HealthStatus:
        raise SQLAlchemyError("sin base")

    body = TestClient(create_app(_handlers(caida))).get("/health").json()

    assert body["jev"]["enabled"] is False


# --- Bloque ``claims``: contadores de la compuerta de afirmaciones (2026-11-26) ---------------


def _fresh_stats(monkeypatch: pytest.MonkeyPatch) -> Any:
    from carvajal.agent.gatekeeper import stats as stats_module

    stats = stats_module.ClaimsStats()
    monkeypatch.setattr(stats_module, "CLAIMS_STATS", stats)
    return stats


def test_health_publica_los_contadores_de_la_compuerta(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: el runtime no está construido; Jev encendido por configuración.
    import apps.api.agent_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_runtime", None)
    stats = _fresh_stats(monkeypatch)
    for _ in range(3):
        stats.record("supported", p=0.9)
    stats.record("unsupported", p=0.3, substituted=True)
    stats.record("unavailable", reason="timeout")
    stats.record("skipped", reason="atajo")

    # Act
    response = _app(
        monkeypatch,
        JEV_ENABLED="true",
        TYPESAFE_API_KEY="clave-falsa",
        AGENT_MODE="tools",
        LLM_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="clave-deepseek-falsa",
    ).get("/health")

    # Assert: sin cambiar el estado global y sin las claves.
    assert response.status_code == 200
    assert response.json()["agent"]["mode_expected"] == "tools"
    claims = response.json()["claims"]
    assert claims["mode"] == "enforce"
    assert claims["threshold"] == 0.5
    assert (claims["judged"], claims["substituted"]) == (4, 1)
    assert claims["substitution_rate"] == 0.25
    assert claims["unavailable"] == {"timeout": 1}
    assert claims["skipped"] == {"atajo": 1}
    assert claims["turns"] == 6
    assert "clave-falsa" not in response.text
    assert "clave-deepseek-falsa" not in response.text


@pytest.mark.parametrize(
    ("entorno", "motivo"),
    [
        pytest.param(
            {"LLM_PROVIDER": "ollama", "DEEPSEEK_API_KEY": "clave-deepseek-falsa"},
            "llm_provider",
            id="otro-proveedor",
        ),
        pytest.param(
            {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": ""},
            "missing_deepseek_api_key",
            id="sin-clave-deepseek",
        ),
    ],
)
def test_health_no_publica_enforce_si_el_agente_atendera_routes(
    monkeypatch: pytest.MonkeyPatch, entorno: dict[str, str], motivo: str
) -> None:
    # Arrange: ``AGENT_MODE=tools`` y Jev encendido, pero el modo ``tools`` no arrancará; el
    # bloque ``claims`` debe coincidir con el ``agent`` del mismo ``/health``.
    import apps.api.agent_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_runtime", None)
    _fresh_stats(monkeypatch)

    # Act
    body = (
        _app(
            monkeypatch,
            JEV_ENABLED="true",
            TYPESAFE_API_KEY="clave-falsa",
            AGENT_MODE="tools",
            **entorno,
        )
        .get("/health")
        .json()
    )

    # Assert
    assert body["agent"]["mode_expected"] == "routes"
    assert body["agent"]["tools_unavailable_reason"] == motivo
    assert body["claims"]["mode"] == "off"


def test_health_sin_jev_publica_la_compuerta_apagada(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    import apps.api.agent_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_runtime", None)
    _fresh_stats(monkeypatch)

    # Act
    claims = _app(monkeypatch).get("/health").json()["claims"]

    # Assert
    assert claims["mode"] == "off"
    assert claims["judged"] == 0
    assert claims["substitution_rate"] is None


def test_health_en_modo_routes_publica_la_compuerta_apagada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: runtime construido en ``routes``: sin juez, aunque Jev esté encendido.
    import apps.api.agent_runtime as runtime_module

    class Runtime:
        judge = None
        mode = "routes"

    monkeypatch.setattr(runtime_module, "_runtime", Runtime())
    _fresh_stats(monkeypatch)

    # Act
    claims = (
        _app(monkeypatch, JEV_ENABLED="true", TYPESAFE_API_KEY="clave-falsa")
        .get("/health")
        .json()["claims"]
    )

    # Assert
    assert claims["mode"] == "off"


def test_health_degradado_tambien_publica_la_compuerta(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    def caida() -> HealthStatus:
        raise SQLAlchemyError("sin base")

    stats = _fresh_stats(monkeypatch)
    stats.record("unsupported", p=0.1, substituted=True)

    # Act
    response = TestClient(create_app(_handlers(caida))).get("/health")

    # Assert
    assert response.status_code == 503
    assert response.json()["claims"]["substituted"] == 1
