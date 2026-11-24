"""Estado del grafo ``tools`` (spec 044) y forma de ``ToolOutcome``.

Todo el estado lo persiste el checkpointer de Postgres, así que cada canal es
JSON-serializable: tipos básicos, ``list``, ``dict`` y mensajes de LangChain. No se
guardan ``set`` ni clases propias; ``ToolOutcome`` es un ``TypedDict`` (un ``dict`` en
tiempo de ejecución) construido y validado con :func:`make_tool_outcome`.
"""

import json
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Final, Literal, TypedDict, cast, get_args

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from carvajal.agent.state import Turn

__all__ = [
    "MODE_PATHS",
    "TOOL_BUSCAR_CORPUS",
    "TOOL_BUSCAR_PRODUCTO",
    "TOOL_BUSCAR_SEDES",
    "TOOL_COTIZAR",
    "TOOL_INDICADOR_FINANCIERO",
    "TOOL_NAMES",
    "TOOL_SIN_CONSULTA",
    "TOOL_STATUSES",
    "UNKNOWN_TOOL",
    "ModePath",
    "ToolAgentState",
    "ToolName",
    "ToolOutcome",
    "ToolStatus",
    "make_tool_outcome",
    "validate_question",
]

ToolName = Literal[
    "buscar_corpus",
    "buscar_producto",
    "cotizar",
    "buscar_sedes",
    "indicador_financiero",
    "sin_consulta",
]
ToolStatus = Literal["ok", "empty", "needs_input", "error"]
ModePath = Literal["shortcut", "agent"]

# Nombres de las seis herramientas de contracts/agent-tools.md.
TOOL_BUSCAR_CORPUS: Final = "buscar_corpus"
TOOL_BUSCAR_PRODUCTO: Final = "buscar_producto"
TOOL_COTIZAR: Final = "cotizar"
TOOL_BUSCAR_SEDES: Final = "buscar_sedes"
TOOL_INDICADOR_FINANCIERO: Final = "indicador_financiero"
TOOL_SIN_CONSULTA: Final = "sin_consulta"

TOOL_NAMES: Final[tuple[str, ...]] = get_args(ToolName)
# Nombre con el que se registra una llamada a una herramienta que no existe: el nombre que
# escribió el modelo puede llevar datos de la persona y no se conserva (FR-014).
UNKNOWN_TOOL: Final = "desconocida"
TOOL_STATUSES: Final[tuple[str, ...]] = get_args(ToolStatus)
MODE_PATHS: Final[tuple[str, ...]] = get_args(ModePath)


class ToolOutcome(TypedDict):
    """Resultado serializado de una llamada a herramienta, guardado en ``results``.

    ``empty`` significa sin evidencia; ``needs_input``, que falta un dato (FR-005).
    ``source_ids`` son los números ``[n]`` asignados en el registro de fuentes y
    ``elapsed_ms`` alimenta el monitoreo.
    """

    tool: ToolName
    args: dict[str, Any]
    status: ToolStatus
    source_ids: list[int]
    elapsed_ms: int


class ToolAgentState(TypedDict, total=False):
    """Canales del grafo ``tools`` que persiste el checkpointer.

    ``question`` nunca está vacía: el servicio la normaliza con
    :func:`validate_question` antes de invocar el grafo. ``messages`` acumula con el
    reductor ``add_messages`` (sistema, historial, pregunta y pasos del turno).
    ``seen_calls`` es una lista (no ``set``) de huellas ``nombre+args`` normalizados.
    ``results`` guarda un :class:`ToolOutcome` por herramienta, en orden.
    ``shortcut_intent`` es la intención que decidió el nodo ``shortcut`` (``greeting`` o
    ``memory_write``; vacía si el turno va al agente): ``direct`` la lee en vez de
    recalcularla. ``recall`` la reinicia en cada turno, porque el checkpoint persiste.
    """

    question: str
    owner_key: str
    thread_id: str
    seed_turns: list[Turn]
    messages: Annotated[list[BaseMessage], add_messages]
    preferences: list[str]
    commercial_context: dict[str, Any]
    tool_calls_used: int
    seen_calls: list[str]
    sources: list[dict[str, Any]]
    results: list[ToolOutcome]
    quotes: list[dict[str, Any]]
    cards: dict[str, Any]
    mode_path: ModePath
    shortcut_intent: str
    missing: list[str]


def validate_question(question: str) -> str:
    """Devuelve la pregunta sin espacios en los extremos; rechaza la vacía."""
    cleaned = question.strip() if isinstance(question, str) else ""
    if not cleaned:
        raise ValueError("La pregunta del turno no puede estar vacía.")
    return cleaned


def _is_int(value: object) -> bool:
    """Entero estricto: ``bool`` no cuenta como número de cita ni como duración."""
    return isinstance(value, int) and not isinstance(value, bool)


def make_tool_outcome(
    *,
    tool: str,
    args: Mapping[str, Any],
    status: str,
    source_ids: Iterable[int] = (),
    elapsed_ms: int = 0,
) -> ToolOutcome:
    """Construye un :class:`ToolOutcome` validado como ``dict`` listo para el estado.

    Copia en profundidad ``args`` (ida y vuelta por JSON) y ``source_ids`` para que el
    estado no comparta referencias mutables con quien llama y sea serializable por el
    checkpointer. Lanza ``ValueError`` ante una herramienta o estado desconocidos,
    ``args`` que no sea un mapeo JSON-serializable, ``source_ids`` que no sean enteros
    positivos o ``elapsed_ms`` negativo o no entero.
    """
    if tool not in TOOL_NAMES:
        raise ValueError(f"herramienta desconocida: {tool!r}")
    if status not in TOOL_STATUSES:
        raise ValueError(f"estado desconocido: {status!r}")
    if not isinstance(args, Mapping):
        raise ValueError("args debe ser un dict")
    try:
        copied_args = json.loads(json.dumps(dict(args), ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise ValueError("args debe ser JSON-serializable") from error
    try:
        ids = list(source_ids)
    except TypeError as error:
        raise ValueError("source_ids debe ser iterable") from error
    if not all(_is_int(n) and n >= 1 for n in ids):
        raise ValueError("source_ids debe contener solo enteros positivos (citas desde 1)")
    if not _is_int(elapsed_ms) or elapsed_ms < 0:
        raise ValueError("elapsed_ms debe ser un entero no negativo")
    # ``tool`` y ``status`` ya se validaron contra los literales: el ``cast`` solo lo
    # declara al verificador de tipos.
    return {
        "tool": cast(ToolName, tool),
        "args": copied_args,
        "status": cast(ToolStatus, status),
        "source_ids": ids,
        "elapsed_ms": elapsed_ms,
    }
