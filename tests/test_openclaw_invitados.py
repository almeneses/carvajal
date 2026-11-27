"""``openclaw/invitados.sh`` con un ``openclaw`` falso: la lista nunca pasa del tope."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "openclaw" / "invitados.sh"
BASE_AUDITORIA = RAIZ / "openclaw" / "auditoria-base.json"

# Guarda la configuración en un JSON y atiende «config get» y «config set» de una ruta.
_OPENCLAW_FALSO = """#!/usr/bin/env python3
import json, os, sys
archivo = os.environ["OPENCLAW_FALSO"]
config = json.load(open(archivo))
_, _, accion, ruta, *resto = sys.argv
if accion == "get":
    print(json.dumps(config[ruta]))
else:
    config[ruta] = json.loads(resto[0])
    json.dump(config, open(archivo, "w"))
"""


def _numeros(cuantos: int) -> list[str]:
    return [f"+5730000{i:05d}" for i in range(cuantos)]


@pytest.fixture
def gateway(tmp_path: Path) -> tuple[dict[str, str], Path]:
    binario = tmp_path / "bin" / "openclaw"
    binario.parent.mkdir()
    binario.write_text(_OPENCLAW_FALSO, encoding="utf-8")
    binario.chmod(binario.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "config.json"
    entorno = {
        **os.environ,
        "PATH": f"{binario.parent}{os.pathsep}{os.environ['PATH']}",
        "OPENCLAW_FALSO": str(config),
        "INVITADOS_BASE": str(tmp_path / "directos-base.json"),
    }
    return entorno, config


def _correr(entorno: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=entorno, capture_output=True, text=True, check=False
    )


def _lista(config: Path) -> list[str]:
    return json.loads(config.read_text(encoding="utf-8"))["channels.whatsapp.allowFrom"]


def test_el_tope_del_script_es_el_maximo_de_la_auditoria() -> None:
    # Arrange
    base = json.loads(BASE_AUDITORIA.read_text(encoding="utf-8"))

    # Act
    tope = re.search(r"^MAXIMO=(\d+)$", SCRIPT.read_text(encoding="utf-8"), re.MULTILINE)

    # Assert
    assert tope, "invitados.sh no declara MAXIMO"
    assert int(tope.group(1)) == base["config"]["allow_from"]["maximo"]


def test_agregar_por_encima_del_tope_no_aplica_la_lista(
    gateway: tuple[dict[str, str], Path],
) -> None:
    # Arrange: la lista ya está en el tope y hay base.
    entorno, config = gateway
    lleno = _numeros(50)
    config.write_text(json.dumps({"channels.whatsapp.allowFrom": lleno}), encoding="utf-8")
    assert _correr(entorno, "fijar-base").returncode == 0

    # Act
    proceso = _correr(entorno, "agregar", "3001234567")

    # Assert
    assert proceso.returncode != 0
    assert "el tope es 50" in proceso.stderr
    assert _lista(config) == lleno


def test_agregar_dentro_del_tope_aplica_la_lista(gateway: tuple[dict[str, str], Path]) -> None:
    # Arrange
    entorno, config = gateway
    config.write_text(json.dumps({"channels.whatsapp.allowFrom": _numeros(49)}), encoding="utf-8")
    assert _correr(entorno, "fijar-base").returncode == 0

    # Act
    proceso = _correr(entorno, "agregar", "3001234567")

    # Assert
    assert proceso.returncode == 0, proceso.stderr
    assert _lista(config)[-1] == "+573001234567"
    assert len(_lista(config)) == 50
