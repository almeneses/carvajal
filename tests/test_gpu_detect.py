"""Pruebas de ``scripts/gpu-detect.sh``: decide GPU o CPU para Ollama en Docker.

Cada caso arma un PATH aislado con binarios falsos (``nvidia-smi``, ``docker``,
``nvidia-container-runtime-hook``) para no depender de la GPU del host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gpu-detect.sh"
REAL_TOOLS = ("bash", "tr", "grep", "sed", "tail")


def _fake(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _run(
    tmp_path: Path,
    mode: str | None,
    fakes: dict[str, str],
    dotenv: str | None = None,
    cdi_spec: str | None = None,
) -> subprocess.CompletedProcess:
    cdi_dir = tmp_path / "cdi"
    cdi_dir.mkdir()
    if cdi_spec is not None:
        (cdi_dir / cdi_spec).write_text("cdiVersion: 0.5.0\n")
    if dotenv is not None:
        (tmp_path / ".env").write_text(dotenv)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in REAL_TOOLS:
        real = shutil.which(tool)
        assert real, f"falta {tool} en el host"
        (bin_dir / tool).symlink_to(real)
    for name, body in fakes.items():
        _fake(bin_dir, name, body)
    env = {"PATH": str(bin_dir), "GPU_DETECT_CDI_DIRS": str(cdi_dir)}
    if mode is not None:
        env["OLLAMA_GPU"] = mode
    return subprocess.run(
        [str(bin_dir / "bash"), str(SCRIPT)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


GPU_OK = "echo 'GPU 0: NVIDIA GeForce (UUID: GPU-x)'"
DOCKER_SIN_NVIDIA = "echo 'io.containerd.runc.v2 runc '"
DOCKER_CON_NVIDIA = "echo 'io.containerd.runc.v2 nvidia runc '"


@pytest.mark.parametrize(
    ("mode", "fakes", "expected"),
    [
        ("1", {}, "1"),
        ("0", {"nvidia-smi": GPU_OK, "nvidia-container-runtime-hook": "true"}, "0"),
        (None, {}, "0"),
        ("auto", {"nvidia-smi": "exit 9", "nvidia-container-runtime-hook": "true"}, "0"),
        ("auto", {"nvidia-smi": GPU_OK, "docker": DOCKER_SIN_NVIDIA}, "0"),
        ("auto", {"nvidia-smi": GPU_OK, "nvidia-container-runtime-hook": "true"}, "1"),
        ("auto", {"nvidia-smi": GPU_OK, "docker": DOCKER_CON_NVIDIA}, "1"),
    ],
    ids=[
        "forzado-gpu",
        "forzado-cpu",
        "auto-sin-driver",
        "auto-driver-roto",
        "auto-sin-toolkit",
        "auto-con-hook",
        "auto-con-runtime",
    ],
)
def test_decide_gpu_o_cpu(
    tmp_path: Path, mode: str | None, fakes: dict[str, str], expected: str
) -> None:
    # Arrange: PATH aislado con los binarios falsos del caso.
    # Act
    result = _run(tmp_path, mode, fakes)

    # Assert
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    ("cdi_spec", "expected"),
    [("nvidia.yaml", "1"), ("nvidia-gpus.json", "1"), ("amd.yaml", "0")],
    ids=["cdi-yaml", "cdi-json", "cdi-ajeno"],
)
def test_acepta_spec_cdi_de_nvidia(tmp_path: Path, cdi_spec: str, expected: str) -> None:
    # Arrange: driver presente, sin hook ni runtime; solo la spec CDI.
    fakes = {"nvidia-smi": GPU_OK, "docker": DOCKER_SIN_NVIDIA}

    # Act
    result = _run(tmp_path, "auto", fakes, cdi_spec=cdi_spec)

    # Assert
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_rechaza_valor_invalido(tmp_path: Path) -> None:
    # Arrange / Act
    result = _run(tmp_path, "si", {})

    # Assert
    assert result.returncode == 2
    assert "OLLAMA_GPU" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("dotenv", "expected"),
    [
        ("X=1\nOLLAMA_GPU=0\n", "0"),
        ('OLLAMA_GPU="1"\n', "1"),
        ("OLLAMA_GPU='0'\n", "0"),
        ("# OLLAMA_GPU=0\n", "1"),
    ],
    ids=["sin-comillas", "comillas-dobles", "comillas-simples", "comentado"],
)
def test_lee_el_modo_desde_dotenv(tmp_path: Path, dotenv: str, expected: str) -> None:
    # Arrange: GPU disponible; .env decide el modo (comentado = auto).
    fakes = {"nvidia-smi": GPU_OK, "nvidia-container-runtime-hook": "true"}

    # Act
    result = _run(tmp_path, None, fakes, dotenv=dotenv)

    # Assert
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_el_entorno_prevalece_sobre_dotenv(tmp_path: Path) -> None:
    # Arrange / Act
    result = _run(tmp_path, "1", {}, dotenv="OLLAMA_GPU=0\n")

    # Assert
    assert result.stdout.strip() == "1"


def test_script_es_ejecutable() -> None:
    assert os.access(SCRIPT, os.X_OK)
