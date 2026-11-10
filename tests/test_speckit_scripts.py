"""Pruebas de humo de los scripts de Spec Kit para Linux/macOS."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BASH_SCRIPTS = REPO_ROOT / ".specify" / "scripts" / "bash"
COMMANDS_DIR = REPO_ROOT / ".opencode" / "commands"
BASH_SCRIPT_REF = re.compile(r"\.specify/scripts/bash/([A-Za-z0-9_.-]+\.sh)")

pytestmark = pytest.mark.skipif(os.name == "nt", reason="requiere bash y utilidades POSIX")


def _run(script: Path, *args: str, feature_dir: str | None = None) -> dict[str, Any]:
    """Ejecuta un script bash y devuelve su salida JSON."""
    env = os.environ.copy()
    env["SPECIFY_FEATURE_NO_PERSIST"] = "1"
    if feature_dir is not None:
        env["SPECIFY_FEATURE_DIRECTORY"] = feature_dir

    try:
        result = subprocess.run(
            ["bash", str(script), *args],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        pytest.fail(f"{script.name} falló ({exc.returncode})\nstderr:\n{exc.stderr}")
    return json.loads(result.stdout)


def _crear_feature_temporal(tmp_path: Path) -> Path:
    """Crea una feature mínima para los scripts que exigen spec y plan."""
    feature_dir = tmp_path / "003-demo"
    feature_dir.mkdir()
    (feature_dir / "spec.md").write_text("# Demo\n", encoding="utf-8")
    (feature_dir / "plan.md").write_text("# Plan\n", encoding="utf-8")
    return feature_dir


def test_resolve_template_devuelve_contenido() -> None:
    """``resolve-template.sh`` devuelve el nombre y el contenido de la plantilla."""
    data = _run(BASH_SCRIPTS / "resolve-template.sh", "spec-template", "--json")

    assert data["TEMPLATE_NAME"] == "spec-template"
    assert data["TEMPLATE_CONTENT"].strip()


def test_check_prerequisites_devuelve_rutas(tmp_path: Path) -> None:
    """``check-prerequisites.sh --paths-only`` resuelve las rutas de la feature."""
    feature_dir = _crear_feature_temporal(tmp_path)

    data = _run(
        BASH_SCRIPTS / "check-prerequisites.sh",
        "--json",
        "--paths-only",
        feature_dir=str(feature_dir),
    )

    for key in ("FEATURE_DIR", "FEATURE_SPEC", "IMPL_PLAN", "TASKS"):
        assert Path(data[key]).is_absolute()
    assert Path(data["FEATURE_DIR"]) == feature_dir.resolve()


def test_setup_plan_devuelve_claves(tmp_path: Path) -> None:
    """``setup-plan.sh --json`` devuelve las claves del plan."""
    feature_dir = _crear_feature_temporal(tmp_path)

    data = _run(BASH_SCRIPTS / "setup-plan.sh", "--json", feature_dir=str(feature_dir))

    assert set(data) == {"FEATURE_SPEC", "IMPL_PLAN", "FEATURE_DIR", "BRANCH"}


def test_setup_tasks_devuelve_claves(tmp_path: Path) -> None:
    """``setup-tasks.sh --json`` devuelve las claves de las tareas."""
    feature_dir = _crear_feature_temporal(tmp_path)

    data = _run(BASH_SCRIPTS / "setup-tasks.sh", "--json", feature_dir=str(feature_dir))

    assert set(data) == {
        "FEATURE_DIR",
        "AVAILABLE_DOCS",
        "TASKS_TEMPLATE",
        "TASKS_TEMPLATE_CONTENT",
    }
    assert data["TASKS_TEMPLATE_CONTENT"].strip()


def test_comandos_mencionan_ambas_variantes() -> None:
    """Los nueve comandos ofrecen la variante Linux/macOS y la de Windows."""
    commands = sorted(COMMANDS_DIR.glob("speckit.*.md"))

    assert len(commands) == 9
    for command in commands:
        text = command.read_text(encoding="utf-8")
        assert ".specify/scripts/bash/" in text, command.name
        assert ".specify/scripts/powershell/" in text, command.name
        scripts = set(BASH_SCRIPT_REF.findall(text))
        assert scripts, command.name
        for name in scripts:
            assert (BASH_SCRIPTS / name).is_file(), f"{command.name} referencia {name}"
