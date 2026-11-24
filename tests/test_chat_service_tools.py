"""Pruebas del servicio de chat en modo ``tools`` (spec 044, T026).

El servicio se ejecuta contra SQLite en memoria, sin Postgres ni red. El runner del modo
``tools`` es un doble que produce eventos tipados (``ToolAgentStatus``, ``ToolAgentToken``
y ``ToolAgentFinal``); una prueba de extremo a extremo usa el runner real con
``ScriptedToolChat`` y un índice en memoria. Cubren:

- eventos ``status`` y ``token`` en orden y el cierre con ``trace`` (``mode``/``path``),
  ``meta``, ``products`` y ``done``;
- persistencia del turno en una transacción (mensajes, resultado comercial y cotización);
- abstención, reintento idempotente por ``client_turn_id`` y errores antes y después del
  primer token; cancelación con ``GeneratorExit``;
- selección del runner: inyectado, por defecto según ``AGENT_MODE`` y respaldo a
  ``routes`` cuando el runtime no construyó el grafo ``tools``;
- respaldo determinista (T037, T041, FR-009): fallo del proveedor, plazo vencido o decisión
  inválida antes del primer token → evento ``fallback`` sin LLM, persistido como completo;
- varias cotizaciones por turno (T038, T042, T043, FR-012a): una tarjeta, un folio y una
  fila ``quotes`` por cotización, en la misma transacción, con reenvío idempotente;
- monitoreo por herramienta (T044, FR-014).
"""

from collections.abc import Generator, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import asdict, replace
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import httpx
import openai
import pytest
from langchain_core.exceptions import OutputParserException
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.store.memory import InMemoryStore
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs
from tests.fakes.tool_chat import ScriptedToolChat, ScriptStep

from apps.api import agent_runtime as agent_runtime_module
from apps.api.agent_runtime import AgentRuntime, _graphs
from apps.api.chat import service as service_module
from apps.api.chat.agent import (
    AgentTurn,
    GraphAgentRunner,
    ToolAgentEvent,
    ToolAgentFinal,
    ToolAgentRunner,
    ToolAgentSources,
    ToolAgentStatus,
    ToolAgentToken,
    TurnTimeout,
)
from apps.api.chat.service import ChatFailure, ChatService
from apps.api.chat.streaming import ChatEvent
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.adapters.storage.quotes import SqlQuoteRepository
from carvajal.agent.graph import AgentDeps, build_agent
from carvajal.agent.router import decide_route
from carvajal.agent.state import Turn
from carvajal.agent.toolagent import fallback
from carvajal.agent.toolagent.graph import build_tool_agent
from carvajal.agent.toolagent.tools import ToolDeps
from carvajal.application.ask import NO_CONTEXT_ANSWER, Source
from carvajal.application.errors import LLMError
from carvajal.application.ports import ScoredChunk
from carvajal.catalog.loader import load_catalog
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings

QUESTION = "¿Cuándo se fundó Carvajal y cuánto cuestan 50 vasos en Bogotá?"
ANSWER_PARTS = ("Carvajal se fundó en 1904 [1]. ", "Los vasos cuestan 3.751 COP.")
ANSWER = "".join(ANSWER_PARTS)

SOURCE = Source(
    "historia-1",
    "historia",
    "historia",
    "core",
    0.9,
    "Inició en 1904.",
    "https://www.carvajal.com/historia/",
    "Historia",
    "oficial",
)


# --- Dobles --------------------------------------------------------------------------------


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 8


class FakeLLM:
    """El modo ``tools`` no usa el LLM del servicio: el grafo trae su propio modelo."""

    def complete(self, *, system: str, prompt: str) -> str:
        raise AssertionError("El modo tools no debe llamar al LLM del servicio.")

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        raise AssertionError("El modo tools no debe llamar al LLM del servicio.")


class FakeToolRunner:
    """Runner del modo ``tools`` que reproduce un guion de eventos.

    ``error_at`` lanza ``error`` antes de producir el evento de esa posición. ``closed``
    indica si el servicio cerró el generador (cancelación o error).
    """

    def __init__(
        self,
        events: Sequence[ToolAgentEvent],
        *,
        error: Exception | None = None,
        error_at: int | None = None,
    ) -> None:
        self.events = list(events)
        self.error = error
        self.error_at = error_at
        self.calls: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.closed = False

    def stream(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
    ) -> Iterator[ToolAgentEvent]:
        self.calls.append(
            {
                "owner_key": owner_key,
                "thread_id": thread_id,
                "question": question,
                "seed_turns": list(seed_turns),
            }
        )
        try:
            for position, event in enumerate(self.events):
                if self.error is not None and position == self.error_at:
                    raise self.error
                yield event
            if self.error is not None and self.error_at is None:
                raise self.error
        finally:
            self.closed = True

    def record(
        self,
        thread_id: str,
        *,
        question: str,
        answer: str,
        incomplete: bool,
        turns: Sequence[Turn],
    ) -> None:
        self.records.append({"question": question, "answer": answer, "incomplete": incomplete})


class RoutesAgent:
    """Runner del modo ``routes``: en modo ``tools`` solo lo usa el respaldo (FR-009).

    ``sources`` y ``products`` son lo que obtendría el camino de reglas; el ``prompt`` se
    deja preparado para comprobar que el respaldo no lo envía a ningún LLM.
    """

    def __init__(
        self,
        *,
        route: str = "rag",
        sources: tuple[Source, ...] = (),
        products: dict[str, Any] | None = None,
        prompt: str | None = None,
    ) -> None:
        self.prepares = 0
        self.records: list[dict[str, Any]] = []
        self.route = route
        self.sources = sources
        self.products = products or {}
        self.prompt = prompt

    def prepare(
        self, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Turn]
    ) -> AgentTurn:
        self.prepares += 1
        return AgentTurn(
            route=self.route,
            intent="documental",
            prompt=self.prompt,
            system="sistema",
            sources=self.sources,
            turns=tuple(seed_turns),
            products=dict(self.products),
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
        self.records.append({"answer": answer, "incomplete": incomplete})


class FakeSpan:
    """Handle de traza que registra metadatos, salidas, hijos y cierres."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.outputs: dict[str, Any] = {}
        self.outcomes: list[str] = []
        self.children: list[tuple[str, str, dict[str, Any], FakeSpan]] = []
        self.generations = 0

    def generation(self) -> Any:
        self.generations += 1
        return nullcontext()

    def child(self, name: str, *, run_type: str = "chain", metadata: Any = None) -> Any:
        child = FakeSpan()
        self.children.append((name, run_type, dict(metadata or {}), child))
        return nullcontext(child)

    def add_metadata(self, **values: Any) -> None:
        self.metadata.update(values)

    def add_outputs(self, **values: Any) -> None:
        self.outputs.update(values)

    def finish(self, *, outcome: str = "ok", error: BaseException | None = None) -> None:
        self.outcomes.append(outcome)


class FakeTracer:
    """Tracer que abre siempre y conserva cada turno."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.turns: list[FakeSpan] = []

    @contextmanager
    def turn(
        self,
        *,
        request_id: str,
        thread_hash: str,
        router: str,
        question: str = "",
    ) -> Generator[FakeSpan]:
        self.calls.append({"request_id": request_id, "router": router, "question": question})
        span = FakeSpan()
        self.turns.append(span)
        try:
            yield span
        except GeneratorExit:
            span.finish(outcome="cancelled")
            raise
        except BaseException as error:
            span.finish(outcome="failed", error=error)
            raise
        else:
            span.finish(outcome="ok")


# --- Utilidades ----------------------------------------------------------------------------


@pytest.fixture()
def engine() -> Iterator[Any]:
    """Base SQLite en memoria con el esquema real, sin Docker."""

    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    # SQLite no soporta índices parciales: se retiran los de ``client_turn_id`` como en
    # ``tests/test_chat_tracing.py``; el reintento se resuelve con ``find_turn``.
    with motor.begin() as conexion:
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    try:
        yield motor
    finally:
        tables.metadata.drop_all(motor)
        motor.dispose()


def tools_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, agent_mode="tools", **overrides)  # type: ignore[call-arg]


def build_service(
    engine: Any,
    tool_runner: Any = None,
    *,
    agent: Any = None,
    tracer: FakeTracer | None = None,
    settings: Settings | None = None,
) -> ChatService:
    return ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        agent=agent,
        tool_agent=tool_runner,
        tracer=tracer or FakeTracer(),
        settings=settings or tools_settings(),
    )


