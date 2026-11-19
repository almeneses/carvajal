"""End-to-end del monitoreo con servicios reales y LangSmith (spec 038).

Ejercita la app de verdad (PostgreSQL, Qdrant, proveedor de LLM y LangSmith) y,
además de comprobar los contratos, **registra la corrida como experimento** en
LangSmith para poder comparar entre fechas: una fila por ruta con si hubo traza,
si quedó libre de PII y cuántos tokens costó, más los agregados (turnos con
traza, PII, tokens y latencia del servicio caído).

Se ejecuta con ``make e2e`` y queda fuera de ``make check``. Se omite si falta
``LANGSMITH_API_KEY`` o si la base de datos no responde.

Variables relevantes del ``.env``::

    LANGSMITH_API_KEY=lsv2_pt_…      # obligatoria para ejecutar
    LANGSMITH_PROJECT=carvajal
    LLM_PROVIDER=deepseek
    DEEPSEEK_API_KEY=…               # para las rutas tool y commercial

La ruta ``rag`` necesita Ollama con ``nomic-embed-text``; si no está, el caso se
registra como «sin respuesta» y el resto sigue.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from carvajal.infrastructure.monitoring import (
    build_tracer,
    commit_sha,
    register_experiment,
)
from carvajal.infrastructure.settings import Settings

pytestmark = pytest.mark.e2e

PREGUNTAS: dict[str, str] = {
    "direct": "¿Cuántos empleados tiene Carvajal y qué fuente lo reporta?",
    "rag": "¿Qué es Carvajal y a qué sectores se dedica?",
    "tool": "¿Cuáles fueron los ingresos consolidados de Carvajal en 2024?",
    "commercial": "necesito vasos para café caliente de 12 oz",
}
PII = ("Ana Pérez", "300 123 4567")
# Dataset propio del e2e: sus ejemplos son estas cuatro preguntas.
DATASET = "carvajal-monitoreo-e2e"


@pytest.fixture(scope="module")
def ajustes() -> Settings:
    """Configuración real; el módulo se omite si no hay llave de LangSmith."""

    base = Settings()
    if not base.langsmith_api_key.strip():
        pytest.skip("sin LANGSMITH_API_KEY: el e2e necesita LangSmith")
    return base


@pytest.fixture(scope="module")
def cliente_langsmith(ajustes: Settings) -> Any:
    """Cliente de LangSmith apuntando al proyecto configurado."""

    from langsmith import Client

    return Client(
        api_key=ajustes.langsmith_api_key.strip(),
        api_url=ajustes.langsmith_endpoint or None,
        info={},
    )


def _app(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App con monitoreo al 100 % y contenido desactivado (modo producción)."""

    monkeypatch.setenv("MONITORING_ENABLED", "true")
    monkeypatch.setenv("MONITORING_SAMPLE_RATE", "1.0")
    monkeypatch.setenv("MONITORING_INCLUDE_CONTENT", "false")
    return TestClient(create_app())


def _turno(cliente: TestClient, pregunta: str) -> dict[str, Any]:
    """Envía un turno y devuelve su ``done``/``error`` y el tiempo al primer token."""

    if CSRF_COOKIE not in cliente.cookies:
        cliente.get("/faq")
    token = cliente.cookies[CSRF_COOKIE]
    inicio = time.perf_counter()
    primero: float | None = None
    eventos: list[tuple[str, dict[str, Any]]] = []
    with cliente.stream(
        "POST",
        "/chat/stream",
        data={"question": pregunta, CSRF_FIELD: token},
        headers={"Accept": "text/event-stream"},
    ) as respuesta:
        buffer = ""
        for trozo in respuesta.iter_text():
            if primero is None:
                primero = time.perf_counter() - inicio
            buffer += trozo
            while "\n\n" in buffer:
                bloque, buffer = buffer.split("\n\n", 1)
                nombre = "message"
                datos: list[str] = []
                for linea in bloque.splitlines():
                    if linea.startswith("event:"):
                        nombre = linea.removeprefix("event:").strip()
                    elif linea.startswith("data:"):
                        datos.append(linea.removeprefix("data:").strip())
                if datos:
                    eventos.append((nombre, json.loads("\n".join(datos))))
    salida: dict[str, Any] = {
        "ttft_ms": round((primero or 0) * 1000, 1),
        "eventos": [nombre for nombre, _ in eventos],
    }
    for nombre, datos_evento in eventos:
        if nombre in {"done", "error"}:
            salida[nombre] = datos_evento
    return salida


