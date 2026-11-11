#!/usr/bin/env python3
"""Calcula el tag de versión que corresponde a un merge en ``dev``.

El plan de migración identifica cada PR con un ID (``W0.1b``, ``W12.3``…). Al
mergear un PR a ``dev`` se crea un tag con ese ID en minúsculas y con puntos
convertidos en guiones (``w0-1b``), de modo que cualquier estado integrado se
pueda recuperar con ``git checkout w0-1b``.

También admite un tag manual (``workflow_dispatch``) para cerrar una ola:
``ola-0``, ``ola-1``…

Variables de entorno:
    PR_TITLE    título del PR (``github.event.pull_request.title``)
    PR_BASE     rama base del PR (``github.event.pull_request.base.ref``)
    MANUAL_TAG  tag explícito para ``workflow_dispatch``
    GITHUB_OUTPUT  si existe, se escribe ahí ``tag=<valor>``
"""

from __future__ import annotations

import os
import re
import sys

# ``(W0.1b)``, ``(w12.3)``… con paréntesis obligatorios para no capturar IDs
# sueltos que aparezcan en la descripción del título.
PLAN_ID = re.compile(r"\(\s*w(\d+)\.(\d+)([a-z]?)\s*\)", re.IGNORECASE)
MANUAL_TAG = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


def tag_from_pr_title(title: str) -> str:
    """Extrae el tag del ID del plan en el título del PR.

    Returns:
        El tag normalizado (``w0-1b``) o cadena vacía si el título no tiene ID.
    """

    match = PLAN_ID.search(title)
    if match is None:
        return ""
    major, minor, suffix = match.groups()
    return f"w{int(major)}-{int(minor)}{suffix.lower()}"


def resolve_tag(title: str, base: str, manual: str) -> str:
    """Decide el tag según el evento: manual, merge a ``dev`` o ninguno."""

    if manual:
        return manual if MANUAL_TAG.match(manual) else ""
    if base != "dev":
        return ""
    return tag_from_pr_title(title)


def write_output(tag: str) -> None:
    """Publica el tag como output del step de GitHub Actions."""

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"tag={tag}\n")


def main() -> int:
    """Punto de entrada del script."""

    title = os.environ.get("PR_TITLE", "")
    base = os.environ.get("PR_BASE", "")
    manual = os.environ.get("MANUAL_TAG", "").strip()

    if manual and not MANUAL_TAG.match(manual):
        print(
            f"::error::Tag manual inválido: {manual!r}. Usa minúsculas, números, puntos o guiones."
        )
        return 1

    tag = resolve_tag(title, base, manual)
    write_output(tag)
    if tag:
        print(f"Tag calculado: {tag}")
    else:
        print("Sin ID de plan en el título; no se crea tag.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
