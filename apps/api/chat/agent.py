"""Adaptador entre el grafo LangGraph y el servicio de chat.

El servicio no conoce LangGraph: recibe un ``AgentRunner`` con dos operaciones
—preparar el turno y registrar el resultado—. Las pruebas inyectan un doble y
la app real usa el grafo con PostgresSaver/Store y Qdrant.
"""

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

import structlog
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from carvajal.agent.state import Turn
from carvajal.agent.toolagent.evidence import (
    GATE_ABSTAIN,
    GATE_EVIDENCE,
    GATE_NO_QUERY,
    GatePath,
    gate,
    unsupported_terms,
)
from carvajal.agent.toolagent.graph import TurnCancel, TurnCancelled, recursion_limit
from carvajal.agent.toolagent.state import TOOL_SIN_CONSULTA, validate_question
from carvajal.agent.toolagent.tools import content_data
from carvajal.application.ask import Source
from carvajal.application.ports import LLMProvider
from carvajal.prompts import NO_CONTEXT_ANSWER

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
_TOOLS_NODE: Final = "tools"
_RECALL_NODE: Final = "recall"
# Claves de un resultado que no respaldan cifras: ``message`` repite argumentos del modelo
# («No hay sedes del directorio en {ciudad}»).
_ECHO_KEYS: Final = frozenset({"message"})
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
      ``abstained`` si la respuesta del modelo se sustituyó por ``NO_CONTEXT_ANSWER``.
    - ``sources``: citas en orden ``[n]``; vacías si hubo abstención.
    - ``products``: tarjetas comerciales (``cards``) con ``quote`` (primera cotización, para
      lectores actuales) y ``quotes``; ``quotes`` también va aparte.
    - ``results``: un ``ToolOutcome`` por herramienta, para el monitoreo.
    - ``turns``: los ``seed_turns`` recibidos, para ``record``.
    - ``usage``: tokens sumados de todos los pasos del modelo (SC-008).
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


ToolAgentEvent = ToolAgentStatus | ToolAgentSources | ToolAgentToken | ToolAgentFinal