def _traza(
    cliente: Any, request_id: str, proyecto: str, *, espera_s: float = 20.0
) -> tuple[Any, list[Any]]:
    """Raíz ``chat_turn`` y su traza; reintenta porque la ingesta es asíncrona."""

    limite = time.monotonic() + espera_s
    while time.monotonic() < limite:
        for run in cliente.list_runs(
            project_name=proyecto, filter='eq(name, "chat_turn")', limit=100
        ):
            metadatos = (run.extra or {}).get("metadata") or {}
            # ``route`` llega en el parche de cierre: esperar a que esté evita
            # leer la raíz recién creada e incompleta.
            if metadatos.get("request_id") == request_id and metadatos.get("route"):
                runs = list(
                    cliente.list_runs(
                        project_name=proyecto,
                        filter=f'eq(trace_id, "{run.trace_id}")',
                        limit=50,
                    )
                )
                return run, runs
        time.sleep(1.0)
    return None, []


def _sin_pii(runs: Sequence[Any]) -> bool:
    """True si ningún run de la traza lleva nombre, teléfono, owner_key ni hilo."""

    volcado = json.dumps(
        [
            {
                "inputs": run.inputs,
                "outputs": run.outputs,
                "extra": run.extra,
                "error": run.error,
            }
            for run in runs
        ],
        ensure_ascii=False,
        default=str,
    )
    return not any(marca in volcado for marca in (*PII, "owner_key", "thread_id"))


def _tokens(runs: Sequence[Any]) -> int:
    """Tokens facturados por el proveedor (runs del modelo, no el marcador)."""

    return sum(run.total_tokens or 0 for run in runs if run.run_type == "llm" and run.name != "llm")


def test_cuatro_rutas_registran_traza_y_metricas(
    monkeypatch: pytest.MonkeyPatch, ajustes: Settings, cliente_langsmith: Any
) -> None:
    """Cada ruta deja traza en LangSmith y la corrida queda como experimento (SC-001/2)."""

    cliente = _app(monkeypatch)
    proyecto = ajustes.langsmith_project
    filas: list[dict[str, Any]] = []
    resultados: dict[str, dict[str, Any]] = {}

    for ruta, pregunta in PREGUNTAS.items():
        salida = _turno(cliente, pregunta)
        request_id = (salida.get("done") or {}).get("request_id")
        assert request_id, f"la ruta {ruta} no publicó request_id: {salida}"
        raiz, runs = _traza(cliente_langsmith, request_id, proyecto)
        assert raiz is not None, f"la ruta {ruta} no dejó traza (request_id={request_id})"
        resultados[ruta] = {
            "request_id": request_id,
            "ruta_observada": (raiz.extra or {}).get("metadata", {}).get("route"),
            "hijos": len(runs) - 1,
            "tokens": _tokens(runs),
            "pii_libre": _sin_pii(runs),
            "ttft_ms": salida["ttft_ms"],
        }
        filas.append(
            {
                "id": f"monitoreo-{ruta}",
                "inputs": {"question": pregunta},
                "expected": {"route": ruta},
                "outputs": {
                    "route": resultados[ruta]["ruta_observada"],
                    "trace": True,
                    "runs": resultados[ruta]["hijos"],
                    "tokens": resultados[ruta]["tokens"],
                    "pii_free": resultados[ruta]["pii_libre"],
                },
                "latency_ms": resultados[ruta]["ttft_ms"],
            }
        )

    # SC-001: las cuatro rutas tienen traza; SC-002: ninguna lleva PII.
    assert all(datos["ruta_observada"] for datos in resultados.values()), resultados
    assert all(datos["pii_libre"] for datos in resultados.values()), resultados

    metricas = {
        "rutas_con_traza": sum(1 for datos in resultados.values() if datos["ruta_observada"]),
        "rutas_totales": len(PREGUNTAS),
        "trazas_con_pii": sum(1 for datos in resultados.values() if not datos["pii_libre"]),
        "tokens_totales": sum(datos["tokens"] for datos in resultados.values()),
        "ttft_p95_ms": round(
            statistics.quantiles([datos["ttft_ms"] for datos in resultados.values()], n=20)[-1],
            1,
        ),
    }
    resultado = register_experiment(
        cliente_langsmith,
        dataset=DATASET,
        experiment=f"monitoreo-{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        rows=filas,
        metrics=metricas,
        metadata={
            "commit": commit_sha() or "desconocido",
            "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "content": "metadata",
            "sample_rate": 1.0,
            "project": proyecto,
        },
        description="E2E del monitoreo: una fila por ruta con traza, tokens y PII.",
    )
    assert resultado.registered, resultado.detail


