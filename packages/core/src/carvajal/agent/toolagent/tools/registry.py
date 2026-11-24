"""Registro central de las herramientas del modo ``tools`` (spec 045, T010; research R9).

Única fuente de verdad: de :data:`TOOLS` se derivan los nombres, los esquemas y las
descripciones que ve el modelo, la implementación, la línea de estado, el consumo del
límite de consultas, el papel ante la compuerta de evidencia y los argumentos que son
ciudad. No hay registro por efecto de importación: :data:`TOOLS` es una tupla explícita en
el orden en que el modelo ve las herramientas (data-model *ToolSpec*), así que un prefijo de
prompt estable favorece la caché del proveedor.

Para añadir una herramienta: escribir su módulo (``tools/<nombre>.py`` con su esquema,
``DESCRIPTION``, ``status`` y ``run``), añadir su :class:`ToolSpec` aquí en su posición y
ampliar el ``Literal`` ``ToolName`` de ``toolagent/state.py`` en el mismo commit;
``tests/test_tool_registry.py`` exige que ambos coincidan.

Las vistas derivadas son de solo lectura (``MappingProxyType`` y ``frozenset``).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal

from pydantic import BaseModel

from carvajal.agent.toolagent import status
from carvajal.agent.toolagent.state import (
    TOOL_BUSCAR_CORPUS,
    TOOL_BUSCAR_PRODUCTO,
    TOOL_BUSCAR_SEDES,
    TOOL_COTIZAR,
    TOOL_INDICADOR_FINANCIERO,
    TOOL_LISTAR_MEMORIA,
    TOOL_OLVIDAR_PREFERENCIA,
    TOOL_RECORDAR_PREFERENCIA,
    TOOL_SIN_CONSULTA,
    ToolName,
)
from carvajal.agent.toolagent.tools import (
    builtin,
    listar_memoria,
    olvidar_preferencia,
    recordar_preferencia,
)
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Draft, _Sources

__all__ = [
    "BUDGET_FREE",
    "CITY_KEYS",
    "CORPUS_TOOLS",
    "DECLARATION_TOOLS",
    "EVIDENCE_ROLES",
    "MEMORY_TOOLS",
    "STATUS_TEMPLATES",
    "TOOLS",
    "TOOL_DESCRIPTIONS",
    "TOOL_NAMES",
    "TOOL_SCHEMAS",
    "EvidenceRole",
    "StatusTemplate",
    "ToolRun",
    "ToolSpec",
    "spec_for",
]

EvidenceRole = Literal["corpus", "data", "declaration", "memory"]
# Texto de la línea de estado a partir de los argumentos validados (FR-025).
StatusTemplate = Callable[[Mapping[str, Any]], str]
# Implementación: argumentos validados, dependencias, contexto del turno y registro de
# fuentes de la llamada; devuelve el borrador que ``run_tool`` numera y formatea.
ToolRun = Callable[[Any, ToolDeps, ToolContext, _Sources], _Draft]


@dataclass(frozen=True)
class ToolSpec:
    """Una herramienta registrada (data-model *ToolSpec*).

    - ``name``: nombre que ve el modelo; único y presente en ``ToolName``.
    - ``args_schema``: modelo Pydantic v2 sin campos de identidad (dueño, hilo).
    - ``description``: texto explícito que ve el modelo (nunca el docstring del esquema).
    - ``run``: determinista salvo las excepciones declaradas; nunca debe lanzar hacia el
      grafo (``run_tool`` convierte cualquier excepción en ``error``).
    - ``status_template``: línea de estado; ``None`` solo para ``sin_consulta``.
    - ``consumes_budget``: si gasta ``AGENT_MAX_TOOL_CALLS``; ``False`` solo
      ``sin_consulta``.
    - ``evidence_role``: ``corpus`` (sin evidencia, abstención), ``data``, ``declaration``
      (``sin_consulta``) o ``memory`` (sin fuentes; ``_outcome`` guarda solo la categoría).
    - ``city_keys``: argumentos que son una ciudad (la línea de estado solo la muestra si
      la persona la dijo; FR-013).
    """

    name: ToolName
    args_schema: type[BaseModel]
    description: str
    run: ToolRun
    status_template: StatusTemplate | None
    consumes_budget: bool = True
    evidence_role: EvidenceRole = "data"
    city_keys: tuple[str, ...] = ()


TOOLS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name=TOOL_BUSCAR_CORPUS,
        args_schema=builtin.BuscarCorpusArgs,
        description=builtin.DESCRIPTIONS[TOOL_BUSCAR_CORPUS],
        run=builtin._buscar_corpus,
        status_template=status.BUILTIN_TEMPLATES[TOOL_BUSCAR_CORPUS],
        evidence_role="corpus",
    ),
    ToolSpec(
        name=TOOL_BUSCAR_PRODUCTO,
        args_schema=builtin.BuscarProductoArgs,
        description=builtin.DESCRIPTIONS[TOOL_BUSCAR_PRODUCTO],
        run=builtin._buscar_producto,
        status_template=status.BUILTIN_TEMPLATES[TOOL_BUSCAR_PRODUCTO],
    ),
    ToolSpec(
        name=TOOL_COTIZAR,
        args_schema=builtin.CotizarArgs,
        description=builtin.DESCRIPTIONS[TOOL_COTIZAR],
        run=builtin._cotizar,
        status_template=status.BUILTIN_TEMPLATES[TOOL_COTIZAR],
        city_keys=("city",),
    ),
    ToolSpec(
        name=TOOL_BUSCAR_SEDES,
        args_schema=builtin.BuscarSedesArgs,
        description=builtin.DESCRIPTIONS[TOOL_BUSCAR_SEDES],
        run=builtin._buscar_sedes,
        status_template=status.BUILTIN_TEMPLATES[TOOL_BUSCAR_SEDES],
        city_keys=("ciudad",),
    ),
    ToolSpec(
        name=TOOL_INDICADOR_FINANCIERO,
        args_schema=builtin.IndicadorFinancieroArgs,
        description=builtin.DESCRIPTIONS[TOOL_INDICADOR_FINANCIERO],
        run=builtin._indicador_financiero,
        status_template=status.BUILTIN_TEMPLATES[TOOL_INDICADOR_FINANCIERO],
    ),
    ToolSpec(
        name=TOOL_SIN_CONSULTA,
        args_schema=builtin.SinConsultaArgs,
        description=builtin.DESCRIPTIONS[TOOL_SIN_CONSULTA],
        run=builtin._sin_consulta,
        status_template=None,
        consumes_budget=False,
        evidence_role="declaration",
    ),
    # Memoria del dueño (spec 045, US2, T050): sin fuentes; ``_outcome`` guarda solo la
    # categoría como argumento (FR-012, FR-018).
    ToolSpec(
        name=TOOL_RECORDAR_PREFERENCIA,
        args_schema=recordar_preferencia.RecordarPreferenciaArgs,
        description=recordar_preferencia.DESCRIPTION,
        run=recordar_preferencia.run,
        status_template=recordar_preferencia.status,
        evidence_role="memory",
    ),
    ToolSpec(
        name=TOOL_LISTAR_MEMORIA,
        args_schema=listar_memoria.ListarMemoriaArgs,
        description=listar_memoria.DESCRIPTION,
        run=listar_memoria.run,
        status_template=listar_memoria.status,
        evidence_role="memory",
    ),
    ToolSpec(
        name=TOOL_OLVIDAR_PREFERENCIA,
        args_schema=olvidar_preferencia.OlvidarPreferenciaArgs,
        description=olvidar_preferencia.DESCRIPTION,
        run=olvidar_preferencia.run,
        status_template=olvidar_preferencia.status,
        evidence_role="memory",
    ),
)

# --- Vistas derivadas (solo lectura) ----------------------------------------------------------

TOOL_NAMES: Final[tuple[str, ...]] = tuple(spec.name for spec in TOOLS)
_BY_NAME: Final[Mapping[str, ToolSpec]] = MappingProxyType({spec.name: spec for spec in TOOLS})
TOOL_SCHEMAS: Final[Mapping[str, type[BaseModel]]] = MappingProxyType(
    {spec.name: spec.args_schema for spec in TOOLS}
)
TOOL_DESCRIPTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {spec.name: spec.description for spec in TOOLS}
)
IMPLEMENTATIONS: Final[Mapping[str, ToolRun]] = MappingProxyType(
    {spec.name: spec.run for spec in TOOLS}
)
STATUS_TEMPLATES: Final[Mapping[str, StatusTemplate]] = MappingProxyType(
    {spec.name: spec.status_template for spec in TOOLS if spec.status_template is not None}
)
EVIDENCE_ROLES: Final[Mapping[str, EvidenceRole]] = MappingProxyType(
    {spec.name: spec.evidence_role for spec in TOOLS}
)
CITY_KEYS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {spec.name: spec.city_keys for spec in TOOLS if spec.city_keys}
)
BUDGET_FREE: Final[frozenset[str]] = frozenset(
    spec.name for spec in TOOLS if not spec.consumes_budget
)
CORPUS_TOOLS: Final[frozenset[str]] = frozenset(
    spec.name for spec in TOOLS if spec.evidence_role == "corpus"
)
DECLARATION_TOOLS: Final[frozenset[str]] = frozenset(
    spec.name for spec in TOOLS if spec.evidence_role == "declaration"
)
MEMORY_TOOLS: Final[frozenset[str]] = frozenset(
    spec.name for spec in TOOLS if spec.evidence_role == "memory"
)

if len(_BY_NAME) != len(TOOLS):  # pragma: no cover - lo vigila tests/test_tool_registry.py
    raise RuntimeError("El registro de herramientas repite un nombre.")


def spec_for(name: str) -> ToolSpec | None:
    """Especificación registrada de ``name`` (``None`` si no existe)."""

    return _BY_NAME.get(name)