def final(
    *,
    answer: str = ANSWER,
    sources: tuple[Source, ...] = (SOURCE,),
    products: dict[str, Any] | None = None,
    quotes: tuple[dict[str, Any], ...] = (),
    abstained: bool = False,
    gate: str = "evidence",
    mode_path: str = "agent",
    results: tuple[dict[str, Any], ...] | None = None,
    preferences: tuple[str, ...] = (),
) -> ToolAgentFinal:
    return ToolAgentFinal(
        answer=answer,
        gate=cast(Any, gate),
        abstained=abstained,
        sources=sources,
        products=products or {},
        quotes=quotes,
        commercial_context={},
        results=results
        if results is not None
        else (
            {"tool": "buscar_corpus", "status": "ok", "source_ids": [1]},
            {"tool": "cotizar", "status": "ok", "source_ids": []},
        ),
        mode_path=mode_path,
        preferences=preferences,
        usage={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    )


def scripted_events(closing: ToolAgentFinal | None = None) -> list[ToolAgentEvent]:
    closing = closing or final()
    return [
        ToolAgentStatus(text="Estoy buscando en la información de Carvajal…", step=1),
        ToolAgentStatus(text="Estoy calculando cuánto te salen 50 vasos en Bogotá…", step=2),
        ToolAgentSources(sources=closing.sources),
        *(ToolAgentToken(text=part) for part in ANSWER_PARTS),
        closing,
    ]


def quote_payload(units: int = 50, total: str = "3751") -> dict[str, Any]:
    return {
        "kind": "cotizacion",
        "version": 2,
        "city": "Bogotá",
        "reply": f"Son {total} COP.",
        "primary": {
            "sku": "7704918100199",
            "name": "Vaso rígido",
            "currency": "COP",
            "units_requested": units,
            "presentation": {
                "basis": "paquete",
                "units": 50,
                "count": units // 50,
                "units_bought": units,
                "units_surplus": 0,
                "price_per_presentation": "3751",
                "subtotal": total,
            },
        },
        "commercial": {
            "subtotal": total,
            "total": total,
            "valid_until": None,
            "shipping": {"requested": False, "status": "not_requested", "cost": None},
            "tax": {"status": "incluido", "rate": None},
        },
        "total": total,
        "pending_lines": [],
    }


def commercial_final() -> ToolAgentFinal:
    quote = quote_payload()
    return final(
        products={"quote": quote, "quotes": [quote], "cities": ["Bogotá"]},
        quotes=(quote,),
    )


def named(events: Sequence[ChatEvent], name: str) -> ChatEvent:
    return next(event for event in events if event.name == name)


def names(events: Sequence[ChatEvent]) -> list[str]:
    return [event.name for event in events]


def stored_messages(engine: Any) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(tables.messages)).mappings()]


def stored_quotes(engine: Any) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(tables.quotes)).mappings()]


# --- Eventos del stream --------------------------------------------------------------------


