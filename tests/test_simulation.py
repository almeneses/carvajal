"""Pruebas sin red del simulador de conversaciones (spec 044, observación simulada).

El servicio de chat es un doble que reproduce eventos SSE por turno, publica metadatos en
el tracer como el ``ChatService`` real y guarda el historial en memoria; el cliente
simulado es un doble con respuestas fijas. Cubren la carga de escenarios, el prompt y la
marca de cierre del cliente, la inferencia del uso y la validación de materiales con el
catálogo versionado, el registro por turno (eventos, latencias, tokens, folios y
persistencia), las métricas automáticas, la corrida concurrente, el resumen y la CLI.
"""

import json
from collections.abc import Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from carvajal import cli
from carvajal.agent.commercial import CommercialRequest, check_material
from carvajal.agent.toolagent import simulation
from carvajal.agent.toolagent.simulation import (
    CLIENT_END,
    ConversationSimulator,
    RecordingTracer,
    Scenario,
    SimulatedClient,
    Transcript,
    TurnRecord,
    client_messages,
    infer_request,
    load_scenarios,
    parse_client_reply,
    render_summary,
    run_simulation,
    select_scenarios,
    summarize,
    unsuitable_materials,
)
from carvajal.catalog.loader import load_catalog
from carvajal.catalog.models import Reference
from carvajal.infrastructure.monitoring import NoopTurn, Turn

# --- Dobles --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    name: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class Conversation:
    id: str


@dataclass(frozen=True)
class Message:
    id: str
    role: str
    content: str
    sources: list[dict[str, Any]] | None
    status: str = "complete"


@dataclass
class ScriptedTurn:
    """Un turno del servicio: eventos (con el reloj que avanza antes de cada uno)."""

    events: list[tuple[float, Event]]
    metadata: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    products: dict[str, Any] | None = None
    raises: Exception | None = None
    persist: bool = True


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeChatService:
    """Doble del ``ChatService``: guion por turno, tracer como el real e historial."""

    def __init__(self, tracer: RecordingTracer, turns: Sequence[ScriptedTurn], clock: Clock):
        self.tracer = tracer
        self.turns = list(turns)
        self.clock = clock
        self.calls: list[dict[str, Any]] = []
        self.created: list[str] = []
        self.messages: list[Message] = []

    def new_conversation(self, owner_key: str) -> Conversation:
        self.created.append(owner_key)
        return Conversation(id=f"thread-{len(self.created)}")

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[Event]:
        self.calls.append(
            {
                "owner_key": owner_key,
                "question": question,
                "client_turn_id": client_turn_id,
                "thread_id": thread_id,
            }
        )
        script = self.turns[len(self.calls) - 1]
        with self.tracer.turn(
            request_id="r", thread_hash="h", router="rules", question=question
        ) as span:
            for tool in script.tools:
                with span.child(tool["tool"], run_type="tool", metadata=tool):
                    pass
            span.add_metadata(**script.metadata)
        answer, message_id = "", f"a{len(self.calls)}"
        for advance, event in script.events:
            self.clock.now += advance
            if script.raises is not None and event.name == "token":
                raise script.raises
            if event.name in ("token", "fallback"):
                answer += str(event.data.get("text", ""))
            if event.name == "done":
                message_id = str(event.data.get("message_id") or message_id)
            yield event
        if script.persist:
            sources: list[dict[str, Any]] = []
            if script.products:
                sources.append({"result_type": "commercial", "result": script.products})
            self.messages.append(Message(f"m{len(self.calls)}", "user", question, None))
            self.messages.append(Message(message_id, "assistant", answer, sources))

    def history(self, owner_key: str, thread_id: str | None = None) -> Sequence[Message]:
        return list(self.messages)


class FakeClient:
    def __init__(self, replies: Sequence[str | None], *, error: Exception | None = None):
        self.replies = list(replies)
        self.error = error
        self.seen: list[int] = []

    def next_message(
        self, scenario: Scenario, exchanges: Sequence[TurnRecord]
    ) -> tuple[str | None, dict[str, int] | None]:
        self.seen.append(len(exchanges))
        if self.error is not None:
            raise self.error
        return self.replies.pop(0), {"input_tokens": 100, "output_tokens": 10}


