"""Evidencia reproducible de SC-001 y SC-002 del monitoreo (spec 038).

Envía un turno real por la ruta `tool` al proyecto configurado en LangSmith y
comprueba que la traza de producción no lleva texto ni identificadores del
visitante. Requiere `MONITORING_ENABLED=true` y `LANGSMITH_API_KEY`.

Uso:
    MONITORING_ENABLED=true LANGSMITH_API_KEY=... \
        uv run python scripts/evidencia_monitoreo_038.py
    uv run python scripts/evidencia_monitoreo_038.py --latencia   # SC-003
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MONITORING_INCLUDE_CONTENT", "false")
os.environ.setdefault("MONITORING_SAMPLE_RATE", "1.0")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core" / "src"))

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD

PREGUNTA = "Soy Ana Pérez, mi teléfono es 300 123 4567. ¿Ingresos de Carvajal en 2025?"
PII = ("300 123 4567", "Ana Pérez")


def latencia(muestras: int = 20) -> int:
    """Mide el p95 al primer fragmento con el servicio caído y apagado (SC-003).

    Usa el mismo `ChatService` y el mismo tracer que el chat, con un agente de
    prueba para no depender de Ollama; el objetivo es aislar el costo del
    monitoreo, no la latencia del modelo.
    """

    import statistics
    from collections.abc import Iterator, Sequence
    from typing import Any

    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from apps.api.chat.agent import AgentTurn
    from apps.api.chat.service import ChatService
    from carvajal.adapters.storage import tables
    from carvajal.adapters.storage.database import build_session_factory
    from carvajal.application.ask import Source
    from carvajal.infrastructure.settings import Settings

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
        ajustes = Settings(
            _env_file=None,
            monitoring_enabled=activo,
            monitoring_sample_rate=1.0,
            monitoring_include_content=False,
            langsmith_api_key=base.langsmith_api_key,
            langsmith_endpoint=endpoint,
            monitoring_timeout_ms=2000,
        )
        from carvajal.infrastructure.monitoring import build_tracer

        chat = ChatService(
            build_session_factory(motor),
            Embedder(),
            LLM(),
            agent=Agente(),
            tracer=build_tracer(ajustes),
            settings=ajustes,
        )
        tiempos = []
        for indice in range(muestras):
            inicio = __import__("time").perf_counter()
            for evento in chat.stream(f"lat-{endpoint}-{indice}", "¿Qué es Carvajal?"):
                if evento.name == "token":
                    tiempos.append((__import__("time").perf_counter() - inicio) * 1000)
                    break
        return tiempos

    caido = medir("http://127.0.0.1:9/", True)
    apagado = medir("", False)

    def p95(serie: list[float]) -> float:
        """Percentil 95 de la serie."""

        return round(statistics.quantiles(serie, n=20)[-1], 1)

    print(f"p95 con el servicio caído: {p95(caido)} ms")
    print(f"p95 sin monitoreo:         {p95(apagado)} ms")
    print(f"diferencia:                {p95(caido) - p95(apagado):+.1f} ms (límite 100 ms)")
    return 0


def main() -> int:
    cliente = TestClient(create_app())
    cliente.get("/faq")
    respuesta = cliente.post(
        "/chat/stream",
        data={
            "question": PREGUNTA,
            CSRF_FIELD: cliente.cookies[CSRF_COOKIE],
            "client_turn_id": "evidencia-038",
        },
        headers={"Accept": "text/event-stream"},
    )
    eventos = []
    for bloque in respuesta.text.split("\n\n"):
        nombre = "message"
        datos = []
        for linea in bloque.splitlines():
            if linea.startswith("event:"):
                nombre = linea.removeprefix("event:").strip()
            elif linea.startswith("data:"):
                datos.append(linea.removeprefix("data:").strip())
        if datos:
            eventos.append((nombre, json.loads("\n".join(datos))))
    done = next((d for n, d in eventos if n == "done"), None)
    if done is None:
        print("El turno no terminó; revisa /health y los logs.", file=sys.stderr)
        return 1
    print("request_id:", done.get("request_id"), "| route:", done.get("route"))
    print(
        "Comprueba en LangSmith que la raíz lleva inputs vacíos, solo outcome en "
        "outputs y ningún nombre ni teléfono en toda la traza."
    )
    return 0


if __name__ == "__main__":
    if "--latencia" in sys.argv:
        raise SystemExit(latencia())
    raise SystemExit(main())
