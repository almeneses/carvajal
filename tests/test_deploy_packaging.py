"""``data/institucional`` viaja en la imagen y en la siembra de producción (spec 045, T006).

Sin esto, ``empresas_del_grupo`` fallaría en producción y en el contenedor MCP de WhatsApp
(research R12): ``.dockerignore`` excluye ``data/*`` salvo lo que se reincluye, y el volumen
``appdata`` tapa ``/app/data``, así que la imagen trae una copia en ``/app/seed-data`` que
el bootstrap y ``scripts/deploy.sh`` siembran. Se verifica leyendo los archivos, sin red ni
Docker.
"""

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _lines(name: str) -> list[str]:
    return [line.strip() for line in (REPO / name).read_text(encoding="utf-8").splitlines()]


def test_dockerignore_reincluye_la_capa_institucional() -> None:
    # Arrange
    lines = _lines(".dockerignore")

    # Act
    excluded = lines.index("data/*")

    # Assert
    assert lines.index("!data/institucional") > excluded
    assert lines.index("!data/institucional/**") > excluded


def test_dockerfile_copia_la_capa_y_su_semilla() -> None:
    lines = _lines("Dockerfile")

    assert "COPY data/institucional ./data/institucional" in lines
    assert "COPY data/institucional /app/seed-data/institucional" in lines


def test_bootstrap_de_compose_siembra_la_capa_institucional() -> None:
    lines = _lines("compose.prod.yml")

    assert "seed_dir /seed/institucional /app/data/institucional" in lines
    assert "seed_dir /app/seed-data/institucional /app/data/institucional" in lines


def test_deploy_siembra_la_capa_institucional_sin_pisar_el_volumen(tmp_path: Path) -> None:
    # Arrange
    script = (REPO / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    match = re.search(r"^SEED_PROVISIONING='\n(.*?)\n'$", script, re.S | re.M)
    assert match, "deploy.sh no define SEED_PROVISIONING"
    app = tmp_path / "app"
    seed = app / "seed-data"
    for folder in ("catalog", "commercial", "institucional"):
        (seed / folder).mkdir(parents=True)
    (seed / "institucional" / "empresas.json").write_text("imagen\n")
    (seed / "institucional" / "nuevo.json").write_text("imagen\n")
    (app / "data" / "institucional").mkdir(parents=True)
    (app / "data" / "institucional" / "empresas.json").write_text("volumen\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").write_text('#!/bin/sh\n[ "$3" = version ] && echo v1\nexit 0\n')
    (bin_dir / "python").chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    # Act
    result = subprocess.run(
        ["sh", "-c", match.group(1).replace("/app", str(app))],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # Assert
    assert result.returncode == 0, result.stderr
    assert (app / "data" / "institucional" / "empresas.json").read_text() == "volumen\n"
    assert (app / "data" / "institucional" / "nuevo.json").read_text() == "imagen\n"