class FakeChat:
    """Chat de LangChain para el cliente simulado: devuelve un texto con uso de tokens."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[Any] = []

    def invoke(self, messages: Any) -> AIMessage:
        self.prompts.append(messages)
        return AIMessage(
            content=self.text,
            usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55},
        )


class InnerTracer:
    """Tracer configurado (el de monitoreo) que registra lo que recibe."""

    def __init__(self) -> None:
        self.metadata: list[dict[str, Any]] = []

    @contextmanager
    def turn(
        self, *, request_id: str, thread_hash: str, router: str, question: str = ""
    ) -> Generator[Turn]:
        outer = self

        class _Turn(NoopTurn):
            __slots__ = ()

            def add_metadata(self, **values: Any) -> None:
                outer.metadata.append(values)

        yield _Turn()


SOURCE = {
    "chunk_id": "historia-1",
    "slug": "historia",
    "section": "historia",
    "excerpt": "Carvajal inició en 1904.",
    "url": "https://www.carvajal.com/historia/",
    "title": "Historia",
}


def _reference(material: str, use: str) -> Reference:
    """Referencia real del catálogo versionado con ese material y ese uso declarado."""

    return next(r for r in load_catalog().references if r.material == material and use in r.uses)


def _suitable_for(request: CommercialRequest) -> Reference:
    catalog = load_catalog()
    return next(r for r in catalog.references if not check_material([r], request)[1])


def _scenario(**overrides: Any) -> Scenario:
    values: dict[str, Any] = {
        "id": "s-01",
        "opening": "Necesito vasos para café, unos 50 vasos.",
        "persona": "Dueña de una cafetería en Bogotá",
        "goal": "Saber qué vaso sirve para café caliente y cuánto cuesta",
        "max_turns": 3,
        "follow_ups": ("¿Cuánto cuestan?",),
    }
    values.update(overrides)
    return Scenario(**values)


def _ok_turn(text: str, *, products: dict[str, Any] | None = None, **meta: Any) -> ScriptedTurn:
    return ScriptedTurn(
        events=[
            (0.0, Event("open", {"stream": "chat"})),
            (0.5, Event("status", {"text": "Buscando en el catálogo…", "step": 1})),
            (0.5, Event("meta", {"sources": [SOURCE]})),
            (1.0, Event("token", {"text": text[: len(text) // 2]})),
            (0.25, Event("token", {"text": text[len(text) // 2 :]})),
            (0.0, Event("trace", {"mode": "tools", "path": "agent", "route": "agent", "gate": ""})),
            (0.0, Event("products", {"html": "<div>tarjeta</div>"})),
            (0.25, Event("done", {"message_id": "a-x", "quote_ids": ["COT-1"]})),
        ],
        metadata={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200, **meta},
        tools=[{"tool": "buscar_producto", "status": "ok", "latency_ms": 12}],
        products=products,
    )


def _simulator(
    turns: Sequence[ScriptedTurn], client: Any, clock: Clock | None = None, **kwargs: Any
) -> tuple[ConversationSimulator, list[FakeChatService]]:
    clock = clock or Clock()
    services: list[FakeChatService] = []

    def factory(tracer: RecordingTracer) -> FakeChatService:
        service = FakeChatService(tracer, turns, clock)
        services.append(service)
        return service

    return ConversationSimulator(factory, client, clock=clock, **kwargs), services


# --- Escenarios ----------------------------------------------------------------------------


def test_carga_escenarios_con_valores_por_defecto(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "escenarios.json"
    raw = {
        "scenarios": [
            {"id": "a", "opening": "Hola", "tags": ["comercial"], "facts_to_check": ["1904"]},
            {"id": "b", "opening": "¿Cuándo se fundó?", "max_turns": 2, "follow_ups": ["x"]},
        ]
    }
    path.write_text(json.dumps(raw), encoding="utf-8")

    # Act
    scenarios = load_scenarios(path)

    # Assert
    assert [s.id for s in scenarios] == ["a", "b"]
    assert scenarios[0].max_turns == simulation.DEFAULT_MAX_TURNS
    assert scenarios[0].tags == ("comercial",)
    assert scenarios[0].facts_to_check == ("1904",)
    assert scenarios[1].follow_ups == ("x",)
    assert scenarios[0].owner_key == "sim-a"


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([{"id": "a"}], "opening"),
        ([{"opening": "hola"}], "sin «id»"),
        ([{"id": "a", "opening": "x"}, {"id": "a", "opening": "y"}], "repetidos"),
        ([{"id": "a", "opening": "x", "max_turns": 0}], "max_turns"),
        ([{"id": "a", "opening": "x", "follow_ups": "texto"}], "follow_ups"),
        ([], "no trae escenarios"),
    ],
)
def test_rechaza_escenarios_invalidos(tmp_path: Path, items: list[Any], message: str) -> None:
    # Arrange
    path = tmp_path / "escenarios.json"
    path.write_text(json.dumps(items), encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match=message):
        load_scenarios(path)


def test_seleccion_por_ids_en_orden_del_archivo_y_limite() -> None:
    # Arrange
    scenarios = [_scenario(id=name) for name in ("a", "b", "c")]

    # Act
    chosen = select_scenarios(scenarios, ids=["c", "a"])
    limited = select_scenarios(scenarios, limit=2)

    # Assert
    assert [s.id for s in chosen] == ["a", "c"]
    assert [s.id for s in limited] == ["a", "b"]
    with pytest.raises(ValueError, match="desconocidos"):
        select_scenarios(scenarios, ids=["z"])


# --- Cliente simulado ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (CLIENT_END, None),
        ("Perfecto, gracias. FIN", None),
        ("  ", None),
        ("Cliente: ¿y en Medellín?", "¿y en Medellín?"),
        ('"¿Tienen tapas?"', "¿Tienen tapas?"),
        ("«Quiero 200 vasos»", "Quiero 200 vasos"),
        ("Lo necesito para fin de mes", "Lo necesito para fin de mes"),
    ],
)
def test_parse_client_reply_reconoce_el_cierre_y_limpia(raw: str, expected: str | None) -> None:
    # Arrange / Act
    reply = parse_client_reply(raw)

    # Assert
    assert reply == expected


def test_prompt_del_cliente_lleva_persona_objetivo_y_conversacion() -> None:
    # Arrange
    scenario = _scenario()
    record = TurnRecord(
        index=0,
        message=scenario.opening,
        answer="Te sugiero el vaso de papel 8 oz.",
        products={"options": [{"name": "Vaso 8 oz", "material": "PAPEL", "sku": "1"}]},
        quote_ids=["COT-1"],
    )

    # Act
    system, human = client_messages(scenario, [record])

    # Assert
    assert scenario.persona in str(system.content)
    assert scenario.goal in str(system.content)
    assert "¿Cuánto cuestan?" in str(system.content)
    assert CLIENT_END in str(system.content)
    assert f"Tú: {scenario.opening}" in str(human.content)
    assert "Asistente: Te sugiero el vaso de papel 8 oz." in str(human.content)
    assert "Vaso 8 oz (PAPEL)" in str(human.content)
    assert "COT-1" in str(human.content)


def test_cliente_simulado_devuelve_mensaje_y_uso_de_tokens() -> None:
    # Arrange
    chat = FakeChat("¿Y cuánto cuesta la caja?")
    client = SimulatedClient(chat)

    # Act
    message, usage = client.next_message(_scenario(), [])
    closed, _ = SimulatedClient(FakeChat("FIN")).next_message(_scenario(), [])

    # Assert
    assert message == "¿Y cuánto cuesta la caja?"
    assert usage == {"input_tokens": 50, "output_tokens": 5, "total_tokens": 55}
    assert closed is None
    assert len(chat.prompts) == 1


# --- Uso pedido y materiales ---------------------------------------------------------------


def test_infer_request_lee_el_uso_del_ultimo_mensaje_y_las_etiquetas() -> None:
    # Arrange
    messages = ["Quiero vasos para jugos", "Mejor para café caliente"]

    # Act
    from_text = infer_request(messages)
    from_tags = infer_request(messages, ["uso:sopas", "temperatura:caliente"])
    microwave = infer_request(["¿Sirve en el microondas?"])
    nothing = infer_request(["¿Cuándo se fundó Carvajal?"])

    # Assert
    assert from_text is not None
    assert (from_text.use, from_text.temperature) == ("bebida_caliente", "caliente")
    assert from_tags is not None
    assert (from_tags.use, from_tags.temperature) == ("sopas", "caliente")
    assert microwave is not None
    assert microwave.microwave is True
    assert nothing is None


def test_unsuitable_materials_marca_lo_que_check_material_descarta() -> None:
    # Arrange
    request = infer_request(["vasos para café"])
    assert request is not None
    cold_pet = _reference("PET", "bebida_fria")
    suitable = _suitable_for(request)
    products = {
        "options": [
            {"sku": cold_pet.sku, "material": cold_pet.material, "country": cold_pet.country},
            {"sku": suitable.sku, "material": suitable.material, "country": suitable.country},
            {"sku": "no-existe", "material": "PET"},
        ]
    }

    # Act
    flagged = unsuitable_materials(products, request, load_catalog())
    unmeasured = unsuitable_materials(products, None, load_catalog())

    # Assert
    assert [item["sku"] for item in flagged] == [cold_pet.sku]
    assert flagged[0]["material"] == "PET"
    assert flagged[0]["reason"]
    assert unmeasured == []


# --- Simulador -----------------------------------------------------------------------------


def test_conversacion_completa_registra_eventos_metricas_y_cierre_por_fin() -> None:
    # Arrange
    cold_pet = _reference("PET", "bebida_fria")
    products = {
        "options": [
            {"sku": cold_pet.sku, "name": "Vaso PET", "material": "PET", "price": "3751 COP"}
        ]
    }
    first = _ok_turn("Carvajal existe desde 1904 y el vaso cuesta 3751 COP.", products=products)
    second = _ok_turn("Para 50 vasos el total es 999 COP.")
    client = FakeClient(["¿Y para 50 vasos?", None])
    simulator, services = _simulator([first, second], client)

    # Act
    transcript = simulator.run(_scenario())

    # Assert: hilo propio con el dueño de prueba y cierre por la marca del cliente
    service = services[0]
    assert service.created == ["sim-s-01"]
    assert transcript.thread_id == "thread-1"
    assert transcript.finished_by == "fin"
    assert [call["question"] for call in service.calls] == [
        _scenario().opening,
        "¿Y para 50 vasos?",
    ]
    assert {call["thread_id"] for call in service.calls} == {"thread-1"}
    assert all(str(call["client_turn_id"]).startswith("sim-") for call in service.calls)
    assert client.seen == [1, 2]
    # Assert: registro del primer turno
    turn = transcript.turns[0]
    assert turn.answer == "Carvajal existe desde 1904 y el vaso cuesta 3751 COP."
    assert (turn.mode, turn.path) == ("tools", "agent")
    assert [e["name"] for e in turn.events] == [
        "open",
        "status",
        "meta",
        "token",
        "trace",
        "products",
        "done",
    ]
    assert turn.events[3]["count"] == 2
    assert turn.sources == [SOURCE]
    assert turn.cards_html == ["<div>tarjeta</div>"]
    assert turn.quote_ids == ["COT-1"]
    assert (turn.first_status_s, turn.first_text_s, turn.total_s) == (0.5, 2.0, 2.5)
    assert turn.usage == {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200}
    assert turn.tools == [{"tool": "buscar_producto", "status": "ok", "latency_ms": 12}]
    assert turn.persisted is True
    assert turn.products == products
    # Assert: métricas (1904 viene de la fuente, 3751 de la tarjeta; el PET no sirve para café)
    assert turn.unsupported_figures == []
    assert [item["sku"] for item in turn.unsuitable_materials] == [cold_pet.sku]
    assert turn.inferred_use == {
        "use": "bebida_caliente",
        "temperature": "caliente",
        "microwave": None,
    }
    # Assert: el segundo turno no tiene respaldo para 999; el 50 lo dijo el cliente
    later = transcript.turns[1]
    assert later.unsupported_figures == ["999"]
    assert later.client_usage == {"input_tokens": 100, "output_tokens": 10}
    assert transcript.judge is None


def test_respaldo_y_motivo_salen_del_trace_y_de_la_traza() -> None:
    # Arrange
    fallback = ScriptedTurn(
        events=[
            (0.2, Event("meta", {"sources": [SOURCE]})),
            (0.3, Event("fallback", {"text": "No pude consultar ahora; esto dice 1904."})),
            (0.0, Event("trace", {"mode": "tools", "path": "fallback", "reason": "timeout"})),
            (0.0, Event("done", {"message_id": "a1", "quote_ids": []})),
        ],
        metadata={"path": "fallback", "fallback_reason": "provider"},
    )
    simulator, _ = _simulator([fallback], FakeClient([]))

    # Act
    transcript = simulator.run(_scenario(max_turns=1))

    # Assert
    turn = transcript.turns[0]
    assert turn.fallback is True
    assert turn.fallback_reason == "provider"
    assert turn.answer == "No pude consultar ahora; esto dice 1904."
    assert turn.first_text_s == 0.5
    assert turn.first_status_s is None
    assert turn.usage is None
    assert transcript.finished_by == "max_turns"


def test_un_error_del_stream_corta_la_conversacion() -> None:
    # Arrange
    failing = ScriptedTurn(
        events=[
            (0.1, Event("status", {"text": "Buscando…", "step": 1})),
            (0.1, Event("error", {"message": "El servicio no está disponible.", "incomplete": 0})),
        ],
        persist=False,
    )
    client = FakeClient(["no debería llegar"])
    simulator, _ = _simulator([failing], client)

    # Act
    transcript = simulator.run(_scenario())

    # Assert
    assert transcript.finished_by == "error"
    assert transcript.turns[0].error == "El servicio no está disponible."
    assert transcript.turns[0].persisted is None
    assert client.seen == []


def test_una_excepcion_del_servicio_queda_en_el_turno() -> None:
    # Arrange
    broken = _ok_turn("texto")
    broken.raises = RuntimeError("se cayó Postgres")
    simulator, _ = _simulator([broken], FakeClient([]))

    # Act
    transcript = simulator.run(_scenario())

    # Assert
    assert transcript.finished_by == "error"
    assert transcript.turns[0].error == "RuntimeError: se cayó Postgres"


def test_max_turns_no_pide_otro_mensaje_al_cliente() -> None:
    # Arrange
    client = FakeClient(["¿Y en Cali?"])
    simulator, _ = _simulator([_ok_turn("Uno."), _ok_turn("Dos.")], client)

    # Act
    transcript = simulator.run(_scenario(max_turns=2))

    # Assert
    assert transcript.finished_by == "max_turns"
    assert len(transcript.turns) == 2
    assert client.seen == [1]


def test_un_fallo_del_cliente_simulado_cierra_con_client_error() -> None:
    # Arrange
    simulator, _ = _simulator([_ok_turn("Uno.")], FakeClient([], error=TimeoutError("lento")))

    # Act
    transcript = simulator.run(_scenario())

    # Assert
    assert transcript.finished_by == "client_error"
    assert transcript.error == "TimeoutError: lento"
    assert len(transcript.turns) == 1


def test_un_fallo_al_abrir_el_hilo_no_tumba_la_corrida() -> None:
    # Arrange
    def factory(tracer: RecordingTracer) -> Any:
        raise ConnectionError("sin base")

    simulator = ConversationSimulator(factory, FakeClient([]))

    # Act
    transcript = simulator.run(_scenario())

    # Assert
    assert transcript.finished_by == "error"
    assert "sin base" in transcript.error


def test_recording_tracer_delega_en_el_tracer_configurado() -> None:
    # Arrange
    inner = InnerTracer()
    tracer = RecordingTracer(inner)

    # Act
    with tracer.turn(request_id="r", thread_hash="h", router="rules") as span:
        with span.child("cotizar", run_type="tool", metadata={"status": "ok"}):
            pass
        with span.child("llm", run_type="llm"):
            pass
        span.add_metadata(path="agent", input_tokens=7)
    metadata, tools = tracer.take()

    # Assert
    assert metadata == {"path": "agent", "input_tokens": 7}
    assert tools == [{"status": "ok", "tool": "cotizar"}]
    assert inner.metadata == [{"path": "agent", "input_tokens": 7}]
    assert tracer.take() == ({}, [])


# --- Corrida y resumen ---------------------------------------------------------------------


def test_run_simulation_concurrente_devuelve_en_orden_del_archivo() -> None:
    # Arrange
    scenarios = [_scenario(id=f"s-{n}", max_turns=1) for n in range(5)]
    seen: list[str] = []

    def factory(tracer: RecordingTracer) -> FakeChatService:
        return FakeChatService(tracer, [_ok_turn("Hola.")], Clock())

    simulator = ConversationSimulator(factory, FakeClient([]))

    # Act
    transcripts = run_simulation(
        simulator, scenarios, concurrency=3, on_transcript=lambda t: seen.append(t.scenario.id)
    )

    # Assert
    assert [t.scenario.id for t in transcripts] == [s.id for s in scenarios]
    assert sorted(seen) == sorted(s.id for s in scenarios)
    with pytest.raises(ValueError, match="concurrency"):
        run_simulation(simulator, scenarios, concurrency=0)


def _transcript(scenario_id: str, turns: Sequence[TurnRecord], finished: str) -> Transcript:
    return Transcript(
        scenario=_scenario(id=scenario_id),
        owner_key=f"sim-{scenario_id}",
        turns=list(turns),
        finished_by=finished,
    )


def test_summarize_y_render_summary_agregan_latencias_respaldo_y_costo() -> None:
    # Arrange
    ok = TurnRecord(
        index=0,
        message="hola",
        path="agent",
        first_status_s=0.4,
        first_text_s=1.0,
        total_s=2.0,
        usage={"input_tokens": 1_000_000, "output_tokens": 100_000},
        client_usage=None,
        quote_ids=["COT-1"],
    )
    loose = TurnRecord(
        index=1,
        message="¿precio?",
        path="agent",
        first_text_s=3.0,
        total_s=4.0,
        usage={"input_tokens": 1_000_000, "output_tokens": 100_000},
        client_usage={"input_tokens": 2_000_000, "output_tokens": 0},
        unsupported_figures=["999 COP"],
        unsuitable_materials=[{"sku": "1", "material": "PET", "reason": "no apto"}],
        inferred_use={"use": "bebida_caliente"},
        products={"options": []},
    )
    fallback = TurnRecord(
        index=0, message="x", path="fallback", fallback_reason="timeout", total_s=6.0
    )
    broken = TurnRecord(index=0, message="y", error="El servicio no está disponible.")
    transcripts = [
        _transcript("a", [ok, loose], "fin"),
        _transcript("b", [fallback], "max_turns"),
        _transcript("c", [broken], "error"),
    ]

    # Act
    data = summarize(transcripts, price_in=0.5, price_out=2.0)
    text = render_summary(transcripts, scenarios_path="esc.json", price_in=0.5, price_out=2.0)

    # Assert
    assert data["turns"] == 4
    assert data["fallback_turns"] == 1
    assert data["fallback_pct"] == 25.0
    assert data["fallback_reasons"] == {"timeout": 1}
    assert data["unsupported_turns"] == 1
    assert data["unsuitable_turns"] == 1
    assert data["turn_errors"] == 1
    assert data["latency"]["total_s"] == {"p50": 4.0, "p95": 6.0}
    assert data["latency"]["first_text_s"] == {"p50": 1.0, "p95": 3.0}
    assert data["cost_usd"]["assistant"] == pytest.approx(1.4)
    assert data["cost_usd"]["client"] == pytest.approx(1.0)
    assert data["cost_usd"]["total"] == pytest.approx(2.4)
    assert data["finished_by"] == {"fin": 1, "max_turns": 1, "error": 1}
    assert "| Respaldo (path=fallback) | 1 (25.0 %) — timeout: 1 |" in text
    assert "`a` turno 2: sin fuente ['999 COP']" in text
    assert "`c` turno 1: error" in text
    assert "| a | 2 | fin | 0 | 1 | 1 | 0 | pendiente |" in text
    assert "2.4000 USD" in text


def test_sin_precios_el_costo_queda_sin_precio() -> None:
    # Arrange
    transcripts = [_transcript("a", [TurnRecord(index=0, message="hola", total_s=1.0)], "fin")]

    # Act
    text = render_summary(transcripts)

    # Assert
    assert "sin precio" in text
    assert summarize(transcripts)["cost_usd"]["total"] is None


def test_la_transcripcion_serializa_el_campo_del_juez() -> None:
    # Arrange
    transcript = _transcript("a", [TurnRecord(index=0, message="hola", path="fallback")], "fin")

    # Act
    payload = json.loads(json.dumps(transcript.as_dict(), default=str))

    # Assert
    assert payload["judge"] is None
    assert payload["owner_key"] == "sim-a"
    assert payload["turns"][0]["fallback"] is True
    assert payload["scenario"]["opening"] == _scenario().opening


# --- CLI -----------------------------------------------------------------------------------


def _scenarios_file(tmp_path: Path) -> Path:
    path = tmp_path / "escenarios.json"
    items = [
        {"id": "a", "opening": "Hola", "max_turns": 1},
        {"id": "b", "opening": "¿Cuándo se fundó Carvajal?", "max_turns": 1},
    ]
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


def _fake_real(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(tracer: RecordingTracer) -> FakeChatService:
        return FakeChatService(tracer, [_ok_turn("Desde 1904.")], Clock())

    monkeypatch.setattr(
        simulation,
        "build_real_simulator",
        lambda settings: ConversationSimulator(factory, FakeClient([])),
    )
    monkeypatch.setattr(simulation, "runtime_mode", lambda: "tools")


def test_cli_simulate_chat_escribe_transcripciones_y_resumen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    _fake_real(monkeypatch)
    out = tmp_path / "corrida"

    # Act
    code = cli.main(
        [
            "simulate-chat",
            "--scenarios",
            str(_scenarios_file(tmp_path)),
            "--output-dir",
            str(out),
            "--ids",
            "b",
            "--concurrency",
            "2",
            "--price-input",
            "0.27",
            "--price-output",
            "1.1",
        ]
    )

    # Assert
    assert code == 0
    lines = (out / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["b"]
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "# Simulación de conversaciones" in summary
    assert "tools (AGENT_MODE=" in summary
    assert "Simulación de conversaciones" in capsys.readouterr().out


def test_cli_simulate_chat_no_sobrescribe_una_corrida(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    _fake_real(monkeypatch)
    out = tmp_path / "corrida"
    out.mkdir()
    (out / "transcripts.jsonl").write_text("{}\n", encoding="utf-8")

    # Act
    code = cli.main(
        ["simulate-chat", "--scenarios", str(_scenarios_file(tmp_path)), "--output-dir", str(out)]
    )

    # Assert
    assert code == 1
    assert (out / "transcripts.jsonl").read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize(
    "extra",
    [["--ids", ""], ["--concurrency", "0"], ["--ids", "zzz"]],
)
def test_cli_simulate_chat_rechaza_argumentos_invalidos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]
) -> None:
    # Arrange
    _fake_real(monkeypatch)

    # Act
    code = cli.main(["simulate-chat", "--scenarios", str(_scenarios_file(tmp_path)), *extra])

    # Assert
    assert code == 1