@runtime_checkable
class ToolStreamRunner(Protocol):
    """Contrato del modo ``tools``: el turno se transmite en vez de prepararse."""

    def stream(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
    ) -> Iterator[ToolAgentEvent]:
        """Ejecuta el grafo y produce ``status``, fuentes, ``token`` y un ``ToolAgentFinal``.

        Cerrar el iterador cancela el turno: el grafo deja de generar y de consultar.
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


@dataclass
class _ModelStep:
    """Un paso del modelo mientras se transmite, con el camino vigente al empezar."""

    path: GatePath
    pending: list[str] = field(default_factory=list)
    emitted: list[str] = field(default_factory=list)
    released: bool = False
    tool_call: bool = False


class _TurnStream:
    """Máquina de la retención por camino (research R2) sobre los eventos del grafo.

    Cada método devuelve los eventos a emitir. El camino de un paso se fija al empezar,
    con los ``results`` ya conocidos: las llamadas del propio paso aún no corrieron.
    """

    def __init__(self, question: str) -> None:
        self.question = question
        self.values: dict[str, Any] = {}
        self.tool_texts: list[str] = []
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

    # -- Eventos del grafo --

    def on_custom(self, payload: Any) -> list[ToolAgentEvent]:
        if isinstance(payload, Mapping) and payload.get("type") == "status":
            return [ToolAgentStatus(text=str(payload.get("text", "")), step=int(payload["step"]))]
        return []

    def on_message(self, chunk: Any, metadata: Any) -> list[ToolAgentEvent]:
        node = metadata.get("langgraph_node") if isinstance(metadata, Mapping) else None
        if self.finished or node != _AGENT_NODE or not isinstance(chunk, AIMessageChunk):
            return []
        step = self.step
        if step is None:
            step = self.step = _ModelStep(path=self._path())
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
            return self._abstain(GATE_ABSTAIN, 0)
        if step.path != GATE_EVIDENCE:
            # ``no_query``: no sale nada mientras el paso corre; se filtra al cerrarlo.
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
            if node == _AGENT_NODE:
                if not self.finished:
                    events.extend(self._close_step(update.get("messages") or []))
                continue
            if node == _RECALL_NODE:
                self.prior_context = dict(update.get("commercial_context") or {})
            for key, value in update.items():
                if key != "messages":
                    self.values[key] = value
                elif node == _TOOLS_NODE:
                    self.tool_texts.extend(_tool_backing(value or []))
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
        )

    # -- Internos --

    def _path(self) -> GatePath:
        return gate(self.values.get("results") or [])

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

    def _close_step(self, messages: Sequence[Any]) -> list[ToolAgentEvent]:
        """Fin de un paso del modelo: descarta el retenido o libera la respuesta final.

        El ``AIMessage`` del estado decide si el paso fue final: el grafo quita las
        llamadas que el modelo pide cuando ya no hay herramientas (límite del turno).
        """

        step = self.step or _ModelStep(path=self._path())
        self.step = step
        try:
            message = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
            if message is not None and (message.tool_calls or message.invalid_tool_calls):
                return []
            text = _text(message.content) if message is not None else "".join(step.pending)
            if step.path == GATE_EVIDENCE:
                return self._emit(self._remainder(text, step))
            if step.path == GATE_ABSTAIN:
                return self._abstain(GATE_ABSTAIN, 0)
            terms = unsupported_terms(text, allowed=self._allowed())
            if terms:
                return self._abstain(GATE_NO_QUERY, len(terms))
            return self._emit(text)
        finally:
            self.step = None

    def _remainder(self, text: str, step: _ModelStep) -> str:
        """Lo que falta emitir del paso final: el retenido o el texto que se descartó."""

        sent = "".join(step.emitted)
        if text.startswith(sent):
            return text[len(sent) :]
        logger.warning("tool_agent.stream_mismatch", sent_chars=len(sent), text_chars=len(text))
        return ""

    def _abstain(self, path: str, terms: int) -> list[ToolAgentEvent]:
        self.abstained = True
        logger.info("tool_agent.abstained", path=path, unsupported_terms=terms)
        return self._emit(NO_CONTEXT_ANSWER)

    def _allowed(self) -> list[str]:
        """Textos que respaldan cifras y nombres en el camino ``no_query``.

        Solo lo que no escribió el modelo: la pregunta, los datos que produjeron las
        herramientas (sin ``message``, que repite argumentos, y sin ``sin_consulta``, cuyo
        ``dato`` escribe el propio modelo) y el contexto comercial con el que empezó el
        turno (el actualizado lleva los argumentos de ``cotizar`` y ``buscar_producto``).
        """

        context = self.prior_context
        return [
            self.question,
            *self.tool_texts,
            json.dumps(context, ensure_ascii=False, default=str) if context else "",
        ]


def _tool_backing(messages: Sequence[Any]) -> list[str]:
    """Datos que produjeron las herramientas, como texto, para respaldar el ``no_query``."""

    texts: list[str] = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name == TOOL_SIN_CONSULTA:
            continue
        data = content_data(_text(message.content))
        if data is None:
            continue
        backing = {key: value for key, value in data.items() if key not in _ECHO_KEYS}
        texts.append(json.dumps(backing, ensure_ascii=False, default=str))
    return texts


class ToolAgentRunner:
    """Runner del modo ``tools``: transmite el grafo con retención y compuerta (R2, R3).

    Por camino de la compuerta (:mod:`carvajal.agent.toolagent.evidence`):

    - ``evidence``: retiene ``RETAIN_CHARS`` de cada paso del modelo; si aparece un
      ``tool_call_chunk`` descarta lo retenido (era «pensamiento» previo a una llamada); si
      no, lo libera y sigue transmitiendo cada trozo.
    - ``abstain`` (sin evidencia tras ``buscar_corpus``): no transmite nada del modelo;
      emite ``NO_CONTEXT_ANSWER`` en cuanto el paso final pasa la retención sin llamar
      herramientas (o al cerrarse, si es más corto) y detiene el grafo.
    - ``no_query``: retiene el paso final completo, lo filtra y lo emite de una vez, o la
      abstención si trae cifras, fechas o nombres propios sin respaldo.

    Antes del primer token emite ``ToolAgentSources``. Los trozos se distinguen por el nodo
    (``langgraph_node``) y por ``tool_call_chunks``.

    Cancelación: cerrar el iterador (o la abstención anticipada) activa una
    :class:`TurnCancel` que corta el trozo siguiente del modelo y las herramientas
    pendientes; sin ella, el ``Pregel.stream`` síncrono esperaría a que terminara el paso.
    """

    def __init__(self, graph: Any, settings: Any) -> None:
        self._graph = graph
        self._settings = settings

    def stream(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
    ) -> Iterator[ToolAgentEvent]:
        """Ejecuta el turno en el hilo y produce sus eventos; el último es el final."""

        cleaned = validate_question(question)
        turns = list(seed_turns)
        state = _TurnStream(cleaned)
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
        }
        events = self._graph.stream(payload, config, stream_mode=list(_STREAM_MODES))
        completed = False
        try:
            for mode, data in events:
                if mode == "messages":
                    chunk, metadata = data
                    yield from state.on_message(chunk, metadata)
                elif mode == "custom":
                    yield from state.on_custom(data)
                elif mode == "updates":
                    yield from state.on_update(data)
                if state.finished:
                    logger.info("tool_agent.stopped_early", reason="abstain")
                    break
            else:
                completed = True
        finally:
            # Detener, desconexión, error o abstención anticipada: se corta el paso en curso
            # antes de cerrar el stream del grafo, que si no esperaría a que terminara.
            if not completed:
                cancel.cancel()
            _close_graph_stream(events)
        final = state.final(turns)
        logger.info(
            "tool_agent.turn_streamed",
            gate=final.gate,
            abstained=final.abstained,
            tools=[r.get("tool") for r in final.results],
            answer_chars=len(final.answer),
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
    return ToolAgentRunner(runtime.tool_graph, Settings())


# ``LLMProvider`` se reexporta en el contrato para documentar que la generación
# sigue fuera del grafo y la consume el servicio.
_ = LLMProvider
