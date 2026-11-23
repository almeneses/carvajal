"""Mide SC-005 del chat comercial unificado (spec 043).

Envía turnos mixtos por SSE y registra, por turno:

- inicio de la petición;
- primer contenido útil (``token`` o ``products``);
- cierre (``done`` o ``error``).

Calcula mediana y p95 de las dos últimas marcas. No decide si el piloto pasa:
solo produce la evidencia que el owner usa para aprobar el umbral de T029.

Uso:
    uv run python scripts/medir_sc005.py --base-url http://127.0.0.1:8013
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_OUTPUT = Path("specs") / "043-chat-comercial-unificado" / "medicion_sc005.json"
QUESTIONS = (
    "¿Qué es Carvajal y dónde puedo comprar vasos para bebida caliente en Bogotá?",
    "Cotiza 20 vasos para bebida caliente en Bogotá",
    "¿Dónde encuentro empaques en Cali?",
    "¿Cuál es el material adecuado para comida caliente?",
    "Cotiza 300 vasos de 3,5 oz en Bogotá con domicilio",
)


def _percentile(values: list[float], fraction: float) -> float:
    """Percentil nearest-rank; suficiente para una muestra pequeña y explícita."""

    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _new_client(base_url: str, timeout: float) -> httpx.Client:
    """Cliente con cookies propias y CSRF/identidad inicializados."""

    client = httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=True)
    client.get("/chat")
    token = client.get("/api/csrf").json()["token"]
    client.headers["X-CSRF-Token"] = token
    return client


def _stream_turn(
    client: httpx.Client,
    question: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    """Ejecuta un turno y devuelve las tres marcas en milisegundos."""

    started = time.perf_counter()
    first_content: float | None = None
    closed: float | None = None
    event_name = "message"
    data_lines: list[str] = []
    error = ""
    route = ""
    sources = 0
    token = client.headers.get("X-CSRF-Token", "")

    def finish() -> None:
        nonlocal event_name, data_lines
        event_name = "message"
        data_lines = []

    with client.stream(
        "POST",
        "/api/chat/stream",
        data={
            "question": question,
            "csrf_token": token,
            "client_turn_id": uuid.uuid4().hex,
        },
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line == "":
                if event_name in {"token", "products"} and first_content is None:
                    first_content = (time.perf_counter() - started) * 1000
                if event_name in {"done", "error"} and closed is None:
                    closed = (time.perf_counter() - started) * 1000
                    payload = "\n".join(data_lines)
                    if event_name == "error":
                        error = payload[:300]
                    else:
                        try:
                            data = json.loads(payload)
                            route = str(data.get("route", ""))
                        except json.JSONDecodeError:
                            pass
                finish()
                continue
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())
            elif line.startswith(":"):
                continue
            if event_name == "meta" and len(data_lines) == 1:
                try:
                    sources = len(json.loads(data_lines[0]).get("sources", []))
                except (json.JSONDecodeError, AttributeError):
                    sources = 0

    if first_content is None:
        first_content = closed or (time.perf_counter() - started) * 1000
    if closed is None:
        closed = (time.perf_counter() - started) * 1000
        error = error or "sin evento de cierre"
    return {
        "question": question,
        "first_content_ms": round(first_content, 2),
        "close_ms": round(closed, 2),
        "route": route,
        "sources": sources,
        "error": error,
        "ok": not error,
    }


def _sequential(base_url: str, turns: int, timeout: float) -> list[dict[str, Any]]:
    client = _new_client(base_url, timeout)
    try:
        return [
            _stream_turn(client, QUESTIONS[index % len(QUESTIONS)], timeout=timeout)
            for index in range(turns)
        ]
    finally:
        client.close()


def _concurrent(
    base_url: str,
    sessions: int,
    timeout: float,
) -> list[dict[str, Any]]:
    def one(index: int) -> dict[str, Any]:
        client = _new_client(base_url, timeout)
        try:
            return _stream_turn(
                client,
                QUESTIONS[index % len(QUESTIONS)],
                timeout=timeout,
            )
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=sessions) as pool:
        futures = [pool.submit(one, index) for index in range(sessions)]
        return [future.result() for future in as_completed(futures)]


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [row for row in rows if row["ok"]]
    first = [row["first_content_ms"] for row in ok]
    close = [row["close_ms"] for row in ok]
    return {
        "turns": len(rows),
        "ok": len(ok),
        "failed": len(rows) - len(ok),
        "first_content_ms": {
            "median": round(statistics.median(first), 2) if first else None,
            "p95": round(_percentile(first, 0.95), 2) if first else None,
        },
        "close_ms": {
            "median": round(statistics.median(close), 2) if close else None,
            "p95": round(_percentile(close, 0.95), 2) if close else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8013")
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument("--concurrent", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    started = datetime.now(UTC).isoformat()
    sequential = _sequential(args.base_url, args.turns, args.timeout)
    concurrent = _concurrent(args.base_url, args.concurrent, args.timeout)
    result = {
        "sc": "SC-005",
        "spec": "043-chat-comercial-unificado",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "sequential": {
            "summary": _summary(sequential),
            "turns": sequential,
        },
        "concurrent": {
            "summary": _summary(concurrent),
            "turns": concurrent,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["sequential"]["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
