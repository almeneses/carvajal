"""Registro de herramientas del modo ``tools`` (spec 045, T002 y T009; research R9).

El *golden* ``tests/golden/tool_schemas_044.json`` se capturó con
``convert_to_openai_tool`` **antes** de convertir ``tools.py`` en el paquete ``tools/``:
nombre, descripción y esquema que ve el modelo de las seis herramientas de la 044 no
cambian con el movimiento (FR-027). Si una tarea cambia una descripción a propósito
(T086), actualiza el *golden* en el mismo commit y lo declara en la evidencia.
"""

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from carvajal.agent.toolagent import state
from carvajal.agent.toolagent.state import ToolName
from carvajal.agent.toolagent.tools import (
    BUDGET_FREE,
    CITY_KEYS,
    CORPUS_TOOLS,
    DATA_OPEN,
    DECLARATION_TOOLS,
    EVIDENCE_ROLES,
    MEMORY_TOOLS,
    STATUS_TEMPLATES,
    TOOL_DESCRIPTIONS,
    TOOL_NAMES,
    TOOL_SCHEMAS,
    TOOLS,
    ToolDeps,
    build_tools,
    spec_for,
)
from carvajal.infrastructure.settings import Settings

GOLDEN = Path(__file__).parent / "golden" / "tool_schemas_044.json"
# Orden que ve el modelo (data-model *ToolSpec*): las seis de la 044 primero.
ORDER_044 = (
    "buscar_corpus",
    "buscar_producto",
    "cotizar",
    "buscar_sedes",
    "indicador_financiero",
    "sin_consulta",
)
IDENTITY_FIELDS = {"owner_key", "thread_id", "conversation_id", "conversacion", "dueno"}


def _deps() -> ToolDeps:
    """Dependencias mínimas: ``build_tools`` no consulta nada al construir las herramientas."""

    return ToolDeps(
        vector_store=None,  # ty: ignore[invalid-argument-type]
        embedder=None,  # ty: ignore[invalid-argument-type]
        settings=Settings(_env_file=None),
    )


def _canonical(value: Any) -> Any:
    """Ordena las listas ``enum``: ``convert_to_openai_tool`` las arma desde un ``set`` al
    aplanar ``Literal[...] | None`` y su orden cambia con ``PYTHONHASHSEED`` entre procesos
    (pasa igual desde la 044; el contenido no cambia)."""

    if isinstance(value, dict):
        return {
            key: sorted(item) if key == "enum" and isinstance(item, list) else _canonical(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _schemas() -> list[dict[str, Any]]:
    """Lo que ``bind_tools`` envía al proveedor, normalizado como el *golden*."""

    converted = [convert_to_openai_tool(tool) for tool in build_tools(_deps())]
    return _canonical(json.loads(json.dumps(converted, ensure_ascii=False, sort_keys=True)))


def test_las_seis_herramientas_de_la_044_coinciden_con_el_golden() -> None:
    """Nombre, descripción y esquema visibles para el modelo no cambian (FR-027, R9)."""

    # Arrange
    golden = _canonical(json.loads(GOLDEN.read_text(encoding="utf-8")))

    # Act
    schemas = _schemas()[: len(golden)]

    # Assert
    assert [s["function"]["name"] for s in schemas] == [g["function"]["name"] for g in golden]
    assert schemas == golden


# --- Registro (T009) --------------------------------------------------------------------------


def test_el_registro_conserva_el_orden_de_la_044_primero() -> None:
    assert TOOL_NAMES[: len(ORDER_044)] == ORDER_044
    assert tuple(spec.name for spec in TOOLS) == TOOL_NAMES


def test_tool_names_sale_del_registro_y_coincide_con_el_literal() -> None:
    """Igualdad estricta en cada commit: el ``Literal`` crece con cada grupo registrado."""

    assert set(get_args(ToolName)) == set(TOOL_NAMES)
    assert set(state.TOOL_NAMES) == set(TOOL_NAMES)
    assert len(set(TOOL_NAMES)) == len(TOOL_NAMES)


def test_las_vistas_derivadas_son_de_solo_lectura() -> None:
    # Arrange
    views = (TOOL_SCHEMAS, TOOL_DESCRIPTIONS, STATUS_TEMPLATES, EVIDENCE_ROLES, CITY_KEYS)

    # Act / Assert
    for view in views:
        with pytest.raises(TypeError):
            view["nueva"] = None  # ty: ignore[invalid-assignment]
    assert isinstance(BUDGET_FREE, frozenset)
    assert isinstance(MEMORY_TOOLS, frozenset)


def test_papeles_cupo_y_claves_de_ciudad_de_las_seis() -> None:
    assert {"buscar_corpus"} <= CORPUS_TOOLS
    assert {"sin_consulta"} == DECLARATION_TOOLS
    assert {"sin_consulta"} == BUDGET_FREE
    assert set(STATUS_TEMPLATES) == set(TOOL_NAMES) - {"sin_consulta"}
    assert CITY_KEYS["cotizar"] == ("city",)
    assert CITY_KEYS["buscar_sedes"] == ("ciudad",)
    assert all(EVIDENCE_ROLES[name] == "data" for name in ORDER_044[1:5])
    assert spec_for("cotizar") is TOOLS[2]
    assert spec_for("inventada") is None


def test_cada_herramienta_lleva_su_descripcion_explicita_y_no_el_docstring() -> None:
    # Act
    tools = build_tools(_deps())

    # Assert
    assert [tool.name for tool in tools] == list(TOOL_NAMES)
    for tool in tools:
        docstring = (TOOL_SCHEMAS[tool.name].__doc__ or "").strip()
        assert tool.description == TOOL_DESCRIPTIONS[tool.name]
        assert tool.description != docstring
        assert tool.args_schema is TOOL_SCHEMAS[tool.name]


def test_una_herramienta_suelta_se_invoca_con_contexto_vacio() -> None:
    # Arrange
    [sin_consulta] = [tool for tool in build_tools(_deps()) if tool.name == "sin_consulta"]

    # Act
    content = sin_consulta.invoke({"motivo": "conversacion"})

    # Assert
    assert DATA_OPEN in content
    assert '"motivo": "conversacion"' in content


def test_ningun_esquema_tiene_campos_de_identidad() -> None:
    """El dueño y el hilo salen del estado del grafo, nunca de argumentos (FR-014)."""

    for name, schema in TOOL_SCHEMAS.items():
        assert not set(schema.model_fields) & IDENTITY_FIELDS, name
