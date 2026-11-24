"""Herramientas del agente con tool calling (spec 044; paquete desde la spec 045).

Contrato: ``specs/044-agente-tool-calling/contracts/agent-tools.md`` y
``specs/045-memoria-herramientas/contracts/agent-tools-045.md``. Hasta la 045 todo vivía en
``toolagent/tools.py``; el código se movió en bloque, sin reescribirlo (research R9):

- ``_base.py``: :class:`ToolDeps`, :class:`ToolContext`, :class:`ToolCallResult`, el
  borrador ``_Draft``, el registro de fuentes ``_Sources``, el formato del ``ToolMessage``
  (:func:`format_tool_content`, :func:`content_data`) y las funciones puras de
  cotizaciones (:func:`merge_quote`, :func:`quote_key`).
- ``builtin.py``: las seis herramientas de la 044 con sus esquemas y ayudantes compartidos.
- ``runner.py``: :func:`run_tool` (valida, ejecuta y nunca lanza), :func:`call_fingerprint`
  y :func:`build_tools` (``@tool`` para ``bind_tools``).
- ``registry.py``: :class:`ToolSpec` y la tupla ordenada :data:`TOOLS`, única fuente de
  verdad de nombres, esquemas, descripciones, líneas de estado, cupo, papel ante la
  compuerta y claves de ciudad (spec 045, research R9). Las vistas derivadas
  (:data:`TOOL_NAMES`, :data:`TOOL_SCHEMAS`, :data:`TOOL_DESCRIPTIONS`, ...) son de solo
  lectura.
- Un módulo por herramienta nueva de la 045 (``ficha_producto.py``, ...).

Diseño (sin cambios desde la 044):

- El nodo ``tools`` del grafo solo orquesta: arma el ``ToolContext`` desde el estado, llama
  a :func:`run_tool` y vuelca el resultado en los canales del estado.
- :func:`format_tool_content` (T032) encierra los datos en un bloque delimitado y
  neutralizado: el modelo recibe datos, nunca órdenes.
- :func:`merge_quote` y :func:`call_fingerprint` son funciones puras para el tope de
  cotizaciones, la deduplicación por huella y el reemplazo cuando solo cambia el domicilio.

Este módulo reexporta lo que antes se importaba de ``toolagent.tools``.
"""

from carvajal.agent.toolagent.tools._base import (
    DATA_CLOSE,
    DATA_OPEN,
    FaqSource,
    MemoryStore,
    QuoteLookup,
    QuoteMerge,
    ToolCallResult,
    ToolContext,
    ToolDeps,
    content_data,
    format_tool_content,
    last_answer,
    merge_quote,
    quote_key,
    seed_questions,
)
from carvajal.agent.toolagent.tools.builtin import (
    BuscarCorpusArgs,
    BuscarProductoArgs,
    BuscarSedesArgs,
    CotizarArgs,
    IndicadorFinancieroArgs,
    SinConsultaArgs,
)
from carvajal.agent.toolagent.tools.registry import (
    BUDGET_FREE,
    CITY_KEYS,
    CORPUS_TOOLS,
    DECLARATION_TOOLS,
    EVIDENCE_ROLES,
    MEMORY_TOOLS,
    STATUS_TEMPLATES,
    TOOL_DESCRIPTIONS,
    TOOL_NAMES,
    TOOL_SCHEMAS,
    TOOLS,
    EvidenceRole,
    ToolSpec,
    spec_for,
)
from carvajal.agent.toolagent.tools.registry import IMPLEMENTATIONS as _IMPLEMENTATIONS
from carvajal.agent.toolagent.tools.runner import build_tools, call_fingerprint, run_tool

__all__ = [
    "BUDGET_FREE",
    "CITY_KEYS",
    "CORPUS_TOOLS",
    "DATA_CLOSE",
    "DATA_OPEN",
    "DECLARATION_TOOLS",
    "EVIDENCE_ROLES",
    "MEMORY_TOOLS",
    "STATUS_TEMPLATES",
    "TOOLS",
    "TOOL_DESCRIPTIONS",
    "TOOL_NAMES",
    "TOOL_SCHEMAS",
    "_IMPLEMENTATIONS",
    "BuscarCorpusArgs",
    "BuscarProductoArgs",
    "BuscarSedesArgs",
    "CotizarArgs",
    "EvidenceRole",
    "FaqSource",
    "IndicadorFinancieroArgs",
    "MemoryStore",
    "QuoteLookup",
    "QuoteMerge",
    "SinConsultaArgs",
    "ToolCallResult",
    "ToolContext",
    "ToolDeps",
    "ToolSpec",
    "build_tools",
    "call_fingerprint",
    "content_data",
    "format_tool_content",
    "last_answer",
    "merge_quote",
    "quote_key",
    "run_tool",
    "seed_questions",
    "spec_for",
]
