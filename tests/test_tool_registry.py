"""Registro de herramientas del modo ``tools`` (spec 045, T002 y T009; research R9).

El *golden* ``tests/golden/tool_schemas_044.json`` se capturó con
``convert_to_openai_tool`` **antes** de convertir ``tools.py`` en el paquete ``tools/``:
nombre, descripción y esquema que ve el modelo de las seis herramientas de la 044 no
cambian con el movimiento (FR-027). Si una tarea cambia una descripción a propósito
(T086), actualiza el *golden* en el mismo commit y lo declara en la evidencia.
"""

import json
from pathlib import Path
from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_tool

from carvajal.agent.toolagent.tools import ToolDeps, build_tools
from carvajal.infrastructure.settings import Settings

GOLDEN = Path(__file__).parent / "golden" / "tool_schemas_044.json"


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
