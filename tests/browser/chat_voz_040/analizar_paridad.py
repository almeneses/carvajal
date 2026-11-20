"""Analiza la paridad de ruta y campos de las mediciones SC-001/SC-002.

Uso:
    uv run python tests/browser/chat_voz_040/analizar_paridad.py \
        specs/040-chat-por-voz/evidencia/dictado-voz1.jsonl \
        specs/040-chat-por-voz/evidencia/dictado-voz2.jsonl \
        --json specs/040-chat-por-voz/evidencia/paridad.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from carvajal.agent.router import decide_route


def cargar(ruta: Path) -> list[dict[str, Any]]:
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    return [json.loads(linea) for linea in lineas if '"id"' in linea]


def firma(decision: Any) -> dict[str, Any]:
    """Datos extraídos que deben coincidir, sea commercial, tool o direct."""

    return {
        "extra": dict(decision.extra) if decision.extra else {},
        "tool_name": decision.tool_name,
        "tool_period": decision.tool_period,
    }


def analizar(intentos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filas: list[dict[str, Any]] = []
    for intento in intentos:
        escrito = decide_route(intento["pregunta"])
        dictado = decide_route(intento["texto"])
        campos = firma(escrito) == firma(dictado)
        filas.append(
            {
                "id": intento["id"],
                "escrito": escrito.route,
                "dictado": dictado.route,
                "campos": campos,
                "ok": escrito.route == dictado.route and campos,
                "texto": intento["texto"],
            }
        )
    return filas


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archivos", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    todas: list[dict[str, Any]] = []
    for ruta in args.archivos:
        filas = analizar(cargar(ruta))
        aciertos = sum(1 for fila in filas if fila["ok"])
        print(f"{ruta.name}: {aciertos}/{len(filas)}")
        todas.extend(filas)

    aciertos = sum(1 for fila in todas if fila["ok"])
    latencias = sorted(
        intento["latencia_ms"]
        for ruta in args.archivos
        for intento in cargar(ruta)
        if isinstance(intento.get("latencia_ms"), int)
    )
    p95 = latencias[math.ceil(0.95 * len(latencias)) - 1] if latencias else None
    resumen = {
        "voz1": [f for r in args.archivos[:1] for f in analizar(cargar(r))],
        "voz2": [f for r in args.archivos[1:2] for f in analizar(cargar(r))],
        "total": {"aciertos": aciertos, "intentos": len(todas)},
        "sc002": {
            "intentos": len(latencias),
            "p95_ms": p95,
            "min": latencias[0] if latencias else None,
            "max": latencias[-1] if latencias else None,
        },
    }
    print(f"Total: {aciertos}/{len(todas)} = {100 * aciertos / len(todas):.1f} %")
    print(f"SC-002: {len(latencias)} intentos, p95 {p95} ms")
    if args.json:
        args.json.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
