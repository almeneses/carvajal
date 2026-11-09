#!/usr/bin/env python3
"""Verifica el presupuesto de cambios de un pull request.

Compara el diff entre la base y la cabeza del PR, ignora artefactos generados y
falla si se supera el límite duro. Entre el blando y el duro solo advierte.

Uso:
    BASE_SHA=... HEAD_SHA=... python3 .github/scripts/pr_size.py

Variables de entorno:
    BASE_SHA        commit base del PR (github.event.pull_request.base.sha)
    HEAD_SHA        commit cabeza del PR (github.event.pull_request.head.sha)
    SOFT_LIMIT      límite para advertir (por defecto 300)
    HARD_LIMIT      límite que hace fallar el check (por defecto 700)
    GITHUB_STEP_SUMMARY  si existe, se escribe ahí el resumen
"""

from __future__ import annotations

import os
import subprocess
import sys

IGNORED = {"uv.lock"}

# Herramientas de terceros que se instalan tal cual: no se revisan por línea, así
# que no consumen el presupuesto de revisión del PR.
GENERATED_PREFIXES = (".specify/", ".opencode/commands/speckit.")

# Dentro de lo generado hay archivos que sí escribe el equipo y por tanto cuentan:
# la proyección de la constitución y las especificaciones de ``specs/``.
REVIEWED = {".specify/memory/constitution.md"}


def is_ignored(path: str, extra: set[str] | None = None) -> bool:
    """Indica si el archivo es un artefacto generado o de terceros.

    ``extra`` añade exclusiones puntuales sin desactivar las globales.
    """

    if path in REVIEWED:
        return False
    if path in IGNORED or path in (extra or set()):
        return True
    return path.startswith(GENERATED_PREFIXES)


def parse_numstat(text: str, ignored: set[str] | None = None) -> tuple[int, int, int]:
    """Suma archivos, adiciones y borrados, ignorando los artefactos generados."""
    files = added = deleted = 0
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        raw_added, raw_deleted, path = parts
        if is_ignored(path, ignored):
            continue
        files += 1
        # Los binarios aparecen como "-" en --numstat.
        added += int(raw_added) if raw_added.isdigit() else 0
        deleted += int(raw_deleted) if raw_deleted.isdigit() else 0
    return files, added, deleted


def read_numstat(base: str, head: str) -> str:
    """Devuelve la salida de ``git diff --numstat`` entre base y head."""
    result = subprocess.run(
        ["git", "diff", "--numstat", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def main() -> int:
    """Punto de entrada del script."""
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "")
    soft = int(os.environ.get("SOFT_LIMIT", "300"))
    hard = int(os.environ.get("HARD_LIMIT", "700"))
    if not base or not head:
        print("Faltan BASE_SHA o HEAD_SHA; se omite el presupuesto de cambios.")
        return 0

    files, added, deleted = parse_numstat(read_numstat(base, head))
    total = added + deleted
    summary = (
        f"**Presupuesto de cambios**: {total} líneas "
        f"(+{added}/-{deleted}) en {files} archivo(s). "
        f"Límite blando {soft}, duro {hard}."
    )
    print(summary)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")

    if total > hard:
        print(f"::error::El PR cambia {total} líneas y supera el límite duro de {hard}.")
        return 1
    if total > soft:
        print(
            f"::warning::El PR cambia {total} líneas; "
            f"se recomienda partirlo (límite blando {soft})."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
