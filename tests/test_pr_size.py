"""Pruebas del medidor de presupuesto de cambios de los PRs."""

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "pr_size.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pr_size", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pr_size = _load()


def test_suma_adiciones_y_borrados() -> None:
    # Arrange
    numstat = "10\t2\tsrc/a.py\n3\t0\tsrc/b.py\n"

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat)

    # Assert
    assert (files, added, deleted) == (2, 13, 2)


def test_ignora_uv_lock() -> None:
    # Arrange
    numstat = "120\t80\tuv.lock\n5\t1\tsrc/a.py\n"

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat)

    # Assert
    assert (files, added, deleted) == (1, 5, 1)


def test_binarios_cuentan_archivo_sin_lineas() -> None:
    # Arrange
    numstat = "-\t-\tassets/logo.png\n"

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat)

    # Assert
    assert (files, added, deleted) == (1, 0, 0)


def test_lineas_malformadas_se_descartan() -> None:
    # Arrange
    numstat = "esto no es numstat\n7\t3\tsrc/a.py\n"

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat)

    # Assert
    assert (files, added, deleted) == (1, 7, 3)
