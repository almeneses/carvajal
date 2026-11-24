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


# --- Conjunto v3 (congelado antes del ajuste de AGENT_SYSTEM) ---------------------------------

V3_PATH = Path("data") / "eval" / "generalization_v3.json"
V3_GROUPS = {*GROUPS, "precio", "supuesto"}
V3_ITEM_KEYS = {"id", "group", "turns", "expect"}


@pytest.fixture(scope="module")
def data_v3() -> dict[str, Any]:
    return json.loads(V3_PATH.read_text(encoding="utf-8"))


def test_v3_cabecera_congelada_con_origen(data_v3: dict[str, Any]) -> None:
    assert data_v3["version"] == 3
    date.fromisoformat(data_v3["frozen_at"])
    assert data_v3["origin"].strip()
    assert data_v3["description"].strip()


def test_v3_ids_unicos_y_grupos_conocidos(data_v3: dict[str, Any]) -> None:
    items = data_v3["items"]
    ids = [item["id"] for item in items]

    assert items
    assert len(set(ids)) == len(ids)
    assert all(item_id.startswith("v3-") for item_id in ids)
    assert {item["group"] for item in items} <= V3_GROUPS


def test_v3_items_con_claves_y_herramientas_validas(data_v3: dict[str, Any]) -> None:
    for item in data_v3["items"]:
        assert set(item) == V3_ITEM_KEYS, (item["id"], set(item) ^ V3_ITEM_KEYS)
        assert item["turns"] and all(turn.strip() for turn in item["turns"]), item["id"]
        expect = item["expect"]
        assert set(expect) <= EXPECT_KEYS, (item["id"], set(expect) - EXPECT_KEYS)
        named = {tool for group in expect.get("tools_any", []) for tool in group}
        named |= set(expect.get("tools_none", []))
        assert named <= TOOLS, (item["id"], named - TOOLS)


# --- Conjunto de herramientas de la spec 045 (FR-030, T004) -----------------------------------

H045_PATH = Path("data") / "eval" / "herramientas_045.json"
# Las 20 consultas de FR-020 en el orden del registro (data-model *ToolSpec*). Se declaran
# aquí porque el conjunto se congela antes de registrar las herramientas nuevas.
TOOLS_045 = (
    "buscar_corpus",
    "buscar_producto",
    "cotizar",
    "buscar_sedes",
    "indicador_financiero",
    "sin_consulta",
    "ficha_producto",
    "comparar_referencias",
    "guia_materiales",
    "convertir_presentacion",
    "ciudades_con_cobertura",
    "consultar_cotizacion",
    "indicadores_disponibles",
    "historia_carvajal",
    "empresas_del_grupo",
    "preguntas_frecuentes",
    "fecha_actual",
    "recordar_preferencia",
    "listar_memoria",
    "olvidar_preferencia",
)
H045_ITEM_KEYS = {"id", "group", "turns", "expect"}
H045_OPTIONAL_KEYS = {"new_thread_before", "path"}
# Marcas del atajo de memoria: con ellas la pregunta no mediría a ``recordar_preferencia``.
SHORTCUT_MARKS = ("llámame", "llamame", "recuerda que", "prefiero", "mi nombre es")


@pytest.fixture(scope="module")
def data_h045() -> dict[str, Any]:
    return json.loads(H045_PATH.read_text(encoding="utf-8"))


def test_h045_cabecera_congelada_con_origen(data_h045: dict[str, Any]) -> None:
    assert data_h045["version"] == 1
    date.fromisoformat(data_h045["frozen_at"])
    assert "asistente de desarrollo" in data_h045["origin"]
    assert data_h045["description"].strip()


def test_h045_una_pregunta_por_cada_una_de_las_20_herramientas(data_h045: dict[str, Any]) -> None:
    # Arrange
    tools = [
        item["expect"]["tools_any"][0][0]
        for item in data_h045["items"]
        if item["group"] == "herramienta"
    ]

    # Act / Assert
    assert len(TOOLS_045) == 20
    assert sorted(tools) == sorted(TOOLS_045)


def test_h045_seis_escenarios_de_memoria(data_h045: dict[str, Any]) -> None:
    memoria = [item for item in data_h045["items"] if item["group"] == "memoria"]
    ids = [item["id"] for item in data_h045["items"]]

    assert len(memoria) == 6
    assert len(set(ids)) == len(ids)
    assert {item["group"] for item in data_h045["items"]} == {"herramienta", "memoria"}


def test_h045_items_con_claves_y_herramientas_validas(data_h045: dict[str, Any]) -> None:
    for item in data_h045["items"]:
        keys = set(item)
        assert H045_ITEM_KEYS <= keys <= H045_ITEM_KEYS | H045_OPTIONAL_KEYS, item["id"]
        assert item["turns"] and all(turn.strip() for turn in item["turns"]), item["id"]
        expect = item["expect"]
        assert set(expect) <= EXPECT_KEYS, (item["id"], set(expect) - EXPECT_KEYS)
        named = {tool for group in expect.get("tools_any", []) for tool in group}
        named |= set(expect.get("tools_none", []))
        assert named <= set(TOOLS_045), (item["id"], named - set(TOOLS_045))
        if "path" in item:
            # «Olvidar todo» lo atiende el atajo: declara el camino y no exige herramientas.
            assert item["path"] == "shortcut", item["id"]
            assert expect["tools_any"] == [] and expect["shortcut"] is True, item["id"]


def test_h045_otra_conversacion_y_sin_marcas_del_atajo(data_h045: dict[str, Any]) -> None:
    # Arrange
    from carvajal.agent.toolagent.evaluation import load_question_set

    items = {item.id: item for item in load_question_set(H045_PATH).items}
    raw = {item["id"]: item for item in data_h045["items"]}

    # Act
    other_thread = [item_id for item_id, item in items.items() if item.new_thread_before]
    remember = [
        turn.casefold()
        for item in raw.values()
        if "recordar_preferencia" in {t for g in item["expect"].get("tools_any", []) for t in g}
        for turn in item["turns"]
    ]

    # Assert
    assert "m045-01" in other_thread
    assert remember
    assert not [turn for turn in remember if any(mark in turn for mark in SHORTCUT_MARKS)]
