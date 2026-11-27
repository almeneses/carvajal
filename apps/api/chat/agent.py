"""Adaptador entre el grafo LangGraph y el servicio de chat.

El servicio no conoce LangGraph: recibe un ``AgentRunner`` con dos operaciones
—preparar el turno y registrar el resultado—. Las pruebas inyectan un doble y
la app real usa el grafo con PostgresSaver/Store y Qdrant.
"""

import contextvars
import json
import math
import queue
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final, Protocol, runtime_checkable

import structlog
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from carvajal.agent.gatekeeper.claims import ECHO_KEYS, build_claims_query, judgeable
from carvajal.agent.gatekeeper.model import GatekeeperDecision, llm_class
from carvajal.agent.gatekeeper.stats import CLAIMS_STATS, ClaimsStats
from carvajal.agent.memory.store import CATEGORIES, MemoryProfile
from carvajal.agent.memory.summary import ConversationSummary, seed_turn_count
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.evidence import (
    FAREWELL_ANSWER,
    FORGET_ANSWER,
    GATE_ABSTAIN,
    GATE_EVIDENCE,
    GATE_NO_QUERY,
    GREETING_ANSWER,
    MEMORY_UNAVAILABLE_ANSWER,
    NO_DATA_ANSWER,
    OUT_OF_SCOPE_ANSWER,
    PREFERENCE_ANSWER,
    GatePath,
    abstention,
    ask_for_missing,
    backing_data,
    filtered_evidence,
    gate,
    missing_fields,
    neutral_only,
    out_of_scope,
    presented_data,
    sites_without_price,
    tool_misses,
    unsupported_terms,
)
from carvajal.agent.toolagent.graph import (
    FAREWELL_INTENT,
    FORGET_INTENT,
    UNAVAILABLE_INTENT,
    TurnCancel,
    TurnCancelled,
    recursion_limit,
    window_turns,
)
from carvajal.agent.toolagent.state import (
    TOOL_COTIZAR,
    validate_question,
)
from carvajal.agent.toolagent.status import REVIEW_STATUS
from carvajal.agent.toolagent.tools import (
    CORPUS_TOOLS,
    DECLARATION_TOOLS,
    MEMORY_TOOLS,
    ROUTE_GROUPS,
    content_data,
)
from carvajal.application.ask import Source
from carvajal.application.ports import ClaimsJudge, JudgeUnavailable, LLMProvider
from carvajal.prompts import NO_CONTEXT_ANSWER
from carvajal.quotation.cards import strip_imitated_cards

__all__ = [
    "RETAIN_CHARS",
    "AgentRunner",
    "AgentTurn",
    "GraphAgentRunner",
    "ToolAgentEvent",
    "ToolAgentFinal",
    "ToolAgentRunner",
    "ToolAgentSources",
    "ToolAgentStatus",
    "ToolAgentToken",
    "ToolStreamRunner",
    "TurnTimeout",
    "default_agent_runner",
    "default_tool_agent_runner",
]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AgentTurn:
    """Resultado de preparar un turno: ruta, prompt y citas."""

    route: str
    intent: str
    prompt: str | None
    system: str
    sources: tuple[Source, ...]
    turns: tuple[Turn, ...]
    preferences: tuple[str, ...] = ()
    tool: str = ""
    tool_period: str = ""
    reason: str = ""
    query: str = ""
    # Resultado estructurado de la ruta comercial (spec 036/043): opciones, ficha,
    # ofertas, sedes, cotización y aclaraciones.
    products: dict[str, Any] = field(default_factory=dict)
    # Contexto comercial persistido en el hilo (ciudad, cantidad, producto, entrega).
    commercial_context: dict[str, Any] = field(default_factory=dict)
    # Resumen acumulado del hilo (spec 045): solo ``covered_turns`` y ``method``.
    summary: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentRunner(Protocol):
    """Contrato que consume ``ChatService``."""

    def prepare(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
    ) -> AgentTurn:
        """Decide la ruta y prepara el prompt sin generar texto."""

        ...

    def record(
        self,
        thread_id: str,
        *,
        question: str,
        answer: str,
        incomplete: bool,
        turns: Sequence[Turn],
    ) -> None:
        """Persiste el turno en el checkpointer del hilo."""

        ...


def _source(payload: dict[str, Any]) -> Source:
    """Reconstruye una cita desde el estado serializado del grafo."""

    return Source(
        chunk_id=str(payload.get("chunk_id", "")),
        slug=str(payload.get("slug", "")),
        section=str(payload.get("section", "")),
        layer=str(payload.get("layer", "")),
        score=float(payload.get("score", 0.0)),
        excerpt=str(payload.get("excerpt", "")),
        url=str(payload.get("url", "")),
        title=str(payload.get("title", "")),
        source_type=str(payload.get("source_type", "oficial")),
        origin=str(payload.get("origin", "")),
        category=str(payload.get("category", "")),
        entity=str(payload.get("entity", "")),
    )


