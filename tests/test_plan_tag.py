"""Pruebas del cálculo de tags de versión para los merges en ``dev``."""

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "plan_tag.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plan_tag", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plan_tag = _load()


def test_extrae_el_id_del_plan() -> None:
    # Arrange
    title = "refactor: mover el paquete a packages/core (W0.1b)"

    # Act
    tag = plan_tag.tag_from_pr_title(title)

    # Assert
    assert tag == "w0-1b"


def test_normaliza_mayusculas_puntos_y_ceros() -> None:
    # Arrange
    title = "feat: caso de uso (W02.03C)"

    # Act
    tag = plan_tag.tag_from_pr_title(title)

    # Assert
    assert tag == "w2-3c"


def test_sin_id_no_genera_tag() -> None:
    # Arrange
    title = "docs: actualizar el runbook"

    # Act
    tag = plan_tag.tag_from_pr_title(title)

    # Assert
    assert tag == ""


def test_solo_tags_de_merge_en_dev() -> None:
    # Arrange
    title = "feat: algo (W4.1)"

    # Act
    en_main = plan_tag.resolve_tag(title, base="main", manual="")
    en_dev = plan_tag.resolve_tag(title, base="dev", manual="")

    # Assert
    assert en_main == ""
    assert en_dev == "w4-1"


def test_tag_manual_gana_al_titulo() -> None:
    # Arrange
    title = "feat: algo (W4.1)"

    # Act
    tag = plan_tag.resolve_tag(title, base="dev", manual="ola-0")

    # Assert
    assert tag == "ola-0"


def test_tag_manual_invalido_no_genera_tag() -> None:
    # Arrange
    manual = "Ola 0"

    # Act
    tag = plan_tag.resolve_tag("", base="dev", manual=manual)

    # Assert
    assert tag == ""


def test_escribe_el_output_de_actions(monkeypatch, tmp_path) -> None:
    # Arrange
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    # Act
    plan_tag.write_output("w2-3")

    # Assert
    assert output.read_text(encoding="utf-8") == "tag=w2-3\n"
