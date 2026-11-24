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


def test_compose_pasa_las_variables_de_jev_sin_contradecir_settings() -> None:
    """Spec 046 (T002): la app recibe la clave y los ajustes ``JEV_*`` con los defaults."""

    from carvajal.infrastructure.settings import Settings

    text = (REPO / "compose.prod.yml").read_text(encoding="utf-8")
    defaults = Settings(_env_file=None)
    expected = {
        "JEV_ENABLED": "false",
        "TYPESAFE_API_KEY": "",
        "JEV_LEDGER": "",
        "JEV_BASE_URL": defaults.jev_base_url,
        "JEV_MODEL": defaults.jev_model,
        "JEV_TIMEOUT_S": str(defaults.jev_timeout_s),
        "JEV_ROUTER_MODE": defaults.jev_router_mode,
        "JEV_ROUTER_MIN_CONFIDENCE": str(defaults.jev_router_min_confidence),
        "JEV_ROUTER_DIRECT_MIN_CONFIDENCE": str(defaults.jev_router_direct_min_confidence),
        "JEV_CLAIMS_MODE": defaults.jev_claims_mode,
        "JEV_CLAIMS_THRESHOLD": str(defaults.jev_claims_threshold),
        "JEV_MAX_CALLS_PER_DAY": str(defaults.jev_max_calls_per_day),
        "JEV_MAX_EVIDENCE_CHARS": str(defaults.jev_max_evidence_chars),
    }
    for name, value in expected.items():
        assert f"{name}: ${{{name}:-{value}}}" in text, name
