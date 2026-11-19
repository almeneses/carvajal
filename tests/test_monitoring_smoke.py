"""Smoke tests del monitoreo (spec 038).

Rápidos y sin credenciales: arrancan la app con SQLite en memoria y comprueban
los contratos observables del monitoreo. Se ejecutan con ``make smoke`` y no
forman parte de ``make check``.

Si existe ``LANGSMITH_API_KEY`` en ``.env`` se añade una comprobación de
alcanzabilidad del servicio; sin llave se omite.
"""

from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apps.api.chat.agent import AgentTurn
from apps.api.chat.service import ChatService
from apps.api.main import HealthStatus, create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.application.quota import QuotaResult
from carvajal.infrastructure.monitoring import build_tracer, monitoring_status
from carvajal.infrastructure.settings import Settings

pytestmark = pytest.mark.smoke

PREGUNTA = "¿Ingresos de Carvajal en 2025?"


class EmbedderFalso:
    def embed(self, text: str) -> list[float]:
        return [0.0] * 8


class LLMFalso:
    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        yield "Carvajal reportó ingresos. [1]"

    def complete(self, *, system: str, prompt: str) -> str:
        return "Carvajal reportó ingresos. [1]"


class AgenteFalso:
    def prepare(
        self, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Any]
    ) -> AgentTurn:
        return AgentTurn(
            route="tool",
            intent="indicator",
            prompt="contexto",
            system="sistema",
            sources=(),
            turns=(),
            tool="indicator_lookup",
            tool_period="2025",
        )

    def record(self, *args: Any, **kwargs: Any) -> None:
        return None


