"""Smoke test reproducible de la demo M2 contra la API real.

Requiere API (:8000), Postgres, Qdrant indexado, Ollama y el LLM configurado.
Ejecuta los cuatro casos de la rúbrica, mide el primer token y guarda evidencia
anonimizada (sin cookies ni claves) en ``docs/evidence/m2/smoke.json``.

Uso:
    uv run python scripts/smoke_m2.py
    CARVAJAL_API_URL=http://localhost:8000 uv run python scripts/smoke_m2.py
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_OUTPUT = Path("docs") / "evidence" / "m2" / "smoke.json"


def parse_sse(lines: list[str]) -> list[dict[str, Any]]:
    """Convierte bloques SSE en ``{event, data}``."""

    events: list[dict[str, Any]] = []
    name = "message"
    data: list[str] = []
    for line in lines:
        if not line.strip():
            if data:
                events.append({"event": name, "data": json.loads("\n".join(data))})
            name, data = "message", []
            continue
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data.append(line.removeprefix("data:").lstrip())
    if data:
        events.append({"event": name, "data": json.loads("\n".join(data))})
    return events


class Api:
    """Cliente con cookies y CSRF; cada instancia es un visitante distinto."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.client = httpx.Client(base_url=self.base, timeout=120)
        self.csrf = ""

    def token(self) -> str:
        response = self.client.get("/api/csrf")
        response.raise_for_status()
        self.csrf = response.json()["token"]
        return self.csrf

    def new_thread(self) -> str:
        response = self.client.post("/api/chat/new", data={"csrf_token": self.token()})
        response.raise_for_status()
        return response.json()["thread_id"]

    def stream(self, question: str, turn: str, thread: str | None = None) -> dict[str, Any]:
        data = {
            "question": question,
            "csrf_token": self.token(),
            "client_turn_id": turn,
        }
        if thread:
            data["thread_id"] = thread
        started = time.perf_counter()
        lines: list[str] = []
        first_token: float | None = None
        with self.client.stream("POST", "/api/chat/stream", data=data) as response:
            for line in response.iter_lines():
                lines.append(line)
                if line.startswith('data: {"text"') and first_token is None:
                    first_token = time.perf_counter() - started
        events = parse_sse(lines)
        text = "".join(
            str(event["data"].get("text", ""))
            for event in events
            if event["event"] in {"token", "fallback"}
        )
        meta = next((event["data"] for event in events if event["event"] == "meta"), {})
        trace = next((event["data"] for event in events if event["event"] == "trace"), {})
        return {
            "question": question,
            "status": response.status_code,
            "events": [event["event"] for event in events],
            "route": trace.get("route", ""),
            "intent": trace.get("intent", ""),
            "tool": trace.get("tool", ""),
            "memory": trace.get("memory", 0),
            "retrieved": trace.get("retrieved", 0),
            "thread_id": str(trace.get("thread_id", "")),
            "sources": [
                {
                    "chunk_id": source.get("chunk_id"),
                    "slug": source.get("slug"),
                    "url": source.get("url"),
                    "source_type": source.get("source_type"),
                    "score": source.get("score"),
                }
                for source in meta.get("sources", [])
            ],
            "answer": text,
            "first_token_ms": round(first_token * 1000) if first_token else None,
            "last_event": events[-1]["event"] if events else "",
            "total_ms": round((time.perf_counter() - started) * 1000),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test de la demo M2.")
    parser.add_argument(
        "--base",
        default=os.environ.get("CARVAJAL_API_URL", "http://localhost:8000"),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    api = Api(args.base)
    cases: list[dict[str, Any]] = []

    direct = api.stream("Hola, ¿qué puedes hacer?", "ev-direct")
    cases.append({"case": "directa", **direct})

    thread = api.new_thread()
    city = api.stream("¿En qué ciudad fue fundada Carvajal?", "ev-city", thread)
    cases.append({"case": "rag_ciudad", **city})
    year = api.stream("¿Y en qué año ocurrió eso?", "ev-year", thread)
    cases.append({"case": "rag_seguimiento", **year})

    tool = api.stream(
        "¿Cuál fue el EBITDA consolidado de Carvajal a septiembre de 2025 y qué margen reportó?",
        "ev-tool",
        thread,
    )
    cases.append({"case": "herramienta", **tool})

    memory = api.stream("Recuerda que prefiero respuestas breves", "ev-memory", thread)
    cases.append({"case": "memoria_guardar", **memory})
    new_thread = api.new_thread()
    recalled = api.stream("Hola", "ev-memory-recall", new_thread)
    cases.append({"case": "memoria_recuperar_otro_hilo", **recalled})

    other = Api(args.base)
    isolated = other.stream("Hola", "ev-isolation")
    cases.append({"case": "aislamiento_otro_visitante", **isolated})

    no_evidence = api.stream(
        "¿Cuál es el número de teléfono de la sede de Carvajal en Tokio?",
        "ev-no-evidence",
        thread,
    )
    cases.append({"case": "sin_evidencia", **no_evidence})

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base,
        "cases": cases,
        "checks": {
            "directa_sin_busqueda": direct["route"] == "direct" and direct["retrieved"] == 0,
            "rag_con_fuente_url": bool(city["sources"]) and bool(city["sources"][0]["url"]),
            "seguimiento_mismo_hilo": bool(city["thread_id"])
            and year["thread_id"] == city["thread_id"],
            "seguimiento_responde_1904": "1904" in year["answer"],
            "herramienta_ebitda": tool["tool"] == "ebitda" and "351.631" in tool["answer"],
            "memoria_entre_hilos": recalled["memory"] >= 1,
            "aislamiento": isolated["memory"] == 0,
            "abstencion_sin_fuentes": not no_evidence["sources"],
            "todos_los_turnos_cierran": all(
                case["status"] == 200 and case["last_event"] == "done" for case in cases
            ),
            "herramienta_cierra_sin_error": tool["last_event"] == "done"
            and "error" not in tool["events"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["checks"], ensure_ascii=False, indent=2))
    print(f"Evidencia: {args.output}")
    return 0 if all(payload["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
