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


def test_ignora_andamiaje_generado_por_spec_kit() -> None:
    # Arrange
    numstat = (
        "800\t0\t.specify/scripts/powershell/common.ps1\n"
        "300\t0\t.opencode/commands/speckit.plan.md\n"
        "20\t0\tspecs/002-api-backend-frontend/spec.md\n"
    )

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat)

    # Assert
    assert (files, added, deleted) == (1, 20, 0)


def test_lo_escrito_por_el_equipo_siempre_cuenta() -> None:
    # Arrange
    numstat = (
        "85\t0\t.specify/memory/constitution.md\n"
        "3\t0\t.specify/memory/.constitution-template.json\n"
    )

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat)

    # Assert
    assert (files, added, deleted) == (1, 85, 0)


def test_exclusiones_extra_no_desactivan_las_globales() -> None:
    # Arrange
    numstat = "900\t0\t.specify/templates/plan-template.md\n7\t0\tassets/logo.svg\n"

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat, extra_ignored={"assets/logo.svg"})

    # Assert
    assert (files, added, deleted) == (0, 0, 0)


def test_normaliza_separadores_de_windows() -> None:
    # Arrange
    numstat = "900\t0\t.specify\\scripts\\powershell\\common.ps1\n5\t0\tsrc\\a.py\n"

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat)

    # Assert
    assert (files, added, deleted) == (1, 5, 0)


def test_normaliza_prefijo_punto_barra() -> None:
    # Arrange
    numstat = "900\t0\t./.specify/scripts/powershell/common.ps1\n5\t0\t./src/a.py\n"

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat)

    # Assert
    assert (files, added, deleted) == (1, 5, 0)


def test_comando_fuera_del_patron_de_spec_kit_cuenta() -> None:
    # Arrange
    numstat = "40\t0\t.opencode/commands/speckit.propio.md.bak\n"

    # Act
    files, added, deleted = pr_size.parse_numstat(numstat)

    # Assert
    assert (files, added, deleted) == (1, 40, 0)


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
