"""Formato del conjunto de generalización de la spec 044 (FR-016).

El contrato vive en ``specs/044-agente-tool-calling/contracts/eval-agent.md``. Esta prueba
no evalúa al agente: solo protege la forma del archivo congelado.
"""

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pytest

SET_PATH = Path("data") / "eval" / "generalization_questions.json"
MINIMUMS = {"compuesta": 10, "coloquial": 10, "seguimiento": 10, "adversarial": 8, "abstencion": 5}
GROUPS = {*MINIMUMS, "simple"}
EXPECT_KEYS = {
    "tools_any",
    "tools_none",
    "abstain",
    "abstain_or_cites",
    "asks_for",
    "forbidden_materials",
    "total_pending",
    "max_quote_cards",
    "no_invented_terms",
    "no_currency_conversion",
    "shortcut",
    "language",
    "no_tool_allowed",
}
TOOLS = {
    "buscar_corpus",
    "buscar_producto",
    "cotizar",
    "buscar_sedes",
    "indicador_financiero",
    "sin_consulta",
}


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    return json.loads(SET_PATH.read_text(encoding="utf-8"))


def test_cabecera_declara_origen_y_congelamiento(data: dict[str, Any]) -> None:
    assert isinstance(data["version"], int) and data["version"] >= 1
    assert "frozen_at" in data
    if data["frozen_at"] is not None:
        date.fromisoformat(data["frozen_at"])
    assert data["origin"].strip()


def test_minimos_por_grupo_e_ids_unicos(data: dict[str, Any]) -> None:
    items = data["items"]
    groups = Counter(item["group"] for item in items)

    assert len(items) >= 50
    assert set(groups) <= GROUPS
    for group, minimum in MINIMUMS.items():
        assert groups[group] >= minimum, group
    assert len({item["id"] for item in items}) == len(items)


def test_cada_item_tiene_turnos_y_claves_conocidas(data: dict[str, Any]) -> None:
    for item in data["items"]:
        assert item["turns"] and all(turn.strip() for turn in item["turns"]), item["id"]
        expect = item["expect"]
        assert set(expect) <= EXPECT_KEYS, (item["id"], set(expect) - EXPECT_KEYS)
        named = {tool for group in expect.get("tools_any", []) for tool in group}
        named |= set(expect.get("tools_none", []))
        assert named <= TOOLS, (item["id"], named - TOOLS)