def test_servicio_caido_no_retrasa_el_primer_fragmento() -> None:
    """SC-003: con el endpoint inalcanzable, el p95 no empeora más de 100 ms.

    El SDK escribe en stderr un aviso de ingesta por cada envío fallido: es el
    comportamiento esperado y forma parte de lo que la prueba demuestra.
    """

    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from apps.api.chat.agent import AgentTurn
    from apps.api.chat.service import ChatService
    from carvajal.adapters.storage import tables
    from carvajal.adapters.storage.database import build_session_factory
    from carvajal.application.ask import Source

    class Embedder:
        def embed(self, text: str) -> list[float]:
            return [0.0] * 8

    class LLM:
        def stream(self, *, system: str, prompt: str) -> Iterator[str]:
            yield "Carvajal fue fundada en 1904."

        def complete(self, *, system: str, prompt: str) -> str:
            return "Carvajal fue fundada en 1904."

    class Agente:
        def prepare(
            self, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Any]
        ) -> AgentTurn:
            return AgentTurn(
                route="rag",
                intent="documental",
                prompt="contexto",
                system="sistema",
                sources=(Source("c1", "historia", "historia", "core", 0.9, "1904"),),
                turns=(),
            )

        def record(self, *args: Any, **kwargs: Any) -> None:
            return None

    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    base = Settings()

    def medir(endpoint: str, activo: bool) -> list[float]:
        ajustes_medicion = Settings(
            _env_file=None,
            monitoring_enabled=activo,
            monitoring_sample_rate=1.0,
            monitoring_include_content=False,
            langsmith_api_key=base.langsmith_api_key,
            langsmith_endpoint=endpoint,
            monitoring_timeout_ms=2000,
        )
        chat = ChatService(
            build_session_factory(motor),
            Embedder(),
            LLM(),
            agent=Agente(),
            tracer=build_tracer(ajustes_medicion),
            settings=ajustes_medicion,
        )
        # Calentamiento: la primera llamada construye el cliente y el pool.
        for caliente in range(3):
            for _ in chat.stream(f"warm-{endpoint}-{caliente}", "¿Qué es Carvajal?"):
                pass
        tiempos: list[float] = []
        for indice in range(20):
            inicio = time.perf_counter()
            for evento in chat.stream(f"e2e-{endpoint}-{indice}", "¿Qué es Carvajal?"):
                if evento.name == "token":
                    tiempos.append((time.perf_counter() - inicio) * 1000)
                    break
        return tiempos

    caido = medir("http://127.0.0.1:9/", True)
    apagado = medir("", False)
    p95_caido = statistics.quantiles(caido, n=20)[-1]
    p95_apagado = statistics.quantiles(apagado, n=20)[-1]

    assert p95_caido - p95_apagado < 100, (
        f"p95 caído={p95_caido:.1f} ms, apagado={p95_apagado:.1f} ms"
    )
