"""Pruebas de la selección de hallazgos del revisor automático de PRs."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "ai_review.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ai_review", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ai_review = _load()

ANCHORS = {"a.py": {1, 2, 3, 4, 5}}


def _finding(line: int, severity: str, comment: str = "detalle") -> dict[str, Any]:
    return {"path": "a.py", "line": line, "severity": severity, "comment": comment}


def test_bloqueantes_e_importantes_se_comentan_en_linea() -> None:
    # Arrange
    data = {"findings": [_finding(1, "bloqueante"), _finding(2, "importante")]}

    # Act
    anchored, unanchored, minors = ai_review.split_findings(data, ANCHORS)

    # Assert
    assert [f["severity"] for f in anchored] == ["bloqueante", "importante"]
    assert unanchored == []
    assert minors == []


def test_menores_no_se_comentan_y_se_limitan_a_dos() -> None:
    # Arrange
    data = {"findings": [_finding(line, "menor") for line in (1, 2, 3)]}

    # Act
    anchored, unanchored, minors = ai_review.split_findings(data, ANCHORS)

    # Assert
    assert anchored == []
    assert unanchored == []
    assert len(minors) == 2


def test_resumen_lista_los_menores_en_su_propia_seccion() -> None:
    # Arrange
    minors = [_finding(3, "menor", "fuga de recurso")]

    # Act
    body = ai_review.render_summary("Resumen del PR.", [], [], minors, False)

    # Assert
    assert "#### Menores (opcionales)" in body
    assert "`a.py:3`: fuga de recurso" in body