class GraphAgentRunner:
    """Runner real sobre el grafo compilado."""

    def __init__(self, graph: Any, settings: Any) -> None:
        self._graph = graph
        self._settings = settings

    def prepare(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
    ) -> AgentTurn:
        """Invoca el grafo con el hilo como ``thread_id``."""

        state = self._graph.invoke(
            {
                "question": question,
                "owner_key": owner_key,
                "thread_id": thread_id,
                "seed_turns": list(seed_turns),
                # Spec 045 (FR-008): turnos persistidos del hilo antes de este; la semilla
                # del servicio (``SeedTurns``) lo trae, si no se usa su largo.
                "turn_count": _turn_count(seed_turns),
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        sources = tuple(_source(payload) for payload in state.get("sources", []))
        turns = tuple(state.get("turns", []))
        route = str(state.get("route", "direct"))
        return AgentTurn(
            route=route,
            intent=str(state.get("intent", "")),
            prompt=state.get("prompt"),
            system=str(state.get("system", "")),
            sources=sources,
            turns=turns,
            preferences=tuple(state.get("preferences", [])),
            tool=str(state.get("tool_name", "")),
            tool_period=str(state.get("tool_period", "")),
            reason=str(state.get("reason", "")),
            query=str(state.get("query", "")),
            products=dict(state.get("tool_result") or {}) if route == "commercial" else {},
            commercial_context=dict(state.get("commercial_context") or {}),
            summary=_summary_meta(state.get("conversation_summary")),
        )

    def record(
        self,
        thread_id: str,
        *,
        question: str,
        answer: str,
        incomplete: bool,
        turns: Sequence[Turn],
    ) -> None:
        """Añade el turno al checkpoint; un fallo aquí no rompe la respuesta."""

        updated = [*turns]
        if not (
            updated and updated[-1]["question"] == question and updated[-1]["answer"] == answer
        ):
            updated.append({"question": question, "answer": answer, "incomplete": incomplete})
        try:
            self._graph.update_state(
                {"configurable": {"thread_id": thread_id}},
                {"turns": updated, "answer": answer, "incomplete": incomplete},
            )
        except Exception as error:  # el checkpointer no debe tumbar la UI
            logger.warning("agent.checkpoint_failed", error=str(error), thread_id=thread_id)


def _summary_meta(value: Any) -> dict[str, Any]:
    """``covered_turns`` y ``method`` del canal del resumen (sin el texto) para ``trace``."""

    summary = ConversationSummary.from_value(value)
    if summary is None:
        return {}
    return {"covered_turns": summary.covered_turns, "method": summary.method}


def _turn_count(seed_turns: Sequence[Turn]) -> int:
    """``turn_count`` de la semilla (``SeedTurns``) o, sin él, el largo de la semilla."""

    return seed_turn_count(seed_turns)


def default_agent_runner() -> GraphAgentRunner:
    """Runner real; importa el runtime de forma diferida para no abrir conexiones."""

    from apps.api.agent_runtime import agent_runtime
    from carvajal.infrastructure.settings import Settings

    runtime = agent_runtime()
    return GraphAgentRunner(runtime.graph, Settings())


# --- Modo ``tools`` (spec 044) ------------------------------------------------------------

# Caracteres que se retienen de cada paso del modelo antes de transmitirlo (research R2):
# DeepSeek declara las llamadas a herramientas en los primeros deltas, así que un paso que
# pasa de aquí sin ``tool_call_chunk`` es la respuesta final.
RETAIN_CHARS: Final = 48
_AGENT_NODE: Final = "agent"
# Redacción del atajo (T039): el mismo modelo, sin herramientas.
_DIRECT_NODE: Final = "direct"
_MODEL_NODES: Final = frozenset({_AGENT_NODE, _DIRECT_NODE})
_TOOLS_NODE: Final = "tools"
_RECALL_NODE: Final = "recall"
# Claves de un resultado que no respaldan cifras: ``message`` repite argumentos del modelo
# («No hay sedes del directorio en {ciudad}»). ``aviso`` y ``busqueda_nombre`` de
# ``buscar_producto`` son prosa y conteos sobre la búsqueda por ``nombre`` (un argumento
# del modelo): ya no lo repiten, pero sus cifras son conteos, no datos del catálogo.
_ECHO_KEYS: Final = ECHO_KEYS
# ``updates`` se suma a ``messages`` y ``custom``: marca el fin de cada paso del modelo y
# trae los canales del estado (resultados, fuentes, tarjetas) sin leer el checkpointer.
_STREAM_MODES: Final = ["messages", "custom", "updates"]


@dataclass(frozen=True)
class ToolAgentStatus:
    """Línea de estado de una herramienta que empieza (evento SSE ``status``)."""

    text: str
    step: int


@dataclass(frozen=True)
class ToolAgentToken:
    """Fragmento de la respuesta ya liberado por la retención y la compuerta."""

    text: str


@dataclass(frozen=True)
class ToolAgentSources:
    """Fuentes ``[n]`` del turno, justo antes del primer ``ToolAgentToken`` (evento ``meta``).

    El cliente sella cada párrafo con las fuentes que conoce al recibirlo: si llegaran
    después del texto, los ``[n]`` de los párrafos ya sellados quedarían a la vista. Vacías
    cuando el turno se abstiene.
    """

    sources: tuple[Source, ...]


@dataclass(frozen=True)
class ToolAgentFinal:
    """Cierre del turno del modo ``tools``: lo que el servicio persiste y muestra.

    - ``answer``: todo el texto emitido en ``ToolAgentToken`` (la abstención incluida).
    - ``gate``: camino de la compuerta (``evidence``, ``abstain`` o ``no_query``) y
      ``abstained`` si la respuesta del modelo se sustituyó por una abstención
      (:func:`~carvajal.agent.toolagent.evidence.abstention`: las búsquedas vacías del
      turno, ``NO_CONTEXT_ANSWER`` si buscó en el corpus o ``NO_DATA_ANSWER`` si no consultó
      nada). El reemplazo neutro del camino ``no_query`` (petición del dato o sedes sin
      precio) y la respuesta de conversación de un turno sin búsquedas (despedida, saludo o
      preferencia) no son abstenciones: ``abstained`` queda en ``False``.
    - ``sources``: citas en orden ``[n]``; vacías si hubo abstención.
    - ``products``: tarjetas comerciales (``cards``) con ``quote`` (primera cotización, para
      lectores actuales) y ``quotes``; ``quotes`` también va aparte.
    - ``results``: un ``ToolOutcome`` por herramienta, para el monitoreo.
    - ``turns``: los ``seed_turns`` recibidos, para ``record``.
    - ``usage``: tokens sumados de todos los pasos del modelo (SC-008).
    - ``summary``: ``covered_turns`` y ``method`` del resumen del hilo (spec 045), sin texto.
    - ``gatekeeper``: decisión del gatekeeper del turno (spec 046, canal del estado), con
      ``llm_class`` derivada de las consultas ejecutadas cuando decidió el LLM.
    - ``claims``: veredicto de la compuerta de afirmaciones (spec 046, data-model).
    """

    answer: str
    gate: GatePath
    abstained: bool
    sources: tuple[Source, ...]
    products: dict[str, Any]
    quotes: tuple[dict[str, Any], ...]
    commercial_context: dict[str, Any]
    results: tuple[dict[str, Any], ...]
    mode_path: str
    preferences: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    turns: tuple[Turn, ...] = ()
    usage: dict[str, int] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    gatekeeper: dict[str, Any] = field(default_factory=dict)
    claims: dict[str, Any] = field(default_factory=dict)


ToolAgentEvent = ToolAgentStatus | ToolAgentSources | ToolAgentToken | ToolAgentFinal


class TurnTimeout(TimeoutError):
    """Venció ``agent_turn_timeout_s`` antes del primer texto del turno (FR-009, FR-010).

    Es la señal del respaldo: :meth:`ToolAgentRunner.stream` la lanza en lugar de producir
    ``ToolAgentFinal`` cuando el plazo vence antes de emitir el primer ``ToolAgentToken``
    (pueden haber salido ``ToolAgentStatus``). El grafo ya quedó cancelado y su stream
    cerrado, así que el servicio puede atender el turno por el respaldo determinista
    (research R8). Hereda de ``TimeoutError`` para que el servicio la trate igual que un
    plazo del proveedor. ``final`` lleva lo obtenido hasta el corte, para el monitoreo; su
    ``answer`` siempre es vacío.

    Si el texto ya empezó, el plazo no corta: el turno termina normal, porque el respaldo
    ya no puede sustituir lo que la persona está leyendo.
    """

    def __init__(self, timeout_s: float, final: "ToolAgentFinal") -> None:
        super().__init__(f"el turno superó el plazo de {timeout_s:g} s antes del primer texto")
        self.timeout_s = timeout_s
        self.final = final


@runtime_checkable
class ToolStreamRunner(Protocol):
    """Contrato del modo ``tools``: el turno se transmite en vez de prepararse."""

    def stream(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
        *,
        draft: Any = None,
    ) -> Iterator[ToolAgentEvent]:
        """Ejecuta el grafo y produce ``status``, fuentes, ``token`` y un ``ToolAgentFinal``.

        Cerrar el iterador cancela el turno: el grafo deja de generar y de consultar.
        ``draft`` es el borrador de la cotización del hilo (spec 054), para la compuerta.
        """

        ...

    def record(
        self,
        thread_id: str,
        *,
        question: str,
        answer: str,
        incomplete: bool,
        turns: Sequence[Turn],
    ) -> None:
        """Cierra el turno en el checkpointer, como ``AgentRunner.record``."""

        ...


def _text(content: Any) -> str:
    """Texto de un mensaje, sea cadena o lista de bloques de contenido."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return ""


# Margen sobre ``JEV_TIMEOUT_S`` que debe quedar del plazo del turno para llamar a la
# compuerta (FR-030).
CLAIMS_MARGIN_S: Final = 0.2


@dataclass
class _ClaimsGate:
    """Compuerta de afirmaciones del turno (spec 046, R6 y R7).

    ``mode`` se fija al empezar el turno: ``enforce`` (retiene el texto con evidencia
    completo y sustituye lo no respaldado), ``shadow`` (juzga y registra sin sustituir ni
    retener) u ``off`` (juez apagado o no disponible: la retención de la 044).
    ``remaining`` da los segundos que quedan del plazo del turno y ``begin_text`` marca el
    inicio del texto antes de la llamada, para que el plazo no descarte la respuesta.
    ``stats`` recibe cada veredicto de Jev (juzgada o sin veredicto) para el bloque
    ``claims`` de ``/health``; ``None`` no cuenta nada.
    """

    judge: ClaimsJudge | None = None
    mode: str = "off"
    threshold: float = 0.3
    timeout_s: float = 0.8
    settings: Any = None
    remaining: Callable[[], float] = lambda: math.inf
    begin_text: Callable[[], bool] = lambda: True
    stats: ClaimsStats | None = None

    @property
    def active(self) -> bool:
        return self.judge is not None and self.mode in ("enforce", "shadow")

    @property
    def holds(self) -> bool:
        return self.active and self.mode == "enforce"


@dataclass
class _ModelStep:
    """Un paso del modelo mientras se transmite, con el camino vigente al empezar."""

    path: GatePath
    # ``evidence`` cuya evidencia no viene del corpus tras una búsqueda documental vacía
    # (:func:`filtered_evidence`): el texto se retiene y se filtra como en ``no_query``.
    filtered: bool = False
    pending: list[str] = field(default_factory=list)
    emitted: list[str] = field(default_factory=list)
    released: bool = False
    tool_call: bool = False


class _TurnStream:
    """Máquina de la retención por camino (research R2) sobre los eventos del grafo.

    Cada método devuelve los eventos a emitir. El camino de un paso se fija al empezar,
    con los ``results`` ya conocidos: las llamadas del propio paso aún no corrieron.
    """

    def __init__(
        self,
        question: str,
        seed_turns: Sequence[Turn] = (),
        turn_count: int | None = None,
        claims: _ClaimsGate | None = None,
        draft: Any = None,
    ) -> None:
        self.question = question
        self.gate_claims = claims or _ClaimsGate()
        # Borrador de la cotización con el que empezó el turno (spec 054): sus líneas viajan
        # a la compuerta de afirmaciones como evidencia (``draft_extract``).
        self.draft = draft
        # Línea de estado de la revisión: se emite una vez, tras la última de herramienta.
        self.reviewing = False
        # Veredicto pendiente: la línea de revisión sale antes de la llamada a Jev y el
        # runner ejecuta esto después de emitirla (:meth:`take_deferred`).
        self._deferred: Callable[[], list[ToolAgentEvent]] | None = None
        self.last_status_step = 0
        # Semilla del turno: las preguntas anteriores de la persona respaldan lo que ella
        # dijo (spec 045, R4); los turnos anteriores a «olvidar todo» no.
        self.seed = [dict(turn) for turn in seed_turns if isinstance(turn, Mapping)]
        self.turn_count = turn_count if turn_count is not None else len(self.seed)
        self.values: dict[str, Any] = {}
        self.tool_texts: list[str] = []
        # Datos de cada ``ToolMessage`` del turno (herramienta, datos): construyen el
        # reemplazo neutro cuando el filtro rechaza el texto de ``no_query``.
        self.tool_payloads: list[tuple[str, dict[str, Any]]] = []
        # Contexto comercial con el que empezó el turno: el que actualizan las herramientas
        # lleva argumentos del modelo y no respalda cifras.
        self.prior_context: dict[str, Any] = {}
        self.step: _ModelStep | None = None
        self.answer: list[str] = []
        self.abstained = False
        self.announced = False
        # El turno ya tiene respuesta (abstención anticipada): el runner detiene el grafo.
        self.finished = False
        self.usage: dict[str, int] = {}
        # Veredicto de la compuerta de afirmaciones (spec 046, data-model).
        self.claims: dict[str, Any] = {
            "status": "skipped" if self.gate_claims.active else "off",
            "p": None,
            "threshold": self.gate_claims.threshold,
            "reason": "",
            "substituted": False,
            "jev": None,
        }
        if self.gate_claims.active:
            self.claims["mode"] = self.gate_claims.mode

    # -- Eventos del grafo --

    def on_custom(self, payload: Any) -> list[ToolAgentEvent]:
        if isinstance(payload, Mapping) and payload.get("type") == "status":
            step = int(payload["step"])
            self.last_status_step = max(self.last_status_step, step)
            return [ToolAgentStatus(text=str(payload.get("text", "")), step=step)]
        return []

    def on_message(self, chunk: Any, metadata: Any) -> list[ToolAgentEvent]:
        node = metadata.get("langgraph_node") if isinstance(metadata, Mapping) else None
        if self.finished or node not in _MODEL_NODES or not isinstance(chunk, AIMessageChunk):
            return []
        step = self.step
        if step is None:
            step = self.step = self._new_step(node)
        self._add_usage(chunk.usage_metadata)
        if chunk.tool_call_chunks:
            if step.emitted and not step.tool_call:
                # Más de RETAIN_CHARS de texto antes de la llamada: ya salió (límite de R2).
                logger.warning("tool_agent.text_before_tool_call", chars=len("".join(step.emitted)))
            step.tool_call = True
            step.pending.clear()
            return []
        piece = _text(chunk.content)
        if not piece or step.tool_call:
            return []
        if step.path == GATE_ABSTAIN:
            # Sin evidencia tras ``buscar_corpus``: un paso que pasa la retención sin llamar
            # herramientas es la redacción final. Se abstiene ya y el runner corta el
            # grafo en vez de esperar (y pagar) una respuesta que se descartaría (R2).
            step.pending.append(piece)
            if len("".join(step.pending)) < RETAIN_CHARS:
                return []
            self.finished = True
            self.step = None
            self._skip_claims("abstencion")
            return self._abstain(GATE_ABSTAIN, 0)
        if step.path == GATE_EVIDENCE and self.gate_claims.holds:
            # Compuerta de afirmaciones activa (FR-014): el paso con evidencia se retiene
            # completo hasta el veredicto. La línea de revisión no sale aquí: el paso aún
            # puede terminar en una llamada a herramienta; sale al cerrar el paso final.
            step.pending.append(piece)
            return []
        if step.path != GATE_EVIDENCE or step.filtered:
            # ``no_query`` (o evidencia sin corpus tras una búsqueda vacía): no sale nada
            # mientras el paso corre; se filtra al cerrarlo.
            step.pending.append(piece)
            return []
        if step.released:
            return self._emit(piece)
        step.pending.append(piece)
        held = "".join(step.pending)
        if len(held) < RETAIN_CHARS:
            return []
        step.released = True
        step.pending.clear()
        return self._emit(held)

    def on_update(self, payload: Any) -> list[ToolAgentEvent]:
        if not isinstance(payload, Mapping):
            return []
        events: list[ToolAgentEvent] = []
        for node, update in payload.items():
            if not isinstance(update, Mapping):
                continue
            if node in _MODEL_NODES:
                if not self.finished:
                    events.extend(self._close_step(update.get("messages") or [], node))
                continue
            if node == _RECALL_NODE:
                self.prior_context = dict(update.get("commercial_context") or {})
            for key, value in update.items():
                if key != "messages":
                    self.values[key] = value
                elif node == _TOOLS_NODE:
                    payloads = _tool_payloads(value or [])
                    self.tool_payloads.extend(payloads)
                    self.tool_texts.extend(_tool_backing(payloads))
        return events

    # -- Cierre --

    def final(self, turns: Sequence[Turn]) -> ToolAgentFinal:
        values = self.values
        quotes = tuple(dict(q) for q in values.get("quotes") or [])
        products = dict(values.get("cards") or {})
        if quotes:
            products.setdefault("quotes", [dict(q) for q in quotes])
            products.setdefault("quote", dict(quotes[0]))
        sources = self._sources()
        return ToolAgentFinal(
            answer="".join(self.answer),
            gate=self._path(),
            abstained=self.abstained,
            sources=sources,
            products=products,
            quotes=quotes,
            commercial_context=dict(values.get("commercial_context") or {}),
            results=tuple(dict(r) for r in values.get("results") or []),
            mode_path=str(values.get("mode_path") or "agent"),
            preferences=tuple(str(p) for p in values.get("preferences") or []),
            missing=tuple(str(m) for m in values.get("missing") or []),
            turns=tuple(turns),
            usage=dict(self.usage),
            summary=_summary_meta(values.get("conversation_summary")),
            gatekeeper=self._gatekeeper(),
            claims=dict(self.claims),
        )

    def _gatekeeper(self) -> dict[str, Any]:
        """Decisión del turno; con ``source=llm``, la clase derivada de lo consultado (FR-006)."""

        decision = GatekeeperDecision.from_value(self.values.get("gatekeeper"))
        if decision is None:
            return {}
        if decision.source == "llm":
            groups = [ROUTE_GROUPS.get(str(r.get("tool")), "") for r in self._results()]
            decision = replace(decision, llm_class=llm_class(groups))
        return decision.to_value()

    def _results(self) -> list[Mapping[str, Any]]:
        return [r for r in self.values.get("results") or [] if isinstance(r, Mapping)]

    # -- Internos --

    def _path(self) -> GatePath:
        return gate(self.values.get("results") or [])

    def _step_path(self, node: Any) -> GatePath:
        """Camino de un paso: el atajo no consulta, así que va por el filtro directo."""

        return GATE_NO_QUERY if node == _DIRECT_NODE else self._path()

    def _new_step(self, node: Any) -> "_ModelStep":
        path = self._step_path(node)
        filtered = path == GATE_EVIDENCE and filtered_evidence(self.values.get("results") or [])
        return _ModelStep(path=path, filtered=filtered)

    def _emit(self, text: str) -> list[ToolAgentEvent]:
        if not text:
            return []
        events: list[ToolAgentEvent] = []
        if not self.announced:
            # Las fuentes salen antes del primer token (evento ``meta`` del servicio).
            self.announced = True
            events.append(ToolAgentSources(sources=self._sources()))
        self.answer.append(text)
        if self.step is not None:
            self.step.emitted.append(text)
        events.append(ToolAgentToken(text=text))
        return events

    def _sources(self) -> tuple[Source, ...]:
        if self.abstained:
            return ()
        return tuple(_source(dict(payload)) for payload in self.values.get("sources") or [])

    def _add_usage(self, usage: Any) -> None:
        if isinstance(usage, Mapping):
            for key, value in usage.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    self.usage[key] = self.usage.get(key, 0) + value

    def _close_step(self, messages: Sequence[Any], node: Any = _AGENT_NODE) -> list[ToolAgentEvent]:
        """Fin de un paso del modelo: descarta el retenido o libera la respuesta final.

        El ``AIMessage`` del estado decide si el paso fue final: el grafo quita las
        llamadas que el modelo pide cuando ya no hay herramientas (límite del turno).
        """

        step = self.step or self._new_step(node)
        self.step = step
        try:
            message = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
            if message is not None and (message.tool_calls or message.invalid_tool_calls):
                return []
            text = _text(message.content) if message is not None else "".join(step.pending)
            if not step.emitted:
                # Las tarjetas del carrito las escribe solo el flujo: una imitada por el modelo
                # mostraba un producto agregado y un total que no existían (ensayo del
                # 2026-11-27). Se quitan antes de juzgar y de emitir.
                text, imitated = strip_imitated_cards(text)
                if imitated:
                    logger.warning("tool_agent.card_imitation_stripped", lines=imitated)
            if out_of_scope(self.tool_payloads):
                # Tema ajeno a Carvajal: negativa fija, sin la redacción del modelo.
                self._skip_claims("fuera_de_alcance")
                logger.info("tool_agent.out_of_scope")
                return self._emit(OUT_OF_SCOPE_ANSWER)
            if step.path == GATE_EVIDENCE and not step.filtered:
                if self.gate_claims.holds:
                    return self._judged(text, step.path)
                began = bool(self.answer)
                events = self._emit(self._remainder(text, step))
                if self.gate_claims.active:
                    # Sombra: se juzga lo ya emitido y solo se registra (no hay retención).
                    self._judge(text, began=began)
                return events
            if step.path == GATE_ABSTAIN:
                self._skip_claims("abstencion")
                return self._abstain(GATE_ABSTAIN, 0)
            if neutral_only(self.values.get("results") or []):
                # Búsqueda documental vacía: la petición del dato o las sedes no respaldan lo
                # que el modelo diga del corpus. Su redacción no sale ni filtrada; sale el
                # reemplazo neutro (con la ausencia del corpus) o la abstención.
                replacement = self._replacement(corpus_miss=True)
                if replacement:
                    self._skip_claims("reemplazo")
                    return self._replace(replacement, 0)
                self._skip_claims("abstencion")
                return self._abstain(GATE_NO_QUERY, 0)
            terms = unsupported_terms(text, allowed=self._allowed())
            if terms:
                replacement = self._replacement()
                if replacement and step.filtered:
                    # Con una petición de dato o sedes, la parte documental declara su
                    # ausencia; sin ellas, el turno se abstiene (FR-004).
                    replacement = self._replacement(corpus_miss=True)
                if replacement:
                    self._skip_claims("reemplazo")
                    return self._replace(replacement, len(terms))
                self._skip_claims("abstencion")
                return self._abstain(step.path, len(terms))
            if node == _DIRECT_NODE:
                # Respuesta directa (atajo por reglas o decidida por Jev): no consulta nada y
                # ya pasó el filtro de ``no_query``; no se juzga (contrato §3).
                source = (self._decision() or GatekeeperDecision("direct", "reglas")).source
                _judgeable, reason = judgeable(text, shortcut=True, source=source)
                self._skip_claims(reason)
                return self._emit(text)
            return self._judged(text, step.path)
        finally:
            self.step = None

    # -- Compuerta de afirmaciones (spec 046) --

    def _decision(self) -> GatekeeperDecision | None:
        return GatekeeperDecision.from_value(self.values.get("gatekeeper"))

    def _review_status(self) -> list[ToolAgentEvent]:
        if self.reviewing:
            return []
        self.reviewing = True
        return [ToolAgentStatus(text=REVIEW_STATUS, step=self.last_status_step + 1)]

    def _skip_claims(self, reason: str) -> None:
        if self.gate_claims.active and self.claims["status"] == "skipped":
            self.claims["reason"] = reason

    def take_deferred(self) -> Callable[[], list[ToolAgentEvent]] | None:
        """Veredicto pendiente del paso final, si lo hay; el runner lo ejecuta una vez."""

        deferred, self._deferred = self._deferred, None
        return deferred

    def _judged(self, text: str, path: str) -> list[ToolAgentEvent]:
        """Texto del modelo que pasó la compuerta determinista: Noul y, si no, sustituto.

        Con la compuerta reteniendo, la línea de revisión sale ya (el paso final cerró sin
        llamadas a herramientas) y el veredicto queda pendiente: el runner la emite y luego
        llama a Jev, así que la línea se ve mientras se revisa (FR-014).
        """

        if self.gate_claims.holds and text.strip():
            self._deferred = lambda: self._verdict(text, path)
            return self._review_status()
        return self._verdict(text, path)

    def _verdict(self, text: str, path: str) -> list[ToolAgentEvent]:
        verdict = self._judge(text)
        if verdict == "unsupported" and self.gate_claims.holds:
            self.claims["substituted"] = True
            replacement = self._replacement()
            if not replacement:
                # Turno que solo presentaba datos deterministas (opciones del catálogo,
                # sedes): el reemplazo los presenta en vez de «No tengo un dato verificado…»,
                # que contradecía las tarjetas (ensayo del 2026-11-26).
                replacement = presented_data(self.tool_payloads)
                if replacement:
                    logger.info("claims.replaced_with_data", tools=self._turn_tools())
            if replacement:
                return self._replace(replacement, 0)
            return self._abstain(path, 0)
        return self._emit(text)

    def _judge(self, text: str, *, began: bool = False) -> str:
        """Llama a la compuerta si el texto es juzgable y queda plazo; devuelve el estado.

        El plazo vale en los dos modos (FR-030): en sombra el texto de ``no_query`` (o uno
        corto con evidencia) sigue retenido mientras se juzga, y una llamada lenta haría
        vencer el turno y perder una respuesta buena. ``began`` indica que el texto ya salió
        antes de este paso (sombra con evidencia larga): el plazo del turno ya no aplica.
        """

        gate = self.gate_claims
        if not gate.active or gate.judge is None:
            return "off"
        ok, reason = judgeable(text, results=self._results())
        if not ok:
            self._skip_claims(reason)
            return "skipped"
        if not began and gate.remaining() <= gate.timeout_s + CLAIMS_MARGIN_S:
            return self._claims_unavailable("sin_plazo")
        if not gate.begin_text():
            return self._claims_unavailable("sin_plazo")
        query = build_claims_query(
            text,
            self.question,
            self.tool_payloads,
            self.values.get("memory"),
            settings=gate.settings,
            prior_questions=self._prior_person_texts(),
            earlier_questions=self._earlier_person_texts(),
            draft=self.draft,
        )
        try:
            verdict = gate.judge.check(query)
        except JudgeUnavailable as error:
            jev = error.call.to_value() if error.call is not None else None
            return self._claims_unavailable(error.reason or error.status, jev)
        status = "unsupported" if verdict.p_supported < gate.threshold else "supported"
        substituted = status == "unsupported" and gate.holds
        self.claims.update(
            status=status,
            p=round(verdict.p_supported, 2),
            reason="sombra" if gate.mode == "shadow" else "",
            jev={
                **verdict.call.to_value(),
                "p_supported": verdict.p_supported,
                "threshold": gate.threshold,
            },
        )
        logger.info(
            "claims.judged",
            status=status,
            p=self.claims["p"],
            substituted=substituted,
            mode=gate.mode,
            # Sin texto: solo qué herramientas respaldaban y el largo de lo juzgado, para
            # separar las sustituciones por tipo de turno (medición del 2026-11-26).
            tools=self._turn_tools(),
            answer_chars=len(text),
        )
        if gate.stats is not None:
            gate.stats.record(status, p=verdict.p_supported, substituted=substituted)
        return status

    def _turn_tools(self) -> list[str]:
        return sorted({tool for tool, _ in self.tool_payloads})

    def _claims_unavailable(self, reason: str, jev: Mapping[str, Any] | None = None) -> str:
        self.claims.update(status="unavailable", reason=reason, jev=dict(jev) if jev else None)
        logger.info("claims.judged", status="unavailable", reason=reason, substituted=False)
        if self.gate_claims.stats is not None:
            self.gate_claims.stats.record("unavailable", reason=reason)
        return "unavailable"

    def _remainder(self, text: str, step: _ModelStep) -> str:
        """Lo que falta emitir del paso final: el retenido o el texto que se descartó."""

        sent = "".join(step.emitted)
        if text.startswith(sent):
            return text[len(sent) :]
        logger.warning("tool_agent.stream_mismatch", sent_chars=len(sent), text_chars=len(text))
        return ""

    def _abstain(self, path: str, terms: int) -> list[ToolAgentEvent]:
        """Sustituye la redacción por la abstención ajustada a lo que consultó el turno.

        Nunca dice «no está en el corpus» si el turno no buscó en el corpus: las búsquedas
        vacías declaran su propia ausencia y un turno sin búsquedas recibe una respuesta de
        conversación (despedida, saludo o preferencia, que no son abstenciones).
        """

        text, abstained = abstention(self.tool_payloads, conversation=self._conversation())
        if not abstained:
            return self._replace(text, terms)
        self.abstained = True
        logger.info("tool_agent.abstained", path=path, unsupported_terms=terms)
        return self._emit(text)

    def _conversation(self) -> str:
        """Respuesta fija de un turno sin búsquedas, según la intención del atajo.

        La despedida fija es solo para el atajo de despedida: un turno que fue al agente
        («Gracias, sin domicilio») pedía algo, y sin respaldo eso es una abstención
        (``NO_DATA_ANSWER``), no un «con gusto».
        """

        intent = str(self.values.get("shortcut_intent") or "")
        if intent == FAREWELL_INTENT and self.values.get("mode_path") == "shortcut":
            return FAREWELL_ANSWER
        if intent == "greeting":
            return GREETING_ANSWER
        if intent == "memory_write":
            return PREFERENCE_ANSWER
        if intent == FORGET_INTENT:
            return FORGET_ANSWER
        if intent == UNAVAILABLE_INTENT:
            return MEMORY_UNAVAILABLE_ANSWER
        return NO_DATA_ANSWER

    def _replace(self, text: str, terms: int) -> list[ToolAgentEvent]:
        """Emite el reemplazo neutro: no es una abstención y conserva las fuentes."""

        logger.info("tool_agent.replaced", path=GATE_NO_QUERY, unsupported_terms=terms)
        return self._emit(text)

    def _replacement(self, *, corpus_miss: bool = False) -> str:
        """Reemplazo neutro de un texto ``no_query`` rechazado; ``""`` si no corresponde.

        La abstención del corpus no sirve cuando el turno pidió un dato o trajo sedes:
        «no está en el corpus» ocultaría la pregunta o los contactos. Solo usa datos de las
        herramientas (spec 044, FR-004 y FR-005):

        - ``cotizar`` ``empty`` con sedes: el aviso sin precio con las sedes citadas.
        - ``needs_input`` o ``sin_consulta`` válido con ``pedir_dato`` (el mismo criterio que
          :func:`gate`): la petición neutra de los campos de ``missing`` (el ``dato`` del
          modelo solo elige el campo, no se copia) y, si falta la ciudad, las
          ``ciudades_disponibles``.

        Con alguno de ellos, las búsquedas vacías del turno (:func:`tool_misses`) van antes:
        «no hay sedes en esa ciudad» no se pierde porque otra herramienta pidiera un dato.
        Con ``corpus_miss`` (caso :func:`neutral_only`), ``NO_CONTEXT_ANSWER`` va antes de la
        petición si ``buscar_corpus`` consultó de verdad (sin ``error``, como en
        :func:`abstention`): la parte documental declara su ausencia en vez de desaparecer.
        Sin nada de eso (conversación, atajo, búsquedas vacías) devuelve ``""`` y el turno
        se abstiene (:meth:`_abstain`).
        """

        memory = _memory_replacement(self.tool_payloads)
        if memory:
            # Turno solo de memoria (FR-017): confirmación o lista con los datos de la
            # herramienta, no la abstención.
            return memory
        sites: dict[Any, Mapping[str, Any]] = {}
        fields: list[str] = []
        cities: list[str] = []
        suggestion: str | None = None
        candidates: list[Mapping[str, Any]] = []
        partial = False
        outside = False
        asked = False
        for tool, data in self.tool_payloads:
            if tool in DECLARATION_TOOLS:
                if data.get("status") == "ok" and data.get("motivo") == "pedir_dato":
                    asked = True
                    fields.extend(missing_fields(str(data.get("dato") or "")))
                continue
            if data.get("status") == "error" and tool == TOOL_COTIZAR and data.get("missing"):
                # «Cotízame 0 vasos»: la cantidad no es válida; se pide, no se abstiene.
                asked = True
                fields.extend(str(item) for item in data.get("missing") or [])
            elif data.get("status") == "needs_input":
                asked = True
                fields.extend(str(item) for item in data.get("missing") or [])
                cities.extend(str(city) for city in data.get("ciudades_disponibles") or [])
                # La ciudad habitual de la memoria no respalda nada (FR-017): solo se ofrece
                # en la pregunta de confirmación que construye ``ask_for_missing``.
                suggestion = suggestion or (str(data.get("sugerencia_ciudad") or "") or None)
                if tool == TOOL_COTIZAR:
                    # Solo las de ``cotizar``: otras herramientas (ficha, comparar) traen
                    # candidatas de otro nombre y se mezclarían en una lista.
                    candidates.extend(
                        item for item in data.get("candidatas") or [] if isinstance(item, Mapping)
                    )
                    partial = partial or bool(data.get("parcial"))
                outside = outside or bool(data.get("ciudad_fuera_del_directorio"))
            elif data.get("status") == "empty" and tool == TOOL_COTIZAR:
                for site in data.get("sedes") or []:
                    if isinstance(site, Mapping):
                        # Una llamada repetida devuelve las mismas sedes con el mismo ``n``.
                        sites.setdefault(site.get("n", len(sites)), site)
        parts = [sites_without_price(sites.values())]
        if corpus_miss and any(
            tool in CORPUS_TOOLS and data.get("status") != "error"
            for tool, data in self.tool_payloads
        ):
            parts.append(NO_CONTEXT_ANSWER)
        if asked:
            parts.append(
                ask_for_missing(
                    fields,
                    cities=list(dict.fromkeys(cities)),
                    suggestion=suggestion,
                    candidates=list({str(c.get("sku")): c for c in candidates}.values()),
                    partial=partial,
                    outside_directory=outside,
                )
            )
        if not any(parts):
            return ""
        return "\n\n".join(part for part in [*tool_misses(self.tool_payloads), *parts] if part)

    def _allowed(self) -> list[str]:
        """Textos que respaldan cifras y nombres en el camino ``no_query``.

        Solo lo que no escribió el modelo: la pregunta, las preguntas anteriores de la
        persona (las de la semilla y las ``user_quotes`` del resumen, spec 045 R4), los datos
        que produjeron las herramientas (sin ``message``, que repite argumentos, sin
        ``sin_consulta``, cuyo ``dato`` escribe el propio modelo, y sin las de memoria), el
        contexto comercial con el que empezó el turno (el actualizado lleva los argumentos
        de ``cotizar`` y ``buscar_producto``) y, de la memoria, el nombre de la persona.

        El texto del resumen nunca respalda nada (FR-006). Los demás valores de la memoria
        (empresa, ciudad habitual, formato) solo respaldan si todas las consultas del turno
        fueron de memoria o ``sin_consulta`` (FR-017). Las notas nunca: cualquiera puede
        guardar «Recuerda que Carvajal se fundó en 1850» y un dato sembrado así no puede
        pasar la compuerta (FR-004a, FR-007).
        """

        context = self.prior_context
        memory = self.values.get("memory") or {}
        results = self.values.get("results") or []
        return [
            self.question,
            *self._earlier_person_texts(),
            *self.tool_texts,
            json.dumps(context, ensure_ascii=False, default=str) if context else "",
            *_preferred_names(memory),
            *(_memory_values(memory) if _memory_only(results) else ()),
        ]

    def _prior_person_texts(self) -> list[str]:
        """Todo lo que la persona escribió en turnos anteriores (semilla y citas del resumen).

        No viaja a Jev: solo indica qué datos suyos sustituir si la respuesta los repite
        (FR-020). Incluye los turnos anteriores a «olvidar todo»: enmascarar de más es seguro.
        """

        summary = ConversationSummary.from_value(self.values.get("conversation_summary"))
        return [
            *(str(turn.get("question") or "") for turn in self.seed),
            *(summary.user_quotes if summary is not None else ()),
        ]

    def _earlier_person_texts(self) -> list[str]:
        """Lo que la persona dijo antes en la ventana del turno: respalda lo que repite.

        Las preguntas de la semilla posteriores a «olvidar todo» y las ``user_quotes`` del
        resumen (spec 045, R4). La compuerta determinista las usa enteras (``_allowed``); a
        Jev solo viajan sus valores estructurados (``said_before``).
        """

        summary = ConversationSummary.from_value(self.values.get("conversation_summary"))
        return [
            *self._seed_questions(),
            *(summary.user_quotes if summary is not None else ()),
        ]

    def _seed_questions(self) -> list[str]:
        state = {
            "seed_turns": self.seed,
            "turn_count": self.turn_count,
            "conversation_summary": self.values.get("conversation_summary"),
        }
        return [str(turn.get("question") or "") for turn in window_turns(state, len(self.seed))]


def _preferred_names(memory: Any) -> list[str]:
    """Nombre con el que la persona pidió que la llamen: la categoría ``nombre`` (spec 045).

    Antes se buscaba con patrones en el texto de las preferencias; la memoria por
    categorías ya lo guarda aparte y sin el resto de la frase.
    """

    name = MemoryProfile.from_snapshot(memory).values.get("nombre")
    return [name] if name else []


def _memory_values(memory: Any) -> list[str]:
    """Valores de las categorías de la memoria (sin las notas, que nunca respaldan)."""

    values = MemoryProfile.from_snapshot(memory).values
    return [values[category] for category in CATEGORIES if values.get(category)]


def _memory_only(results: Sequence[Mapping[str, Any]]) -> bool:
    """Todas las consultas del turno fueron de memoria o ``sin_consulta`` (FR-017)."""

    return all(r.get("tool") in MEMORY_TOOLS or r.get("tool") in DECLARATION_TOOLS for r in results)


_SAVED: Final = "Listo, lo guardé."
_NOT_SAVED: Final = "No guardé ese dato: {motivo}."
_FORGOTTEN: Final = "Listo, lo olvidé."
_NOTHING: Final = "No tenía nada guardado de eso."
_REASONS: Final = {
    "dato_sensible": "es un dato personal sensible y ese tipo de dato no se guarda",
    "no_dicho": "no lo encontré en tu mensaje",
    "limite_notas": "ya hay cinco notas; borra una para guardar otra",
    "demasiado_largo": "es demasiado largo",
    "vacio": "está vacío",
}
_LABELS: Final = {
    "nombre": "Nombre",
    "empresa": "Empresa",
    "ciudad_habitual": "Ciudad habitual (solo la uso si la confirmas)",
    "formato_preferido": "Formato preferido",
}


def _memory_replacement(payloads: Sequence[tuple[str, Mapping[str, Any]]]) -> str:
    """Reemplazo determinista de un turno solo de memoria (FR-017); ``""`` si no aplica.

    Usa solo los datos de las herramientas: la confirmación de guardado u olvido, o la lista
    de ``listar_memoria``. Si el turno consultó algo más que memoria y ``sin_consulta``, no
    aplica.
    """

    memory = [(tool, data) for tool, data in payloads if tool in MEMORY_TOOLS]
    if not memory or any(
        tool not in MEMORY_TOOLS and tool not in DECLARATION_TOOLS for tool, _ in payloads
    ):
        return ""
    parts: list[str] = []
    for tool, data in memory:
        status = data.get("status")
        if tool == "listar_memoria":
            if status == "ok":
                parts.append(_memory_listing(data.get("memoria") or {}))
            elif status == "empty":
                parts.append("No tengo nada guardado de ti.")
        elif tool == "recordar_preferencia":
            if status == "ok":
                parts.append(_SAVED)
            elif status == "empty":
                reason = _REASONS.get(str(data.get("motivo") or ""), "no se pudo guardar")
                parts.append(_NOT_SAVED.format(motivo=reason))
        elif tool == "olvidar_preferencia":
            if status == "ok":
                parts.append(_FORGOTTEN)
            elif data.get("motivo") == "nada_que_olvidar":
                parts.append(_NOTHING)
        if status == "error":
            parts.append(MEMORY_UNAVAILABLE_ANSWER)
    return " ".join(dict.fromkeys(part for part in parts if part))


def _memory_listing(memory: Mapping[str, Any]) -> str:
    lines = [
        f"- {_LABELS[category]}: {memory[category]}"
        for category in CATEGORIES
        if isinstance(memory.get(category), str) and memory[category]
    ]
    lines.extend(f"- Nota: {note}" for note in memory.get("notas") or [] if isinstance(note, str))
    if not lines:
        return "No tengo nada guardado de ti."
    return "Esto es lo que recuerdo de ti:\n" + "\n".join(lines)


def _tool_payloads(messages: Sequence[Any]) -> list[tuple[str, dict[str, Any]]]:
    """Herramienta y datos de cada ``ToolMessage`` (bloque ``<datos>``), en orden."""

    payloads: list[tuple[str, dict[str, Any]]] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        data = content_data(_text(message.content))
        if data is not None:
            payloads.append((str(message.name or ""), data))
    return payloads


def _tool_backing(payloads: Sequence[tuple[str, Mapping[str, Any]]]) -> list[str]:
    """Datos que produjeron las herramientas, como texto, para respaldar el ``no_query``.

    Vale cualquier estado: las sedes y ciudades de un ``empty`` o los campos de un
    ``needs_input`` son datos de la herramienta. Quedan fuera ``sin_consulta`` (su ``dato``
    lo escribe el modelo), las de memoria (sus valores respaldan solo según el camino,
    spec 045 FR-017, en :meth:`_TurnStream._allowed`) y las claves de ``_ECHO_KEYS``
    (``message``, que repite argumentos, y la prosa y los conteos de la búsqueda por
    nombre). De ``preguntas_frecuentes`` solo cuentan los fragmentos (:func:`backing_data`).
    """

    return [
        json.dumps(
            {
                key: value
                for key, value in backing_data(tool, data).items()
                if key not in _ECHO_KEYS
            },
            ensure_ascii=False,
            default=str,
        )
        for tool, data in payloads
        if tool not in DECLARATION_TOOLS and tool not in MEMORY_TOOLS
    ]


class ToolAgentRunner:
    """Runner del modo ``tools``: transmite el grafo con retención y compuerta (R2, R3).

    Por camino de la compuerta (:mod:`carvajal.agent.toolagent.evidence`):

    - ``evidence``: retiene ``RETAIN_CHARS`` de cada paso del modelo; si aparece un
      ``tool_call_chunk`` descarta lo retenido (era «pensamiento» previo a una llamada); si
      no, lo libera y sigue transmitiendo cada trozo.
    - ``abstain`` (sin evidencia tras ``buscar_corpus``): no transmite nada del modelo;
      emite la abstención (``NO_CONTEXT_ANSWER`` precedida de las demás búsquedas vacías del
      turno) en cuanto el paso final pasa la retención sin llamar herramientas (o al
      cerrarse, si es más corto) y detiene el grafo.
    - ``no_query``: retiene el paso final completo, lo filtra y lo emite de una vez. Si trae
      cifras, fechas o nombres propios sin respaldo, lo sustituye: por la petición neutra
      del dato si el turno pidió uno, por las sedes sin precio si ``cotizar`` las trajo, o
      por la abstención ajustada a lo consultado si no hay ninguno de esos datos. Si el
      turno buscó en el corpus sin evidencia y ninguna herramienta pidió el dato
      (``neutral_only``), aunque una herramienta pidiera un dato (``needs_input``), no emite
      la redacción del modelo: solo el reemplazo neutro, con ``NO_CONTEXT_ANSWER`` antes de
      la petición del dato.

    Antes del primer token emite ``ToolAgentSources``. Los trozos se distinguen por el nodo
    (``langgraph_node``) y por ``tool_call_chunks``.

    Atajo (``mode_path="shortcut"``, T039): el grafo redacta en el nodo ``direct`` sin
    herramientas; no hay líneas de estado ni compuerta de evidencia de herramientas. El
    texto se retiene completo y pasa el filtro de la ruta directa (el del camino
    ``no_query``), con la pregunta y el nombre que la persona pidió como respaldo.

    Cancelación: cerrar el iterador (o la abstención anticipada) activa una
    :class:`TurnCancel` que corta el trozo siguiente del modelo y las herramientas
    pendientes; sin ella, el ``Pregel.stream`` síncrono esperaría a que terminara el paso.

    Plazo (``agent_turn_timeout_s``, T040): si vence antes del primer ``ToolAgentToken``,
    el turno se cancela y :meth:`stream` lanza :class:`TurnTimeout` para que el servicio
    use el respaldo. ``clock`` (segundos monotónicos) permite fijar el reloj en pruebas.
    El grafo corre en un hilo propio (:class:`_GraphPump`) y entrega sus eventos por una
    cola: el plazo despierta al runner aunque el proveedor no haya mandado ni un trozo, y
    el turno no espera a que el paso en curso termine (sigue cancelado en segundo plano).
    """

    def __init__(
        self,
        graph: Any,
        settings: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        claims_judge: ClaimsJudge | None = None,
        claims_stats: ClaimsStats | None = None,
    ) -> None:
        self._graph = graph
        self._settings = settings
        self._clock = clock
        self._claims_judge = claims_judge
        # Contadores de la compuerta (bloque ``claims`` de ``/health``): los del proceso
        # salvo que una prueba inyecte los suyos.
        self._claims_stats = claims_stats if claims_stats is not None else CLAIMS_STATS

    def _claims_mode(self) -> tuple[str, str]:
        """Modo de la compuerta para el turno, evaluado una vez al empezar (FR-014).

        Devuelve el modo y, si la compuerta está pedida (``enforce`` o ``shadow``) con juez
        pero Jev no está disponible al empezar (circuito abierto, tope diario), el motivo:
        el turno sigue en ``off`` (data-model) pero los contadores lo cuentan como
        ``unavailable`` para distinguir una caída de Jev de la compuerta apagada a propósito.
        """

        mode = str(getattr(self._settings, "jev_claims_mode", "off") or "off")
        judge = self._claims_judge
        if mode not in ("enforce", "shadow") or judge is None:
            return "off", ""
        if not judge.available():
            status = getattr(judge, "unavailable_status", None)
            reason = status() if callable(status) else None
            return "off", str(reason or "no_disponible")
        return mode, ""

    def stream(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
        *,
        draft: Any = None,
    ) -> Iterator[ToolAgentEvent]:
        """Ejecuta el turno en el hilo y produce sus eventos; el último es el final.

        ``draft`` es el borrador de la cotización con el que empieza el turno (spec 054):
        solo lo lee la compuerta de afirmaciones, como evidencia.
        """

        cleaned = validate_question(question)
        turns = list(seed_turns)
        turn_count = _turn_count(seed_turns)
        timeout_s = float(self._settings.agent_turn_timeout_s)
        mode, unavailable = self._claims_mode()
        gate = _ClaimsGate(
            judge=self._claims_judge if mode != "off" else None,
            mode=mode,
            threshold=float(getattr(self._settings, "jev_claims_threshold", 0.3)),
            timeout_s=float(getattr(self._settings, "jev_timeout_s", 0.8)),
            settings=self._settings,
            remaining=lambda: timeout_s - (self._clock() - started),
            begin_text=lambda: deadline.begin_text(),
            stats=self._claims_stats,
        )
        state = _TurnStream(cleaned, turns, turn_count, gate, draft=draft)
        cancel = TurnCancel()
        config = cancel.config(
            {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": recursion_limit(self._settings),
            }
        )
        payload = {
            "question": cleaned,
            "owner_key": owner_key,
            "thread_id": thread_id,
            "seed_turns": turns,
            # Spec 045 (FR-008): turnos persistidos antes de este (``SeedTurns``).
            "turn_count": turn_count,
        }
        pump = _GraphPump(
            lambda: self._graph.stream(payload, config, stream_mode=list(_STREAM_MODES))
        )
        deadline = _Deadline(timeout_s, cancel, on_expire=pump.wake)
        started = self._clock()
        pump.start()
        deadline.start()
        completed = False
        try:
            try:
                for mode, data in pump:
                    if not deadline.expired and self._clock() - started >= timeout_s:
                        deadline.expire()
                    if mode == "messages":
                        chunk, metadata = data
                        out = state.on_message(chunk, metadata)
                    elif mode == "custom":
                        out = state.on_custom(data)
                    elif mode == "updates":
                        out = state.on_update(data)
                    else:
                        out = []
                    stop = False
                    while True:
                        if any(isinstance(e, ToolAgentToken) for e in out) and not (
                            deadline.begin_text()
                        ):
                            out = []
                        if deadline.expired:
                            stop = True
                            break
                        yield from out
                        # Veredicto de la compuerta tras emitir la línea de revisión.
                        deferred = state.take_deferred()
                        if deferred is None:
                            break
                        out = deferred()
                    if stop:
                        break
                    if state.finished:
                        logger.info("tool_agent.stopped_early", reason="abstain")
                        break
                else:
                    # El bucle termina sin ``break`` cuando el grafo terminó o cuando el plazo
                    # despertó al runner (``pump.wake``).
                    completed = not deadline.expired
            except TurnCancelled:
                # El temporizador canceló el paso en curso: el corte es el plazo, no un error.
                if not deadline.expired:
                    raise
        finally:
            deadline.stop()
            # Detener, desconexión, error, plazo o abstención anticipada: se corta el paso en
            # curso antes de cerrar el stream del grafo, que si no esperaría a que terminara.
            if not completed:
                cancel.cancel()
            # Con el plazo vencido no se espera al grafo: el paso en curso puede seguir
            # esperando al proveedor, y el respaldo debe entrar ya. El hilo termina solo al
            # llegar el siguiente trozo o al vencer el tiempo de espera del cliente. En los
            # demás cortes se espera a que el stream se cierre, como antes.
            pump.stop(wait=not deadline.expired)
        if deadline.expired and not completed:
            partial = state.final(turns)
            raise TurnTimeout(timeout_s, replace(partial, answer=""))
        final = state.final(turns)
        if final.claims.get("status") == "off" and unavailable:
            # Compuerta pedida pero Jev caído al empezar: no es un ``off`` a propósito.
            self._claims_stats.record("unavailable", reason=unavailable)
        elif final.claims.get("status") in ("skipped", "off"):
            # Turno sin llamada a Jev: los juzgados y los ``unavailable`` ya se contaron
            # al decidir el veredicto.
            self._claims_stats.record_verdict(final.claims)
        logger.info(
            "tool_agent.turn_streamed",
            gate=final.gate,
            abstained=final.abstained,
            tools=[r.get("tool") for r in final.results],
            answer_chars=len(final.answer),
            claims=final.claims.get("status"),
        )
        yield final

    def record(
        self,
        thread_id: str,
        *,
        question: str,
        answer: str,
        incomplete: bool,
        turns: Sequence[Turn],
    ) -> None:
        """No hace nada: el grafo ``tools`` no guarda el historial en el checkpoint.

        El historial viaja en ``seed_turns`` desde la base del chat en cada turno y el
        nodo ``recall`` rehace los mensajes; el checkpoint ya conserva lo que el grafo
        necesita (``commercial_context``). Existe para que el servicio cierre el turno
        igual en ambos modos.
        """

        return None

    def clear_commercial_context(self, thread_id: str) -> None:
        """Olvida el pedido en curso del hilo (producto, material, ciudad, referencia).

        Lo llama el servicio cuando el flujo de cotización cancela todo («olvida todo»,
        «cancela todo»): ese turno no pasa por el grafo y, sin esto, la siguiente
        cotización heredaba el producto anterior (2026-11-25). Un fallo no rompe el turno.
        """

        try:
            self._graph.update_state(
                {"configurable": {"thread_id": thread_id}}, {"commercial_context": {}}
            )
        except Exception as error:  # el checkpointer no debe tumbar la respuesta
            logger.warning("tool_agent.context_reset_failed", error=str(error), thread_id=thread_id)


class _GraphPump:
    """Consume ``graph.stream`` en un hilo propio y entrega sus eventos por una cola.

    El ``Pregel.stream`` síncrono bloquea a quien lo itera mientras el paso en curso espera
    al proveedor; si lo iterara el runner, el plazo no podría cortar un modelo que aún no
    mandó su primer trozo (ningún callback corre). Aquí el runner espera en la cola y
    :meth:`wake` lo despierta al vencer el plazo, sin esperar al grafo.

    El hilo corre con una copia del contexto de quien lo inicia (configuración de
    LangChain y contexto de tracing). Al terminar, por fin del grafo, error o
    :meth:`stop`, el propio hilo cierra el stream; :meth:`stop` con ``wait=True`` espera a
    ese cierre. Los errores del grafo se relanzan en el runner tal cual.
    """

    _EVENT: Final = "event"
    _END: Final = "end"
    _ERROR: Final = "error"
    _WAKE: Final = "wake"

    def __init__(self, open_stream: Callable[[], Iterator[Any]]) -> None:
        self._open_stream = open_stream
        self._queue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self._stopped = threading.Event()
        context = contextvars.copy_context()
        self._thread = threading.Thread(
            target=context.run, args=(self._run,), name="tool-agent-graph", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def wake(self) -> None:
        """Despierta al runner (plazo vencido): la iteración termina sin más eventos."""

        self._queue.put((self._WAKE, None))

    def stop(self, *, wait: bool) -> None:
        """Pide al hilo que deje de publicar y, con ``wait``, espera a que cierre el grafo."""

        self._stopped.set()
        if wait and self._thread.is_alive():
            self._thread.join()

    def __iter__(self) -> Iterator[Any]:
        while True:
            kind, payload = self._queue.get()
            if kind == self._EVENT:
                yield payload
            elif kind == self._ERROR:
                raise payload
            else:  # fin del grafo o plazo vencido
                return

    def _run(self) -> None:
        events: Iterator[Any] | None = None
        try:
            events = self._open_stream()
            for item in events:
                if self._stopped.is_set():
                    break
                self._queue.put((self._EVENT, item))
        except Exception as error:  # se relanza en el hilo del runner
            self._queue.put((self._ERROR, error))
        else:
            self._queue.put((self._END, None))
        finally:
            if events is not None:
                _close_graph_stream(events)


class _Deadline:
    """Plazo del turno hasta el primer texto, seguro entre hilos.

    Un ``threading.Timer`` lo vence a tiempo aunque el grafo esté bloqueado (un paso lento
    del modelo o un proveedor que no responde): :meth:`expire` cancela el turno con la
    :class:`TurnCancel` y llama a ``on_expire``, que despierta al runner sin esperar al
    grafo. La señal corta el trozo siguiente del modelo, el nodo siguiente o la
    herramienta siguiente en segundo plano. Además, el runner lo comprueba con su reloj
    entre eventos del grafo.

    :meth:`begin_text` y :meth:`expire` se excluyen con un candado: o el texto empezó antes
    del plazo y ya no vence, o venció y el texto no sale.
    """

    def __init__(
        self,
        timeout_s: float,
        cancel: TurnCancel,
        *,
        on_expire: Callable[[], None] = lambda: None,
    ) -> None:
        self.timeout_s = timeout_s
        self._cancel = cancel
        self._on_expire = on_expire
        self._lock = threading.Lock()
        self._texting = False
        self._expired = False
        self._timer = threading.Timer(timeout_s, self.expire)
        self._timer.daemon = True

    @property
    def expired(self) -> bool:
        return self._expired

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.cancel()

    def expire(self) -> None:
        """Vence el plazo si el texto aún no empezó y cancela el turno."""

        with self._lock:
            if self._texting or self._expired:
                return
            self._expired = True
        logger.warning("tool_agent.turn_timeout", timeout_s=self.timeout_s)
        self._cancel.cancel()
        self._on_expire()

    def begin_text(self) -> bool:
        """Marca el inicio del texto; ``False`` si el plazo ya venció."""

        with self._lock:
            if not self._expired:
                self._texting = True
            return self._texting


def _close_graph_stream(events: Any) -> None:
    """Cierra el stream del grafo; la cancelación del paso en curso no es un error."""

    close = getattr(events, "close", None)
    if not callable(close):
        return
    try:
        close()
    except TurnCancelled:
        logger.info("tool_agent.cancelled")
    except Exception as error:  # cerrar no debe tapar la causa original del corte
        logger.warning("tool_agent.close_failed", error=repr(error))


def default_tool_agent_runner() -> ToolAgentRunner | None:
    """Runner del modo ``tools`` si el runtime lo construyó; ``None`` en modo ``routes``."""

    from apps.api.agent_runtime import agent_runtime
    from carvajal.infrastructure.settings import Settings

    runtime = agent_runtime()
    if runtime.mode != "tools" or runtime.tool_graph is None:
        return None
    # Spec 046: la compuerta de afirmaciones usa el mismo juez que el gatekeeper.
    return ToolAgentRunner(runtime.tool_graph, Settings(), claims_judge=runtime.judge)


# ``LLMProvider`` se reexporta en el contrato para documentar que la generación
# sigue fuera del grafo y la consume el servicio.
_ = LLMProvider
