"""El despliegue objetivo (Compose) usa exactamente 3 workers de uvicorn (047).

La imagen conserva un worker; el Compose lo sobreescribe. Volver a 1 es un
cambio de variable, sin tocar el estado persistido.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.prod.yml"
DOCKERFILE = ROOT / "Dockerfile"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_el_compose_objetivo_activa_tres_workers() -> None:
    # Arrange
    app = _compose()["services"]["app"]

    # Act
    command = app["command"]
    workers_index = command.index("--workers")

    # Assert
    assert app["environment"]["UVICORN_WORKERS"] == "${UVICORN_WORKERS:-3}"
    assert command[workers_index + 1] == "${UVICORN_WORKERS:-3}"
    assert app["environment"]["UVICORN_WORKERS"].endswith(":-3}")


def test_el_compose_no_anade_servicios_ni_pierde_el_proxy() -> None:
    # Arrange
    compose = _compose()

    # Assert
    assert set(compose["services"]) == {"db", "app", "qdrant", "ollama"}
    command = compose["services"]["app"]["command"]
    assert "--proxy-headers" in command
    assert "--no-access-log" in command


def test_volver_a_un_worker_se_documenta_sin_borrar_estado() -> None:
    # Arrange
    compose = COMPOSE.read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "despliegue-ovh.md").read_text(encoding="utf-8")

    # Assert
    assert "UVICORN_WORKERS=1" in compose or "UVICORN_WORKERS=1" in docs
    assert "UVICORN_WORKERS=1" in docs
    assert "no toca la base" in docs.lower() or "no toca la base" in docs
    assert "appdata" in docs


def test_la_imagen_conserva_un_worker_y_el_compose_lo_sobreescribe() -> None:
    # Arrange
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    compose = _compose()

    # Assert
    assert '"--workers"' not in dockerfile
    assert compose["services"]["app"]["command"][0] == "uvicorn"
