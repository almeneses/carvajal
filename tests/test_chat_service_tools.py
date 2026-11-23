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
  ``routes`` cuando el runtime no construyó el grafo ``tools``.
"""

from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import StaticPool
from tests.fakes.tool_chat import ScriptedToolChat, ScriptStep

from apps.api.chat import service as service_module
from apps.api.chat.agent import (
    AgentTurn,
    ToolAgentEvent,
    ToolAgentFinal,
    ToolAgentRunner,
    ToolAgentSources,
    ToolAgentStatus,
    ToolAgentToken,
)
from apps.api.chat.service import ChatFailure, ChatService
from apps.api.chat.streaming import ChatEvent
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.graph import build_tool_agent
from carvajal.agent.toolagent.tools import ToolDeps
from carvajal.application.ask import NO_CONTEXT_ANSWER, Source
from carvajal.application.ports import ScoredChunk
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
    """Runner del modo ``routes`` que no debería usarse cuando el modo es ``tools``."""

    def __init__(self) -> None:
        self.prepares = 0
        self.records: list[dict[str, Any]] = []

    def prepare(
        self, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Turn]
    ) -> AgentTurn:
        self.prepares += 1
        return AgentTurn(
            route="rag",
            intent="documental",
            prompt=None,
            system="sistema",
            sources=(),
            turns=(),
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


def quote_payload() -> dict[str, Any]:
    return {
        "kind": "cotizacion",
        "version": 2,
        "city": "Bogotá",
        "reply": "Son 3.751 COP.",
        "primary": {
            "sku": "7704918100199",
            "name": "Vaso rígido",
            "currency": "COP",
            "units_requested": 50,
            "presentation": {
                "basis": "paquete",
                "units": 50,
                "count": 1,
                "units_bought": 50,
                "units_surplus": 0,
                "price_per_presentation": "3751",
                "subtotal": "3751",
            },
        },
        "commercial": {
            "subtotal": "3751",
            "total": "3751",
            "valid_until": None,
            "shipping": {"requested": False, "status": "not_requested", "cost": None},
            "tax": {"status": "incluido", "rate": None},
        },
        "total": "3751",
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


def test_una_respuesta_vacia_se_sustituye_por_la_abstencion(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner([final(answer="", sources=(SOURCE,))])
    service = build_service(engine, runner)

    # Act
    events = list(service.stream("dueno-vacio", QUESTION))
    history = list(service.history("dueno-vacio"))

    # Assert
    assert named(events, "token").data == {"text": NO_CONTEXT_ANSWER}
    assert named(events, "meta").data == {"sources": []}
    assert history[1].content == NO_CONTEXT_ANSWER
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


def test_un_error_antes_del_primer_token_no_guarda_el_turno(engine: Any) -> None:
    # Arrange
    tracer = FakeTracer()
    runner = FakeToolRunner(scripted_events(), error=RuntimeError("DeepSeek caído"), error_at=1)
    service = build_service(engine, runner, tracer=tracer)

    # Act
    events = list(service.stream("dueno-falla", QUESTION))

    # Assert
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
