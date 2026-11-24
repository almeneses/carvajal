"""Evaluación comparativa del agente: modo ``routes`` frente a modo ``tools`` (spec 044, US5).

``carvajal eval-agent`` corre turnos completos en proceso, sin HTTP ni Postgres: cada modo
tiene su ``InMemorySaver`` y su ``InMemoryStore``, y ambos comparten el mismo índice
vectorial, el mismo embedder y el mismo catálogo. Cada ítem va en su propio
``thread_id``; los turnos anteriores al último se ejecutan en el mismo hilo para sembrar el
historial y el contexto comercial, y solo el último se puntúa (``contracts/eval-agent.md``).

Las verificaciones son deterministas (research R11):

- por ítem, las claves de ``expect`` (:data:`EXPECT_KEYS`); una clave ausente no se mide;
- en todos los ítems, SC-001: toda cifra, precio o fecha de la respuesta aparece en algún
  resultado de herramienta o fuente del turno (:func:`unsupported_figures`). Los números de
  las preguntas del ítem (y del historial sembrado) no cuentan como afirmación nueva. El
  respaldo es simétrico: en ``routes`` son los fragmentos del corpus, el resultado
  comercial o del indicador y las fuentes, nunca el prompt compuesto ni sus instrucciones;
- un turno atendido por el respaldo determinista no cuenta como resuelto (``sin_respaldo``).

En modo ``routes`` no hay herramientas: las que «usó» el turno se derivan de la ruta
(:func:`routes_tools`): ``rag → buscar_corpus``, ``tool → indicador_financiero``,
``commercial → buscar_producto | cotizar | buscar_sedes`` según lo que trae el resultado
comercial, y ``direct`` → atajo (saludo o preferencia) o ``sin_consulta``.

Los drivers reales viven en ``apps/api`` (``GraphAgentRunner``, ``ToolAgentRunner``): se
importan de forma diferida, solo al construir el runtime de evaluación, porque la
evaluación necesita el checkout del repositorio y no forma parte del paquete instalado.
:func:`ensure_apps_importable` pone la raíz del checkout en ``sys.path``, porque el script
de consola ``carvajal`` no la añade.
"""

import importlib.util
import json
import math
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final, Protocol

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

from carvajal.agent.state import Turn
from carvajal.agent.toolagent import fallback
from carvajal.agent.toolagent.graph import SHORTCUT_INTENTS
from carvajal.agent.toolagent.state import (
    TOOL_BUSCAR_CORPUS,
    TOOL_BUSCAR_PRODUCTO,
    TOOL_BUSCAR_SEDES,
    TOOL_COTIZAR,
    TOOL_INDICADOR_FINANCIERO,
    TOOL_SIN_CONSULTA,
)
from carvajal.agent.toolagent.tools import DECLARATION_TOOLS, TOOL_NAMES, content_data
from carvajal.prompts import ABSENCE_POLICY, NO_CONTEXT_ANSWER

__all__ = [
    "EXPECT_KEYS",
    "MODES",
    "ROUTE_TOOLS",
    "CombinedUsage",
    "EvalItem",
    "EvalRuntime",
    "ItemResult",
    "ModeDriver",
    "ModeReport",
    "QuestionSet",
    "RoutesDriver",
    "ToolCall",
    "ToolsDriver",
    "TurnObservation",
    "UsageRecorder",
    "build_runtime",
    "check_item",
    "detect_language",
    "ensure_apps_importable",
    "evaluate_mode",
    "experiment_rows",
    "fallback_reason",
    "is_abstention",
    "load_question_set",
    "render_report",
    "results_payload",
    "routes_tools",
    "select_items",
    "unsupported_figures",
]

logger = structlog.get_logger(__name__)

MODES: Final[tuple[str, ...]] = ("routes", "tools")

# Claves de ``expect`` de contracts/eval-agent.md; una clave desconocida es un error del
# conjunto, no algo que se ignore en silencio.
EXPECT_KEYS: Final = frozenset(
    {
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
)
# Verificación global de SC-001, presente en todos los ítems.
CHECK_SOURCED: Final = "datos_con_fuente"
# Un turno atendido por el respaldo determinista no cuenta como resuelto por el agente.
CHECK_FALLBACK: Final = "sin_respaldo"
# Motivos de respaldo que indican un proveedor caído, lento o mal configurado.
DEGRADED_REASONS: Final = frozenset({"provider", "timeout"})
# Comodín de ``asks_for`` en el conjunto de enrutamiento: pide cualquier dato.
ANY_DATUM: Final = "*"

# Mapeo de contracts/eval-agent.md para el modo ``routes`` y para convertir
# ``routing_questions.json`` en expectativas de herramientas.
ROUTE_TOOLS: Final[dict[str, tuple[str, ...]]] = {
    "rag": (TOOL_BUSCAR_CORPUS,),
    "tool": (TOOL_INDICADOR_FINANCIERO,),
    "commercial": (TOOL_BUSCAR_PRODUCTO, TOOL_COTIZAR, TOOL_BUSCAR_SEDES),
}
# El grafo ``routes`` solo conoce herramientas de la 044; si el registro dejara de tener
# alguna, la conversión de ``routing_questions.json`` no podría esperarla (spec 045, T015).
if not {name for names in ROUTE_TOOLS.values() for name in names} <= set(TOOL_NAMES):
    raise RuntimeError("ROUTE_TOOLS nombra herramientas fuera del registro.")
# Intenciones de la ruta directa que piden un dato a la persona.
_ASKING_INTENTS: Final = frozenset({"commercial_clarify", "missing_period"})


# --- Conjunto de preguntas -------------------------------------------------------------------


@dataclass(frozen=True)
class EvalItem:
    """Un ítem del conjunto: turnos a ejecutar (el último se evalúa) y lo esperado.

    ``history`` son turnos ya respondidos que se siembran sin ejecutarse (el historial de
    ``routing_questions.json``); ``turns`` se ejecutan en orden en el mismo hilo, salvo que
    ``new_thread_before`` nombre el índice (desde 0) de un turno: antes de ese turno se abre
    otra conversación del mismo dueño (``thread_id`` nuevo, mismo ``owner_key``, sin
    historial), como la persona que vuelve otro día (spec 045, T003).
    """

    id: str
    group: str
    turns: tuple[str, ...]
    expect: Mapping[str, Any]
    history: tuple[Turn, ...] = ()
    new_thread_before: tuple[int, ...] = ()

    @property
    def question(self) -> str:
        """Pregunta evaluada: el último turno."""

        return self.turns[-1]

    @property
    def asked(self) -> tuple[str, ...]:
        """Todo lo que preguntó la persona (historial sembrado y turnos): sus cifras no son
        afirmaciones nuevas de la respuesta."""

        return (*(turn["question"] for turn in self.history), *self.turns)


@dataclass(frozen=True)
class QuestionSet:
    """Conjunto cargado con sus metadatos (origen y congelación, FR-016)."""

    path: Path
    items: tuple[EvalItem, ...]
    version: int | None = None
    frozen_at: str = ""
    origin: str = ""
    description: str = ""


def _check_expect(item_id: str, expect: Mapping[str, Any]) -> None:
    unknown = sorted(set(expect) - EXPECT_KEYS)
    if unknown:
        raise ValueError(f"El ítem {item_id} trae claves de expect desconocidas: {unknown}")


def _routing_expect(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Expectativas de un ítem de ``routing_questions.json`` (ruta esperada → herramientas)."""

    route = str(raw.get("route", ""))
    intent = str(raw.get("intent", ""))
    expect: dict[str, Any] = {"language": "es"}
    if route in ROUTE_TOOLS:
        expect["tools_any"] = [list(ROUTE_TOOLS[route])]
    elif intent in SHORTCUT_INTENTS:
        expect["shortcut"] = True
    elif intent == "missing_period":
        expect["asks_for"] = ["periodo"]
        expect["no_tool_allowed"] = True
    elif intent == "commercial_clarify":
        # Lo que pide DIRECT_INSTRUCTIONS: temperatura o uso, sin cotizar a ciegas.
        expect["asks_for"] = ["temperatura", "uso"]
        expect["tools_none"] = [TOOL_COTIZAR]
        expect["no_tool_allowed"] = True
    else:
        # Otra respuesta directa: basta con pedir un dato conocido.
        expect["asks_for"] = [ANY_DATUM]
        expect["no_tool_allowed"] = True
    forbidden = (raw.get("constraints") or {}).get("forbidden_materials")
    if forbidden:
        expect["forbidden_materials"] = list(forbidden)
    return expect


def _new_threads(item_id: str, raw: Any, turns: int) -> tuple[int, ...]:
    """Índices de ``new_thread_before``: enteros entre 1 y el último turno, sin repetir."""

    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(
        isinstance(index, int) and not isinstance(index, bool) for index in raw
    ):
        raise ValueError(f"El ítem {item_id} trae un new_thread_before que no es una lista.")
    if any(index < 1 or index >= turns for index in raw) or len(set(raw)) != len(raw):
        raise ValueError(
            f"El ítem {item_id} trae un new_thread_before fuera de 1..{turns - 1} o repetido."
        )
    return tuple(sorted(raw))


def _item(raw: Mapping[str, Any]) -> EvalItem:
    item_id = str(raw["id"])
    if "question" in raw:  # formato de routing_questions.json
        history = tuple(
            Turn(
                question=str(turn.get("question", "")),
                answer=str(turn.get("answer", "")),
                incomplete=bool(turn.get("incomplete", False)),
            )
            for turn in raw.get("turns") or []
        )
        return EvalItem(
            id=item_id,
            group=str(raw.get("group", "")),
            turns=(str(raw["question"]),),
            expect=_routing_expect(raw),
            history=history,
        )
    turns = tuple(str(turn) for turn in raw.get("turns") or [])
    if not turns:
        raise ValueError(f"El ítem {item_id} no tiene turnos.")
    expect = dict(raw.get("expect") or {})
    _check_expect(item_id, expect)
    return EvalItem(
        id=item_id,
        group=str(raw.get("group", "")),
        turns=turns,
        expect=expect,
        new_thread_before=_new_threads(item_id, raw.get("new_thread_before"), len(turns)),
    )


def load_question_set(path: Path) -> QuestionSet:
    """Carga el conjunto de generalización o el de enrutamiento, en el orden del archivo."""

    data = json.loads(path.read_text(encoding="utf-8"))
    items = tuple(_item(raw) for raw in data["items"])
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"El conjunto {path} repite identificadores.")
    version = data.get("version")
    return QuestionSet(
        path=path,
        items=items,
        version=int(version) if isinstance(version, int) else None,
        frozen_at=str(data.get("frozen_at") or ""),
        origin=str(data.get("origin") or ""),
        description=str(data.get("description") or ""),
    )


def select_items(
    items: Sequence[EvalItem], *, ids: Sequence[str] = (), limit: int | None = None
) -> list[EvalItem]:
    """Corrida parcial: los ``ids`` pedidos (en el orden del conjunto) y como mucho ``limit``.

    Raises:
        ValueError: si algún identificador no existe en el conjunto.
    """

    selected = list(items)
    if ids:
        wanted = set(ids)
        missing = sorted(wanted - {item.id for item in items})
        if missing:
            raise ValueError(f"Identificadores inexistentes en el conjunto: {missing}")
        selected = [item for item in selected if item.id in wanted]
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit debe ser un entero positivo.")
        selected = selected[:limit]
    return selected


# --- Observación de un turno -----------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """Una herramienta del turno (real en ``tools``, derivada de la ruta en ``routes``)."""

    name: str
    status: str = "ok"
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnObservation:
    """Lo que produjo un turno, con lo necesario para puntuarlo.

    - ``backing``: textos que respaldan cifras (resultados de herramientas y fuentes).
    - ``missing``: lo que el turno dejó pendiente, informativo y distinto por modo
      (pendientes internos del resultado comercial en ``routes``, llamadas omitidas por el
      límite en ``tools``). Ninguna verificación lo usa.
    - ``requested``: datos que el turno pidió de forma estructurada, con el mismo criterio
      en ambos modos: ``needs_input`` de una herramienta (cotización por aclarar, período
      del indicador, temperatura) y el ``dato`` de ``sin_consulta(pedir_dato)``.
    - ``quotes``: cotizaciones mostradas como tarjeta (el ``quoted`` del motor).
    - ``path``: ``agent``, ``shortcut`` o ``fallback`` en ``tools``; en ``routes``, la ruta
      (``shortcut`` para el saludo y la preferencia declarada).
    - ``usage``: tokens del turno; ``None`` si el proveedor no los publica.
    - Latencias en segundos desde el envío: primer estado, primer texto y fin.
    """

    answer: str
    tools: tuple[ToolCall, ...] = ()
    sources: tuple[Mapping[str, Any], ...] = ()
    products: Mapping[str, Any] = field(default_factory=dict)
    quotes: tuple[Mapping[str, Any], ...] = ()
    backing: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    requested: tuple[str, ...] = ()
    abstained: bool = False
    path: str = "agent"
    usage: Mapping[str, int] | None = None
    first_status_s: float | None = None
    first_text_s: float | None = None
    total_s: float = 0.0
    fallback_reason: str = ""

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.tools]

    @property
    def shortcut(self) -> bool:
        return self.path == "shortcut"


def _plain(value: Any) -> Any:
    """Convierte dataclasses (``Source``) a ``dict`` para serializar."""

    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _source_texts(sources: Iterable[Mapping[str, Any]]) -> list[str]:
    """Textos citables de las fuentes (sin la URL: sus dígitos no respaldan cifras)."""

    texts: list[str] = []
    for source in sources:
        texts.extend(str(source.get(key) or "") for key in ("excerpt", "title", "section", "slug"))
    return [text for text in texts if text]


# Fuentes que no son del corpus documental: catálogo, precios, canales e indicadores.
_NON_CORPUS_PREFIXES: Final = ("catalogo:", "precio:", "canal:", "tool:")
_CATALOG_SECTION: Final = "catalogo_empaques"


def _corpus_sources(sources: Iterable[Any]) -> list[Mapping[str, Any]]:
    """Fuentes del corpus documental (no del catálogo comercial)."""

    found: list[Mapping[str, Any]] = []
    for raw in sources:
        source = _plain(raw)
        if not isinstance(source, Mapping):
            continue
        chunk_id = str(source.get("chunk_id") or "")
        if source.get("section") == _CATALOG_SECTION or chunk_id.startswith(_NON_CORPUS_PREFIXES):
            continue
        found.append(source)
    return found


def _shown_quotes(products: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Cotizaciones con precio mostradas como tarjeta (``quotes`` o, si no, ``quote``)."""

    quotes = products.get("quotes")
    candidates = list(quotes) if isinstance(quotes, list) and quotes else [products.get("quote")]
    return [
        quote
        for quote in candidates
        if isinstance(quote, Mapping) and quote.get("kind", "cotizacion") == "cotizacion"
    ]


# Bloques de datos del corpus dentro del prompt (``ask._build_prompt``): el único texto del
# prompt que es resultado de una consulta. Las instrucciones, el historial, la pregunta
# anterior y las preferencias no respaldan cifras (igual que en ``tools``).
_CONTEXT_BLOCK: Final = re.compile(r"<contexto>(.*?)</contexto>", re.DOTALL)


def _routes_backing(turn: Any, tool_result: Mapping[str, Any]) -> tuple[str, ...]:
    """Respaldo del turno ``routes`` con el mismo criterio que ``tools``.

    Fragmentos del corpus recuperados, resultado comercial o del indicador y fuentes; nunca
    el prompt compuesto.
    """

    sources = [_plain(source) for source in getattr(turn, "sources", ()) or ()]
    products = getattr(turn, "products", None) or {}
    return tuple(
        text
        for text in (
            *_CONTEXT_BLOCK.findall(getattr(turn, "prompt", None) or ""),
            *_source_texts(sources),
            _dumps(dict(products)) if products else "",
            _dumps(dict(tool_result)) if tool_result else "",
        )
        if text
    )


def routes_tools(turn: Any) -> tuple[tuple[ToolCall, ...], bool]:
    """Herramientas equivalentes a la ruta de un turno ``routes`` y si fue atajo.

    ``turn`` es un ``AgentTurn`` (``route``, ``intent``, ``prompt``, ``sources``,
    ``products``). La ruta comercial ejecuta una secuencia fija; cuenta como
    ``buscar_producto`` siempre, como ``cotizar`` si el resultado trae una cotización (o su
    aclaración) y como ``buscar_sedes`` si trae sedes.
    """

    route = str(getattr(turn, "route", ""))
    intent = str(getattr(turn, "intent", ""))
    sources = tuple(getattr(turn, "sources", ()) or ())
    products: Mapping[str, Any] = getattr(turn, "products", None) or {}
    if route == "rag":
        found = getattr(turn, "prompt", None) is not None and bool(sources)
        return (ToolCall(TOOL_BUSCAR_CORPUS, "ok" if found else "empty"),), False
    if route == "tool":
        return (ToolCall(TOOL_INDICADOR_FINANCIERO, "ok" if sources else "empty"),), False
    if route == "commercial":
        found = bool(products.get("options") or products.get("ficha") or products.get("channel"))
        calls = [ToolCall(TOOL_BUSCAR_PRODUCTO, "ok" if found else "empty")]
        quote = products.get("quote")
        if isinstance(quote, Mapping):
            kind = quote.get("kind")
            status = "ok" if kind == "cotizacion" else "needs_input" if kind else "empty"
            calls.append(ToolCall(TOOL_COTIZAR, status))
        if products.get("sites"):
            calls.append(ToolCall(TOOL_BUSCAR_SEDES, "ok"))
        if intent == "commercial_mixed":
            # La parte documental solo cuenta si trajo fuentes del corpus: si la recuperación
            # falló o vino vacía, el grafo sigue solo con la parte comercial.
            corpus = "ok" if _corpus_sources(sources) else "empty"
            calls.append(ToolCall(TOOL_BUSCAR_CORPUS, corpus))
        return tuple(calls), False
    if intent in SHORTCUT_INTENTS:
        return (), True
    motivo = "pedir_dato" if intent in _ASKING_INTENTS else "conversacion"
    return (ToolCall(TOOL_SIN_CONSULTA, "ok", {"motivo": motivo}),), False


def _routes_requested(turn: Any) -> list[str]:
    """Datos pedidos de forma estructurada en ``routes``: los equivalentes de ``needs_input``.

    La aclaración de la cotización (``cotizar`` con ``needs_input`` en ``tools``) y el
    período que falta (``indicador_financiero`` con ``needs_input``). Los ``pending`` del
    resultado comercial son notas internas, no un pedido a la persona.
    """

    products: Mapping[str, Any] = getattr(turn, "products", None) or {}
    requested: list[str] = []
    quote = products.get("quote")
    if isinstance(quote, Mapping) and quote.get("kind") == "aclaracion":
        requested.extend(str(item) for item in quote.get("missing") or [])
    if str(getattr(turn, "intent", "")) == "missing_period":
        requested.append("periodo")
    return requested


def _routes_missing(turn: Any) -> list[str]:
    products: Mapping[str, Any] = getattr(turn, "products", None) or {}
    return [*(str(item) for item in products.get("pending") or []), *_routes_requested(turn)]


def routes_observation(
    turn: Any, answer: str, *, tool_result: Mapping[str, Any] | None = None
) -> TurnObservation:
    """Observación de un turno ``routes`` (sin tiempos ni uso), como la arma el driver.

    ``turn`` es el ``AgentTurn`` del grafo y ``tool_result``, el resultado del indicador
    (ruta ``tool``). Abstención estructural: sin contexto (RAG) o indicador sin registro,
    como la compuerta de ``tools``.
    """

    tools, shortcut = routes_tools(turn)
    products = dict(getattr(turn, "products", None) or {})
    sources = tuple(_plain(source) for source in getattr(turn, "sources", ()) or ())
    result = dict(tool_result or {})
    abstained = getattr(turn, "prompt", None) is None or (bool(result) and not result.get("found"))
    return TurnObservation(
        answer=answer,
        tools=tools,
        sources=sources,
        products=products,
        quotes=tuple(_shown_quotes(products)),
        backing=_routes_backing(turn, result),
        missing=tuple(_routes_missing(turn)),
        requested=tuple(_routes_requested(turn)),
        abstained=abstained,
        path="shortcut" if shortcut else str(getattr(turn, "route", "")),
    )


# --- Uso de tokens del proveedor de texto ----------------------------------------------------


class UsageProbe(Protocol):
    """Fuente del uso de tokens de un turno ``routes``."""

    def reset(self) -> None: ...

    def take(self) -> dict[str, int] | None: ...


class UsageRecorder:
    """Envuelve un chat de LangChain y acumula ``usage_metadata`` de lo que devuelve.

    ``DeepSeekProvider`` registra el uso en el log pero no lo devuelve; para la evaluación
    se construye el mismo cliente con ``stream_usage=True`` y se lee aquí.
    """

    def __init__(self, chat: Any) -> None:
        self._chat = chat
        self._usage: dict[str, int] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)

    def reset(self) -> None:
        self._usage = {}

    def take(self) -> dict[str, int] | None:
        usage, self._usage = self._usage, {}
        return usage or None

    def _add(self, message: Any) -> None:
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, Mapping):
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    self._usage[key] = self._usage.get(key, 0) + value

    def invoke(self, input: Sequence[BaseMessage]) -> BaseMessage:
        message = self._chat.invoke(input)
        self._add(message)
        return message

    def stream(self, input: Sequence[BaseMessage]) -> Iterator[BaseMessage]:
        for chunk in self._chat.stream(input):
            self._add(chunk)
            yield chunk


