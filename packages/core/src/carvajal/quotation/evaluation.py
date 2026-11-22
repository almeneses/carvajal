"""Casos dorados de cotización (spec 042, SC-002).

Cada caso fija un texto, su historial y el resultado esperado con rutas con puntos
(``primary.presentation.subtotal``). Se ejecutan en CI: si un cambio altera un total,
una presentación o una ventana, la prueba falla antes de llegar a producción.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from carvajal.quotation.engine import quote

__all__ = ["DEFAULT_CASES", "evaluate", "load_cases"]

DEFAULT_CASES = Path("data") / "eval" / "cotizacion_cases.json"
BOGOTA = ZoneInfo("America/Bogota")


def load_cases(path: Path | str = DEFAULT_CASES) -> list[dict[str, Any]]:
    """Casos del archivo JSON."""

    return json.loads(Path(path).read_text(encoding="utf-8"))


def _value(result: dict[str, Any], path: str) -> Any:
    """Valor anidado del payload o el inicio de la respuesta."""

    current: Any = result
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            current = current[int(part)] if part.isdigit() and int(part) < len(current) else None
        else:
            return None
    return current


def evaluate(cases: list[dict[str, Any]]) -> list[str]:
    """Devuelve los fallos; vacío si todos los casos coinciden."""

    failures: list[str] = []
    for case in cases:
        day = date.fromisoformat(case.get("today", "2026-11-27"))
        now = datetime(day.year, day.month, day.day, 9, 0, tzinfo=BOGOTA)
        result = quote(
            case["text"],
            questions=case.get("questions") or [],
            today=day,
            now=now,
            allow_demo=bool(case.get("allow_demo", True)),
            profile=case.get("profile"),
        )
        for path, expected in (case.get("expect") or {}).items():
            if path == "reply_start":
                actual: Any = result.get("reply", "")
                matched = str(actual).startswith(str(expected))
            else:
                actual = _value(result, path)
                matched = str(actual).lower() == str(expected).lower()
            if not matched:
                failures.append(f"{case['name']}: {path} = {actual!r}, se esperaba {expected!r}")
    return failures
