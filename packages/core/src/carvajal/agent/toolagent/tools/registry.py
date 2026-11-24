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
    TOOL_CIUDADES_CON_COBERTURA,
    TOOL_COMPARAR_REFERENCIAS,
    TOOL_CONSULTAR_COTIZACION,
    TOOL_CONVERTIR_PRESENTACION,
    TOOL_COTIZAR,
    TOOL_EMPRESAS_DEL_GRUPO,
    TOOL_FECHA_ACTUAL,
    TOOL_FICHA_PRODUCTO,
    TOOL_GUIA_MATERIALES,
    TOOL_HISTORIA_CARVAJAL,
    TOOL_INDICADOR_FINANCIERO,
    TOOL_INDICADORES_DISPONIBLES,
    TOOL_LISTAR_MEMORIA,
    TOOL_OLVIDAR_PREFERENCIA,
    TOOL_PREGUNTAS_FRECUENTES,
    TOOL_RECORDAR_PREFERENCIA,
    TOOL_SIN_CONSULTA,
    ToolName,
)
from carvajal.agent.toolagent.tools import (
    builtin,
    ciudades_con_cobertura,
    comparar_referencias,
    consultar_cotizacion,
    convertir_presentacion,
    empresas_del_grupo,
    fecha_actual,
    ficha_producto,
    guia_materiales,
    historia_carvajal,
    indicadores_disponibles,
    listar_memoria,
    olvidar_preferencia,
    preguntas_frecuentes,
    recordar_preferencia,
)
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Draft, _Sources
from carvajal.application.ports import GateScope

__all__ = [
    "BUDGET_FREE",
    "CITY_KEYS",
    "CORPUS_TOOLS",
    "DECLARATION_TOOLS",
    "EVIDENCE_ROLES",
    "MEMORY_TOOLS",
    "ROUTE_GROUPS",
    "STATUS_TEMPLATES",
    "TOOLS",
    "TOOLS_BY_SCOPE",
    "TOOL_DESCRIPTIONS",
    "TOOL_NAMES",
    "TOOL_SCHEMAS",
    "EvidenceRole",
    "RouteGroup",
    "StatusTemplate",
    "ToolRun",
    "ToolSpec",
    "spec_for",
]