class CombinedUsage:
    """Suma el uso de varias sondas (proveedor de texto y enrutador LLM de ``routes``).

    Si alguna parte que consume tokens no se puede medir (``None``), el total es
    desconocido: ``take`` devuelve ``None`` y el informe dice «sin dato» en vez de mostrar
    un costo menor que el real.
    """

    def __init__(self, *probes: UsageProbe | None) -> None:
        self._probes = probes

    def reset(self) -> None:
        for probe in self._probes:
            if probe is not None:
                probe.reset()

    def take(self) -> dict[str, int] | None:
        parts = [probe.take() if probe is not None else None for probe in self._probes]
        if any(part is None for part in parts):
            return None
        total: dict[str, int] = {}
        for part in parts:
            for key, value in (part or {}).items():
                total[key] = total.get(key, 0) + value
        return total


# --- Drivers -------------------------------------------------------------------------------


class ModeDriver(Protocol):
    """Ejecuta un turno completo de un modo y devuelve su observación."""

    mode: str

    def run_turn(
        self, *, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Turn]
    ) -> TurnObservation: ...


class RoutesDriver:
    """Modo ``routes``: ``GraphAgentRunner.prepare`` y el proveedor de texto, como el chat.

    Igual que ``ChatService``: sin ``prompt`` responde ``NO_CONTEXT_ANSWER``; si el
    streaming falla o termina vacío antes de producir texto, pide la respuesta completa.
    """

    mode = "routes"

    def __init__(
        self,
        runner: Any,
        llm: Any,
        *,
        usage: UsageProbe | None = None,
        graph: Any = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._runner = runner
        self._llm = llm
        self._usage = usage
        self._graph = graph
        self._clock = clock

    def _tool_result(self, turn: Any, thread_id: str) -> dict[str, Any]:
        """Resultado del indicador (ruta ``tool``): ``AgentTurn`` no lo expone; se lee del
        checkpoint en memoria del grafo."""

        if str(getattr(turn, "route", "")) != "tool":
            return {}
        result = getattr(turn, "tool_result", None)
        if result is None and self._graph is not None:
            try:
                snapshot = self._graph.get_state({"configurable": {"thread_id": thread_id}})
            except Exception as error:  # sin checkpoint no hay respaldo extra, no un fallo
                logger.warning("eval_agent.state_unavailable", error=str(error))
            else:
                result = (getattr(snapshot, "values", None) or {}).get("tool_result")
        return dict(result) if isinstance(result, Mapping) else {}

    def run_turn(
        self, *, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Turn]
    ) -> TurnObservation:
        started = self._clock()
        if self._usage is not None:
            self._usage.reset()
        turn = self._runner.prepare(owner_key, thread_id, question, list(seed_turns))
        first: float | None = None
        if turn.prompt is None:
            answer = NO_CONTEXT_ANSWER
            first = self._clock()
        else:
            parts: list[str] = []
            try:
                for fragment in self._llm.stream(system=turn.system, prompt=turn.prompt):
                    if first is None and fragment:
                        first = self._clock()
                    parts.append(fragment)
            except Exception as error:
                if "".join(parts).strip():
                    raise
                logger.warning("eval_agent.stream_fallback", error=str(error))
            answer = "".join(parts)
            if not answer.strip():
                answer = self._llm.complete(system=turn.system, prompt=turn.prompt)
                first = self._clock()
        total = self._clock() - started
        self._runner.record(
            thread_id, question=question, answer=answer, incomplete=False, turns=turn.turns
        )
        observation = routes_observation(
            turn, answer, tool_result=self._tool_result(turn, thread_id)
        )
        return replace(
            observation,
            usage=self._usage.take() if self._usage is not None else None,
            first_text_s=(first - started) if first is not None else None,
            total_s=total,
        )


def fallback_reason(error: BaseException) -> str | None:
    """Motivo del respaldo del modo ``tools``, con el criterio de ``ChatService``.

    ``timeout`` (plazo del turno o del cliente), ``provider`` (``LLMError``, API de
    DeepSeek o conexión) e ``invalid_decision`` (bucle que agota el ``recursion_limit``).
    Otro error no es del proveedor y marca el ítem como error.
    """

    import openai
    from langchain_core.exceptions import OutputParserException
    from langgraph.errors import GraphRecursionError

    from carvajal.application.errors import LLMError

    if isinstance(error, (TimeoutError, openai.APITimeoutError)):
        return "timeout"
    if isinstance(error, (LLMError, openai.APIError, ConnectionError)):
        return "provider"
    if isinstance(error, (OutputParserException, GraphRecursionError)):
        return "invalid_decision"
    return None


def _tool_backing(messages: Sequence[Any]) -> list[str]:
    """Datos de las herramientas del turno (sin ``message`` ni ``sin_consulta``)."""

    texts: list[str] = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name in DECLARATION_TOOLS:
            continue
        content = message.content if isinstance(message.content, str) else _dumps(message.content)
        data = content_data(content)
        if data is None:
            continue
        texts.append(_dumps({key: value for key, value in data.items() if key != "message"}))
    return texts


def _has_evidence(final: Any) -> bool:
    """Lo obtenido antes del plazo alcanza para el respaldo: el mismo predicado de
    ``ChatService`` (``has_cards`` incluye contactos, canal, ciudades, ofertas…)."""

    from apps.api.chat.products import has_cards

    queried = any(
        r.get("status") == "ok"
        and r.get("tool") in TOOL_NAMES
        and r.get("tool") not in DECLARATION_TOOLS
        for r in final.results
    )
    return queried and (bool(final.sources) or has_cards(dict(final.products or {})))


def _calls(results: Iterable[Mapping[str, Any]]) -> tuple[ToolCall, ...]:
    return tuple(
        ToolCall(str(r.get("tool") or ""), str(r.get("status") or ""), dict(r.get("args") or {}))
        for r in results
    )


