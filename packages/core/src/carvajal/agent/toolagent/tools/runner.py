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
from langchain_core.tools import BaseTool, tool
from pydantic import ValidationError

from carvajal.agent.toolagent.state import (
    TOOL_SIN_CONSULTA,
    UNKNOWN_TOOL,
    ToolOutcome,
    ToolStatus,
    make_tool_outcome,
)
from carvajal.agent.toolagent.tools._base import (
    STATE_UPDATE_KEYS,
    ToolCallResult,
    ToolContext,
    ToolDeps,
    _Draft,
    _Sources,
    format_tool_content,
)
from carvajal.agent.toolagent.tools.registry import (
    BUDGET_FREE,
    CITY_KEYS,
    CORPUS_TOOLS,
    DECLARATION_TOOLS,
    EVIDENCE_ROLES,
    IMPLEMENTATIONS,
    MEMORY_TOOLS,
    STATUS_TEMPLATES,
    TOOL_DESCRIPTIONS,
    TOOL_NAMES,
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


# Argumentos inválidos que son un dato por pedir («0 vasos»): el reemplazo neutro pide la
# cantidad en vez de abstenerse con «No tengo un dato verificado».
# Solo ``quantity``: un error en ``cantidades`` (p. ej. cinco cantidades) no es que falte.
_ARG_FIELDS: dict[str, str] = {"quantity": "cantidad"}


def _missing_from(error: ValidationError) -> list[str]:
    found = []
    for item in error.errors():
        location = item.get("loc", ())
        field = _ARG_FIELDS.get(str(location[0])) if location else None
        if field and field not in found:
            found.append(field)
    return found


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
        # Los mismos argumentos que el ``ToolOutcome``: sin valores de memoria (FR-012).
        args=dict(outcome["args"]),
        payload=payload,
        content=format_tool_content(name, payload),
        outcome=outcome,
        sources=sources.entries,
        quotes=draft.quotes if draft.quotes is not None else [dict(q) for q in context.quotes],
        quote=draft.quote,
        context_update=draft.context_update,
        cards=draft.cards,
        state_update=_state_update(name, draft),
    )


def _state_update(name: str, draft: _Draft) -> dict[str, Any]:
    """Canales que la herramienta actualiza; ninguno si falló o si la clave no se permite."""

    if draft.status == "error" or not draft.state_update:
        return {}
    unknown = sorted(set(draft.state_update) - STATE_UPDATE_KEYS)
    if unknown:
        logger.warning("tool_agent.state_update_ignored", tool=name, keys=unknown)
    return {key: value for key, value in draft.state_update.items() if key in STATE_UPDATE_KEYS}


def _outcome(
    name: str, args: dict[str, Any], status: ToolStatus, ids: list[int], elapsed: int
) -> ToolOutcome:
    """``ToolOutcome`` validado; una herramienta desconocida se registra como ``UNKNOWN_TOOL``.

    El nombre inventado lo escribe el modelo y puede llevar datos de la persona: no llega
    al estado ni al monitoreo (FR-014). Las herramientas de memoria guardan solo la
    categoría como argumento (spec 045, FR-012 y FR-018): el canal ``results``, la traza,
    la evaluación y el monitoreo heredan la redacción, también con ``content=full``.
    """

    if name in MEMORY_TOOLS:
        category = args.get("categoria")
        args = {"categoria": category} if isinstance(category, str) else {}
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
    implementation = IMPLEMENTATIONS.get(name)
    if schema is None or implementation is None:
        return _Draft("error", "Herramienta desconocida; no se consultó nada."), raw_args
    try:
        validated = schema.model_validate(dict(args))
    except ValidationError as error:
        return _Draft("error", _validation_message(error), missing=_missing_from(error)), raw_args
    except TypeError:
        return _Draft("error", "Argumentos inválidos; no se consultó nada."), raw_args
    clean_args = validated.model_dump(mode="json", exclude_none=True)
    return implementation(validated, deps, context, sources), clean_args


# --- LangChain -----------------------------------------------------------------------------


def build_tools(deps: ToolDeps) -> list[BaseTool]:
    """Las herramientas del registro como ``BaseTool`` para ``bind_tools``, en su orden.

    Cada una se declara con ``@tool(name, description=..., args_schema=...)`` dentro de la
    fábrica (necesita ``deps``): la descripción va explícita para que el modelo nunca vea el
    docstring del esquema (FR-021). Invocadas sueltas corren con un ``ToolContext`` vacío y
    devuelven el contenido para el modelo; dentro del grafo, el nodo ``tools`` llama a
    :func:`run_tool` con el estado.
    """

    def make(name: str) -> BaseTool:
        @tool(name, description=TOOL_DESCRIPTIONS[name], args_schema=TOOL_SCHEMAS[name])
        def call(**kwargs: Any) -> str:
            return run_tool(name, kwargs, deps, ToolContext()).content

        return call

    return [make(name) for name in TOOL_NAMES]


__all__ = [
    "BUDGET_FREE",
    "CITY_KEYS",
    "CORPUS_TOOLS",
    "DECLARATION_TOOLS",
    "EVIDENCE_ROLES",
    "IMPLEMENTATIONS",
    "MEMORY_TOOLS",
    "STATUS_TEMPLATES",
    "TOOL_DESCRIPTIONS",
    "TOOL_NAMES",
    "TOOL_SCHEMAS",
    "build_tools",
    "call_fingerprint",
    "run_tool",
]