EvidenceRole = Literal["corpus", "data", "declaration", "memory"]
# Grupo de la consulta para el gatekeeper (spec 046, research R3): ``rag`` (documental),
# ``structured`` (catálogo, precios, sedes, cifras, fechas, cotizaciones y memoria) o ``any``
# (solo ``sin_consulta``, disponible en todos los alcances).
RouteGroup = Literal["rag", "structured", "any"]
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
    - ``route_group``: grupo del gatekeeper (spec 046). Es un campo propio y no se deriva
      de ``evidence_role``: ``historia_carvajal`` y ``empresas_del_grupo`` son ``data`` pero
      responden preguntas documentales.
    """

    name: ToolName
    args_schema: type[BaseModel]
    description: str
    run: ToolRun
    status_template: StatusTemplate | None
    consumes_budget: bool = True
    evidence_role: EvidenceRole = "data"
    city_keys: tuple[str, ...] = ()
    route_group: RouteGroup = "structured"


TOOLS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name=TOOL_BUSCAR_CORPUS,
        args_schema=builtin.BuscarCorpusArgs,
        description=builtin.DESCRIPTIONS[TOOL_BUSCAR_CORPUS],
        run=builtin._buscar_corpus,
        status_template=status.BUILTIN_TEMPLATES[TOOL_BUSCAR_CORPUS],
        evidence_role="corpus",
        route_group="rag",
    ),
    ToolSpec(
        name=TOOL_BUSCAR_PRODUCTO,
        args_schema=builtin.BuscarProductoArgs,
        description=builtin.DESCRIPTIONS[TOOL_BUSCAR_PRODUCTO],
        run=builtin._buscar_producto,
        status_template=status.BUILTIN_TEMPLATES[TOOL_BUSCAR_PRODUCTO],
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_COTIZAR,
        args_schema=builtin.CotizarArgs,
        description=builtin.DESCRIPTIONS[TOOL_COTIZAR],
        run=builtin._cotizar,
        status_template=status.BUILTIN_TEMPLATES[TOOL_COTIZAR],
        city_keys=("city",),
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_BUSCAR_SEDES,
        args_schema=builtin.BuscarSedesArgs,
        description=builtin.DESCRIPTIONS[TOOL_BUSCAR_SEDES],
        run=builtin._buscar_sedes,
        status_template=status.BUILTIN_TEMPLATES[TOOL_BUSCAR_SEDES],
        city_keys=("ciudad",),
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_INDICADOR_FINANCIERO,
        args_schema=builtin.IndicadorFinancieroArgs,
        description=builtin.DESCRIPTIONS[TOOL_INDICADOR_FINANCIERO],
        run=builtin._indicador_financiero,
        status_template=status.BUILTIN_TEMPLATES[TOOL_INDICADOR_FINANCIERO],
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_SIN_CONSULTA,
        args_schema=builtin.SinConsultaArgs,
        description=builtin.DESCRIPTIONS[TOOL_SIN_CONSULTA],
        run=builtin._sin_consulta,
        status_template=None,
        consumes_budget=False,
        evidence_role="declaration",
        route_group="any",
    ),
    # Catálogo (spec 045, US3, T069): una fuente por dato, sin ciudad como argumento.
    ToolSpec(
        name=TOOL_FICHA_PRODUCTO,
        args_schema=ficha_producto.FichaProductoArgs,
        description=ficha_producto.DESCRIPTION,
        run=ficha_producto.run,
        status_template=ficha_producto.status,
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_COMPARAR_REFERENCIAS,
        args_schema=comparar_referencias.CompararReferenciasArgs,
        description=comparar_referencias.DESCRIPTION,
        run=comparar_referencias.run,
        status_template=comparar_referencias.status,
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_GUIA_MATERIALES,
        args_schema=guia_materiales.GuiaMaterialesArgs,
        description=guia_materiales.DESCRIPTION,
        run=guia_materiales.run,
        status_template=guia_materiales.status,
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_CONVERTIR_PRESENTACION,
        args_schema=convertir_presentacion.ConvertirPresentacionArgs,
        description=convertir_presentacion.DESCRIPTION,
        run=convertir_presentacion.run,
        status_template=convertir_presentacion.status,
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_CIUDADES_CON_COBERTURA,
        args_schema=ciudades_con_cobertura.CiudadesConCoberturaArgs,
        description=ciudades_con_cobertura.DESCRIPTION,
        run=ciudades_con_cobertura.run,
        status_template=ciudades_con_cobertura.status,
        route_group="structured",
    ),
    # Institucionales, cotizaciones guardadas y fecha (spec 045, US4, T081).
    # ``consultar_cotizacion`` solo lee cotizaciones de procedencia ``real`` del dueño;
    # ``preguntas_frecuentes`` es la única que llama a un LLM y verifica cada entrada
    # contra su pasaje (rol ``corpus``: sin entradas verificadas, abstención; R18).
    ToolSpec(
        name=TOOL_CONSULTAR_COTIZACION,
        args_schema=consultar_cotizacion.ConsultarCotizacionArgs,
        description=consultar_cotizacion.DESCRIPTION,
        run=consultar_cotizacion.run,
        status_template=consultar_cotizacion.status,
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_INDICADORES_DISPONIBLES,
        args_schema=indicadores_disponibles.IndicadoresDisponiblesArgs,
        description=indicadores_disponibles.DESCRIPTION,
        run=indicadores_disponibles.run,
        status_template=indicadores_disponibles.status,
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_HISTORIA_CARVAJAL,
        args_schema=historia_carvajal.HistoriaCarvajalArgs,
        description=historia_carvajal.DESCRIPTION,
        run=historia_carvajal.run,
        status_template=historia_carvajal.status,
        route_group="rag",
    ),
    ToolSpec(
        name=TOOL_EMPRESAS_DEL_GRUPO,
        args_schema=empresas_del_grupo.EmpresasDelGrupoArgs,
        description=empresas_del_grupo.DESCRIPTION,
        run=empresas_del_grupo.run,
        status_template=empresas_del_grupo.status,
        route_group="rag",
    ),
    ToolSpec(
        name=TOOL_PREGUNTAS_FRECUENTES,
        args_schema=preguntas_frecuentes.PreguntasFrecuentesArgs,
        description=preguntas_frecuentes.DESCRIPTION,
        run=preguntas_frecuentes.run,
        status_template=preguntas_frecuentes.status,
        evidence_role="corpus",
        route_group="rag",
    ),
    ToolSpec(
        name=TOOL_FECHA_ACTUAL,
        args_schema=fecha_actual.FechaActualArgs,
        description=fecha_actual.DESCRIPTION,
        run=fecha_actual.run,
        status_template=fecha_actual.status,
        route_group="structured",
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
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_LISTAR_MEMORIA,
        args_schema=listar_memoria.ListarMemoriaArgs,
        description=listar_memoria.DESCRIPTION,
        run=listar_memoria.run,
        status_template=listar_memoria.status,
        evidence_role="memory",
        route_group="structured",
    ),
    ToolSpec(
        name=TOOL_OLVIDAR_PREFERENCIA,
        args_schema=olvidar_preferencia.OlvidarPreferenciaArgs,
        description=olvidar_preferencia.DESCRIPTION,
        run=olvidar_preferencia.run,
        status_template=olvidar_preferencia.status,
        evidence_role="memory",
        route_group="structured",
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
ROUTE_GROUPS: Final[Mapping[str, RouteGroup]] = MappingProxyType(
    {spec.name: spec.route_group for spec in TOOLS}
)
# Consultas que el modelo ve en cada alcance del gatekeeper, en el orden de ``TOOLS``
# (data-model *Grupo de cada consulta*): ``direct`` no consulta nada.
TOOLS_BY_SCOPE: Final[Mapping[GateScope, tuple[str, ...]]] = MappingProxyType(
    {
        "all": TOOL_NAMES,
        "rag": tuple(spec.name for spec in TOOLS if spec.route_group in ("rag", "any")),
        "structured": tuple(
            spec.name for spec in TOOLS if spec.route_group in ("structured", "any")
        ),
        "direct": (),
    }
)

if len(_BY_NAME) != len(TOOLS):  # pragma: no cover - lo vigila tests/test_tool_registry.py
    raise RuntimeError("El registro de herramientas repite un nombre.")


def spec_for(name: str) -> ToolSpec | None:
    """Especificación registrada de ``name`` (``None`` si no existe)."""

    return _BY_NAME.get(name)