def _asked_data(results: Iterable[Mapping[str, Any]]) -> list[str]:
    """El ``dato`` de cada ``sin_consulta(pedir_dato)`` del turno."""

    asked: list[str] = []
    for result in results:
        args = result.get("args") or {}
        if result.get("tool") in DECLARATION_TOOLS and args.get("motivo") == "pedir_dato":
            asked.append(str(args.get("dato") or "dato"))
    return asked


def _tool_missing(final: Any) -> list[str]:
    return [*(str(item) for item in final.missing), *_asked_data(final.results)]


def _turn_messages(messages: Sequence[Any]) -> list[Any]:
    """Mensajes del último turno: los posteriores a la última pregunta de la persona."""

    starts = [i for i, message in enumerate(messages) if isinstance(message, HumanMessage)]
    return list(messages[starts[-1] + 1 :] if starts else messages)


def _tool_requested(final: Any, messages: Sequence[Any]) -> list[str]:
    """Datos pedidos de forma estructurada en ``tools``: ``needs_input`` y ``pedir_dato``.

    Las llamadas omitidas por el límite (``final.missing``) no piden nada a la persona.
    """

    requested: list[str] = []
    for message in _turn_messages(messages):
        if not isinstance(message, ToolMessage) or message.name in DECLARATION_TOOLS:
            continue
        content = message.content if isinstance(message.content, str) else _dumps(message.content)
        data = content_data(content) or {}
        if data.get("status") == "needs_input":
            requested.extend(str(item) for item in data.get("missing") or [])
    return [*requested, *_asked_data(final.results)]


def tools_observation(final: Any, answer: str, messages: Sequence[Any] = ()) -> TurnObservation:
    """Observación de un turno ``tools`` (sin tiempos), como la arma el driver.

    ``final`` es el ``ToolAgentFinal`` del turno y ``messages``, los del checkpoint (de ahí
    salen el respaldo y los ``needs_input``).
    """

    products = dict(final.products or {})
    sources = tuple(_plain(source) for source in final.sources)
    quotes = tuple(dict(q) for q in final.quotes) or tuple(_shown_quotes(products))
    return TurnObservation(
        answer=answer,
        tools=_calls(final.results),
        sources=sources,
        products=products,
        quotes=quotes,
        backing=(
            *_tool_backing(messages),
            *_source_texts(sources),
            _dumps(products) if products else "",
            _dumps(list(quotes)) if quotes else "",
        ),
        missing=tuple(_tool_missing(final)),
        requested=tuple(_tool_requested(final, messages)),
        abstained=bool(final.abstained),
        path=str(final.mode_path or "agent"),
        usage=dict(final.usage) or None,
    )


class ToolsDriver:
    """Modo ``tools``: ``ToolAgentRunner.stream`` con el respaldo determinista del servicio.

    Mide el primer estado, el primer texto y el fin del turno. Si el proveedor falla, vence
    el plazo o devuelve una decisión inválida (o un cierre vacío) antes del primer texto,
    el turno lo atiende el respaldo (FR-009) como en ``ChatService._fallback_tools``: con lo
    obtenido si el plazo venció con evidencia, o con el grafo ``routes`` de reglas.
    """

    mode = "tools"

    def __init__(
        self,
        runner: Any,
        *,
        graph: Any = None,
        fallback_runner: Any = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._runner = runner
        self._graph = graph
        self._fallback = fallback_runner
        self._clock = clock

    def run_turn(
        self, *, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Turn]
    ) -> TurnObservation:
        from apps.api.chat.agent import ToolAgentFinal, ToolAgentStatus, ToolAgentToken

        started = self._clock()
        first_status: float | None = None
        first_text: float | None = None
        parts: list[str] = []
        final: Any = None
        try:
            for event in self._runner.stream(owner_key, thread_id, question, list(seed_turns)):
                if isinstance(event, ToolAgentStatus) and first_status is None:
                    first_status = self._clock()
                elif isinstance(event, ToolAgentToken):
                    if first_text is None:
                        first_text = self._clock()
                    parts.append(event.text)
                elif isinstance(event, ToolAgentFinal):
                    final = event
        except Exception as error:
            reason = fallback_reason(error)
            if reason is None or "".join(parts).strip() or self._fallback is None:
                raise
            obtained = getattr(error, "final", None)
            return self._fallback_turn(
                reason,
                obtained,
                owner_key=owner_key,
                thread_id=thread_id,
                question=question,
                seed_turns=seed_turns,
                started=started,
                first_status=first_status,
            )
        if final is None:
            raise RuntimeError("El agente terminó sin cierre del turno.")
        answer = "".join(parts)
        if not answer.strip() and self._fallback is not None:
            return self._fallback_turn(
                "empty_answer",
                final,
                owner_key=owner_key,
                thread_id=thread_id,
                question=question,
                seed_turns=seed_turns,
                started=started,
                first_status=first_status,
            )
        total = self._clock() - started
        return replace(
            tools_observation(final, answer, self._state_messages(thread_id)),
            first_status_s=(first_status - started) if first_status is not None else None,
            first_text_s=(first_text - started) if first_text is not None else None,
            total_s=total,
        )

    def _state_messages(self, thread_id: str) -> list[Any]:
        """Mensajes del hilo (resultados de las herramientas), leídos del checkpoint."""

        if self._graph is None:
            return []
        try:
            snapshot = self._graph.get_state({"configurable": {"thread_id": thread_id}})
        except Exception as error:  # sin checkpoint no hay respaldo extra, no un fallo
            logger.warning("eval_agent.state_unavailable", error=str(error))
            return []
        values = getattr(snapshot, "values", None) or {}
        return list(values.get("messages") or [])

    def _fallback_turn(
        self,
        reason: str,
        obtained: Any,
        *,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
        started: float,
        first_status: float | None,
    ) -> TurnObservation:
        logger.warning("eval_agent.fallback", reason=reason)
        attempted = _calls(obtained.results) if obtained is not None else ()
        usage = dict(obtained.usage) if obtained is not None and obtained.usage else None
        if reason == "timeout" and obtained is not None and _has_evidence(obtained):
            products = dict(obtained.products or {})
            sources = tuple(_plain(source) for source in obtained.sources)
            answer = fallback.render(
                products, sources, note=fallback.timeout_note(len(obtained.missing))
            )
            tools = attempted
            missing = tuple(_tool_missing(obtained))
            requested = tuple(_asked_data(obtained.results))
            # Como el servicio: el turno se cierra en el runner de herramientas.
            self._runner.record(
                thread_id,
                question=question,
                answer=answer,
                incomplete=False,
                turns=tuple(obtained.turns) or tuple(seed_turns),
            )
        else:
            turn = self._fallback.prepare(owner_key, thread_id, question, list(seed_turns))
            products = dict(turn.products or {})
            sources = tuple(_plain(source) for source in turn.sources)
            answer = fallback.render(products, sources)
            derived, _ = routes_tools(turn)
            tools = (*attempted, *derived)
            missing = tuple(_routes_missing(turn))
            requested = tuple(_routes_requested(turn))
            self._fallback.record(
                thread_id, question=question, answer=answer, incomplete=False, turns=turn.turns
            )
        now = self._clock()
        return TurnObservation(
            answer=answer,
            tools=tools,
            sources=sources,
            products=products,
            quotes=tuple(_shown_quotes(products)),
            backing=(*_source_texts(sources), _dumps(products) if products else ""),
            missing=missing,
            requested=requested,
            path="fallback",
            usage=usage,
            first_status_s=(first_status - started) if first_status is not None else None,
            first_text_s=now - started,
            total_s=now - started,
            fallback_reason=reason,
        )


# --- Verificaciones ------------------------------------------------------------------------


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# Cifra con separadores de miles o decimales («2.000», «3,5», «1 500»).
_NUMBER_BODY: Final = r"\d+(?:(?:[.,]|[ \u00a0\u202f](?=\d{3}\b))\d+)*"
# Multiplicadores en palabras: «1 millón», «25 mil», «1,5 millones», «3 billion».
_MULTIPLIERS: Final[dict[str, int]] = {
    "mil millones": 10**9,
    "billones": 10**12,
    "billon": 10**12,
    "millones": 10**6,
    "millon": 10**6,
    "mil": 10**3,
    "billions": 10**9,
    "billion": 10**9,
    "millions": 10**6,
    "million": 10**6,
    "thousand": 10**3,
}
_MULTIPLIER_WORDS: Final = "|".join(re.escape(word).replace("on", "[oó]n") for word in _MULTIPLIERS)
_NUMBER: Final = re.compile(
    rf"(?P<num>{_NUMBER_BODY})(?:\s*(?P<pct>%|por ciento)"
    rf"|\s+(?P<mult>{_MULTIPLIER_WORDS})\b)?",
    re.IGNORECASE,
)
# Citas «[1]», «[1, 2]» y marcadores de lista al inicio de línea: no son cifras afirmadas.
_CITATION: Final = re.compile(r"\[\s*\d+(?:\s*[,;\u2013-]\s*\d+)*\s*\]")
_LIST_MARKER: Final = re.compile(r"^\s*\d{1,2}[.)]\s+", re.MULTILINE)
_URL: Final = re.compile(r"https?://\S+|www\.\S+")
# Códigos de referencia («CO-VTER-08»): identificadores, no cifras.
_CODE: Final = re.compile(r"\b(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")
# Campos técnicos de los resultados: sus números no respaldan cifras de la respuesta.
_TECHNICAL_KEYS: Final = frozenset(
    {"score", "chunk_id", "url", "source_url", "n", "position", "rank", "thread_id"}
)

_MONTHS: Final[dict[str, int]] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH: Final = "(?P<month>" + "|".join(_MONTHS) + ")"
# Fechas completas o parciales, de la más específica a la menos (se consumen en orden).
_DATES: Final = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?P<year>\d{4})-(?P<mnum>\d{1,2})-(?P<day>\d{1,2})"
        r"(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?",
        r"\b(?P<day>\d{1,2})[/.-](?P<mnum>\d{1,2})[/.-](?P<year>\d{4})\b",
        rf"\b(?P<day>\d{{1,2}})\s+de\s+{_MONTH}(?:\s+(?:de|del)\s+(?P<year>\d{{4}}))?\b",
        rf"\b{_MONTH}\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>\d{{4}})\b",
        rf"\b(?P<day>\d{{1,2}})\s+{_MONTH}\s+(?P<year>\d{{4}})\b",
        rf"\b{_MONTH}\s+(?:(?:de|del|of)\s+)?(?P<year>\d{{4}})\b",
    )
)

DateKey = tuple[str, int, int, int]


def _date_key(match: re.Match[str]) -> DateKey | None:
    """Clave comparable de una fecha: completa, día y mes, o mes y año."""

    parts = match.groupdict()
    month = _MONTHS[parts["month"].lower()] if parts.get("month") else int(parts["mnum"])
    year = int(parts["year"]) if parts.get("year") else 0
    day = int(parts["day"]) if parts.get("day") else 0
    if not 1 <= month <= 12 or day > 31:
        return None
    if day and year:
        return ("dmy", year, month, day)
    if day:
        return ("dm", 0, month, day)
    return ("my", year, month, 0)


def _implied_dates(key: DateKey) -> set[DateKey]:
    """Fechas menos específicas que una fecha del respaldo también respalda."""

    kind, year, month, day = key
    implied = {key}
    if kind == "dmy":
        implied |= {("dm", 0, month, day), ("my", year, month, 0)}
    return implied


def _take_dates(text: str, *, keep_year: bool) -> tuple[str, list[tuple[str, DateKey]]]:
    """Fechas del texto y el texto sin ellas (sus partes no cuentan como cifras sueltas).

    Con ``keep_year`` (respaldo), el año de la fecha queda como cifra suelta: «2025» se
    respalda con una fecha de 2025.
    """

    found: list[tuple[str, DateKey]] = []
    for pattern in _DATES:

        def consume(match: re.Match[str]) -> str:
            key = _date_key(match)
            if key is None:
                return match.group()
            found.append((match.group(), key))
            return f" {key[1]} " if keep_year and key[1] else " "

        text = pattern.sub(consume, text)
    return text, found


def _decimal(integer: str, decimals: str = "") -> Decimal | None:
    try:
        return Decimal(f"{integer or '0'}.{decimals}" if decimals else integer or "0")
    except InvalidOperation:
        return None


def _strict_value(raw: str) -> Decimal | None:
    """Lectura única de una cifra escrita por una persona (es-CO o en-US).

    Un separador seguido de exactamente tres dígitos es de miles («5.000», «412,610»);
    si no, es decimal («3,5», «0.19»). Con dos separadores distintos, el último es el
    decimal («1.234,56», «1,234.56»).
    """

    text = re.sub(r"[ \u00a0\u202f]", "", raw)
    separators = re.findall(r"[.,]", text)
    groups = re.split(r"[.,]", text)
    if not separators:
        return _decimal(text)
    if len(set(separators)) > 1:
        return _decimal("".join(groups[:-1]), groups[-1])
    if groups[0] != "0" and all(len(group) == 3 for group in groups[1:]):
        return _decimal("".join(groups))
    if len(separators) == 1:
        return _decimal(groups[0], groups[1])
    return _decimal("".join(groups))


def _lenient_values(raw: str) -> set[Decimal]:
    """Lecturas posibles de una cifra del respaldo (corpus o JSON): miles o decimales."""

    text = re.sub(r"[ \u00a0\u202f]", "", raw)
    groups = re.split(r"[.,]", text)
    values: set[Decimal | None] = set()
    if len(groups) == 1:
        values.add(_decimal(groups[0]))
    else:
        if all(len(group) == 3 for group in groups[1:]):
            values.add(_decimal("".join(groups)))
        if all(len(group) == 3 for group in groups[1:-1]):
            values.add(_decimal("".join(groups[:-1]), groups[-1]))
        values.add(_strict_value(raw))
    return {value for value in values if value is not None}


