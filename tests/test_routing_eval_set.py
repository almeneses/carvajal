"""Conjunto de evaluación del enrutamiento y no regresión (spec 036, T034-T035, SC-005).

Las preguntas del grupo «existente» llevan la ruta que les daba el router antes de la 036:
si alguna cambia, la ruta comercial rompió una ruta existente.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from carvajal.agent.commercial_flow import run_commercial
from carvajal.agent.router import decide_route

EVAL_SET = Path(__file__).resolve().parents[1] / "data" / "eval" / "routing_questions.json"
ITEMS: list[dict[str, Any]] = json.loads(EVAL_SET.read_text(encoding="utf-8"))["items"]
ROUTES = {"direct", "rag", "tool", "commercial"}


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in items]


def test_el_conjunto_tiene_el_tamano_y_la_forma_esperados() -> None:
    assert len(ITEMS) >= 100
    assert len(set(_ids(ITEMS))) == len(ITEMS)
    assert {item["group"] for item in ITEMS} == {"existente", "comercial", "colombia"}
    assert all(item["route"] in ROUTES and item["intent"] for item in ITEMS)
    existentes = [item for item in ITEMS if item["group"] == "existente"]
    retrieval = json.loads((EVAL_SET.parent / "retrieval_questions.json").read_text("utf-8"))
    assert {item.get("source") for item in existentes} >= {
        f"retrieval_questions.json#{query['id']}" for query in retrieval["queries"]
    }


EXISTENTES = [item for item in ITEMS if item["group"] == "existente"]
# El grupo «colombia» (spec 039) se evalúa igual que el comercial: ruta, datos y materiales.
COMERCIALES = [item for item in ITEMS if item["group"] in {"comercial", "colombia"}]


@pytest.mark.parametrize("item", EXISTENTES, ids=_ids(EXISTENTES))
def test_las_rutas_existentes_no_cambian(item: dict[str, Any]) -> None:
    decision = decide_route(item["question"], item.get("turns"))

    assert (decision.route, decision.intent) == (item["route"], item["intent"])


@pytest.mark.parametrize("item", COMERCIALES, ids=_ids(COMERCIALES))
def test_las_preguntas_comerciales_extraen_lo_esperado(item: dict[str, Any]) -> None:
    decision = decide_route(item["question"], item.get("turns"))

    assert (decision.route, decision.intent) == (item["route"], item["intent"])
    for key, value in item["expected"].items():
        assert decision.extra[key] == value, key


CON_REGLA = [
    item for item in COMERCIALES if "constraints" in item and item["route"] == "commercial"
]


@pytest.mark.parametrize("item", CON_REGLA, ids=_ids(CON_REGLA))
def test_ninguna_recomendacion_viola_la_regla_de_material(item: dict[str, Any]) -> None:
    decision = decide_route(item["question"], item.get("turns"))

    result = run_commercial(decision.extra)

    prohibidos = set(item["constraints"]["forbidden_materials"])
    assert not {option["material"] for option in result["options"]} & prohibidos
