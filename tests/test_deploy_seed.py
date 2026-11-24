"""Pruebas de la siembra del aprovisionamiento comercial en ``scripts/deploy.sh``.

El fragmento ``SEED_PROVISIONING`` corre dentro del contenedor ``app`` con rutas bajo
``/app``. Aquí se ejecuta con una raíz temporal en lugar de ``/app`` y un ``python``
falso que imita ``python -m carvajal.commercial version|validar``.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "scripts" / "deploy.sh"
VERSION = "2026-11-18"
FAKE_PYTHON = f"""#!/bin/sh
# python -m carvajal.commercial <orden>
case "$3" in
  version) echo {VERSION} ;;
  validar) [ -f data/catalog/empaques/referencias.csv ] || exit 3 ;;
esac
"""


def _snippet(root: Path) -> str:
    match = re.search(r"^SEED_PROVISIONING='\n(.*?)\n'$", DEPLOY.read_text(), re.S | re.M)
    assert match, "deploy.sh no define SEED_PROVISIONING"
    return match.group(1).replace("/app", str(root))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    seed = app / "seed-data"
    (seed / "catalog" / "empaques").mkdir(parents=True)
    (seed / "commercial").mkdir(parents=True)
    (seed / "catalog" / "empaques" / "referencias.csv").write_text("imagen\n")
    (seed / "commercial" / "proveedores.csv").write_text("imagen\n")
    (app / "data").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").write_text(FAKE_PYTHON)
    (bin_dir / "python").chmod(0o755)
    return app


def _run(root: Path, expected: str | None = VERSION) -> subprocess.CompletedProcess:
    env = {"PATH": f"{root.parent / 'bin'}:{os.environ['PATH']}"}
    if expected is not None:
        env["EXPECTED_COMMERCIAL_VERSION"] = expected
    return subprocess.run(
        ["sh", "-c", _snippet(root)], env=env, capture_output=True, text=True, check=False
    )


def test_siembra_lo_que_falta_y_valida(root: Path) -> None:
    # Act
    result = _run(root)

    # Assert
    assert result.returncode == 0, result.stderr
    assert (root / "data" / "catalog" / "empaques" / "referencias.csv").read_text() == "imagen\n"
    assert "sembrado: data/catalog/empaques/referencias.csv" in result.stdout
    assert f"catálogo comercial {VERSION} válido" in result.stdout


def test_no_pisa_los_archivos_del_volumen_y_es_idempotente(root: Path) -> None:
    # Arrange: el volumen ya tiene su propio proveedores.csv.
    local = root / "data" / "commercial" / "proveedores.csv"
    local.parent.mkdir(parents=True)
    local.write_text("volumen\n")
    first = _run(root)

    # Act
    second = _run(root)

    # Assert
    assert first.returncode == second.returncode == 0
    assert local.read_text() == "volumen\n"
    assert "sembrado" not in second.stdout


def test_una_version_distinta_detiene_el_despliegue(root: Path) -> None:
    # Act
    result = _run(root, expected="2026-12-01")

    # Assert
    assert result.returncode == 1
    assert f"catálogo comercial {VERSION}; se esperaba 2026-12-01" in result.stderr


def test_un_catalogo_invalido_detiene_el_despliegue(root: Path) -> None:
    # Arrange: la imagen no trae el archivo que exige el validador.
    (root / "seed-data" / "catalog" / "empaques" / "referencias.csv").unlink()

    # Act
    result = _run(root)

    # Assert
    assert result.returncode != 0


def test_una_imagen_sin_seed_data_se_salta_la_siembra(root: Path) -> None:
    # Arrange: imágenes anteriores a la 043 (p. ej., en un rollback manual).
    (root / "seed-data").rename(root / "otra")

    # Act
    result = _run(root, expected=None)

    # Assert
    assert result.returncode == 0
    assert "se omite" in result.stdout
    assert not (root / "data" / "catalog").exists()