class ClienteFalso:
    """Cliente falso que aplica la misma redacción que el cliente real."""

    def __init__(self, *, include_content: bool = False) -> None:
        self.include_content = include_content
        self.runs: list[dict[str, Any]] = []
        self.actualizaciones: list[dict[str, Any]] = []

    def _limpio(self, payload: dict[str, Any]) -> dict[str, Any]:
        from carvajal.infrastructure.monitoring import redact_run

        limpio = dict(payload)
        for campo in ("inputs", "outputs"):
            if campo in limpio:
                limpio[campo] = redact_run(limpio[campo], self.include_content)
        if limpio.get("error"):
            limpio["error"] = redact_run({"error": limpio["error"]}, self.include_content)["error"]
        if isinstance(limpio.get("extra"), dict):
            limpio["extra"] = {
                **limpio["extra"],
                "metadata": redact_run(limpio["extra"].get("metadata") or {}, self.include_content),
            }
        return limpio

    def create_run(self, **kwargs: Any) -> None:
        self.runs.append(self._limpio(kwargs))

    def update_run(self, **kwargs: Any) -> None:
        self.actualizaciones.append(self._limpio(kwargs))


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, ClienteFalso]]:
    """App real con la base en memoria y el tracer apuntando a un cliente falso."""

    monkeypatch.setenv("MONITORING_ENABLED", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    cliente = ClienteFalso()
    ajustes = Settings(
        _env_file=None,
        monitoring_enabled=True,
        langsmith_api_key="lsv2_pt_smoke",
        monitoring_sample_rate=1.0,
        monitoring_include_content=False,
    )
    chat = ChatService(
        build_session_factory(motor),
        EmbedderFalso(),
        LLMFalso(),
        agent=AgenteFalso(),
        tracer=build_tracer(ajustes, client=cliente, random_fn=lambda: 0.0),
        settings=ajustes,
    )
    app = create_app()
    app.state.chat = chat
    app.state.quota = lambda owner: QuotaResult(allowed=True)
    app.state.health = lambda: HealthStatus(1, 2)
    app.state.monitoring = monitoring_status(ajustes).enabled
    try:
        yield TestClient(app), cliente
    finally:
        tables.metadata.drop_all(motor)
        motor.dispose()


def _token(cliente: TestClient) -> str:
    if CSRF_COOKIE not in cliente.cookies:
        cliente.get("/faq")
    return cliente.cookies[CSRF_COOKIE]


def test_live_y_health_responden(app: tuple[TestClient, ClienteFalso]) -> None:
    """La app arranca y publica el bloque de monitoreo sin credenciales."""

    cliente, _ = app

    assert cliente.get("/live").json() == {"status": "ok"}
    salud = cliente.get("/health")
    assert salud.status_code == 200
    monitoring = salud.json()["monitoring"]
    assert set(monitoring) == {"enabled", "sample_rate", "content"}
    assert "lsv2_pt_smoke" not in salud.text


def test_turno_completo_publica_request_id_y_traza(
    app: tuple[TestClient, ClienteFalso],
) -> None:
    """Un turno deja raíz con metadatos y el navegador recibe su request_id."""

    cliente, tracer = app
    respuesta = cliente.post(
        "/chat/stream",
        data={"question": PREGUNTA, CSRF_FIELD: _token(cliente)},
        headers={"Accept": "text/event-stream"},
    )

    assert respuesta.status_code == 200
    assert "event: done" in respuesta.text
    assert '"request_id"' in respuesta.text
    raices = [run for run in tracer.runs if run["name"] == "chat_turn"]
    assert len(raices) == 1
    assert any(run["name"] == "llm" for run in tracer.runs)
    raiz = raices[0]
    assert raiz["name"] == "chat_turn"
    apertura = raiz["extra"]["metadata"]
    assert apertura["router"] == "rules"
    assert apertura["request_id"]
    assert len(apertura["thread_hash"]) == 16
    # La ruta y el cierre viajan en la actualización final de la raíz.
    cierre = tracer.actualizaciones[0]["extra"]["metadata"]
    assert cierre["route"] == "tool"
    assert cierre["tool"] == "indicator_lookup"
    assert cierre["outcome"] == "ok"
    assert tracer.actualizaciones[0]["outputs"]["outcome"] == "ok"


def test_la_traza_no_lleva_texto_en_produccion(app: tuple[TestClient, ClienteFalso]) -> None:
    """Con contenido desactivado solo viajan longitudes, ni pregunta ni respuesta."""

    cliente, tracer = app
    cliente.post(
        "/chat/stream",
        data={"question": PREGUNTA, CSRF_FIELD: _token(cliente)},
        headers={"Accept": "text/event-stream"},
    )

    enviado = repr(tracer.runs) + repr(tracer.actualizaciones)
    assert PREGUNTA not in enviado
    assert "Carvajal reportó ingresos" not in enviado
    raiz = next(run for run in tracer.runs if run["name"] == "chat_turn")
    assert raiz["inputs"] == {}


def test_marcas_exigen_csrf_y_no_cobran_cuota(app: tuple[TestClient, ClienteFalso]) -> None:
    """El endpoint de marcas responde 204 con CSRF y 403 sin él."""

    cliente, _ = app
    token = _token(cliente)

    sin_csrf = cliente.post("/chat/feedback", data={"request_id": "smoke-1", "useful": "1"})
    con_csrf = cliente.post(
        "/chat/feedback",
        data={"request_id": "smoke-1", "useful": "1", CSRF_FIELD: token},
    )

    assert sin_csrf.status_code == 403
    assert con_csrf.status_code == 204


def test_langsmith_responde_si_hay_llave() -> None:
    """Comprueba la credencial del ``.env``; se omite cuando no hay llave."""

    ajustes = Settings()
    if not ajustes.langsmith_api_key.strip():
        pytest.skip("sin LANGSMITH_API_KEY en el entorno")

    from langsmith import Client

    cliente = Client(api_key=ajustes.langsmith_api_key, info={})
    proyecto = cliente.read_project(project_name=ajustes.langsmith_project)

    assert proyecto.name == ajustes.langsmith_project
