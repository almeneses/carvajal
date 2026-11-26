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


def _snippet(root: Path, name: str = "SEED_PROVISIONING") -> str:
    match = re.search(rf"^{name}='\n(.*?)\n'$", DEPLOY.read_text(), re.S | re.M)
    assert match, f"deploy.sh no define {name}"
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


STAMP = "20261126T020000Z-sha-prueba"


def _run(
    root: Path, expected: str | None = VERSION, name: str = "SEED_PROVISIONING"
) -> subprocess.CompletedProcess:
    env = {"PATH": f"{root.parent / 'bin'}:{os.environ['PATH']}", "DEPLOY_STAMP": STAMP}
    if expected is not None:
        env["EXPECTED_COMMERCIAL_VERSION"] = expected
    return subprocess.run(
        ["sh", "-c", _snippet(root, name)], env=env, capture_output=True, text=True, check=False
    )


def _volume_catalog(root: Path, text: str) -> Path:
    """El volumen ya tiene un referencias.csv propio (de un despliegue anterior)."""

    local = root / "data" / "catalog" / "empaques" / "referencias.csv"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(text)
    return local


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


# --- Catálogo: el de la imagen manda (2026-11-25) ----------------------------------------


def test_un_catalogo_distinto_se_reemplaza_con_respaldo(root: Path) -> None:
    # Arrange: el volumen conserva el catálogo de un despliegue anterior con la misma
    # versión (el caso de producción: precios.csv de la #256 bajo el código de la #258).
    local = _volume_catalog(root, "viejo\n")

    # Act
    result = _run(root)

    # Assert
    assert result.returncode == 0, result.stderr
    assert local.read_text() == "imagen\n"
    assert "actualizado: data/catalog/empaques/referencias.csv" in result.stdout
    backup = root / "data" / "respaldos" / f"catalogo-{STAMP}" / "empaques" / "referencias.csv"
    assert backup.read_text() == "viejo\n"


def test_un_catalogo_igual_no_se_toca_ni_se_respalda(root: Path) -> None:
    # Arrange
    _volume_catalog(root, "imagen\n")

    # Act
    result = _run(root)

    # Assert
    assert result.returncode == 0, result.stderr
    assert "actualizado" not in result.stdout
    assert not (root / "data" / "respaldos").exists()


def test_la_evidencia_comercial_del_volumen_no_se_pisa(root: Path) -> None:
    # Arrange: commercial/ guarda datos que se registran en el servidor.
    local = root / "data" / "commercial" / "proveedores.csv"
    local.parent.mkdir(parents=True)
    local.write_text("volumen\n")

    # Act
    result = _run(root)

    # Assert
    assert result.returncode == 0, result.stderr
    assert local.read_text() == "volumen\n"


def test_una_version_distinta_restaura_el_catalogo_reemplazado(root: Path) -> None:
    # Arrange
    local = _volume_catalog(root, "viejo\n")

    # Act
    result = _run(root, expected="2026-12-01")

    # Assert
    assert result.returncode == 1
    assert local.read_text() == "viejo\n"
    assert "catálogo restaurado" in result.stderr


def test_un_catalogo_que_no_valida_restaura_el_anterior(root: Path) -> None:
    # Arrange: el validador falso falla si el catálogo de la imagen dice «roto».
    (root.parent / "bin" / "python").write_text(
        FAKE_PYTHON.replace(
            "validar) [",
            "validar) ! grep -q roto data/catalog/empaques/referencias.csv || exit 4; [",
        )
    )
    (root / "seed-data" / "catalog" / "empaques" / "referencias.csv").write_text("roto\n")
    local = _volume_catalog(root, "viejo\n")

    # Act
    result = _run(root)

    # Assert
    assert result.returncode == 1
    assert local.read_text() == "viejo\n"


def test_el_rollback_restaura_el_catalogo_del_despliegue(root: Path) -> None:
    # Arrange: el despliegue reemplazó el catálogo y después falló (p. ej., /health).
    local = _volume_catalog(root, "viejo\n")
    assert _run(root).returncode == 0
    assert local.read_text() == "imagen\n"

    # Act
    result = _run(root, name="RESTORE_CATALOG")

    # Assert
    assert result.returncode == 0, result.stderr
    assert local.read_text() == "viejo\n"
    assert "catálogo restaurado" in result.stdout


def test_el_rollback_sin_respaldo_no_hace_nada(root: Path) -> None:
    # Act
    result = _run(root, name="RESTORE_CATALOG")

    # Assert
    assert result.returncode == 0
    assert result.stdout == ""


def test_deploy_restaura_el_catalogo_en_el_rollback() -> None:
    # El rollback usa el mismo DEPLOY_STAMP que la siembra.
    text = DEPLOY.read_text()
    rollback = text[text.index("rollback() {") : text.index("\n}\n", text.index("rollback() {"))]

    assert '"$RESTORE_CATALOG"' in rollback
    assert 'DEPLOY_STAMP="$DEPLOY_STAMP"' in rollback
    assert text.count('-e DEPLOY_STAMP="$DEPLOY_STAMP"') == 2