def test_modo_tools_emite_status_meta_tokens_y_cierra_con_trace_y_done(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-tools", QUESTION))

    # Assert: ``meta`` antes del primer token, como en ``routes``; el cliente sella cada
    # párrafo con las fuentes que conoce al recibirlo.
    assert names(events) == [
        "open",
        "status",
        "status",
        "meta",
        "token",
        "token",
        "trace",
        "done",
    ]
    statuses = [event.data for event in events if event.name == "status"]
    assert statuses == [
        {"text": "Estoy buscando en la información de Carvajal…", "step": 1},
        {"text": "Estoy calculando cuánto te salen 50 vasos en Bogotá…", "step": 2},
    ]
    assert [event.data["text"] for event in events if event.name == "token"] == list(ANSWER_PARTS)
    assert named(events, "meta").data == {"sources": [asdict(SOURCE)]}
    assert runner.calls[0]["question"] == QUESTION
    assert runner.calls[0]["owner_key"] == "dueno-tools"


def test_el_trace_del_modo_tools_lleva_mode_path_y_los_campos_actuales(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(final(preferences=("respuestas breves",))))
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-trace", QUESTION))

    # Assert
    trace = named(events, "trace").data
    done = named(events, "done").data
    assert trace["mode"] == "tools"
    assert trace["path"] == "agent"
    assert trace["route"] == "agent"
    assert trace["thread_id"] == done["thread_id"]
    assert trace["tool"] == "buscar_corpus,cotizar"
    assert trace["gate"] == "evidence"
    assert trace["memory"] == 1
    assert trace["retrieved"] == 1
    assert {"intent", "reason"} <= set(trace)
    assert done["route"] == "agent"


def test_el_atajo_se_reporta_con_route_y_path_shortcut(engine: Any) -> None:
    # Arrange
    closing = final(answer="¡Hola!", sources=(), results=(), gate="no_query", mode_path="shortcut")
    runner = FakeToolRunner([ToolAgentToken(text="¡Hola!"), closing])
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-atajo", "hola"))

    # Assert
    trace = named(events, "trace").data
    assert trace["route"] == "shortcut"
    assert trace["path"] == "shortcut"
    assert trace["tool"] == ""


# --- Persistencia --------------------------------------------------------------------------


def test_el_modo_tools_persiste_el_turno_y_cierra_el_runner(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-persiste", QUESTION, client_turn_id="turno-1"))
    history = list(service.history("dueno-persiste"))

    # Assert
    done = named(events, "done").data
    assert [message.role for message in history] == ["user", "assistant"]
    assert history[0].content == QUESTION
    assert history[1].content == ANSWER
    assert history[1].status == "complete"
    assert history[1].sources == [asdict(SOURCE)]
    assert done["message_id"] == history[1].id
    assert done["quote_id"] is None
    assert runner.records == [{"question": QUESTION, "answer": ANSWER, "incomplete": False}]


def test_el_turno_comercial_guarda_tarjetas_resultado_y_cotizacion_juntos(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-cotiza", QUESTION, client_turn_id="turno-c"))

    # Assert
    assert names(events)[-3:] == ["trace", "products", "done"]
    assert names(events).index("meta") < names(events).index("token")
    folio = named(events, "done").data["quote_id"]
    assert folio
    assert 'class="coti' in named(events, "products").data["html"]
    quotes = stored_quotes(engine)
    assert [row["id"] for row in quotes] == [folio]
    assistant = next(row for row in stored_messages(engine) if row["role"] == "assistant")
    assert quotes[0]["message_id"] == assistant["id"]
    commercial = assistant["sources"][-1]
    assert commercial["result_type"] == "commercial"
    assert commercial["result"]["quote"]["quote"]["id"] == folio
    assert commercial["result"]["quotes"][0]["quote"]["id"] == folio


def test_la_abstencion_emite_la_respuesta_fija_sin_fuentes(engine: Any) -> None:
    # Arrange
    closing = final(answer=NO_CONTEXT_ANSWER, sources=(), abstained=True, gate="abstain")
    runner = FakeToolRunner([ToolAgentToken(text=NO_CONTEXT_ANSWER), closing])
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-abstiene", QUESTION))
    history = list(service.history("dueno-abstiene"))

    # Assert
    assert named(events, "token").data == {"text": NO_CONTEXT_ANSWER}
    assert named(events, "meta").data == {"sources": []}
    assert named(events, "trace").data["gate"] == "abstain"
    assert history[1].content == NO_CONTEXT_ANSWER
    assert history[1].sources == []


def test_una_respuesta_vacia_se_atiende_con_el_respaldo(engine: Any) -> None:
    # Arrange: un cierre sin texto es una salida inválida del proveedor (FR-009).
    agent = RoutesAgent(sources=(SOURCE,))
    runner = FakeToolRunner([final(answer="", sources=(SOURCE,))])
    service = build_service(engine, runner, agent=agent)

    # Act
    events = list(service.stream("dueno-vacio", QUESTION))
    history = list(service.history("dueno-vacio"))

    # Assert
    assert "token" not in names(events)
    expected = fallback.render({}, (SOURCE,))
    assert named(events, "fallback").data == {"text": expected, "sources": [asdict(SOURCE)]}
    assert named(events, "trace").data["path"] == "fallback"
    assert named(events, "trace").data["reason"] == "empty_answer"
    assert history[1].content == expected
    assert history[1].status == "complete"
    assert named(events, "done")


# --- Reintento idempotente -----------------------------------------------------------------


def test_el_reintento_reproduce_el_turno_sin_volver_al_agente(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    first = list(service.stream("dueno-replay", QUESTION, client_turn_id="turno-r"))

    # Act
    replay = list(service.stream("dueno-replay", QUESTION, client_turn_id="turno-r"))

    # Assert
    assert len(runner.calls) == 1
    assert names(replay) == ["open", "trace", "meta", "products", "token", "done"]
    assert "status" not in names(replay)
    assert named(replay, "trace").data["route"] == "replay"
    assert named(replay, "token").data["text"] == ANSWER
    assert named(replay, "done").data["quote_id"] == named(first, "done").data["quote_id"]
    assert len(stored_messages(engine)) == 2
    assert len(stored_quotes(engine)) == 1


# --- Errores y cancelación -----------------------------------------------------------------


def test_un_error_ajeno_al_proveedor_antes_del_primer_token_no_guarda_el_turno(
    engine: Any,
) -> None:
    # Arrange: un error que no es del proveedor (un fallo de programación) no se enmascara
    # con el respaldo.
    tracer = FakeTracer()
    agent = RoutesAgent()
    runner = FakeToolRunner(scripted_events(), error=RuntimeError("DeepSeek caído"), error_at=1)
    service = build_service(engine, runner, tracer=tracer, agent=agent)

    # Act
    events = list(service.stream("dueno-falla", QUESTION))

    # Assert
    assert agent.prepares == 0
    assert "fallback" not in names(events)
    assert names(events)[-1] == "error"
    assert named(events, "error").data["incomplete"] is False
    assert list(service.history("dueno-falla")) == []
    assert runner.records == []
    assert runner.closed is True
    assert tracer.turns[0].outcomes == ["failed"]


def test_un_error_tras_el_primer_token_guarda_el_turno_incompleto(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(), error=RuntimeError("corte"), error_at=4)
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-parcial", QUESTION))
    history = list(service.history("dueno-parcial"))

    # Assert
    assert named(events, "error").data["incomplete"] is True
    assert history[1].content == ANSWER_PARTS[0]
    assert history[1].status == "incomplete"
    assert history[1].sources == []
    assert stored_quotes(engine) == []
    assert runner.records == [{"question": QUESTION, "answer": ANSWER_PARTS[0], "incomplete": True}]


def test_ask_en_modo_tools_eleva_el_error_traducido(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner([], error=RuntimeError("DeepSeek caído"))
    service = build_service(engine, runner)

    # Act / Assert
    with pytest.raises(ChatFailure):
        service.ask("dueno-ask-falla", QUESTION)


def test_la_cancelacion_durante_el_texto_guarda_el_turno_incompleto(engine: Any) -> None:
    # Arrange: research R2, «Detener» durante el texto persiste ``incomplete``.
    tracer = FakeTracer()
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner, tracer=tracer)
    generator = cast(Generator[ChatEvent], service.stream("dueno-cancela", QUESTION))
    received = [next(generator) for _ in range(5)]

    # Act
    generator.close()
    history = list(service.history("dueno-cancela"))

    # Assert
    assert names(received) == ["open", "status", "status", "meta", "token"]
    assert runner.closed is True
    assert tracer.turns[0].outcomes == ["cancelled"]
    assert tracer.turns[0].outputs["answer"] == ANSWER_PARTS[0]
    assert [message.role for message in history] == ["user", "assistant"]
    assert history[1].content == ANSWER_PARTS[0]
    assert history[1].status == "incomplete"
    assert history[1].sources == []
    assert stored_quotes(engine) == []
    assert runner.records == [{"question": QUESTION, "answer": ANSWER_PARTS[0], "incomplete": True}]


def test_la_cancelacion_antes_del_primer_token_no_guarda_nada(engine: Any) -> None:
    # Arrange
    tracer = FakeTracer()
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner, tracer=tracer)
    generator = cast(Generator[ChatEvent], service.stream("dueno-cancela-antes", QUESTION))
    received = [next(generator) for _ in range(3)]

    # Act
    generator.close()

    # Assert
    assert names(received) == ["open", "status", "status"]
    assert runner.closed is True
    assert tracer.turns[0].outcomes == ["cancelled"]
    assert runner.records == []
    assert list(service.history("dueno-cancela-antes")) == []


# --- Monitoreo -----------------------------------------------------------------------------


def test_el_monitoreo_registra_modo_camino_y_el_hijo_del_modelo(engine: Any) -> None:
    # Arrange
    tracer = FakeTracer()
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner, tracer=tracer, settings=tools_settings())

    # Act
    list(service.stream("dueno-monitoreo", QUESTION))

    # Assert
    span = tracer.turns[0]
    assert span.outcomes == ["ok"]
    assert span.metadata["mode"] == "tools"
    assert span.metadata["path"] == "agent"
    assert span.metadata["route"] == "agent"
    assert span.metadata["gate"] == "evidence"
    assert span.metadata["answer_chars"] == len(ANSWER)
    assert span.outputs["answer"] == ANSWER
    name, run_type, metadata, child = span.children[0]
    assert (name, run_type) == ("llm", "llm")
    assert metadata["provider"] == "deepseek"
    assert metadata["model"] == tools_settings().deepseek_model
    assert child.outputs["answer_chars"] == len(ANSWER)
    assert child.outputs["usage"] == {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}


# --- Selección del runner ------------------------------------------------------------------


def test_en_modo_routes_el_runner_tools_inyectado_no_se_usa(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    agent = RoutesAgent()
    service = build_service(
        engine,
        runner,
        agent=agent,
        settings=Settings(_env_file=None, agent_mode="routes"),  # type: ignore[call-arg]
    )

    # Act
    events = list(service.stream("dueno-routes", QUESTION))

    # Assert
    assert runner.calls == []
    assert agent.prepares == 1
    assert names(events) == ["open", "trace", "meta", "token", "done"]
    assert "mode" not in named(events, "trace").data


def test_con_solo_el_runner_routes_inyectado_no_construye_el_runtime(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    def unexpected() -> None:
        raise AssertionError("No debe construirse el runtime real.")

    monkeypatch.setattr(service_module, "default_tool_agent_runner", unexpected)
    agent = RoutesAgent()
    service = build_service(engine, agent=agent)

    # Act
    events = list(service.stream("dueno-inyectado", QUESTION))

    # Assert
    assert agent.prepares == 1
    assert named(events, "token").data["text"] == NO_CONTEXT_ANSWER


def test_por_defecto_usa_el_runner_tools_del_runtime(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    monkeypatch.setattr(service_module, "default_tool_agent_runner", lambda: runner)
    service = build_service(engine)

    # Act
    events = list(service.stream("dueno-defecto", QUESTION))

    # Assert
    assert len(runner.calls) == 1
    assert named(events, "trace").data["mode"] == "tools"


def test_sin_grafo_tools_en_el_runtime_cae_al_modo_routes(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    agent = RoutesAgent()
    monkeypatch.setattr(service_module, "default_tool_agent_runner", lambda: None)
    monkeypatch.setattr(service_module, "default_agent_runner", lambda: agent)
    service = build_service(engine)

    # Act
    events = list(service.stream("dueno-sin-grafo", QUESTION))

    # Assert
    assert agent.prepares == 1
    assert named(events, "trace").data["route"] == "rag"


def test_ask_en_modo_tools_devuelve_texto_y_fuentes(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)

    # Act
    answer = service.ask("dueno-ask", QUESTION)

    # Assert
    assert answer.text == ANSWER
    assert answer.sources == (SOURCE,)


# --- Extremo a extremo con el grafo real ---------------------------------------------------


class SingleBatchStore:
    """Índice en memoria que devuelve siempre el mismo lote."""

    def __init__(self, hits: Sequence[ScoredChunk]) -> None:
        self.hits = list(hits)

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise AssertionError("El servicio no indexa.")

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        return self.hits[:limit]

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("El servicio no borra.")


def test_por_defecto_sin_deepseek_api_key_el_turno_va_por_routes_sin_error(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: CI o desarrollo sin clave con el valor por defecto (``AGENT_MODE=tools``).
    # El runtime se arma con el ``_graphs`` real y los runners por defecto lo leen.
    monkeypatch.delenv("AGENT_MODE", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with capture_logs() as logs:
        graph, tool_graph = _graphs(
            settings,
            vector_store=SingleBatchStore([]),
            embedder=FakeEmbedder(),
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
            router=decide_route,
        )
    runtime = AgentRuntime(
        graph=graph,
        qdrant=Mock(),
        checkpointer=None,
        store=None,
        _stack=ExitStack(),
        tool_graph=tool_graph,
    )
    monkeypatch.setattr(agent_runtime_module, "_runtime", runtime)
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        tracer=FakeTracer(),
        settings=settings,
    )

    # Act
    events = list(service.stream("dueno-sin-clave", "¿Qué es Carvajal?"))
    answer = service.ask("dueno-sin-clave", "¿Qué es Carvajal?")

    # Assert: se registra el modo no disponible y el turno sigue por ``routes``, sin 503.
    assert settings.agent_mode == "tools"
    assert runtime.mode == "routes"
    evento = next(item for item in logs if item["event"] == "agent.tools_mode_unavailable")
    assert evento["reason"] == "missing_deepseek_api_key"
    assert evento["log_level"] == "warning"
    assert names(events) == ["open", "trace", "meta", "token", "done"]
    assert named(events, "trace").data["route"] == "rag"
    assert "mode" not in named(events, "trace").data
    assert named(events, "token").data["text"] == NO_CONTEXT_ANSWER
    assert answer.text == NO_CONTEXT_ANSWER


def test_extremo_a_extremo_con_el_runner_real_y_un_chat_guionado(engine: Any) -> None:
    # Arrange
    hit = ScoredChunk(
        chunk=Chunk(
            id="historia:3",
            document_id="historia",
            text="Carvajal fue fundada en Cali en 1904.",
            position=0,
            section="historia",
            layer="core",
        ),
        score=0.9,
        metadata={
            "url": "https://www.carvajal.com/historia/",
            "title": "Historia",
            "source_type": "oficial",
        },
    )
    answer = "Carvajal fue fundada en Cali en 1904 [1], como una imprenta familiar."
    settings = tools_settings()
    chat = ScriptedToolChat(
        steps=[
            ScriptStep(tool_calls=[{"name": "buscar_corpus", "args": {"consulta": "fundación"}}]),
            ScriptStep(text=answer),
        ]
    )
    graph = build_tool_agent(
        ToolDeps(vector_store=SingleBatchStore([hit]), embedder=FakeEmbedder(), settings=settings),
        chat,
        checkpointer=InMemorySaver(),
    )
    service = build_service(engine, ToolAgentRunner(graph, settings), settings=settings)

    # Act
    events = list(service.stream("dueno-e2e", "¿Cuándo se fundó Carvajal?"))
    history = list(service.history("dueno-e2e"))

    # Assert
    assert names(events)[0:2] == ["open", "status"]
    assert "".join(e.data["text"] for e in events if e.name == "token") == answer
    assert len(named(events, "meta").data["sources"]) == 1
    assert names(events).count("meta") == 1
    assert names(events).index("meta") < names(events).index("token")
    assert named(events, "trace").data["tool"] == "buscar_corpus"
    assert history[1].content == answer
    stored = history[1].sources
    assert stored is not None
    assert stored[0]["url"] == "https://www.carvajal.com/historia/"


def test_una_fuente_de_tipo_sistema_llega_a_la_api_y_al_historial(engine: Any) -> None:
    # Arrange: ``fecha_actual`` cita el reloj del servidor con ``source_type="sistema"``
    # (spec 045, T082); la interfaz solo distingue ``tercero`` y no debe romperse.
    from datetime import UTC, datetime

    from apps.api.runtime import templates

    answer = "Hoy es martes 24 de noviembre de 2026 en Colombia [1]."
    settings = tools_settings()
    chat = ScriptedToolChat(
        steps=[
            ScriptStep(tool_calls=[{"name": "fecha_actual", "args": {}}]),
            ScriptStep(text=answer),
        ]
    )
    graph = build_tool_agent(
        ToolDeps(
            vector_store=SingleBatchStore([]),
            embedder=FakeEmbedder(),
            settings=settings,
            now=datetime(2026, 11, 24, 15, 0, tzinfo=UTC),
        ),
        chat,
        checkpointer=InMemorySaver(),
    )
    service = build_service(engine, ToolAgentRunner(graph, settings), settings=settings)

    # Act
    events = list(service.stream("dueno-reloj", "¿Qué día es hoy?"))
    history = list(service.history("dueno-reloj"))
    html = templates.get_template("_chat_thread.html").render(messages=history, error=None)

    # Assert
    sources = named(events, "meta").data["sources"]
    assert [source["source_type"] for source in sources] == ["sistema"]
    assert "".join(e.data["text"] for e in events if e.name == "token") == answer
    stored = history[1].sources
    assert stored is not None
    assert stored[0]["source_type"] == "sistema"
    assert stored[0]["chunk_id"] == "reloj:2026-11-24"
    assert "24 de noviembre de 2026" in html
    assert "etiqueta-tercero" not in html


def test_si_las_fuentes_cambian_tras_anunciarlas_se_reemite_meta(engine: Any) -> None:
    # Arrange: el runner anunció una fuente, pero el cierre trae otra lista (por ejemplo, el
    # modelo siguió consultando tras un texto largo).
    closing = final()
    runner = FakeToolRunner(
        [
            ToolAgentSources(sources=()),
            ToolAgentToken(text=ANSWER),
            closing,
        ]
    )
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-reanuncia", QUESTION))

    # Assert
    metas = [event.data for event in events if event.name == "meta"]
    assert metas == [{"sources": []}, {"sources": [asdict(SOURCE)]}]


# --- Respaldo determinista (T037, T041, FR-009) --------------------------------------------


def _connection_error() -> Exception:
    # ``openai`` tipa la petición con su propio cliente HTTP; el error solo la guarda.
    request = cast(Any, httpx.Request("POST", "https://api.deepseek.com"))
    return openai.APIConnectionError(request=request)


def _timeout() -> Exception:
    # Sin fuentes ni tarjetas obtenidas: el respaldo es el camino de reglas.
    obtained = final(
        answer="",
        sources=(),
        results=({"tool": "buscar_corpus", "status": "ok", "source_ids": [1], "elapsed_ms": 7},),
    )
    return TurnTimeout(45, obtained)


@pytest.mark.parametrize(
    ("make_error", "reason"),
    [
        (lambda: LLMError("El proveedor de LLM no devolvió una respuesta utilizable."), "provider"),
        (_connection_error, "provider"),
        (_timeout, "timeout"),
        (lambda: OutputParserException("argumentos ilegibles"), "invalid_decision"),
        (lambda: GraphRecursionError("bucle de decisiones"), "invalid_decision"),
    ],
    ids=["llm_error", "red", "plazo", "salida_invalida", "bucle"],
)
def test_un_fallo_del_proveedor_antes_del_primer_token_usa_el_respaldo(
    engine: Any, make_error: Any, reason: str
) -> None:
    # Arrange: el proveedor falla tras las líneas de estado, sin texto todavía.
    tracer = FakeTracer()
    agent = RoutesAgent(sources=(SOURCE,), prompt="prompt que nadie debe enviar")
    runner = FakeToolRunner(scripted_events(), error=make_error(), error_at=2)
    service = build_service(engine, runner, agent=agent, tracer=tracer)

    # Act
    events = list(service.stream("dueno-respaldo", QUESTION, client_turn_id="turno-f"))
    history = list(service.history("dueno-respaldo"))

    # Assert: sin LLM (``FakeLLM`` lanza si se llama), con el aviso fijo y la cita.
    assert names(events) == [
        "open",
        "status",
        "status",
        "meta",
        "fallback",
        "trace",
        "done",
    ]
    expected = fallback.render({}, (SOURCE,))
    assert expected.startswith(fallback.FALLBACK_NOTICE)
    assert "[1]" in expected
    assert named(events, "fallback").data == {"text": expected, "sources": [asdict(SOURCE)]}
    assert named(events, "meta").data == {"sources": [asdict(SOURCE)]}
    trace = named(events, "trace").data
    assert (trace["mode"], trace["path"], trace["route"]) == ("tools", "fallback", "agent")
    assert trace["reason"] == reason
    assert trace["fallback_route"] == "rag"
    assert named(events, "done").data["route"] == "agent"
    assert agent.prepares == 1
    assert runner.closed is True
    assert history[1].content == expected
    assert history[1].status == "complete"
    assert history[1].sources == [asdict(SOURCE)]
    assert agent.records == [{"answer": expected, "incomplete": False}]
    span = tracer.turns[0]
    assert span.outcomes == ["ok"]
    assert span.metadata["path"] == "fallback"
    assert span.metadata["fallback_reason"] == reason
    assert span.outputs["answer"] == expected


def test_el_respaldo_muestra_tarjetas_y_emite_el_folio_de_la_cotizacion(engine: Any) -> None:
    # Arrange
    quote = quote_payload()
    agent = RoutesAgent(route="commercial", products={"quote": quote, "cities": ["Bogotá"]})
    runner = FakeToolRunner([], error=LLMError("caído"))
    service = build_service(engine, runner, agent=agent)

    # Act
    events = list(service.stream("dueno-respaldo-coti", QUESTION, client_turn_id="turno-fc"))

    # Assert
    done = named(events, "done").data
    folio = sha256(f"{done['conversation_id']}:turno-fc".encode()).hexdigest()[:12]
    assert done["quote_id"] == folio
    assert done["quote_ids"] == [folio]
    text = named(events, "fallback").data["text"]
    assert text.startswith(fallback.FALLBACK_NOTICE)
    assert f"Folio #{folio}" in text
    assert 'class="coti' in named(events, "products").data["html"]
    assert [row["id"] for row in stored_quotes(engine)] == [folio]


def test_el_respaldo_sin_resultados_invita_a_reintentar(engine: Any) -> None:
    # Arrange
    agent = RoutesAgent()
    runner = FakeToolRunner([], error=LLMError("caído"))
    service = build_service(engine, runner, agent=agent)

    # Act
    events = list(service.stream("dueno-respaldo-vacio", QUESTION))
    history = list(service.history("dueno-respaldo-vacio"))

    # Assert
    expected = f"{fallback.FALLBACK_NOTICE}\n\n{fallback.RETRY_INVITATION}"
    assert named(events, "fallback").data == {"text": expected, "sources": []}
    assert "products" not in names(events)
    assert history[1].content == expected


def test_un_fallo_del_proveedor_tras_el_primer_token_guarda_el_turno_incompleto(
    engine: Any,
) -> None:
    # Arrange: con texto a la vista, el respaldo ya no puede sustituirlo.
    agent = RoutesAgent(sources=(SOURCE,))
    runner = FakeToolRunner(scripted_events(), error=LLMError("corte"), error_at=4)
    service = build_service(engine, runner, agent=agent)

    # Act
    events = list(service.stream("dueno-respaldo-tarde", QUESTION))
    history = list(service.history("dueno-respaldo-tarde"))

    # Assert
    assert agent.prepares == 0
    assert "fallback" not in names(events)
    assert named(events, "error").data["incomplete"] is True
    assert history[1].content == ANSWER_PARTS[0]
    assert history[1].status == "incomplete"
    assert stored_quotes(engine) == []


def test_si_el_respaldo_tambien_falla_no_se_guarda_nada(engine: Any) -> None:
    # Arrange
    class BrokenRoutes(RoutesAgent):
        def prepare(
            self, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Turn]
        ) -> AgentTurn:
            raise ConnectionError("Qdrant caído")

    runner = FakeToolRunner([], error=LLMError("caído"))
    service = build_service(engine, runner, agent=BrokenRoutes())

    # Act
    events = list(service.stream("dueno-respaldo-roto", QUESTION))

    # Assert
    assert names(events)[-1] == "error"
    assert named(events, "error").data["incomplete"] is False
    assert list(service.history("dueno-respaldo-roto")) == []


def test_el_plazo_vencido_registra_en_el_monitoreo_lo_obtenido(engine: Any) -> None:
    # Arrange
    tracer = FakeTracer()
    runner = FakeToolRunner(scripted_events(), error=_timeout(), error_at=1)
    service = build_service(engine, runner, agent=RoutesAgent(), tracer=tracer)

    # Act
    list(service.stream("dueno-plazo", QUESTION))

    # Assert: el hijo de la herramienta que alcanzó a correr y los tokens consumidos.
    span = tracer.turns[0]
    tool_children = [c for c in span.children if c[1] == "tool"]
    assert [(name, meta["status"]) for name, _, meta, _ in tool_children] == [
        ("buscar_corpus", "ok")
    ]
    assert span.metadata["total_tokens"] == 150
    assert span.metadata["error_type"] == "TurnTimeout"


def test_el_respaldo_de_extremo_a_extremo_usa_el_grafo_routes_en_el_mismo_hilo(
    engine: Any,
) -> None:
    # Arrange: ambos grafos comparten checkpointer, como en ``agent_runtime``. DeepSeek falla
    # en el primer turno; el segundo vuelve al modo ``tools`` en el mismo hilo.
    hit = ScoredChunk(
        chunk=Chunk(
            id="historia:3",
            document_id="historia",
            text="Carvajal fue fundada en Cali en 1904.",
            position=0,
            section="historia",
            layer="core",
        ),
        score=0.9,
        metadata={
            "url": "https://www.carvajal.com/historia/",
            "title": "Historia",
            "source_type": "oficial",
        },
    )
    settings = tools_settings()
    store = SingleBatchStore([hit])
    checkpointer = InMemorySaver()
    answer = "Carvajal fue fundada en Cali en 1904 [1]."
    chat = ScriptedToolChat(
        steps=[
            ScriptStep(error=LLMError("El proveedor de LLM no respondió.")),
            ScriptStep(tool_calls=[{"name": "buscar_corpus", "args": {"consulta": "fundación"}}]),
            ScriptStep(text=answer),
        ]
    )
    tool_graph = build_tool_agent(
        ToolDeps(vector_store=store, embedder=FakeEmbedder(), settings=settings),
        chat,
        checkpointer=checkpointer,
    )
    routes_graph = build_agent(
        AgentDeps(vector_store=store, embedder=FakeEmbedder(), settings=settings),
        checkpointer=checkpointer,
    )
    service = build_service(
        engine,
        ToolAgentRunner(tool_graph, settings),
        agent=GraphAgentRunner(routes_graph, settings),
        settings=settings,
    )

    # Act
    first = list(service.stream("dueno-e2e-respaldo", "¿Cuándo se fundó Carvajal?"))
    second = list(service.stream("dueno-e2e-respaldo", "¿Y dónde se fundó?"))
    history = list(service.history("dueno-e2e-respaldo"))

    # Assert
    text = named(first, "fallback").data["text"]
    assert text.startswith(fallback.FALLBACK_NOTICE)
    assert "1904" in text
    assert named(first, "trace").data["path"] == "fallback"
    assert "".join(e.data["text"] for e in second if e.name == "token") == answer
    assert named(second, "trace").data["path"] == "agent"
    assert [m.status for m in history] == ["complete"] * 4
    assert history[1].content == text


# --- Varias cotizaciones por turno (T038, T042, T043, FR-012a) -----------------------------


def folio_for(conversation_id: str, client_turn_id: str, index: int) -> str:
    """Fórmula de ``data-model.md`` (Quote): el índice 0 coincide con el folio de hoy."""

    seed = f"{conversation_id}:{client_turn_id}" + (f":{index}" if index else "")
    return sha256(seed.encode()).hexdigest()[:12]


def two_quotes_final() -> ToolAgentFinal:
    quotes = (quote_payload(50, "3751"), quote_payload(100, "7502"))
    return final(
        products={"quote": quotes[0], "quotes": list(quotes), "cities": ["Bogotá"]},
        quotes=quotes,
    )


def test_dos_cotizaciones_dan_dos_tarjetas_dos_folios_y_dos_filas(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(two_quotes_final()))
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-dos", QUESTION, client_turn_id="turno-2"))

    # Assert
    done = named(events, "done").data
    conversation_id = done["conversation_id"]
    folios = [folio_for(conversation_id, "turno-2", 0), folio_for(conversation_id, "turno-2", 1)]
    assert done["quote_ids"] == folios
    assert done["quote_id"] == folios[0]
    html = named(events, "products").data["html"]
    assert html.count('class="coti coti-resumen"') == 2
    assert html.index(f"#{folios[0]}") < html.index(f"#{folios[1]}")
    rows = stored_quotes(engine)
    assert sorted(row["id"] for row in rows) == sorted(folios)
    assistant = next(row for row in stored_messages(engine) if row["role"] == "assistant")
    assert {row["message_id"] for row in rows} == {assistant["id"]}
    totals = {row["id"]: str(row["total"]) for row in rows}
    assert totals[folios[0]].startswith("3751")
    assert totals[folios[1]].startswith("7502")
    result = assistant["sources"][-1]["result"]
    assert [q["quote"]["id"] for q in result["quotes"]] == folios
    assert result["quote"] == result["quotes"][0]


def test_sin_client_turn_id_cada_cotizacion_recibe_un_folio_distinto(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(two_quotes_final()))
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-dos-azar", QUESTION))

    # Assert
    folios = named(events, "done").data["quote_ids"]
    assert len(folios) == len(set(folios)) == 2
    assert all(len(folio) == 12 for folio in folios)


def test_las_cotizaciones_se_guardan_en_la_misma_transaccion_del_turno(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: la segunda fila falla; no debe quedar ni la primera ni el turno.
    original = SqlQuoteRepository.record
    calls: list[str] = []

    def failing(self: SqlQuoteRepository, quote_id: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(quote_id)
        if len(calls) == 2:
            raise RuntimeError("fallo al guardar la segunda cotización")
        return original(self, quote_id, *args, **kwargs)

    monkeypatch.setattr(SqlQuoteRepository, "record", failing)
    runner = FakeToolRunner(scripted_events(two_quotes_final()))
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-atomico", QUESTION, client_turn_id="turno-a"))

    # Assert
    assert len(calls) == 2
    assert names(events)[-1] == "error"
    assert stored_quotes(engine) == []
    assert stored_messages(engine) == []


def test_el_reenvio_devuelve_los_mismos_folios_en_el_mismo_orden(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(two_quotes_final()))
    service = build_service(engine, runner)
    first = list(service.stream("dueno-reenvio", QUESTION, client_turn_id="turno-rr"))

    # Act
    replay = list(service.stream("dueno-reenvio", QUESTION, client_turn_id="turno-rr"))

    # Assert
    assert len(runner.calls) == 1
    folios = named(first, "done").data["quote_ids"]
    assert named(replay, "done").data["quote_ids"] == folios
    assert named(replay, "done").data["quote_id"] == folios[0]
    html = named(replay, "products").data["html"]
    assert html.count('class="coti coti-resumen"') == 2
    assert html.index(f"#{folios[0]}") < html.index(f"#{folios[1]}")
    assert len(stored_quotes(engine)) == 2


def test_un_turno_sin_cotizaciones_cierra_con_quote_ids_vacio(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-sin-coti", QUESTION))

    # Assert
    done = named(events, "done").data
    assert done["quote_ids"] == []
    assert done["quote_id"] is None


def test_el_reenvio_de_un_turno_routes_conserva_su_unica_cotizacion(engine: Any) -> None:
    # Arrange: un resultado guardado sin ``quotes`` (modo ``routes``) cae a ``[quote]``.
    quote = quote_payload()
    agent = RoutesAgent(route="commercial", products={"quote": quote})
    service = build_service(
        engine,
        agent=agent,
        settings=Settings(_env_file=None, agent_mode="routes"),  # type: ignore[call-arg]
    )
    first = list(service.stream("dueno-routes-coti", QUESTION, client_turn_id="turno-rc"))

    # Act
    replay = list(service.stream("dueno-routes-coti", QUESTION, client_turn_id="turno-rc"))

    # Assert
    folio = named(first, "done").data["quote_id"]
    assert folio == folio_for(named(first, "done").data["conversation_id"], "turno-rc", 0)
    assert named(first, "done").data["quote_ids"] == [folio]
    assert named(replay, "done").data["quote_ids"] == [folio]
    assistant = next(row for row in stored_messages(engine) if row["role"] == "assistant")
    assert "quotes" not in assistant["sources"][-1]["result"]
    assert named(replay, "products").data["html"].count('class="coti coti-resumen"') == 1


FIXTURES = load_catalog(str(Path(__file__).parent / "fixtures" / "catalog"))
HOY = date(2026, 11, 27)
CALIENTE = {"family": "vaso", "use": "bebida_caliente", "temperature": "caliente"}


def quoting_service(
    engine: Any, calls: list[dict[str, Any]], *, max_quotes: int = 3
) -> tuple[ChatService, ScriptedToolChat]:
    """Servicio con el grafo real y un guion que pide ``cotizar`` con cada argumento dado."""

    settings = tools_settings(agent_max_quotes=max_quotes, agent_max_tool_calls=8)
    chat = ScriptedToolChat(
        steps=[
            ScriptStep(
                tool_calls=[
                    {"name": "cotizar", "args": args, "id": f"call-{i}"}
                    for i, args in enumerate(calls)
                ]
            ),
            ScriptStep(text="Estas son las cotizaciones con el precio publicado [1]."),
        ]
    )
    graph = build_tool_agent(
        ToolDeps(
            vector_store=SingleBatchStore([]),
            embedder=FakeEmbedder(),
            settings=settings,
            catalog=FIXTURES,
            today=HOY,
        ),
        chat,
        checkpointer=InMemorySaver(),
    )
    return build_service(engine, ToolAgentRunner(graph, settings), settings=settings), chat


def test_extremo_a_extremo_dos_cantidades_dan_dos_cotizaciones(engine: Any) -> None:
    # Arrange
    calls = [
        {**CALIENTE, "quantity": 1000, "city": "Bogotá"},
        {**CALIENTE, "quantity": 2000, "city": "Bogotá"},
    ]
    service, _ = quoting_service(engine, calls)

    # Act
    events = list(
        service.stream("dueno-e2e-dos", "cotiza 1000 y 2000 vasos en Bogotá", client_turn_id="t")
    )

    # Assert
    done = named(events, "done").data
    assert len(done["quote_ids"]) == 2
    assert done["quote_ids"][0] == folio_for(done["conversation_id"], "t", 0)
    assert done["quote_ids"][1] == folio_for(done["conversation_id"], "t", 1)
    units = [row["payload"]["primary"]["units_requested"] for row in _by_folio(engine, done)]
    assert units == [1000, 2000]
    html = named(events, "products").data["html"]
    assert html.count('class="coti coti-resumen"') == 2


def test_extremo_a_extremo_solo_cambia_el_domicilio_y_se_reemplaza(engine: Any) -> None:
    # Arrange
    args = {**CALIENTE, "quantity": 1000, "city": "Bogotá"}
    service, _ = quoting_service(engine, [args, {**args, "delivery": False}])

    # Act
    events = list(
        service.stream("dueno-e2e-domicilio", "y sin domicilio, en Bogotá", client_turn_id="d")
    )

    # Assert
    done = named(events, "done").data
    assert len(done["quote_ids"]) == 1
    (row,) = _by_folio(engine, done)
    assert row["payload"]["delivery"]["requested"] is False


def test_extremo_a_extremo_respeta_el_tope_de_cotizaciones(engine: Any) -> None:
    # Arrange
    calls = [{**CALIENTE, "quantity": q, "city": "Bogotá"} for q in (1000, 2000, 3000)]
    service, _ = quoting_service(engine, calls, max_quotes=2)

    # Act
    events = list(
        service.stream("dueno-e2e-tope", "cotiza tres cantidades en Bogotá", client_turn_id="m")
    )

    # Assert
    done = named(events, "done").data
    assert len(done["quote_ids"]) == 2
    assert len(stored_quotes(engine)) == 2


def _by_folio(engine: Any, done: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = {row["id"]: row for row in stored_quotes(engine)}
    return [rows[folio] for folio in done["quote_ids"]]


# --- Monitoreo por herramienta (T044, FR-014) ----------------------------------------------


def tool_results() -> tuple[dict[str, Any], ...]:
    return (
        {
            "tool": "buscar_corpus",
            "args": {"consulta": "fundación de Carvajal"},
            "status": "ok",
            "source_ids": [1],
            "elapsed_ms": 12,
        },
        {
            "tool": "cotizar",
            "args": {"city": "Bogotá", "quantity": 50},
            "status": "needs_input",
            "source_ids": [],
            "elapsed_ms": 3,
        },
    )


def test_el_monitoreo_abre_un_hijo_por_herramienta_sin_contenido(engine: Any) -> None:
    # Arrange
    tracer = FakeTracer()
    runner = FakeToolRunner(scripted_events(final(results=tool_results())))
    service = build_service(engine, runner, tracer=tracer)

    # Act
    list(service.stream("dueno-hijos", QUESTION))

    # Assert
    span = tracer.turns[0]
    tools = [
        (name, meta, child) for name, run_type, meta, child in span.children if run_type == "tool"
    ]
    assert [(name, meta) for name, meta, _ in tools] == [
        (
            "buscar_corpus",
            {
                "tool": "buscar_corpus",
                "step": 1,
                "status": "ok",
                "latency_ms": 12,
                "source_count": 1,
            },
        ),
        (
            "cotizar",
            {
                "tool": "cotizar",
                "step": 2,
                "status": "needs_input",
                "latency_ms": 3,
                "source_count": 0,
            },
        ),
    ]
    assert all(child.outputs == {} for _, _, child in tools)
    assert span.metadata["mode"] == "tools"
    assert span.metadata["path"] == "agent"
    assert span.metadata["tools"] == ["buscar_corpus", "cotizar"]
    assert span.metadata["input_tokens"] == 120
    assert span.metadata["output_tokens"] == 30
    assert span.metadata["total_tokens"] == 150


def test_con_contenido_completo_el_hijo_lleva_argumentos_y_resultado(engine: Any) -> None:
    # Arrange
    tracer = FakeTracer()
    runner = FakeToolRunner(scripted_events(final(results=tool_results())))
    settings = tools_settings(monitoring_include_content=True)
    service = build_service(engine, runner, tracer=tracer, settings=settings)

    # Act
    list(service.stream("dueno-contenido", QUESTION))

    # Assert
    tools = [child for _, run_type, _, child in tracer.turns[0].children if run_type == "tool"]
    assert tools[0].outputs == {
        "args": {"consulta": "fundación de Carvajal"},
        "status": "ok",
        "source_ids": [1],
    }
    assert tools[1].outputs["args"] == {"city": "Bogotá", "quantity": 50}


def test_el_atajo_no_abre_hijos_de_herramienta(engine: Any) -> None:
    # Arrange
    tracer = FakeTracer()
    closing = final(answer="¡Hola!", sources=(), results=(), gate="no_query", mode_path="shortcut")
    runner = FakeToolRunner([ToolAgentToken(text="¡Hola!"), closing])
    service = build_service(engine, runner, tracer=tracer)

    # Act
    list(service.stream("dueno-atajo-monitoreo", "hola"))

    # Assert
    span = tracer.turns[0]
    assert [c for c in span.children if c[1] == "tool"] == []
    assert span.metadata["path"] == "shortcut"


# --- Revisión de la entrega US3+US4 --------------------------------------------------------


def _read_until(generator: Generator[ChatEvent], name: str) -> list[ChatEvent]:
    """Consume el stream hasta el primer evento ``name`` (incluido)."""

    received: list[ChatEvent] = []
    for event in generator:
        received.append(event)
        if event.name == name:
            return received
    raise AssertionError(f"el stream terminó sin el evento {name}")


def test_desconectar_tras_el_respaldo_conserva_el_turno_completo_con_su_folio(
    engine: Any,
) -> None:
    # Arrange: el folio ya está en el texto del ``fallback`` cuando la persona se va.
    quote = quote_payload()
    agent = RoutesAgent(route="commercial", products={"quote": quote, "cities": ["Bogotá"]})
    runner = FakeToolRunner([], error=LLMError("caído"))
    service = build_service(engine, runner, agent=agent)
    generator = cast(
        Generator[ChatEvent],
        service.stream("dueno-respaldo-corte", QUESTION, client_turn_id="turno-rc"),
    )
    received = _read_until(generator, "fallback")

    # Act
    generator.close()
    history = list(service.history("dueno-respaldo-corte"))
    retry = list(service.stream("dueno-respaldo-corte", QUESTION, client_turn_id="turno-rc"))

    # Assert: ningún folio queda en el historial sin su fila ``quotes`` (FR-012a).
    text = named(received, "fallback").data["text"]
    assert history[1].content == text
    assert history[1].status == "complete"
    folio = sha256(f"{history[1].conversation_id}:turno-rc".encode()).hexdigest()[:12]
    assert f"Folio #{folio}" in text
    assert [row["id"] for row in stored_quotes(engine)] == [folio]
    assert "error" not in names(retry)
    assert named(retry, "done").data["quote_ids"] == [folio]


def test_desconectar_tras_las_tarjetas_conserva_el_turno_completo_con_sus_folios(
    engine: Any,
) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(two_quotes_final()))
    service = build_service(engine, runner)
    generator = cast(
        Generator[ChatEvent],
        service.stream("dueno-tarjetas-corte", QUESTION, client_turn_id="turno-tc"),
    )
    received = _read_until(generator, "products")

    # Act
    generator.close()
    history = list(service.history("dueno-tarjetas-corte"))

    # Assert
    conversation_id = history[1].conversation_id
    folios = [folio_for(conversation_id, "turno-tc", i) for i in range(2)]
    html = named(received, "products").data["html"]
    assert all(f"#{folio}" in html for folio in folios)
    assert history[1].status == "complete"
    assert history[1].content == ANSWER
    assert sorted(row["id"] for row in stored_quotes(engine)) == sorted(folios)


def _timeout_with(obtained: ToolAgentFinal) -> TurnTimeout:
    return TurnTimeout(45, obtained)


def test_el_plazo_vencido_entrega_lo_obtenido_y_declara_lo_que_falto(engine: Any) -> None:
    # Arrange: antes del plazo ya corrieron la búsqueda y la cotización (R9, FR-010).
    quote = quote_payload()
    obtained = final(
        answer="",
        sources=(SOURCE,),
        products={"quote": quote, "quotes": [quote], "cities": ["Bogotá"]},
        quotes=(quote,),
        results=(
            {"tool": "buscar_corpus", "status": "ok", "source_ids": [1], "elapsed_ms": 7},
            {"tool": "cotizar", "status": "ok", "source_ids": [], "elapsed_ms": 3},
        ),
    )
    obtained = replace(obtained, missing=("buscar_sedes {}",))
    agent = RoutesAgent(route="rag")
    runner = FakeToolRunner(scripted_events(), error=_timeout_with(obtained), error_at=2)
    tracer = FakeTracer()
    service = build_service(engine, runner, agent=agent, tracer=tracer)

    # Act
    events = list(service.stream("dueno-plazo-obtenido", QUESTION, client_turn_id="turno-po"))

    # Assert: no se repite el camino de reglas; el texto sale de lo obtenido.
    assert agent.prepares == 0
    done = named(events, "done").data
    folio = folio_for(done["conversation_id"], "turno-po", 0)
    text = named(events, "fallback").data["text"]
    assert text.startswith(fallback.FALLBACK_NOTICE)
    assert f"Folio #{folio}" in text
    assert "[1]" in text
    assert fallback.TIMEOUT_NOTE in text
    assert "Quedó 1 consulta sin hacer" in text
    assert named(events, "meta").data == {"sources": [asdict(SOURCE)]}
    assert done["quote_ids"] == [folio]
    assert 'class="coti' in named(events, "products").data["html"]
    assert [row["id"] for row in stored_quotes(engine)] == [folio]
    trace = named(events, "trace").data
    assert (trace["path"], trace["reason"], trace["fallback_route"]) == (
        "fallback",
        "timeout",
        "tools",
    )
    assert tracer.turns[0].metadata["fallback_route"] == "tools"


def test_el_plazo_vencido_sin_evidencia_usa_el_camino_de_reglas(engine: Any) -> None:
    # Arrange: lo obtenido no trae fuentes ni tarjetas; el respaldo es el de reglas.
    obtained = final(
        answer="",
        sources=(),
        results=({"tool": "buscar_corpus", "status": "empty", "source_ids": [], "elapsed_ms": 7},),
    )
    agent = RoutesAgent(sources=(SOURCE,))
    runner = FakeToolRunner(scripted_events(), error=_timeout_with(obtained), error_at=2)
    service = build_service(engine, runner, agent=agent)

    # Act
    events = list(service.stream("dueno-plazo-vacio", QUESTION))

    # Assert
    assert agent.prepares == 1
    assert named(events, "fallback").data["text"] == fallback.render({}, (SOURCE,))
    assert named(events, "trace").data["fallback_route"] == "rag"


def test_un_cierre_sin_texto_con_evidencia_redacta_con_lo_obtenido(engine: Any) -> None:
    # Arrange: el grafo ya buscó y cotizó, pero la redacción llegó vacía (FR-009, R9).
    quote = quote_payload()
    closing = final(
        answer="",
        sources=(SOURCE,),
        products={"quote": quote, "quotes": [quote], "cities": ["Bogotá"]},
        quotes=(quote,),
        results=(
            {"tool": "buscar_corpus", "status": "ok", "source_ids": [1], "elapsed_ms": 7},
            {"tool": "cotizar", "status": "ok", "source_ids": [], "elapsed_ms": 3},
        ),
    )
    agent = RoutesAgent(route="rag")
    runner = FakeToolRunner([closing])
    service = build_service(engine, runner, agent=agent)

    # Act
    events = list(service.stream("dueno-vacio-evidencia", QUESTION, client_turn_id="turno-ve"))

    # Assert: no se repite el camino de reglas ni se descarta la cotización calculada.
    assert agent.prepares == 0
    done = named(events, "done").data
    folio = folio_for(done["conversation_id"], "turno-ve", 0)
    text = named(events, "fallback").data["text"]
    assert text.startswith(fallback.FALLBACK_NOTICE)
    assert f"Folio #{folio}" in text
    assert "[1]" in text
    assert fallback.TIMEOUT_NOTE not in text
    assert done["quote_ids"] == [folio]
    assert 'class="coti' in named(events, "products").data["html"]
    assert [row["id"] for row in stored_quotes(engine)] == [folio]
    trace = named(events, "trace").data
    assert (trace["path"], trace["reason"], trace["fallback_route"]) == (
        "fallback",
        "empty_answer",
        "tools",
    )


def test_el_monitoreo_no_cuenta_sin_consulta_como_consulta(engine: Any) -> None:
    # Arrange: un turno que solo declara que no consulta (FR-004a, FR-014).
    tracer = FakeTracer()
    closing = final(
        answer="Hola, ¿en qué te ayudo?",
        sources=(),
        gate="no_query",
        results=({"tool": "sin_consulta", "status": "ok", "source_ids": [], "elapsed_ms": 0},),
    )
    runner = FakeToolRunner([ToolAgentToken(text="Hola, ¿en qué te ayudo?"), closing])
    service = build_service(engine, runner, tracer=tracer)

    # Act
    events = list(service.stream("dueno-sin-consulta", "Cuéntame algo"))

    # Assert
    span = tracer.turns[0]
    assert [c for c in span.children if c[1] == "tool"] == []
    assert span.metadata["tool_calls"] == 0
    assert span.metadata["tools"] == []
    assert span.metadata["no_query"] is True
    assert named(events, "trace").data["tool"] == ""


def test_el_monitoreo_no_registra_nombres_de_herramienta_inventados(engine: Any) -> None:
    # Arrange: el nombre lo escribe el modelo y puede llevar datos de la persona (FR-014).
    tracer = FakeTracer()
    invented = "cotizar_para_ana_perez_3001234567"
    closing = final(
        results=(
            {"tool": "sin_consulta", "status": "ok", "source_ids": [], "elapsed_ms": 0},
            {"tool": "buscar_corpus", "status": "ok", "source_ids": [1], "elapsed_ms": 5},
            {"tool": invented, "status": "error", "source_ids": [], "elapsed_ms": 0},
        )
    )
    runner = FakeToolRunner(scripted_events(closing))
    service = build_service(engine, runner, tracer=tracer)

    # Act
    events = list(service.stream("dueno-inventada", QUESTION))

    # Assert
    span = tracer.turns[0]
    children = [(name, meta["tool"]) for name, kind, meta, _ in span.children if kind == "tool"]
    assert children == [("buscar_corpus", "buscar_corpus"), ("desconocida", "desconocida")]
    assert span.metadata["tools"] == ["buscar_corpus", "desconocida"]
    assert span.metadata["tool_calls"] == 2
    assert span.metadata["no_query"] is True
    assert invented not in repr(span.metadata)
    assert invented not in repr(named(events, "trace").data)


def test_el_respaldo_por_plazo_tampoco_registra_nombres_inventados(engine: Any) -> None:
    # Arrange
    tracer = FakeTracer()
    obtained = final(
        answer="",
        sources=(),
        results=({"tool": "dime_tu_cedula_123", "status": "error", "source_ids": []},),
    )
    runner = FakeToolRunner([], error=_timeout_with(obtained))
    service = build_service(engine, runner, agent=RoutesAgent(), tracer=tracer)

    # Act
    list(service.stream("dueno-plazo-inventada", QUESTION))

    # Assert
    span = tracer.turns[0]
    assert span.metadata["tools"] == ["desconocida"]
    assert "dime_tu_cedula_123" not in repr(span.metadata)
    assert [c[0] for c in span.children if c[1] == "tool"] == ["desconocida"]


# --- Resumen en segundo plano (spec 045, T027) ------------------------------------------------


class RecordingWorker:
    """Doble del ``SummaryWorker``: anota cada ``submit`` y lo que ya se había emitido."""

    def __init__(self, emitted: list[str] | None = None) -> None:
        self.submits: list[tuple[str, str]] = []
        self.emitted = emitted if emitted is not None else []
        self.seen_at_submit: list[list[str]] = []

    def submit(self, owner_key: str, conversation_id: str) -> bool:
        self.submits.append((owner_key, conversation_id))
        self.seen_at_submit.append(list(self.emitted))
        return True


def _with_worker(engine: Any, runner: Any = None, **kwargs: Any) -> tuple[ChatService, Any]:
    worker = RecordingWorker()
    service = build_service(engine, runner, **kwargs)
    service._summary_worker = cast(Any, worker)
    return service, worker


def _consume(service: ChatService, worker: RecordingWorker, *args: Any, **kwargs: Any) -> list[Any]:
    events = []
    for event in service.stream(*args, **kwargs):
        worker.emitted.append(event.name)
        events.append(event)
    return events


def test_el_turno_tools_encola_el_resumen_tras_persistir_y_despues_del_texto(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service, worker = _with_worker(engine, runner)

    # Act
    events = _consume(service, worker, "dueno-resumen", QUESTION)

    # Assert: una sola vez, con el dueño del turno, después de todo el texto y antes de
    # ``trace``/``done`` (no se espera: el doble vuelve de inmediato).
    conversation_id = named(events, "done").data["thread_id"]
    assert worker.submits == [("dueno-resumen", conversation_id)]
    assert worker.seen_at_submit[0].count("token") == len(ANSWER_PARTS)
    assert "done" not in worker.seen_at_submit[0]


def test_el_respaldo_y_el_modo_routes_tambien_encolan(engine: Any) -> None:
    # Arrange
    failing = FakeToolRunner(scripted_events(), error=LLMError("caído"), error_at=2)
    fallback_service, fallback_worker = _with_worker(engine, failing, agent=RoutesAgent())
    routes_service, routes_worker = _with_worker(
        engine,
        agent=RoutesAgent(prompt="prompt"),
        settings=Settings(_env_file=None, agent_mode="routes"),  # type: ignore[call-arg]
    )
    routes_service._llm = cast(Any, Mock(stream=Mock(return_value=iter(["Hola."]))))

    # Act
    fallback_events = list(fallback_service.stream("dueno-respaldo", QUESTION))
    routes_events = list(routes_service.stream("dueno-routes", QUESTION))

    # Assert
    assert "fallback" in names(fallback_events)
    assert [owner for owner, _ in fallback_worker.submits] == ["dueno-respaldo"]
    assert "done" in names(routes_events)
    assert [owner for owner, _ in routes_worker.submits] == ["dueno-routes"]


def test_un_turno_incompleto_tambien_encola(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(), error=RuntimeError("corte"), error_at=4)
    service, worker = _with_worker(engine, runner)

    # Act
    list(service.stream("dueno-parcial", QUESTION))

    # Assert
    assert [owner for owner, _ in worker.submits] == ["dueno-parcial"]


def test_la_replica_idempotente_no_encola(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service, worker = _with_worker(engine, runner)
    list(service.stream("dueno-replica", QUESTION, client_turn_id="turno-x"))

    # Act
    replay = list(service.stream("dueno-replica", QUESTION, client_turn_id="turno-x"))

    # Assert
    assert named(replay, "trace").data["route"] == "replay"
    assert len(worker.submits) == 1


def test_un_worker_que_falla_no_rompe_el_turno(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)
    service._summary_worker = cast(Any, Mock(submit=Mock(side_effect=RuntimeError("cola"))))

    # Act
    events = list(service.stream("dueno-falla-worker", QUESTION))

    # Assert
    assert names(events)[-1] == "done"


def test_el_resumidor_real_nunca_corre_antes_del_primer_token(engine: Any) -> None:
    # Arrange: un resumidor que anota si ya salió algún token cuando se le llama.
    from langchain_core.messages import AIMessage

    from apps.api.chat.summary_worker import SummaryWorker

    emitted: list[str] = []
    calls: list[int] = []

    class Model:
        def invoke(self, *args: Any, **kwargs: Any) -> Any:
            calls.append(emitted.count("token"))
            return AIMessage(content="Resumen.")

    settings = tools_settings(agent_summary_mode="llm", agent_history_turns=1)
    worker = SummaryWorker(
        build_session_factory(engine), settings, store=InMemoryStore(), model=Model()
    )
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner, settings=settings)
    service._summary_worker = worker

    # Act: dos turnos (el segundo deja el primero fuera de la ventana de 1).
    for question in (QUESTION, "¿Y en Cali?"):
        runner.events = scripted_events()
        for event in service.stream("dueno-real", question):
            emitted.append(event.name)
    worker.close(wait=True)

    # Assert
    assert calls and all(count > 0 for count in calls)


def test_la_semilla_trae_la_ventana_mas_la_holgura_y_el_total(engine: Any) -> None:
    # Arrange: 15 turnos persistidos.
    from carvajal.adapters.storage.chat import SqlChatRepository

    session_factory = build_session_factory(engine)
    with session_factory() as session:
        repository = SqlChatRepository(session)
        conversation = repository.create("dueno-semilla")
        for index in range(15):
            repository.append_turn(conversation.id, question=f"p{index}", answer=f"r{index}")
        session.commit()

        # Act
        seed = service_module._seed_turns(repository, conversation.id, 8)

    # Assert: 8 + 2 turnos, los más recientes, y el total del hilo.
    assert [turn["question"] for turn in seed] == [f"p{i}" for i in range(5, 15)]
    assert seed.turn_count == 15


def test_el_runner_recibe_la_semilla_con_turn_count(engine: Any) -> None:
    # Arrange
    received: list[Any] = []

    class SeedRunner(FakeToolRunner):
        def stream(self, owner_key: str, thread_id: str, question: str, seed_turns: Any) -> Any:
            received.append(seed_turns)
            return super().stream(owner_key, thread_id, question, seed_turns)

    runner = SeedRunner(scripted_events())
    service = build_service(engine, runner)
    list(service.stream("dueno-tc", QUESTION))
    runner.events = scripted_events()

    # Act
    list(service.stream("dueno-tc", "¿Y en Cali?"))

    # Assert
    assert [getattr(seed, "turn_count", None) for seed in received] == [0, 1]


def test_la_entrada_del_grafo_lleva_turn_count() -> None:
    # Arrange
    from carvajal.agent.memory.summary import SeedTurns

    payloads: list[Any] = []

    class Graph:
        def stream(self, payload: Any, config: Any = None, **kwargs: Any) -> Any:
            payloads.append(payload)
            return iter([])

    runner = ToolAgentRunner(Graph(), tools_settings())
    seed = SeedTurns([{"question": "hola", "answer": "hola", "incomplete": False}], turn_count=12)

    # Act
    list(runner.stream("dueno", "hilo", "¿Y ahora?", seed))

    # Assert
    assert payloads[0]["turn_count"] == 12
    assert payloads[0]["seed_turns"] == list(seed)


def test_el_trace_lleva_memoria_por_categorias_y_el_resumen_sin_texto(engine: Any) -> None:
    # Arrange
    closing = replace(
        final(preferences=("nombre: Ana", "nota: una", "nota: dos")),
        summary={"covered_turns": 4, "method": "llm"},
    )
    service = build_service(engine, FakeToolRunner(scripted_events(closing)))

    # Act
    trace = named(list(service.stream("dueno-trace-045", QUESTION)), "trace").data

    # Assert
    assert trace["memory"] == 2
    assert trace["summary"] == {"covered_turns": 4, "method": "llm"}


# --- Compuerta de afirmaciones con Jev (spec 046, T033) ---------------------------------------

UNSUPPORTED_ANSWER = "Carvajal fue fundada en Cali en 1904 [1]; la de 16 oz es la más usada."


def jev_service(engine: Any, p: float = 0.2) -> tuple[ChatService, Any]:
    """Servicio con el grafo real, una búsqueda con evidencia y un juez falso de Noul."""

    from tests.fakes.judge import FakeJudge

    hit = ScoredChunk(
        chunk=Chunk(
            id="historia:1",
            document_id="historia",
            text="Carvajal fue fundada en Cali en 1904.",
            position=0,
            section="historia",
            layer="core",
        ),
        score=0.9,
        metadata={
            "url": "https://www.carvajal.com/historia/",
            "title": "Historia",
            "source_type": "oficial",
        },
    )
    settings = tools_settings()
    chat = ScriptedToolChat(
        steps=[
            ScriptStep(tool_calls=[{"name": "buscar_corpus", "args": {"consulta": "fundación"}}]),
            ScriptStep(text=UNSUPPORTED_ANSWER),
        ]
    )
    judge = FakeJudge(p=p)
    graph = build_tool_agent(
        ToolDeps(vector_store=SingleBatchStore([hit]), embedder=FakeEmbedder(), settings=settings),
        chat,
        checkpointer=InMemorySaver(),
    )
    runner = ToolAgentRunner(graph, settings, claims_judge=judge)
    return build_service(engine, runner, settings=settings), judge


def test_no_respaldada_el_servicio_emite_y_guarda_solo_el_sustituto(engine: Any) -> None:
    # Arrange
    service, judge = jev_service(engine)

    # Act
    events = list(service.stream("dueno-jev", "¿Cuándo se fundó Carvajal?"))
    history = list(service.history("dueno-jev"))

    # Assert
    tokens = "".join(e.data["text"] for e in events if e.name == "token")
    assert "más usada" not in tokens
    assert tokens == NO_CONTEXT_ANSWER
    assert history[1].content == NO_CONTEXT_ANSWER
    assert names(events)[-1] == "done"
    assert len(judge.claims) == 1


def test_no_respaldada_ask_devuelve_solo_el_sustituto(engine: Any) -> None:
    service, _ = jev_service(engine)

    answer = service.ask("dueno-jev-ask", "¿Cuándo se fundó Carvajal?")

    assert answer.text == NO_CONTEXT_ANSWER
    assert "más usada" not in answer.text
