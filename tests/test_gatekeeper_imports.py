"""Cada módulo nuevo de la spec 046 se importa solo, sin ciclos (T062; research R1).

Se importa en un subproceso aislado para que el orden de las demás pruebas no oculte un
ciclo ``registry → gatekeeper → toolagent``.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULES = [
    "carvajal.adapters.judge.jev",
    "carvajal.adapters.judge",
    "carvajal.agent.gatekeeper.model",
    "carvajal.agent.gatekeeper.privacy",
    "carvajal.agent.gatekeeper.rules",
    "carvajal.agent.gatekeeper.decide",
    "carvajal.agent.gatekeeper.claims",
    "carvajal.agent.gatekeeper.evaluation",
    "carvajal.agent.toolagent.tools.registry",
]


@pytest.mark.parametrize("module", MODULES)
def test_el_modulo_se_importa_en_un_proceso_aislado(module: str) -> None:
    # Act
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    # Assert
    assert result.returncode == 0, result.stderr[-2000:]