def _key(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _scaled(match: re.Match[str], values: set[Decimal]) -> set[str]:
    """Aplica el multiplicador («mil», «millón») o el porcentaje a las lecturas."""

    multiplier = match.group("mult")
    if multiplier:
        factor = _MULTIPLIERS[_fold(" ".join(multiplier.split()))]
        return {_key(value * factor) for value in values}
    keys = {_key(value) for value in values}
    if match.group("pct"):
        keys |= {_key(value / 100) for value in values}
    return keys


def _clean(text: str) -> str:
    text = _CITATION.sub(" ", _URL.sub(" ", text))
    return _CODE.sub(" ", _LIST_MARKER.sub("", text))


# Cardinales en palabras (plegados): «dos mil vasos» es 2000. En la pregunta respaldan la
# cifra que la repite; en una abstención, una cantidad en palabras es un dato aportado.
# «cero» no se lee: casi nunca es una cantidad («cero plástico»).
_WORD_VALUES: Final[dict[str, int]] = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiun": 21,
    "veintiuno": 21,
    "veintiuna": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
    "cien": 100,
    "ciento": 100,
    **{
        f"{stem}ient{ending}": value
        for stem, value in (
            ("dosc", 200),
            ("tresc", 300),
            ("cuatroc", 400),
            ("seisc", 600),
            ("setec", 700),
            ("ochoc", 800),
            ("novec", 900),
        )
        for ending in ("os", "as")
    },
    "quinientos": 500,
    "quinientas": 500,
}
_WORD_SCALES: Final[dict[str, int]] = {
    "mil": 10**3,
    "millon": 10**6,
    "millones": 10**6,
    "billon": 10**12,
    "billones": 10**12,
}
# Solos, «un», «una» y «uno» son artículos o pronombres («una caja»), no cantidades.
_ARTICLES: Final = frozenset({"un", "una", "uno"})
# «mil» solo cuenta si cuantifica un sustantivo, no en frases hechas («mil gracias»).
_IDIOMATIC_MIL: Final = frozenset({"gracias", "disculpas", "perdones", "veces", "besos"})
_WORD_TOKEN: Final = re.compile(r"[a-z]+|[^a-z\s]+")


def _spoken_value(words: Sequence[str]) -> int:
    """Valor de una secuencia de cardinales («dos mil quinientos» → 2500)."""

    total = current = 0
    for word in words:
        if word == "y":
            continue
        if word == "mil":
            current = (current or 1) * 10**3
        elif word in _WORD_SCALES:
            total += (current or 1) * _WORD_SCALES[word]
            current = 0
        else:
            current += _WORD_VALUES[word]
    return total + current


def _joins(run: Sequence[str], token: str) -> bool:
    """``token`` continúa la secuencia ``run`` según la gramática de los cardinales.

    Tras una centena va una cifra menor («ciento cincuenta»); tras «y», una unidad que
    sigue a una decena («treinta y cinco»); tras una escala, cualquier cardinal («mil
    dos»); una escala sigue a un cardinal o a otra escala («dos mil millones»). Dos
    unidades seguidas («tres cuatro») son dos cantidades, no una suma.
    """

    if not run:
        return token != "y"
    last = run[-1]
    if token == "y":
        return last in _WORD_VALUES and _WORD_VALUES[last] in range(30, 100, 10)
    if token in _WORD_SCALES:
        return last != "y"
    value = _WORD_VALUES[token]
    if last == "y":
        return value < 10
    if last in _WORD_SCALES:
        return True
    previous = _WORD_VALUES[last]
    return previous >= 100 and previous % 100 == 0 and value < 100


def _spoken_numbers(text: str) -> list[Decimal]:
    """Cantidades escritas en palabras en español («quinientos», «dos mil», «un millón»).

    Una secuencia son cardinales seguidos según :func:`_joins`; la puntuación o cualquier
    otra palabra la corta. Un texto en inglés no se lee («once again» no es 11), un
    artículo suelto no es cantidad y «mil» suelto solo cuenta si cuantifica algo.
    """

    if detect_language(text) == "en":
        return []
    found: list[Decimal] = []
    run: list[str] = []

    def close(following: str) -> None:
        while run and run[-1] == "y":
            run.pop()
        bare_mil = run == ["mil"] and (not following.isalpha() or following in _IDIOMATIC_MIL)
        if run and not set(run) <= _ARTICLES and not bare_mil:
            found.append(Decimal(_spoken_value(run)))
        run.clear()

    for token in _WORD_TOKEN.findall(_fold(text)):
        if token in _WORD_VALUES or token in _WORD_SCALES or token == "y":
            if not _joins(run, token):
                close(token)
            if token != "y" or run:
                run.append(token)
        else:
            close(token)
    close("")
    return found


# Conteos de registros: «7 sedes» se respalda si una herramienta del turno devolvió una lista
# de 7 sedes, aunque ningún campo traiga el total. Clave de la lista en el resultado (de la
# herramienta o de las tarjetas) → sustantivos con los que la respuesta la cuenta. Solo
# claves que ambos modos producen: los fragmentos del corpus van como JSON en ``tools`` y
# como texto en ``routes``, y la lista completa de ciudades del catálogo (``cities`` del
# resultado comercial, ``ciudades_con_cobertura``) la trae ``routes`` en todo turno
# comercial; ninguna de las dos respalda un conteo.
_COUNT_NOUNS: Final[dict[str, str]] = {
    "sedes": r"sedes?|sites?",
    "sites": r"sedes?|sites?",
    "opciones": r"opci[oó]n(?:es)?|options?",
    "options": r"opci[oó]n(?:es)?|options?",
    "descartadas": r"descartadas?",
    "discarded": r"descartadas?",
    "ciudades_disponibles": r"ciudad(?:es)?|cit(?:y|ies)",
    "cities": r"ciudad(?:es)?|cit(?:y|ies)",
    "cities_with_distributors": r"ciudad(?:es)?|cit(?:y|ies)",
    "contacts": r"contactos?|contacts?",
    "distributors": r"distribuidor(?:es)?|distributors?",
    "offers": r"ofertas?|offers?",
    "quotes": r"cotizaci[oó]n(?:es)?|quotes?",
}
# ``cities`` solo cuenta dentro de una cotización (``kind``): es la misma lista que
# ``ciudades_disponibles`` de ``cotizar`` en ``tools``.
_QUOTE_ONLY_KEYS: Final = frozenset({"cities"})
# Solo números enteros: «1.005 sedes» no es un conteo de 5.
_COUNTED: Final = {
    noun: re.compile(rf"(?<![\d.,])\b(?P<count>\d+)(?![.,]\d)\s+(?P<noun>{noun})\b", re.IGNORECASE)
    for noun in set(_COUNT_NOUNS.values())
}


def _record_lists(value: Any) -> Iterator[tuple[str, int]]:
    """``(sustantivos, tamaño)`` de cada lista no vacía con clave conocida de un JSON."""

    if isinstance(value, Mapping):
        quote = "kind" in value
        for key, item in value.items():
            known = str(key) in _COUNT_NOUNS and (quote or str(key) not in _QUOTE_ONLY_KEYS)
            if isinstance(item, list) and item and known:
                yield _COUNT_NOUNS[str(key)], len(item)
            yield from _record_lists(item)
    elif isinstance(value, list):
        for item in value:
            yield from _record_lists(item)


def _record_counts(backing: Iterable[str]) -> set[tuple[str, int]]:
    """Conteos de los registros que devolvieron las herramientas del turno (JSON)."""

    counts: set[tuple[str, int]] = set()
    for text in backing:
        stripped = text.strip()
        if stripped[:1] not in "{[":
            continue
        try:
            data = json.loads(stripped)
        except ValueError:
            continue
        counts.update(_record_lists(data))
    return counts


def _drop_counts(answer: str, counts: set[tuple[str, int]]) -> str:
    """Quita de la respuesta los conteos respaldados («7 sedes» → «sedes»).

    Solo vale el número pegado al sustantivo contado y con el tamaño exacto de una lista
    devuelta: «7 días», «las sedes son 7» o «7 sedes» con 5 devueltas siguen siendo cifras.
    """

    for noun, pattern in _COUNTED.items():

        def drop(match: re.Match[str], noun: str = noun) -> str:
            if (noun, int(match.group("count"))) in counts:
                return match.group("noun")
            return match.group()

        answer = pattern.sub(drop, answer)
    return answer


