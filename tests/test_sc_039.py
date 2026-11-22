"""Criterios de éxito de la spec 039 sobre el catálogo real y el grupo «colombia».

- SC-002: ninguna recomendación para Colombia trae referencias de otro país ni viola una regla
  de material (las restricciones se prueban en ``test_routing_eval_set``).
- SC-003: todo precio de Colombia mostrado lleva COP, nota de IVA, fecha y fuente.
- SC-004: los totales de las preguntas de Colombia coinciden con el cálculo esperado; sin
  precio publicado, se declara la ausencia sin convertir desde México (T029).
- SC-001: toda referencia de Colombia viene de una fuente con fecha y un SKU único.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from carvajal.agent.commercial_flow import run_commercial
from carvajal.agent.router import decide_route
from carvajal.catalog.loader import load_catalog

EVAL_SET = Path(__file__).resolve().parents[1] / "data" / "eval" / "routing_questions.json"
ITEMS: list[dict[str, Any]] = json.loads(EVAL_SET.read_text(encoding="utf-8"))["items"]
COLOMBIA = [item for item in ITEMS if item["group"] == "colombia"]
CON_COTIZACION = [item for item in COLOMBIA if "quote" in item]
HOY = date(2026, 11, 18)


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in items]


def test_hay_al_menos_veinte_preguntas_de_colombia() -> None:
    assert len(COLOMBIA) >= 20
    assert all(item["expected"].get("country") == "CO" for item in COLOMBIA)


@pytest.mark.parametrize("item", CON_COTIZACION, ids=_ids(CON_COTIZACION))
def test_sc004_el_total_de_colombia_es_el_esperado(item: dict[str, Any]) -> None:
    decision = decide_route(item["question"], item.get("turns"))
    resultado = run_commercial(decision.extra, today=HOY)

    assert {opcion["country"] for opcion in resultado["options"]} == {"CO"}
    assert "MXN" not in json.dumps(resultado, ensure_ascii=False)
    esperado = item["quote"]
    opcion = next(o for o in resultado["options"] if o["sku"] == esperado["sku"])
    if esperado.get("no_price"):
        assert "offers" not in opcion
        assert "no se estima ni se convierte" in opcion["price_note"]
    else:
        clave = "total" if decision.extra.get("quantity") else "price_per_box"
        assert opcion["offers"][0][clave] == esperado["total"]


def test_sc003_todo_precio_de_colombia_lleva_moneda_iva_fecha_y_fuente() -> None:
    catalogo = load_catalog()
    colombianas = [ref for ref in catalogo.references if ref.country == "CO"]
    ofertas = [oferta for ref in colombianas for oferta in catalogo.offers(ref)]

    assert ofertas
    for oferta in ofertas:
        assert oferta.currency == "COP"
        assert oferta.tax_status in {"incluido", "no_incluido", "no_indicado"}
        assert oferta.captured_at
        assert oferta.source_name and oferta.source_url.startswith("https://")


def test_sc001_las_referencias_de_colombia_tienen_fuente_fecha_y_sku_unico() -> None:
    colombianas = [ref for ref in load_catalog().references if ref.country == "CO"]

    assert len(colombianas) >= 90
    assert len({ref.sku for ref in colombianas}) == len(colombianas)
    assert all(ref.source_url.startswith("https://") and ref.captured_at for ref in colombianas)
    assert all(ref.price is None for ref in colombianas)  # el precio va en precios.csv
