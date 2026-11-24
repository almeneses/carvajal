"""Ejecución de las herramientas: :func:`run_tool`, huella y ``StructuredTool``.

:func:`run_tool` es el punto de entrada sin LangChain: valida los argumentos con el esquema
Pydantic de la herramienta, la ejecuta contra el estado relevante del turno
(:class:`ToolContext`) y devuelve un :class:`ToolCallResult`. Nunca lanza: una entrada
inválida o una excepción interna se convierten en ``status: error``. :func:`build_tools`
envuelve las mismas funciones para ``bind_tools`` y :func:`call_fingerprint` da la huella
de deduplicación con los argumentos validados.
"""

import json
import time
from collections.abc import Mapping
from typing import Any, cast

import structlog
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import ValidationError

from carvajal.agent.toolagent.state import (
    TOOL_NAMES,
    TOOL_SIN_CONSULTA,
    UNKNOWN_TOOL,
    ToolOutcome,
    ToolStatus,
    make_tool_outcome,
)
from carvajal.agent.toolagent.tools._base import (
    ToolCallResult,
    ToolContext,
    ToolDeps,
    _Draft,
    _Sources,
    format_tool_content,
)
from carvajal.agent.toolagent.tools.builtin import (
    _IMPLEMENTATIONS,
    TOOL_DESCRIPTIONS,
    TOOL_SCHEMAS,
)

logger = structlog.get_logger(__name__)

# Movido en bloque desde ``toolagent/tools.py`` (spec 045, T008; research R9): el código
# no se reescribió. Ver el docstring de ``toolagent/tools/__init__.py``.

MAX_ERROR_CHARS = 300

# --- Ayudantes de ejecución -----------------------------------------------------------------


def _validation_message(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ())) or "argumentos"
        parts.append(f"{location}: {item.get('msg', 'valor inválido')}")
    message = "Argumentos inválidos; no se consultó nada. " + "; ".join(parts)
    return message[:MAX_ERROR_CHARS]


def _json_safe(args: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(dict(args), ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {}


# --- Huella de deduplicación ----------------------------------------------------------------


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        number = float(value)
        return int(number) if number.is_integer() else number
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_normalize_value(item) for item in value]
    return str(value)


def call_fingerprint(name: str, args: Mapping[str, Any]) -> str:
    """Huella ``nombre+args`` normalizados para ``ToolAgentState.seen_calls``.

    Si los argumentos validan contra el esquema, se usan los validados (con sus valores
    por defecto); se descartan los nulos, se ordenan las claves y se comparan cadenas sin
    mayúsculas ni espacios extra. Es JSON-serializable (una cadena).
    """

    schema = TOOL_SCHEMAS.get(name)
    values: Mapping[str, Any] = args
    if schema is not None:
        try:
            values = schema.model_validate(dict(args)).model_dump(mode="json")
        except ValidationError:
            values = args
    normalized = {
        str(key): _normalize_value(value) for key, value in values.items() if value is not None
    }
    return f"{name}:{json.dumps(normalized, sort_keys=True, ensure_ascii=False)}"


# --- Punto de entrada ----------------------------------------------------------------------


def run_tool(
    name: str, args: Mapping[str, Any], deps: ToolDeps, context: ToolContext
) -> ToolCallResult:
    """Valida y ejecuta una herramienta sobre el estado del turno; nunca lanza.

    Devuelve lo nuevo para el estado: el registro de fuentes y la lista de cotizaciones
    completos tras la llamada, el ``ToolOutcome`` y el contenido para el ``ToolMessage``.
    """

    started = time.perf_counter()
    sources = _Sources(context.sources)
    outcome_args = _json_safe(args) if isinstance(args, Mapping) else {}
    try:
        draft, outcome_args = _execute(name, args, deps, context, sources, outcome_args)
    except Exception as error:  # una herramienta nunca tumba el turno
        logger.warning("tool_agent.tool_failed", tool=name, error=repr(error))
        sources = _Sources(context.sources)
        draft = _Draft("error", "La herramienta falló por un error interno; no hay datos.")
    if draft.status == "error":
        # Un error no deja fuentes: el registro vuelve al estado previo a la llamada.
        sources = _Sources(context.sources)
    payload = {
        "status": draft.status,
        "message": draft.message,
        "missing": list(draft.missing),
        **draft.data,
    }
    elapsed = int((time.perf_counter() - started) * 1000)
    outcome = _outcome(name, outcome_args, draft.status, sources.ids, elapsed)
    return ToolCallResult(
        tool=name,
        args=outcome_args,
        payload=payload,
        content=format_tool_content(name, payload),
        outcome=outcome,
        sources=sources.entries,
        quotes=draft.quotes if draft.quotes is not None else [dict(q) for q in context.quotes],
        quote=draft.quote,
        context_update=draft.context_update,
        cards=draft.cards,
    )


def _outcome(
    name: str, args: dict[str, Any], status: ToolStatus, ids: list[int], elapsed: int
) -> ToolOutcome:
    """``ToolOutcome`` validado; una herramienta desconocida se registra como ``UNKNOWN_TOOL``.

    El nombre inventado lo escribe el modelo y puede llevar datos de la persona: no llega
    al estado ni al monitoreo (FR-014).
    """

    if name in TOOL_NAMES:
        return make_tool_outcome(
            tool=name, args=args, status=status, source_ids=ids, elapsed_ms=elapsed
        )
    known = make_tool_outcome(
        tool=TOOL_SIN_CONSULTA, args=args, status=status, source_ids=(), elapsed_ms=elapsed
    )
    return cast(ToolOutcome, {**known, "tool": UNKNOWN_TOOL})


def _execute(
    name: str,
    args: Mapping[str, Any],
    deps: ToolDeps,
    context: ToolContext,
    sources: _Sources,
    raw_args: dict[str, Any],
) -> tuple[_Draft, dict[str, Any]]:
    schema = TOOL_SCHEMAS.get(name)
    implementation = _IMPLEMENTATIONS.get(name)
    if schema is None or implementation is None:
        return _Draft("error", "Herramienta desconocida; no se consultó nada."), raw_args
    try:
        validated = schema.model_validate(dict(args))
    except ValidationError as error:
        return _Draft("error", _validation_message(error)), raw_args
    except TypeError:
        return _Draft("error", "Argumentos inválidos; no se consultó nada."), raw_args
    clean_args = validated.model_dump(mode="json", exclude_none=True)
    return implementation(validated, deps, context, sources), clean_args


# --- LangChain -----------------------------------------------------------------------------


def build_tools(deps: ToolDeps) -> list[BaseTool]:
    """Las seis herramientas como ``StructuredTool`` para ``bind_tools``.

    Invocadas sueltas corren con un ``ToolContext`` vacío y devuelven el contenido para el
    modelo; dentro del grafo, el nodo ``tools`` llama a :func:`run_tool` con el estado.
    """

    def make(name: str) -> BaseTool:
        def call(**kwargs: Any) -> str:
            return run_tool(name, kwargs, deps, ToolContext()).content

        return StructuredTool.from_function(
            func=call,
            name=name,
            description=TOOL_DESCRIPTIONS[name],
            args_schema=TOOL_SCHEMAS[name],
        )

    return [make(name) for name in TOOL_NAMES]