def _json_leaves(value: Any) -> Iterator[str]:
    """Hojas de un resultado JSON sin los campos técnicos (puntajes, ids, URLs)."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) not in _TECHNICAL_KEYS:
                yield from _json_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_leaves(item)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int | float):
        yield repr(value) if isinstance(value, float) else str(value)
    else:
        yield str(value)


def _backing_pieces(text: str) -> Iterator[str]:
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, Mapping | list):
            yield from _json_leaves(data)
            return
    yield text


@dataclass(frozen=True)
class _Claim:
    raw: str
    values: frozenset[str] = frozenset()
    date: DateKey | None = None


def _claims(text: str, *, strict: bool, keep_year: bool = True) -> list[_Claim]:
    """Cifras y fechas de un texto; ``strict`` lee cada cifra de una sola forma.

    ``keep_year`` deja el año de cada fecha como cifra suelta (para lo que respalda; en la
    respuesta, la fecha se juzga entera).
    """

    remaining, dates = _take_dates(_clean(text), keep_year=keep_year)
    claims = [_Claim(raw, date=key) for raw, key in dates]
    for match in _NUMBER.finditer(remaining):
        raw = match.group("num")
        if strict:
            value = _strict_value(raw)
            values = {value} if value is not None else set()
        else:
            values = _lenient_values(raw)
        if values:
            claims.append(_Claim(match.group().strip(), frozenset(_scaled(match, values))))
    return claims


def unsupported_figures(
    answer: str, backing: Iterable[str], *, ignore: Iterable[str] = ()
) -> list[str]:
    """Cifras y fechas de la respuesta que ningún texto de ``backing`` respalda (SC-001).

    Compara valores, no texto. La respuesta se lee de una sola forma (es-CO: «5.000» es
    cinco mil; «3,5» es tres y medio); el respaldo admite ambas lecturas solo si es
    ambiguo. «mil» y «millón» multiplican. Las fechas se comparan completas («23 de
    noviembre de 2026», «23/11/2026» y «2026-11-23» son la misma). Los campos técnicos
    del JSON (``score``, ``chunk_id``, URLs) y los códigos de referencia no respaldan
    nada. Los números de ``ignore`` (las preguntas del ítem), también los escritos en
    palabras («dos mil»), no cuentan como afirmación nueva. Un conteo pegado a su
    sustantivo («7 sedes») se respalda si una lista del respaldo tiene ese tamaño exacto.
    """

    backing = list(backing)
    values: set[str] = {_key(value) for text in ignore for value in _spoken_numbers(text)}
    dates: set[DateKey] = set()
    known = [
        *(
            c
            for text in backing
            for piece in _backing_pieces(text)
            for c in _claims(piece, strict=False)
        ),
        *(c for text in ignore for c in _claims(text, strict=True)),
    ]
    for claim in known:
        values |= claim.values
        if claim.date is not None:
            dates |= _implied_dates(claim.date)
    flagged: list[str] = []
    answer = _drop_counts(answer, _record_counts(backing))
    for claim in _claims(answer, strict=True, keep_year=False):
        supported = claim.date in dates if claim.date is not None else bool(claim.values & values)
        if not supported and claim.raw not in flagged:
            flagged.append(claim.raw)
    return flagged


_ABSENCE_SENTENCES: Final = tuple(
    _fold(case["say"]).rstrip(".") for key, case in ABSENCE_POLICY.items() if key != "partial"
)
# La evidencia parcial responde algo: no es abstención aunque declare un hueco.
_PARTIAL_MARKERS: Final = (
    *(_fold(ABSENCE_POLICY["partial"][key]).rstrip(".") for key in ("say", "offer")),
    "lo que si consta",
    "lo que si tengo es",
    "lo que si encontre",
    "what i did find",
)
# «Lo que sí aparece es…» o «lo que sí aparece en el corpus:» afirma contenido; la oferta de
# ``no_context`` («puedo contarte lo que sí aparece sobre el tema») no.
_PARTIAL_CLAIM: Final = re.compile(
    r"\blo que si (?:aparecen?|constan?|figuran?)"
    r"(?: (?:en|el|la|los|las|corpus|fuentes|catalogo|resultados|registros?)){0,3}"
    r"\s*(?:\b(?:es|son)\b|:)"
)
# «No lo tengo claro», «no tengo esa cifra a mano»: duda o aplazamiento, no ausencia.
_NOT_ABSENT: Final = r"(?! (?:claro|clara|a mano|en stock|en inventario|seguro|segura|presente))"
# Declaración de ausencia del dato (texto plegado): las frases de :data:`ABSENCE_POLICY` y
# sus variaciones gramaticales (singular o plural, con o sin pronombre), atadas al lugar
# donde el dato no está cuando la frase sola es ambigua («sin registro en…», «isn't in the
# corpus»).
_ABSENCE: Final = re.compile(
    "|".join(re.escape(sentence) for sentence in _ABSENCE_SENTENCES)
    + r"|\bno (?:(?:lo|la|los|las) )?(?:aparecen?|figuran?|constan?|estan?) en "
    r"(?:(?:el|la|los|las|mis|sus) )?(?:corpus|fuentes?|resultados?|registros?|catalogo"
    r"|indicador(?:es)?|documentos?|directorio|datos)\b"
    rf"|\bno (?:lo|la|los|las) tengo\b{_NOT_ABSENT}"
    rf"|\bno tengo (?:ese|esa|esos|esas) (?:datos?|cifras?|informacion|registros?)\b{_NOT_ABSENT}"
    r"|\bno (?:tengo|hay|tiene|tienen) (?:registros?|informacion|datos?)\b"
    r"|\bsin registros? (?:en|para)\b"
    r"|\bno (?:lo |la |los |las )?(?:encontre|encontramos|se encontr\w*)\b"
    r"|\b(?:isn['\u2019]t|is not|aren['\u2019]t|are not) in "
    r"(?:the |carvajal['\u2019]s |our |my )?(?:public )?"
    r"(?:corpus|sources?|catalog(?:ue)?|records?|data|documents?|results?)\b"
    r"|\bnot in the (?:public )?corpus\b"
    r"|\bi (?:couldn['\u2019]t|could not|didn['\u2019]t|did not) find\b"
    r"|\bi (?:don['\u2019]t|do not) have (?:that|this|those|any|the) "
    r"(?:data|figures?|information|records?|numbers?)\b"
)
# Negativa por fuera de alcance; solo cuenta en un turno que no consultó herramientas.
_REFUSAL: Final = re.compile(
    r"\bno (?:puedo|hago|manejo|resuelvo|respondo|atiendo|ofrezco)\b"
    r"|\bfuera de (?:mi |nuestro |su )?alcance\b|\bsolo respondo\b"
    r"|\bi (?:can['\u2019]t|cannot) help\b|\bout of scope\b|\boutside (?:my|the) scope\b"
)
# Tramos que pueden ir antes de la declaración sin afirmar nada: cortesía, el relato de la
# búsqueda («revisé el corpus») o un marco breve («para 1990»).
_COURTESY: Final = re.compile(
    r"(?:hola|buenas|buenos dias|buenas (?:tardes|noches)|lo siento|lo lamento|disculpa"
    r"|disculpame|perdon|gracias(?: por (?:preguntar|tu pregunta|escribir))?|entiendo"
    r"|buena pregunta|lamentablemente|por ahora|de momento|por el momento|en este momento"
    r"|sinceramente|honestamente|sorry|hi|hello|thanks|i'm sorry|unfortunately|for now)"
)
_SEARCH_META: Final = re.compile(
    r"\b(?:busque|buscamos|revise|revisamos|consulte|consultamos|mire|verifique|explore"
    r"|lo busque|searched|checked|looked|reviewed)\b"
)
_FRAME: Final = re.compile(
    r"(?:para|en|sobre|de|del|respecto a|acerca de|con respecto a|about|for|in|on|regarding"
    r"|as for)\b"
)
_FRAME_WORDS: Final = 5
_CONNECTOR: Final = re.compile(r"^(?:y|e|pero|aunque|so|and|but)\s+")
_PREAMBLE_BREAK: Final = re.compile(r"[,;:]")
# Una cláusula adversativa tras la declaración responde de todos modos, salvo que ofrezca
# un siguiente paso o vuelva a negar («pero puedo buscar…», «aunque no se estima»).
_ADVERSATIVE: Final = re.compile(
    r"\b(?:pero|aunque|sin embargo|no obstante|but|however|though|although)\b"
)
_HEDGED: Final = re.compile(
    r"\W*(?:(?:si|tambien) (?:quieres|deseas|gustas|prefieres|me (?:dices|das|indicas))"
    r"|si (?:puedo|podemos|te puedo)|puedo|podemos|podria|te (?:puedo|ayudo|ofrezco|sugiero)"
    r"|lo (?:puedo|busco|consulto)|no\b|tampoco|ni\b|nada|ningun|i can|we can|i could"
    r"|if you|not\b|there is no|nothing)"
)
_OUT_OF_SCOPE: Final = "fuera_de_alcance"
_PERIOD: Final = re.compile(r"(?:19|20)\d{2}")
# La pregunta pide una fecha: entonces un período en la respuesta es el dato, no el de las
# fuentes.
_ASKS_DATE: Final = re.compile(
    r"\bcuando\b|\bque (?:ano|fecha|dia|mes)\b|\bfecha\b"
    r"|\bwhen\b|\bwhat (?:year|date|day|month)\b|\bwhich (?:year|date)\b"
)


def _is_period(raw: str) -> bool:
    """Un año o una fecha: describe un período (de una fuente o de una sugerencia)."""

    return bool(_PERIOD.fullmatch(raw)) or any(pattern.fullmatch(raw) for pattern in _DATES)


def _defers(observation: TurnObservation) -> bool:
    """El turno aplaza pidiendo un dato: ``sin_consulta(pedir_dato)``.

    Es la señal explícita que ambos modos producen (``routes`` la deriva de
    ``commercial_clarify`` y ``missing_period``). ``missing`` no sirve: en ``routes`` trae
    pendientes internos del resultado comercial y en ``tools``, llamadas omitidas por el
    límite.
    """

    return any(
        call.name in DECLARATION_TOOLS and call.args.get("motivo") == "pedir_dato"
        for call in observation.tools
    )


def _no_real_query(observation: TurnObservation) -> bool:
    """El turno no consultó: ninguna llamada o solo ``sin_consulta`` (cualquier motivo).

    Mismo criterio en ambos modos: ``routes`` nunca deriva ``fuera_de_alcance`` (su ruta
    directa es ``conversacion``), así que el motivo no puede decidir.
    """

    return all(call.name in DECLARATION_TOOLS for call in observation.tools)


def _declaration(folded: str, *, refusal: bool) -> re.Match[str] | None:
    """La primera declaración de ausencia (o de negativa, si ``refusal``) del texto."""

    found = [_ABSENCE.search(folded), _REFUSAL.search(folded) if refusal else None]
    return min((m for m in found if m is not None), key=lambda m: m.start(), default=None)


def _bare(clause: str) -> bool:
    """Un tramo previo a la declaración que no afirma nada."""

    words = re.findall(r"[\w'\u2019]+", _CONNECTOR.sub("", clause.strip()))
    if not words:
        return True
    plain = " ".join(words)
    return bool(
        _COURTESY.fullmatch(plain)
        or _NEGATION.search(plain)
        or _SEARCH_META.search(plain)
        or (len(words) <= _FRAME_WORDS and _FRAME.match(plain))
    )


def _answers_anyway(folded: str, declaration: re.Match[str]) -> bool:
    """La respuesta afirma algo además de declarar la ausencia o negarse.

    Afirma si antes de la declaración hay una frase o una cláusula que dice algo («Ganó el
    América; no lo encontré…») o si en el resto de su frase o en la siguiente sigue una
    adversativa con contenido («no puedo confirmarlo, pero ganó el Cali»).
    """

    sentences = _SENTENCE_BREAK.split(folded[: declaration.start()])
    clauses = [*sentences[:-1], *_PREAMBLE_BREAK.split(sentences[-1])[:-1]]
    if not all(_bare(clause) for clause in clauses):
        return True
    window = " ".join(_SENTENCE_BREAK.split(folded[declaration.end() :], maxsplit=2)[:2])
    return any(not _HEDGED.match(window[match.end() :]) for match in _ADVERSATIVE.finditer(window))


def _question_values(ignore: Iterable[str]) -> set[str]:
    """Valores de las cifras de la pregunta, en dígitos o en palabras."""

    return {
        *(_key(value) for text in ignore for value in _spoken_numbers(text)),
        *(value for text in ignore for c in _claims(text, strict=True) for value in c.values),
    }


def _adds_data(observation: TurnObservation, ignore: Sequence[str]) -> bool:
    """La respuesta aporta datos además de declarar la ausencia.

    Aporta datos si cita el corpus (``[n]``: es una respuesta parcial), si afirma evidencia
    parcial («lo que sí consta…», «lo que sí aparece es…»), si trae una cifra sin respaldo
    en el turno, si da una cifra que no es de la pregunta ni un período, o una cantidad en
    palabras que no es de la pregunta («unos tres billones»). Un año o una fecha
    respaldados («fragmentos de 2018 a 2026») describen las fuentes, salvo que la pregunta
    pida una fecha: entonces son el dato.
    """

    answer = observation.answer
    folded = _fold(answer)
    if _CITATION.search(answer) or _PARTIAL_CLAIM.search(folded):
        return True
    if any(marker in folded for marker in _PARTIAL_MARKERS):
        return True
    if unsupported_figures(answer, observation.backing, ignore=ignore):
        return True
    periods = not any(_ASKS_DATE.search(_fold(text)) for text in ignore)
    loose = unsupported_figures(answer, (), ignore=ignore)
    if any(not (periods and _is_period(raw)) for raw in loose):
        return True
    asked = _question_values(ignore)
    return any(_key(value) not in asked for value in _spoken_numbers(answer))


def is_abstention(observation: TurnObservation, *, ignore: Iterable[str] = ()) -> bool:
    """La respuesta es una abstención: la marca estructural o una ausencia declarada.

    Criterio (el mismo en ``routes`` y ``tools``; nada depende del camino ni de
    ``missing``):

    - Cuenta la marca estructural del driver: compuerta de ``tools``, ruta sin contexto o
      indicador sin registro en ``routes``.
    - Aplazar pidiendo un dato (``sin_consulta(pedir_dato)``) no es abstenerse.
    - Si no, la respuesta declara que el dato pedido no está (una frase de
      :data:`ABSENCE_POLICY` salvo ``partial``, o :data:`_ABSENCE` con sus variaciones). En
      un turno que no consultó herramientas (ninguna o solo ``sin_consulta``) también vale
      una negativa («no puedo…», «fuera de mi alcance»).
    - La declaración no puede ir acompañada de una afirmación (:func:`_answers_anyway`): ni
      una frase previa que dice algo ni una adversativa con contenido («…, pero ganó el
      Cali»).
    - Y la respuesta no aporta datos (:func:`_adds_data`): ni citas, ni cifras sin respaldo,
      ni cifras o cantidades en palabras que no sean de la pregunta o un período.
    """

    if observation.abstained:
        return True
    if _defers(observation):
        return False
    ignore = tuple(ignore)
    folded = _fold(observation.answer)
    declaration = _declaration(folded, refusal=_no_real_query(observation))
    if declaration is None or _answers_anyway(folded, declaration):
        return False
    return not _adds_data(observation, ignore)


_ES_WORDS: Final = frozenset(
    [
        "el",
        "la",
        "los",
        "las",
        "de",
        "del",
        "que",
        "y",
        "en",
        "para",
        "con",
        "por",
        "una",
        "un",
        "es",
        "se",
        "tu",
        "te",
        "puedo",
        "esta",
        "este",
        "eso",
        "como",
        "cuando",
        "donde",
        "mas",
        "pero",
        "tienes",
        "quieres",
        "necesitas",
        "tenemos",
        "hay",
        "cuesta",
        "precio",
        "caja",
        "cajas",
        "unidades",
        "puedes",
        "recomiendo",
        "disponible",
        "comprar",
        "aqui",
        "cada",
        "sin",
        "sobre",
        "tiene",
        "son",
        "tambien",
        "opcion",
        "opciones",
        "fuente",
        "segun",
    ]
)
_EN_WORDS: Final = frozenset(
    [
        "the",
        "and",
        "of",
        "to",
        "for",
        "with",
        "is",
        "you",
        "in",
        "we",
        "your",
        "are",
        "can",
        "this",
        "it",
        "that",
        "our",
        "have",
        "need",
        "would",
        "which",
        "from",
        "costs",
        "cost",
        "price",
        "prices",
        "box",
        "boxes",
        "units",
        "unit",
        "each",
        "per",
        "recommend",
        "available",
        "buy",
        "here",
        "there",
        "it's",
        "i'd",
        "i'll",
        "option",
        "options",
        "source",
        "according",
        "also",
    ]
)
# Menos palabras funcionales que esto: idioma indeterminado (tarjetas o cifras sueltas).
_MIN_FUNCTION_WORDS: Final = 2


def detect_language(text: str, *, exclude: Iterable[str] = ()) -> str:
    """``es``, ``en`` o vacío (indeterminado), contando palabras funcionales.

    ``exclude`` son textos que no cuentan (nombres del catálogo o títulos de fuentes, que
    van en español aunque la respuesta esté en otro idioma).
    """

    for name in sorted(set(exclude), key=len, reverse=True):
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    words = re.findall(r"[a-z']+", _fold(_URL.sub(" ", text)))
    spanish = sum(word in _ES_WORDS for word in words)
    english = sum(word in _EN_WORDS for word in words)
    if spanish + english < _MIN_FUNCTION_WORDS:
        return ""
    return "en" if english > spanish else "es"


def _card_texts(observation: TurnObservation) -> list[str]:
    """Nombres y títulos de tarjetas y fuentes (varias palabras), para el idioma."""

    leaves = [
        *_json_leaves(dict(observation.products)),
        *(leaf for quote in observation.quotes for leaf in _json_leaves(dict(quote))),
        *(str(source.get("title") or "") for source in observation.sources),
    ]
    return [leaf for leaf in leaves if len(leaf) >= 8 and len(leaf.split()) >= 2]


# Datos que se pueden pedir y cómo se nombran (plegados) en un pedido estructurado o en
# una pregunta de la respuesta.
_ASK_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "cantidad": ("cantidad", "cuant", "unidades", "quantity"),
    "ciudad": ("ciudad", "donde", "city"),
    "temperatura": ("temperatura", "caliente", "fri", "temperature"),
    "uso": ("uso", "para que", "use"),
    "periodo": ("periodo", "ano", "fecha", "period", "year"),
    "producto": ("producto", "familia", "product"),
    "pais": ("pais", "country"),
}
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.!?])\s+|\n+")


def _split_sentences(text: str) -> list[str]:
    """Frases del texto; un punto dentro de una cifra («5.000») no corta la frase."""

    return [part.strip() for part in _SENTENCE_BREAK.split(text) if part.strip()]


def _questions_asked(answer: str) -> list[str]:
    """Frases interrogativas de la respuesta (pedir un dato en texto)."""

    return [s for s in _split_sentences(answer) if s.endswith("?") or s.startswith("¿")]


# Pedido de un dato en una frase no interrogativa (texto plegado): imperativo («dime la
# ciudad»), necesidad («necesito que me indiques…»), falta («para cotizar me falta…») o
# utilidad («también me sirve el uso»). «Me sirve el producto» a secas afirma, no pide.
_REQUEST: Final = re.compile(
    r"\b(?:dime|digame|diganme|indicame|indiqueme|cuentame|cuenteme|confirmame|confirmeme"
    r"|precisame|especificame|aclarame|pasame|comparteme|enviame|escribeme"
    r"|necesito (?:saber|conocer|que me|el dato|un dato|los datos)"
    r"|me falta(?:n|ria|rian)?|falta(?:n)? (?:el|la|los|las|un|una|saber|conocer|definir)"
    r"|me (?:sirve|serviria)n? (?:saber|conocer|que me)|tambien me (?:sirve|serviria)n?"
    r"|me serviria(?:n)? (?:el|la|los|las)"
    r"|tell me|let me know|i need (?:to know|the|your)|please (?:share|provide|confirm|tell))\b"
)
# El indicativo («me dices», «me confirmas») también narra lo ya dicho («como me dices que
# la ciudad es Cali»): solo pide en una pregunta (ya cubierta) o con «por favor».
_POLITE_REQUEST: Final = re.compile(
    r"\bme (?:dices|dirias|indicas|confirmas|cuentas|compartes|pasas)\b"
)
_PLEASE: Final = re.compile(r"\b(?:por favor|porfa|please)\b")
# Antes del verbo, estas palabras lo vuelven eco de un dato ya dado («you tell me…»,
# «por lo que me falta…»).
_ECHO_BEFORE: Final = re.compile(
    r"\b(?:you|como|segun|por lo que|con lo que|con los datos que)\s*$"
)
# «Dime si te interesa otro producto», «let me know if this works»: cierres genéricos.
_GENERIC_CLOSER: Final = re.compile(
    r"\s*(?:si|if)\b(?:\s+\S+){0,2}?\s+(?:te |le )?(?:interesa|quieres|deseas|gustaria"
    r"|necesitas|sirve|prefieres|works|need|want|would like|'d like|have any)\b"
)


def _requests_made(answer: str) -> list[str]:
    """Pedidos no interrogativos: el tramo de la frase desde el verbo de pedido.

    El dato tiene que nombrarse después del pedido: en «con la ciudad que me diste, dime si
    prefieres papel» la ciudad no se pide. No piden un verbo negado («no me falta la
    ciudad»), el eco de lo dicho («como me dices…») ni un cierre genérico («dime si te
    interesa otro producto»).
    """

    spans: list[str] = []
    for sentence in _split_sentences(answer):
        folded = _fold(sentence)
        matches = list(_REQUEST.finditer(folded))
        if _PLEASE.search(folded):
            matches.extend(_POLITE_REQUEST.finditer(folded))
        for match in matches:
            before = folded[: match.start()]
            if _negated(folded, match.start()) or _ECHO_BEFORE.search(before):
                continue
            if _GENERIC_CLOSER.match(folded[match.end() :]):
                continue
            spans.append(folded[match.start() :])
    return spans


def _mentions(text: str, term: str) -> bool:
    folded = _fold(text)
    return any(re.search(rf"\b{re.escape(word)}", folded) for word in _ASK_TERMS.get(term, (term,)))


def _asks_for(observation: TurnObservation, expected: Sequence[str]) -> bool:
    """El turno pide un dato: estructurado (``requested``) o en el texto, nombrándolo.

    Lo estructurado es ``requested`` (``needs_input`` o ``sin_consulta(pedir_dato)``, igual
    en ambos modos), nunca ``missing``: los pendientes internos del resultado comercial o
    las llamadas omitidas por el límite no le piden nada a la persona. En el texto cuenta
    una pregunta que nombra el dato o un pedido no interrogativo (:data:`_REQUEST`) que lo
    nombra después del verbo («también dime la ciudad»). Con el comodín, basta un pedido
    estructurado o un pedido en texto que nombre algún dato conocido; un cierre genérico
    («¿Te ayudo con algo más?») no pide nada, y mencionar el dato sin pedirlo («en tu ciudad
    hay 3 sedes») tampoco.
    """

    questions = [
        *_questions_asked(observation.answer),
        *_requests_made(observation.answer),
    ]
    if ANY_DATUM in expected:
        return (
            bool(observation.requested)
            or _defers(observation)
            or any(_mentions(text, term) for term in _ASK_TERMS for text in questions)
        )
    asked = [*observation.requested, *questions]
    return any(_mentions(text, _fold(term)) for term in expected for text in asked)


_NEGATION: Final = re.compile(
    r"\b(?:no|sin|ningun|ninguna|ninguno|ni|tampoco|nunca|not|cannot|can't|never)\b"
)
# Lo que corta el alcance de una negación hacia adelante.
_CLAUSE_BREAK: Final = re.compile(r"[,;:]|\b(?:pero|aunque|sin embargo|but|however)\b")
_NEGATION_WORDS: Final = 4
_TERM_CLAIM: Final = re.compile(
    r"\b(?:\d+\s*(?:a|-|o)\s*)?\d+\s*(?:dias?|horas?|semanas?)(?:\s+habiles?)?\b"
    r"|\bentrega inmediata\b|\bllega(?:ria|n|ran)?\s+(?:manana|hoy|pasado manana)\b"
    r"|\ben stock\b|\binventario (?:confirmado|disponible)\b"
    r"|\b(?:hay|tenemos|contamos con|quedan) (?:existencias|inventario|stock|unidades)\b"
    r"|\bdisponibles? (?:hoy|ya|de inmediato|para entrega)\b"
)
_CONVERSION: Final = re.compile(
    r"\b(?:equival\w*|convert\w*|conversion|tasa de cambio|al cambio|trm)\b"
)
_CURRENCY_WORDS: Final = (
    r"pesos mexicanos|pesos colombianos|pesos|mxn|cop|usd|us\$|dolares|dollars|euros?|eur"
)
_CURRENCY_AFTER: Final = re.compile(rf"(?P<num>{_NUMBER_BODY})\s*(?P<cur>{_CURRENCY_WORDS})\b")
_CURRENCY_BEFORE: Final = re.compile(
    rf"\b(?P<cur>mxn|cop|usd|us\$|eur)\s*\$?\s*(?P<num>{_NUMBER_BODY})"
)
_CURRENCY_CODES: Final[dict[str, str]] = {
    "pesos mexicanos": "MXN",
    "pesos colombianos": "COP",
    "pesos": "PESOS",
    "dolares": "USD",
    "dollars": "USD",
    "us$": "USD",
    "euro": "EUR",
    "euros": "EUR",
}


def _currency(word: str) -> str:
    folded = " ".join(word.split())
    return _CURRENCY_CODES.get(folded, folded.upper())


def _negated(sentence: str, start: int) -> bool:
    """Una negación en las pocas palabras de la misma cláusula justo antes de ``start``."""

    clause = _CLAUSE_BREAK.split(sentence[:start])[-1]
    window = " ".join(clause.split()[-_NEGATION_WORDS:])
    return bool(_NEGATION.search(window))


def _invented_terms(observation: TurnObservation) -> list[str]:
    """Plazos o existencias afirmados que ningún resultado del turno publica.

    Una negación solo excusa la afirmación si va justo antes, en la misma cláusula («no
    puedo prometer entrega inmediata»); «sin costo de envío» no excusa «2 días hábiles».
    """

    backing = _fold(" ".join(observation.backing))
    flagged: list[str] = []
    for sentence in _split_sentences(_fold(observation.answer)):
        for match in _TERM_CLAIM.finditer(sentence):
            if _negated(sentence, match.start()) or match.group() in backing:
                continue
            flagged.append(match.group())
    return flagged


def _priced(sentence: str) -> list[tuple[str, str, str]]:
    """Cifras con moneda de una frase: ``(moneda, valor, texto)``."""

    found: list[tuple[str, str, str]] = []
    for pattern in (_CURRENCY_AFTER, _CURRENCY_BEFORE):
        for match in pattern.finditer(sentence):
            value = _strict_value(match.group("num"))
            if value is not None:
                found.append((_currency(match.group("cur")), _key(value), match.group()))
    return found


def _converted_prices(observation: TurnObservation) -> list[str]:
    """Frases que presentan un precio convertido entre monedas.

    Es conversión si la frase da cifras en dos monedas distintas (con valores distintos)
    y alguna no la publica el respaldo, o si habla de convertir y da una cifra en una
    moneda que ningún resultado del turno usa. Mencionar la TRM sin cifra convertida no
    lo es.
    """

    folded_backing = _fold(" ".join(observation.backing))
    backing_currencies = {
        _currency(m.group()) for m in re.finditer(rf"\b(?:{_CURRENCY_WORDS})\b", folded_backing)
    }
    flagged: list[str] = []
    for sentence in _split_sentences(_fold(observation.answer)):
        priced = _priced(sentence)
        currencies = {currency for currency, _, _ in priced}
        values = {value for _, value, _ in priced}
        unsupported = [
            text for _, _, text in priced if unsupported_figures(text, observation.backing)
        ]
        two_currencies = len(currencies) >= 2 and len(values) >= 2 and bool(unsupported)
        foreign = bool(backing_currencies) and any(
            currency not in backing_currencies for currency in currencies
        )
        if two_currencies or (_CONVERSION.search(sentence) and foreign):
            flagged.append(sentence)
    return flagged


def _materials(observation: TurnObservation) -> set[str]:
    """Materiales de las tarjetas y opciones recomendadas (no los descartados)."""

    products = observation.products
    found: set[str] = set()
    for option in products.get("options") or []:
        if isinstance(option, Mapping) and option.get("material"):
            found.add(str(option["material"]).upper())
    ficha = products.get("ficha")
    if isinstance(ficha, Mapping) and ficha.get("material"):
        found.add(str(ficha["material"]).upper())
    for quote in observation.quotes:
        for entry in [quote.get("primary"), *(quote.get("alternatives") or [])]:
            if isinstance(entry, Mapping) and entry.get("material"):
                found.add(str(entry["material"]).upper())
    return found


@dataclass(frozen=True)
class CheckResult:
    """Resultado de una verificación con su detalle para la tabla de fallos."""

    name: str
    passed: bool
    detail: str = ""


# Una llamada con ``error`` (o argumentos inválidos) no cuenta como haber usado la
# herramienta; ``needs_input`` y ``empty`` sí, porque la consulta se hizo. Mismo criterio en
# ambos modos (en ``routes`` las derivadas nunca llevan ``error``).
_USED_STATUSES: Final = frozenset({"ok", "needs_input", "empty"})


def _tools_any(
    observation: TurnObservation, groups: Sequence[Sequence[str]], allow: bool
) -> CheckResult:
    used = {call.name for call in observation.tools if call.status in _USED_STATUSES}
    if allow and (observation.shortcut or used <= DECLARATION_TOOLS):
        return CheckResult("tools_any", True)
    missing = [list(group) for group in groups if not used & set(group)]
    detail = f"faltan {missing}; usó {sorted(used) or 'ninguna'}" if missing else ""
    return CheckResult("tools_any", not missing, detail)


def check_item(item: EvalItem, observation: TurnObservation) -> list[CheckResult]:
    """Verificaciones del ítem (claves presentes de ``expect``) y la global de SC-001."""

    expect = item.expect
    used = set(observation.tool_names)
    allow = bool(expect.get("no_tool_allowed"))
    checks: list[CheckResult] = []
    if "tools_any" in expect:
        checks.append(_tools_any(observation, expect["tools_any"], allow))
    elif "no_tool_allowed" in expect and not allow:
        # Sin herramientas esperadas, ``no_tool_allowed: false`` exige alguna consulta real.
        queried = {
            call.name for call in observation.tools if call.status in _USED_STATUSES
        } - DECLARATION_TOOLS
        checks.append(CheckResult("no_tool_allowed", bool(queried), "solo sin_consulta"))
    if "tools_none" in expect:
        called = sorted(used & set(expect["tools_none"]))
        checks.append(CheckResult("tools_none", not called, f"llamó {called}" if called else ""))
    asked = item.asked
    abstained = is_abstention(observation, ignore=asked)
    if "abstain" in expect:
        wanted = bool(expect["abstain"])
        checks.append(
            CheckResult(
                "abstain",
                abstained == wanted,
                "" if abstained == wanted else ("no se abstuvo" if wanted else "se abstuvo"),
            )
        )
    if expect.get("abstain_or_cites"):
        # La abstención no excusa cifras: solo puede repetir las de la pregunta. Si no se
        # abstiene, ni esas: toda cifra debe venir de una herramienta.
        loose = unsupported_figures(
            observation.answer, observation.backing, ignore=asked if abstained else ()
        )
        passed = not loose
        checks.append(CheckResult("abstain_or_cites", passed, "" if passed else f"cifras {loose}"))
    if "asks_for" in expect:
        passed = _asks_for(observation, list(expect["asks_for"]))
        checks.append(
            CheckResult("asks_for", passed, "" if passed else f"no pidió {expect['asks_for']}")
        )
    if expect.get("forbidden_materials"):
        bad = sorted(
            _materials(observation) & {str(m).upper() for m in expect["forbidden_materials"]}
        )
        checks.append(
            CheckResult("forbidden_materials", not bad, f"recomendó {bad}" if bad else "")
        )
    if expect.get("total_pending"):
        totals = [q.get("total") for q in observation.quotes if q.get("total") is not None]
        checks.append(CheckResult("total_pending", not totals, f"total {totals}" if totals else ""))
    if "max_quote_cards" in expect:
        count = len(observation.quotes)
        passed = count <= int(expect["max_quote_cards"])
        checks.append(CheckResult("max_quote_cards", passed, "" if passed else f"{count} tarjetas"))
    if expect.get("no_invented_terms"):
        terms = _invented_terms(observation)
        checks.append(
            CheckResult("no_invented_terms", not terms, f"afirmó {terms}" if terms else "")
        )
    if expect.get("no_currency_conversion"):
        converted = _converted_prices(observation)
        checks.append(
            CheckResult("no_currency_conversion", not converted, "; ".join(converted)[:120])
        )
    if "shortcut" in expect:
        wanted = bool(expect["shortcut"])
        passed = observation.shortcut == wanted
        checks.append(
            CheckResult("shortcut", passed, "" if passed else f"camino {observation.path}")
        )
    if "language" in expect:
        # Sin palabras funcionales suficientes (tarjetas, cifras) el idioma es indeterminado
        # y no cuenta como fallo.
        language = detect_language(observation.answer, exclude=_card_texts(observation))
        passed = language in ("", str(expect["language"]))
        checks.append(CheckResult("language", passed, "" if passed else f"idioma {language}"))
    loose = unsupported_figures(observation.answer, observation.backing, ignore=asked)
    checks.append(CheckResult(CHECK_SOURCED, not loose, f"sin fuente {loose}" if loose else ""))
    if observation.path == "fallback":
        # El respaldo determinista no es una respuesta del agente: el ítem no se da por
        # resuelto (se reporta aparte en «Turnos por atajo / por respaldo»).
        checks.append(
            CheckResult(CHECK_FALLBACK, False, f"respaldo ({observation.fallback_reason})")
        )
    return checks


# --- Corrida y agregados -------------------------------------------------------------------


@dataclass
class ItemResult:
    """Resultado de un ítem en un modo: observación del último turno y verificaciones."""

    item: EvalItem
    mode: str
    observation: TurnObservation | None = None
    checks: list[CheckResult] = field(default_factory=list)
    error: str = ""
    # Segundos de todo el ítem (todos sus turnos) hasta terminar o fallar.
    elapsed_s: float | None = None

    @property
    def passed(self) -> bool:
        return not self.error and all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]

    def check(self, name: str) -> CheckResult | None:
        return next((check for check in self.checks if check.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        observation = self.observation
        return {
            "id": self.item.id,
            "group": self.item.group,
            "mode": self.mode,
            "turns": list(self.item.turns),
            "expect": dict(self.item.expect),
            "passed": self.passed,
            "error": self.error,
            "checks": {check.name: check.passed for check in self.checks},
            "failures": [{"check": c.name, "detail": c.detail} for c in self.failures],
            "answer": observation.answer if observation else "",
            "tools": [asdict(call) for call in observation.tools] if observation else [],
            "path": observation.path if observation else "",
            "fallback_reason": observation.fallback_reason if observation else "",
            "abstained": (
                is_abstention(observation, ignore=self.item.asked) if observation else False
            ),
            "sources": [dict(s) for s in observation.sources] if observation else [],
            "quotes": len(observation.quotes) if observation else 0,
            "missing": list(observation.missing) if observation else [],
            "requested": list(observation.requested) if observation else [],
            "usage": dict(observation.usage or {}) if observation else {},
            "first_status_s": observation.first_status_s if observation else None,
            "first_text_s": observation.first_text_s if observation else None,
            "total_s": observation.total_s if observation else None,
            "elapsed_s": self.elapsed_s,
        }


# Latencias en las que un ítem con error cuenta como infinito.
_CENSORED_LATENCIES: Final = frozenset({"first_text_s", "total_s"})


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    """Percentil por rango más cercano, como el informe del enrutador."""

    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))]


@dataclass
class ModeReport:
    """Agregados de un modo sobre los ítems evaluados (tabla de contracts/eval-agent.md)."""

    mode: str
    results: list[ItemResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    def _observed(self) -> list[TurnObservation]:
        return [r.observation for r in self.results if r.observation is not None]

    @property
    def resolved(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def errors(self) -> int:
        return sum(bool(r.error) for r in self.results)

    @property
    def compound_total(self) -> int:
        return sum(r.item.group == "compuesta" for r in self.results)

    @property
    def compound_complete(self) -> int:
        """Compuestas con todas sus partes respaldadas: cada grupo con un resultado ``ok``."""

        complete = 0
        for result in self.results:
            observation = result.observation
            if result.item.group != "compuesta" or observation is None or result.error:
                continue
            if observation.path == "fallback":  # el respaldo no es una respuesta del agente
                continue
            ok = {call.name for call in observation.tools if call.status == "ok"}
            groups = result.item.expect.get("tools_any") or []
            if groups and all(ok & set(group) for group in groups):
                complete += 1
        return complete

    @property
    def abstain_total(self) -> int:
        return sum(bool(r.item.expect.get("abstain")) for r in self.results)

    @property
    def abstain_hits(self) -> int:
        hits = 0
        for result in self.results:
            check = result.check("abstain")
            if result.item.expect.get("abstain") and check is not None and check.passed:
                hits += 1
        return hits

    @property
    def material_violations(self) -> int:
        return sum(
            (check := r.check("forbidden_materials")) is not None and not check.passed
            for r in self.results
        )

    @property
    def unsourced(self) -> int:
        return sum(
            (check := r.check(CHECK_SOURCED)) is not None and not check.passed for r in self.results
        )

    @property
    def memory_answers(self) -> int:
        """Respuestas de memoria: solo ``sin_consulta`` donde se esperaba una consulta."""

        count = 0
        for result in self.results:
            observation = result.observation
            expect = result.item.expect
            if observation is None or observation.shortcut or observation.path == "fallback":
                continue
            if expect.get("no_tool_allowed") or expect.get("shortcut"):
                continue
            if set(observation.tool_names) <= DECLARATION_TOOLS:
                count += 1
        return count

    @property
    def shortcut_turns(self) -> int:
        return sum(o.shortcut for o in self._observed())

    @property
    def fallback_turns(self) -> int:
        return sum(o.path == "fallback" for o in self._observed())

    def latencies(self, name: str) -> list[float]:
        """Latencias observadas; un ítem con error cuenta como infinito en el primer texto y
        el fin, para que el modo que más falla no muestre un p95 mejor."""

        values = [getattr(o, name) for o in self._observed()]
        observed = [float(v) for v in values if v is not None]
        if name in _CENSORED_LATENCIES:
            observed += [math.inf] * self.errors
        return observed

    @property
    def degraded_turns(self) -> int:
        """Turnos atendidos por el respaldo porque el proveedor falló o no respondió a tiempo."""

        return sum(
            o.path == "fallback" and o.fallback_reason in DEGRADED_REASONS for o in self._observed()
        )

    @property
    def degraded(self) -> bool:
        """Ningún ítem que dependía del proveedor llegó a una respuesta del agente.

        Los atajos no llaman al proveedor y no cuentan; cada ítem restante terminó en error o
        en un respaldo por ``provider``/``timeout`` (clave inválida, DeepSeek caído o lento).
        """

        relevant = [
            r for r in self.results if r.error or (r.observation and not r.observation.shortcut)
        ]
        return bool(relevant) and all(
            r.error
            or (
                r.observation is not None
                and r.observation.path == "fallback"
                and r.observation.fallback_reason in DEGRADED_REASONS
            )
            for r in relevant
        )

    def _usage(self) -> list[Mapping[str, int]]:
        return [o.usage for o in self._observed() if o.usage]

    @property
    def tokens_in(self) -> float | None:
        usage = self._usage()
        return sum(u.get("input_tokens", 0) for u in usage) / len(usage) if usage else None

    @property
    def tokens_out(self) -> float | None:
        usage = self._usage()
        return sum(u.get("output_tokens", 0) for u in usage) / len(usage) if usage else None

    def cost(self, price_in: float | None, price_out: float | None) -> float | None:
        """Costo medio por turno en USD (precios por millón de tokens); ``None`` sin datos."""

        usage = self._usage()
        if not usage or price_in is None or price_out is None:
            return None
        costs = [
            (u.get("input_tokens", 0) * price_in + u.get("output_tokens", 0) * price_out) / 1e6
            for u in usage
        ]
        return sum(costs) / len(costs)

    def metrics(
        self, *, price_in: float | None = None, price_out: float | None = None
    ) -> dict[str, Any]:
        """Agregados serializables (JSON y metadatos del experimento)."""

        def rate(hits: int, total: int) -> float | None:
            return round(hits / total, 4) if total else None

        def seconds(name: str, quantile: float) -> float | str | None:
            value = _percentile(self.latencies(name), quantile)
            if value is None:
                return None
            return "inf" if math.isinf(value) else round(value, 3)

        cost = self.cost(price_in, price_out)
        return {
            "items": self.total,
            "resolved": self.resolved,
            "resolved_rate": rate(self.resolved, self.total),
            "compound_total": self.compound_total,
            "compound_complete": self.compound_complete,
            "compound_rate": rate(self.compound_complete, self.compound_total),
            "abstain_total": self.abstain_total,
            "abstain_hits": self.abstain_hits,
            "abstain_rate": rate(self.abstain_hits, self.abstain_total),
            "material_violations": self.material_violations,
            "unsourced": self.unsourced,
            "memory_answers": self.memory_answers,
            "first_text_p50_s": seconds("first_text_s", 0.5),
            "first_text_p95_s": seconds("first_text_s", 0.95),
            "total_p50_s": seconds("total_s", 0.5),
            "total_p95_s": seconds("total_s", 0.95),
            "first_status_p95_s": seconds("first_status_s", 0.95),
            "tokens_in_per_turn": round(self.tokens_in, 1) if self.tokens_in is not None else None,
            "tokens_out_per_turn": (
                round(self.tokens_out, 1) if self.tokens_out is not None else None
            ),
            "turns_with_usage": len(self._usage()),
            "cost_per_turn_usd": round(cost, 6) if cost is not None else None,
            "shortcut_turns": self.shortcut_turns,
            "fallback_turns": self.fallback_turns,
            "degraded_turns": self.degraded_turns,
            "degraded": self.degraded,
            "errors": self.errors,
        }


def _run_item(driver: ModeDriver, item: EvalItem) -> TurnObservation:
    """Ejecuta los turnos del ítem en su hilo; devuelve la observación del último.

    Antes de cada índice de ``new_thread_before`` se abre otra conversación del mismo dueño:
    ``thread_id`` con sufijo ``-h{n}`` y semilla vacía, con el mismo ``owner_key``.
    """

    base = f"eval-{driver.mode}-{item.id}"
    thread_id = base
    owner_key = f"eval-{driver.mode}-{item.id}"
    seed: list[Turn] = list(item.history)
    observation: TurnObservation | None = None
    for index, question in enumerate(item.turns):
        if index in item.new_thread_before:
            thread_id = f"{base}-h{item.new_thread_before.index(index) + 1}"
            seed = []
        observation = driver.run_turn(
            owner_key=owner_key, thread_id=thread_id, question=question, seed_turns=list(seed)
        )
        seed.append(Turn(question=question, answer=observation.answer, incomplete=False))
    if observation is None:  # ``_item`` exige al menos un turno
        raise ValueError(f"El ítem {item.id} no tiene turnos.")
    return observation


def evaluate_mode(
    driver: ModeDriver,
    items: Sequence[EvalItem],
    *,
    progress: Callable[[ItemResult], None] | None = None,
    report: ModeReport | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> ModeReport:
    """Corre cada ítem con el driver y lo puntúa; un fallo de un ítem no corta la corrida.

    Cada resultado se añade a ``report`` en cuanto termina: si la corrida se interrumpe
    (``KeyboardInterrupt``), quien llama conserva lo ya corrido.
    """

    report = report if report is not None else ModeReport(mode=driver.mode)
    for item in items:
        result = ItemResult(item=item, mode=driver.mode)
        started = clock()
        try:
            observation = _run_item(driver, item)
        except Exception as error:  # red, proveedor o grafo: el ítem queda como error
            logger.warning(
                "eval_agent.item_failed", item=item.id, mode=driver.mode, error=repr(error)
            )
            result.error = f"{type(error).__name__}: {error}"[:300]
        else:
            result.observation = observation
            result.checks = check_item(item, observation)
        result.elapsed_s = clock() - started
        report.results.append(result)
        if progress is not None:
            progress(result)
    return report


# --- Informe -------------------------------------------------------------------------------


def _pct(hits: int, total: int) -> str:
    return f"{hits / total:.1%} ({hits}/{total})" if total else "—"


def _secs(value: float | None) -> str:
    if value is None:
        return "—"
    return "∞" if math.isinf(value) else f"{value:.2f}"


def _num(value: float | None) -> str:
    return f"{value:.0f}" if value is not None else "sin dato"


def _warnings(reports: Sequence[ModeReport], interrupted: bool) -> list[str]:
    """Avisos al inicio del informe: corrida incompleta o proveedor degradado."""

    warnings: list[str] = []
    if interrupted:
        warnings.append("La corrida fue interrumpida: el informe cubre solo los ítems terminados.")
    for report in reports:
        if report.degraded:
            warnings.append(
                f"El modo `{report.mode}` no obtuvo ninguna respuesta del proveedor (todo "
                "terminó en error o en respaldo por `provider`/`timeout`): revisa la clave, "
                "la red y el plazo antes de leer sus métricas."
            )
        elif report.errors or report.degraded_turns:
            warnings.append(
                f"El modo `{report.mode}` tuvo {report.errors} ítem(s) con error y "
                f"{report.degraded_turns} turno(s) por respaldo del proveedor."
            )
    return [f"> **Aviso:** {warning}" for warning in warnings]


def render_report(
    reports: Sequence[ModeReport],
    question_set: QuestionSet,
    *,
    price_in: float | None = None,
    price_out: float | None = None,
    interrupted: bool = False,
) -> str:
    """Informe Markdown: avisos, métricas por modo, fallos por ítem y limitación de origen."""

    modes = [report.mode for report in reports]
    rows: list[tuple[str, Callable[[ModeReport], str]]] = [
        ("Ítems resueltos (todas las verificaciones)", lambda r: _pct(r.resolved, r.total)),
        (
            "Compuestas con todas las partes",
            lambda r: _pct(r.compound_complete, r.compound_total),
        ),
        ("Abstenciones correctas", lambda r: _pct(r.abstain_hits, r.abstain_total)),
        (
            "Violaciones de material / datos sin fuente",
            lambda r: f"{r.material_violations} / {r.unsourced}",
        ),
        (
            "Respuestas de memoria (`sin_consulta` en pregunta factual)",
            lambda r: str(r.memory_answers),
        ),
        (
            "Latencia primer texto p95 / fin p95 (s)",
            lambda r: (
                f"{_secs(_percentile(r.latencies('first_text_s'), 0.95))} / "
                f"{_secs(_percentile(r.latencies('total_s'), 0.95))}"
            ),
        ),
        (
            "Latencia primer texto p50 / fin p50 (s)",
            lambda r: (
                f"{_secs(_percentile(r.latencies('first_text_s'), 0.5))} / "
                f"{_secs(_percentile(r.latencies('total_s'), 0.5))}"
            ),
        ),
        (
            "Primera línea de estado p95 (s)",
            lambda r: _secs(_percentile(r.latencies("first_status_s"), 0.95)),
        ),
        (
            "Tokens entrada / salida por turno",
            lambda r: f"{_num(r.tokens_in)} / {_num(r.tokens_out)}",
        ),
        (
            "Costo medio por turno (USD)",
            lambda r: (
                f"{cost:.5f}"
                if (cost := r.cost(price_in, price_out)) is not None
                else ("sin precios" if price_in is None or price_out is None else "sin dato")
            ),
        ),
        (
            "Turnos por atajo / por respaldo",
            lambda r: f"{r.shortcut_turns} / {r.fallback_turns}",
        ),
        ("Ítems con error", lambda r: str(r.errors)),
        (
            "Turnos por respaldo del proveedor (`provider`/`timeout`)",
            lambda r: str(r.degraded_turns),
        ),
    ]
    details = [
        f"versión {question_set.version}" if question_set.version is not None else "",
        f"congelado el {question_set.frozen_at}" if question_set.frozen_at else "",
    ]
    described = ", ".join(detail for detail in details if detail)
    evaluated = max((report.total for report in reports), default=0)
    lines = [
        "# Evaluación del agente: routes frente a tools",
        "",
        f"Conjunto: `{question_set.path}`"
        + (f" ({described})" if described else "")
        + f"; ítems evaluados: {evaluated}.",
        "",
    ]
    warnings = _warnings(reports, interrupted)
    if warnings:
        lines += [*warnings, ""]
    lines += [
        "| Métrica | " + " | ".join(modes) + " |",
        "|---|" + "---|" * len(modes),
    ]
    for label, cell in rows:
        lines.append(f"| {label} | " + " | ".join(cell(report) for report in reports) + " |")
    lines += [
        "",
        "Tokens: en `tools`, `usage_metadata` de todos los pasos del modelo del turno; en "
        "`routes`, el uso del proveedor de texto si lo publica (sin dato si no). Solo se mide "
        "el último turno de cada ítem. Un ítem con error cuenta como latencia infinita (∞) "
        "en el primer texto y el fin. Un turno por respaldo no cuenta como resuelto ni como "
        "compuesta completa.",
    ]
    failures = [r for report in reports for r in report.results if not r.passed]
    lines += ["", "## Fallos por ítem", ""]
    if not failures:
        lines.append("Ningún ítem falló.")
    else:
        lines += ["| Ítem | Grupo | Modo | Verificación | Detalle |", "|---|---|---|---|---|"]
        for result in failures:
            if result.error:
                lines.append(
                    f"| `{result.item.id}` | {result.item.group} | {result.mode} | error | "
                    f"{_cell(result.error)} |"
                )
                continue
            for check in result.failures:
                lines.append(
                    f"| `{result.item.id}` | {result.item.group} | {result.mode} | "
                    f"{check.name} | {_cell(check.detail)} |"
                )
    lines += [
        "",
        "## Limitación de origen del conjunto (FR-016)",
        "",
        question_set.origin
        or "El conjunto no declara su origen: trátalo como posible sesgo de quien lo redactó.",
    ]
    return "\n".join(lines)


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())[:200]


def results_payload(
    reports: Sequence[ModeReport],
    question_set: QuestionSet,
    *,
    price_in: float | None = None,
    price_out: float | None = None,
    interrupted: bool = False,
) -> dict[str, Any]:
    """Resultados crudos por ítem y agregados por modo, para ``--json``."""

    return {
        "questions": str(question_set.path),
        "interrupted": interrupted,
        "version": question_set.version,
        "frozen_at": question_set.frozen_at,
        "origin": question_set.origin,
        "prices_usd_per_million": {"input": price_in, "output": price_out},
        "modes": {
            report.mode: {
                "metrics": report.metrics(price_in=price_in, price_out=price_out),
                "items": [result.to_dict() for result in report.results],
            }
            for report in reports
        },
    }


def experiment_rows(report: ModeReport) -> list[dict[str, Any]]:
    """Filas para ``register_experiment``: entradas, verdad de referencia y salida."""

    rows: list[dict[str, Any]] = []
    for result in report.results:
        data = result.to_dict()
        inputs: dict[str, Any] = {"turns": list(result.item.turns)}
        if result.item.history:
            inputs["history"] = [dict(turn) for turn in result.item.history]
        total = result.observation.total_s if result.observation else result.elapsed_s or 0.0
        rows.append(
            {
                "id": result.item.id,
                "inputs": inputs,
                "expected": dict(result.item.expect),
                "outputs": {
                    key: data[key]
                    for key in ("answer", "tools", "path", "passed", "failures", "error", "usage")
                },
                "latency_ms": round(total * 1000, 3),
            }
        )
    return rows


# --- Runtime en proceso --------------------------------------------------------------------


@dataclass
class EvalRuntime:
    """Drivers listos por modo y el cierre de los recursos compartidos."""

    drivers: dict[str, ModeDriver]
    close: Callable[[], None] = lambda: None


def build_tool_chat(settings: Any) -> Any:
    """``ChatOpenAI`` de DeepSeek del modo ``tools``, como ``agent_runtime`` (temperatura 0)."""

    from carvajal.adapters.llm.deepseek import build_deepseek_chat

    timeout = min(settings.llm_timeout_seconds, settings.agent_turn_timeout_s)
    return build_deepseek_chat(
        settings, stream_usage=True, for_tools=True, timeout_s=timeout, max_retries=0
    )


def _text_provider(settings: Any) -> tuple[Any, UsageProbe | None]:
    """Proveedor de texto del modo ``routes``; con DeepSeek, además, su uso de tokens."""

    if settings.llm_provider.strip().lower() == "deepseek":
        from carvajal.adapters.llm._chat import _LangChainChat
        from carvajal.adapters.llm.deepseek import build_deepseek_chat

        recorder = UsageRecorder(build_deepseek_chat(settings, stream_usage=True))
        return _LangChainChat(recorder), recorder
    from carvajal.adapters.llm import build_llm_provider

    # Otros proveedores no exponen su uso por este contrato: el informe dice «sin dato».
    return build_llm_provider(settings), None


_REPO_MARKER: Final = Path("apps") / "api" / "chat" / "agent.py"


def ensure_apps_importable() -> None:
    """Pone en ``sys.path`` la raíz del checkout para importar ``apps`` (los runners reales).

    El script de consola ``carvajal`` no añade el directorio actual a ``sys.path`` (a
    diferencia de ``python -m``) y el ``.pth`` editable solo apunta a ``packages/core/src``.
    Se busca la raíz hacia arriba desde este módulo (instalación editable) y desde el
    directorio actual; solo se añade una carpeta que contenga ``apps/api/chat/agent.py``.
    """

    try:
        if importlib.util.find_spec("apps.api.chat.agent") is not None:
            return
    except ModuleNotFoundError:
        pass
    here = Path(__file__).resolve()
    cwd = Path.cwd().resolve()
    for root in (*here.parents, cwd, *cwd.parents):
        if (root / _REPO_MARKER).is_file():
            sys.path.insert(0, str(root))
            importlib.invalidate_caches()
            return


def _routes_router(settings: Any) -> tuple[Any, UsageProbe | None, bool]:
    """Enrutador de ``routes`` con su sonda de uso.

    Devuelve ``(router, sonda, consume_tokens)``. Con ``AGENT_ROUTER=llm`` el enrutador
    llama al proveedor en cada turno: se mide con el mismo :class:`UsageRecorder` si el
    proveedor es DeepSeek; si no, su uso queda desconocido.
    """

    if settings.agent_router == "llm":
        from carvajal.agent.routers.llm import LLMRouter

        provider, probe = _text_provider(settings)
        return LLMRouter(provider), probe, True
    from carvajal.agent.routers import build_router

    return build_router(settings.agent_router, settings), None, False


def build_runtime(
    settings: Any,
    modes: Sequence[str] = MODES,
    *,
    vector_store: Any = None,
    embedder: Any = None,
    tool_chat: Any = None,
    llm: Any = None,
    usage: UsageProbe | None = None,
    catalog: Any = None,
    router: Any = None,
    clock: Callable[[], float] = time.perf_counter,
) -> EvalRuntime:
    """Construye los modos pedidos sobre el mismo índice, embedder y catálogo.

    Persistencia en memoria (``InMemorySaver``/``InMemoryStore``, una pareja por modo). Sin
    dobles, usa Qdrant (envuelto en ``LayerPriorityStore``), Ollama y DeepSeek con la
    configuración del entorno, como ``apps/api/agent_runtime.py``. ``catalog`` es ``None``
    por defecto (el de producción, con el contrato comercial de Colombia).
    """

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    ensure_apps_importable()
    from apps.api.chat.agent import GraphAgentRunner, ToolAgentRunner
    from carvajal.agent.graph import AgentDeps, build_agent
    from carvajal.agent.router import decide_route
    from carvajal.agent.toolagent.graph import build_tool_agent
    from carvajal.agent.toolagent.tools import ToolDeps

    unknown = sorted(set(modes) - set(MODES))
    if unknown:
        raise ValueError(f"Modos desconocidos: {unknown}")
    closers: list[Callable[[], None]] = []
    if vector_store is None:
        from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
        from carvajal.application.retrieval import LayerPriorityStore

        qdrant = QdrantVectorStore(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection=settings.qdrant_collection,
            dimensions=settings.qdrant_dimensions,
            distance=settings.qdrant_distance,
        )
        closers.append(qdrant.close)
        vector_store = LayerPriorityStore(qdrant, min_score=settings.rag_min_score)
    if embedder is None:
        from carvajal.adapters.embeddings import OllamaEmbedder

        embedder = OllamaEmbedder(base_url=settings.ollama_base_url)

    def close() -> None:
        for closer in closers:
            closer()

    def routes_graph(selected: Any, checkpointer: Any, store: Any) -> Any:
        deps = AgentDeps(
            vector_store=vector_store, embedder=embedder, settings=settings, catalog=catalog
        )
        return build_agent(deps, checkpointer=checkpointer, store=store, router=selected)

    drivers: dict[str, ModeDriver] = {}
    try:
        if "routes" in modes:
            router_usage: UsageProbe | None = None
            router_spends = False
            if router is None:
                router, router_usage, router_spends = _routes_router(settings)
            graph = routes_graph(router, InMemorySaver(), InMemoryStore())
            text_llm, text_usage = (llm, usage) if llm is not None else _text_provider(settings)
            routes_usage = CombinedUsage(text_usage, router_usage) if router_spends else text_usage
            drivers["routes"] = RoutesDriver(
                GraphAgentRunner(graph, settings),
                text_llm,
                usage=routes_usage,
                graph=graph,
                clock=clock,
            )
        if "tools" in modes:
            checkpointer, store = InMemorySaver(), InMemoryStore()
            deps = ToolDeps(
                vector_store=vector_store, embedder=embedder, settings=settings, catalog=catalog
            )
            tool_graph = build_tool_agent(
                deps,
                tool_chat if tool_chat is not None else build_tool_chat(settings),
                checkpointer=checkpointer,
                store=store,
            )
            # Respaldo determinista: grafo ``routes`` con reglas en el mismo checkpointer,
            # como el runtime de la app (FR-009).
            fallback_graph = routes_graph(decide_route, checkpointer, store)
            drivers["tools"] = ToolsDriver(
                ToolAgentRunner(tool_graph, settings),
                graph=tool_graph,
                fallback_runner=GraphAgentRunner(fallback_graph, settings),
                clock=clock,
            )
    except Exception:
        close()
        raise
    ordered = {mode: drivers[mode] for mode in MODES if mode in drivers}
    return EvalRuntime(drivers=ordered, close=close)
