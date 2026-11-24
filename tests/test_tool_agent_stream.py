"""Pruebas del adaptador de streaming del modo ``tools`` (spec 044, T016 y T031).

``ToolAgentRunner.stream`` consume ``graph.stream`` y produce eventos tipados. Cubren
research R2 y R3 con ``ScriptedToolChat`` y dobles sin red:

- retención de 48 caracteres en el paso final con evidencia;
- descarte del texto previo a una llamada a herramienta;
- eventos ``status`` en orden, antes del texto y sin resultados;
- los tres caminos de la compuerta: evidencia, abstención sin transmitir nada del modelo y
  ``sin_consulta`` retenido completo, filtrado y emitido de una vez;
- el atajo (T039): sin línea de estado ni herramientas, con el filtro de la ruta directa;
- el plazo del turno (T040): vencido antes del primer token, ``TurnTimeout``.
"""

import re
import threading
import time
from collections.abc import Generator, Iterator, Sequence
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from tests.fakes.tool_chat import ScriptedToolChat, ScriptStep

from apps.api.chat.agent import (
    AgentRunner,
    GraphAgentRunner,
    ToolAgentFinal,
    ToolAgentRunner,
    ToolAgentSources,
    ToolAgentStatus,
    ToolAgentToken,
    ToolStreamRunner,
    TurnTimeout,
)
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.evidence import FAREWELL_ANSWER, GREETING_ANSWER, NO_DATA_ANSWER
from carvajal.agent.toolagent.graph import build_tool_agent, recursion_limit
from carvajal.agent.toolagent.tools import ToolDeps
from carvajal.application.ask import Source
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings
from carvajal.prompts import NO_CONTEXT_ANSWER

QUESTION = "¿Cuándo se fundó Carvajal?"
ANSWER = (
    "Carvajal fue fundada en Cali en 1904 [1]. Empezó como una imprenta familiar y "
    "hoy tiene varias empresas."
)


# --- Dobles --------------------------------------------------------------------------------


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 8


class QueueStore:
    """Índice de prueba: cada búsqueda devuelve el siguiente lote de la cola."""

    def __init__(self, *batches: Sequence[ScoredChunk]) -> None:
        self.batches = [list(batch) for batch in batches]
        self.calls = 0

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise AssertionError("El adaptador no indexa.")

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        index = min(self.calls, len(self.batches) - 1) if self.batches else 0
        self.calls += 1
        return self.batches[index][:limit] if self.batches else []

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("El adaptador no borra.")


def _hit(chunk_id: str, text: str) -> ScoredChunk:
    document_id = chunk_id.split(":")[0]
    return ScoredChunk(
        chunk=Chunk(
            id=chunk_id,
            document_id=document_id,
            text=text,
            position=0,
            section="historia",
            layer="core",
        ),
        score=0.9,
        metadata={
            "url": f"https://www.carvajal.com/{document_id}/",
            "title": document_id.capitalize(),
            "source_type": "oficial",
        },
    )


FUNDACION = _hit("historia:3", "Carvajal fue fundada en Cali en 1904.")


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args}


def _runner(
    steps: Sequence[ScriptStep],
    *,
    store: QueueStore | None = None,
    settings: Settings | None = None,
    chat: ScriptedToolChat | None = None,
    embedder: Any = None,
) -> tuple[ToolAgentRunner, ScriptedToolChat]:
    settings = settings or Settings()
    chat = chat or ScriptedToolChat(steps=list(steps))
    deps = ToolDeps(
        vector_store=store or QueueStore(),
        embedder=embedder or FakeEmbedder(),
        settings=settings,
    )
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver())
    return ToolAgentRunner(graph, settings), chat


def _stream(runner: ToolAgentRunner, question: str = QUESTION) -> list[Any]:
    return list(runner.stream("dueno-1", "hilo-1", question, []))


def _tokens(events: Sequence[Any]) -> list[str]:
    return [e.text for e in events if isinstance(e, ToolAgentToken)]


def _final(events: Sequence[Any]) -> ToolAgentFinal:
    assert isinstance(events[-1], ToolAgentFinal)
    return events[-1]


# --- Retención (research R2) ---------------------------------------------------------------


def test_retiene_48_caracteres_y_luego_transmite_cada_trozo() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
        ScriptStep(text=ANSWER),
    ]
    runner, _ = _runner(steps, store=QueueStore([FUNDACION]))

    # Act
    tokens = _tokens(_stream(runner))

    # Assert
    assert "".join(tokens) == ANSWER
    assert tokens[0] == ANSWER[:48]
    # Tras liberar la retención, cada trozo del modelo (8 caracteres) sale tal cual.
    assert all(len(t) <= 8 for t in tokens[1:])
    assert len(tokens) > 2


def test_una_respuesta_final_corta_sale_entera_al_terminar_el_paso() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
        ScriptStep(text="Se fundó en 1904 [1]."),
    ]
    runner, _ = _runner(steps, store=QueueStore([FUNDACION]))

    # Act
    tokens = _tokens(_stream(runner))

    # Assert
    assert tokens == ["Se fundó en 1904 [1]."]


def test_descarta_el_texto_previo_a_una_llamada_a_herramienta() -> None:
    # Arrange
    steps = [
        ScriptStep(
            text="Voy a buscar eso.",
            tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")],
        ),
        ScriptStep(
            text="Déjame revisar.",
            tool_calls=[_call("buscar_corpus", consulta="historia de Carvajal")],
        ),
        ScriptStep(text=ANSWER),
    ]
    runner, _ = _runner(steps, store=QueueStore([FUNDACION]))

    # Act
    events = _stream(runner)

    # Assert
    joined = "".join(_tokens(events))
    assert joined == ANSWER
    assert "Voy a buscar" not in joined
    assert "Déjame revisar" not in joined
    assert _final(events).answer == ANSWER


def test_el_texto_de_un_paso_cortado_por_el_limite_se_conserva() -> None:
    # Arrange: con límite 1, el segundo paso va sin herramientas; si el modelo insiste en
    # llamar una, el grafo la quita y el texto es la respuesta final.
    settings = Settings(agent_max_tool_calls=1)
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
        ScriptStep(text="Se fundó en 1904 [1].", tool_calls=[_call("buscar_corpus", consulta="x")]),
    ]
    runner, _ = _runner(steps, store=QueueStore([FUNDACION]), settings=settings)

    # Act
    events = _stream(runner)

    # Assert
    assert "".join(_tokens(events)) == "Se fundó en 1904 [1]."
    assert _final(events).gate == "evidence"


# --- Estado (contracts/sse-events.md) ------------------------------------------------------


def test_status_en_orden_antes_del_texto_y_sin_resultados() -> None:
    # Arrange
    steps = [
        ScriptStep(
            tool_calls=[
                _call("sin_consulta", motivo="pedir_dato", dato="ciudad"),
                _call("buscar_corpus", consulta="fundación de Carvajal"),
            ]
        ),
        ScriptStep(
            tool_calls=[
                _call("indicador_financiero", nombre="ingresos_consolidados", periodo="2024")
            ]
        ),
        ScriptStep(text=ANSWER),
    ]
    runner, _ = _runner(steps, store=QueueStore([FUNDACION]))

    # Act
    events = _stream(runner)

    # Assert
    statuses = [e for e in events if isinstance(e, ToolAgentStatus)]
    assert [s.step for s in statuses] == [1, 2]
    first_token = next(i for i, e in enumerate(events) if isinstance(e, ToolAgentToken))
    assert all(events.index(s) < first_token for s in statuses)
    for status in statuses:
        assert "1904" not in status.text
        assert "[1]" not in status.text
        assert "buscar_corpus" not in status.text


# --- Compuerta de evidencia (research R3) --------------------------------------------------


def test_sin_evidencia_tras_buscar_corpus_emite_la_abstencion_y_nada_del_modelo() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="sede en Tokio")]),
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="oficina Tokio Carvajal")]),
        ScriptStep(text="Carvajal tiene una oficina en Tokio desde 1990, con 40 empleados."),
    ]
    runner, _ = _runner(steps, store=QueueStore([]))

    # Act
    events = _stream(runner, "¿Carvajal tiene sede en Tokio?")

    # Assert
    assert _tokens(events) == [NO_CONTEXT_ANSWER]
    final = _final(events)
    assert final.gate == "abstain"
    assert final.abstained is True
    assert final.answer == NO_CONTEXT_ANSWER
    assert final.sources == ()


def test_la_reformulacion_con_evidencia_libera_la_respuesta() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="año de creación")]),
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
        ScriptStep(text=ANSWER),
    ]
    runner, _ = _runner(steps, store=QueueStore([], [FUNDACION]))

    # Act
    events = _stream(runner)

    # Assert
    assert "".join(_tokens(events)) == ANSWER
    assert _final(events).gate == "evidence"


def test_sin_consulta_limpio_se_emite_completo_de_una_vez() -> None:
    # Arrange
    text = "Hola, soy Carva, el asistente de Carvajal. ¿En qué te ayudo hoy? Cuéntame qué buscas."
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text=text),
    ]
    runner, _ = _runner(steps)

    # Act
    events = _stream(runner, "Cuéntame algo")

    # Assert
    assert _tokens(events) == [text]
    assert not any(isinstance(e, ToolAgentStatus) for e in events)
    final = _final(events)
    assert final.gate == "no_query"
    assert final.abstained is False


def test_sin_consulta_con_cifras_o_nombres_propios_se_abstiene() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="Carvajal se fundó en 1904 y su filial Bioform hace vajillas."),
    ]
    runner, _ = _runner(steps)

    # Act
    events = _stream(runner, "Cuéntame algo")

    # Assert: sin búsqueda en el corpus, la abstención no dice «no está en el corpus».
    assert _tokens(events) == [NO_DATA_ANSWER]
    final = _final(events)
    assert final.gate == "no_query"
    assert final.abstained is True


def test_sin_consulta_puede_repetir_las_cifras_de_la_pregunta() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="pedir_dato", dato="ciudad")]),
        ScriptStep(text="¿En qué ciudad necesitas los 2.000 vasos?"),
    ]
    runner, _ = _runner(steps)

    # Act
    events = _stream(runner, "Necesito 2000 vasos para café")

    # Assert
    assert _tokens(events) == ["¿En qué ciudad necesitas los 2.000 vasos?"]


def test_sin_consulta_no_valida_su_propio_dato() -> None:
    # Arrange: el ``dato`` lo escribe el modelo; no puede respaldar una cifra.
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="pedir_dato", dato="desde 1904")]),
        ScriptStep(text="Existe desde 1904."),
    ]
    runner, _ = _runner(steps)

    # Act
    events = _stream(runner, "¿Desde cuándo existe?")

    # Assert: la cifra no sale; al pedir un dato el reemplazo es una petición neutra, no
    # la abstención del corpus, y tampoco repite el ``dato`` del modelo.
    [token] = _tokens(events)
    assert token != NO_CONTEXT_ANSWER
    assert "1904" not in token
    assert token.rstrip().endswith("?")
    assert _final(events).abstained is False


# --- Resultado final -----------------------------------------------------------------------


def test_el_final_trae_fuentes_resultados_y_camino() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
        ScriptStep(
            text=ANSWER,
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        ),
    ]
    runner, _ = _runner(steps, store=QueueStore([FUNDACION]))

    # Act
    events = _stream(runner)

    # Assert
    final = _final(events)
    assert len(final.sources) == 1
    assert isinstance(final.sources[0], Source)
    assert final.sources[0].chunk_id == "historia:3"
    assert [r["tool"] for r in final.results] == ["buscar_corpus"]
    assert final.results[0]["source_ids"] == [1]
    assert final.mode_path == "agent"
    assert final.answer == ANSWER
    assert final.usage["total_tokens"] == 15
    assert sum(isinstance(e, ToolAgentFinal) for e in events) == 1


class _StubGraph:
    """Grafo mínimo: reproduce una secuencia de eventos y registra la invocación."""

    def __init__(self, events: list[tuple[str, Any]]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    def stream(self, payload: Any, config: Any = None, **kwargs: Any) -> Iterator[Any]:
        self.calls.append({"input": payload, "config": config, **kwargs})
        yield from self.events


def _agent_step(text: str, message_id: str) -> list[tuple[str, Any]]:
    meta = {"langgraph_node": "agent"}
    return [
        ("messages", (AIMessageChunk(content=text, id=message_id), meta)),
        ("updates", {"agent": {"messages": [AIMessage(content=text, id=message_id)]}}),
    ]


def test_el_final_trae_tarjetas_cotizaciones_y_contexto() -> None:
    # Arrange
    quote = {"sku": "V-12", "total": 1000}
    source = {"chunk_id": "oferta:1", "slug": "oferta", "url": "https://x.co", "n": 1}
    tool_update = {
        "messages": [ToolMessage(content="ok", tool_call_id="c1", name="cotizar")],
        "results": [
            {"tool": "cotizar", "args": {}, "status": "ok", "source_ids": [1], "elapsed_ms": 1}
        ],
        "sources": [source],
        "quotes": [quote],
        "cards": {"options": [{"sku": "V-12"}], "quotes": [quote]},
        "commercial_context": {"city": "Bogotá", "quantity": 1000},
    }
    events: list[tuple[str, Any]] = [
        ("updates", {"recall": {"mode_path": "agent", "preferences": ["breve"]}}),
        ("custom", {"type": "status", "text": "Estoy calculando…", "step": 1}),
        ("updates", {"tools": tool_update}),
        *_agent_step("Te sale en 1000 [1].", "m2"),
    ]
    graph = _StubGraph(events)
    settings = Settings()
    runner = ToolAgentRunner(graph, settings)

    # Act
    seed: list[Turn] = [{"question": "a", "answer": "b", "incomplete": False}]
    out = list(runner.stream("dueno-1", "hilo-9", "Cotiza 1000 vasos", seed))

    # Assert
    final = _final(out)
    assert final.quotes == (quote,)
    assert final.products["quotes"] == [quote]
    assert final.products["quote"] == quote
    assert final.products["options"] == [{"sku": "V-12"}]
    assert final.commercial_context == {"city": "Bogotá", "quantity": 1000}
    assert final.preferences == ("breve",)
    assert final.sources[0].chunk_id == "oferta:1"
    assert out[0] == ToolAgentStatus(text="Estoy calculando…", step=1)
    call = graph.calls[0]
    assert call["config"]["configurable"]["thread_id"] == "hilo-9"
    assert call["config"]["recursion_limit"] == recursion_limit(settings)
    assert {"messages", "custom"} <= set(call["stream_mode"])
    assert call["input"]["owner_key"] == "dueno-1"
    assert call["input"]["seed_turns"] == seed
    assert final.turns == tuple(seed)


def test_ignora_mensajes_de_otros_nodos() -> None:
    # Arrange
    tools_meta = {"langgraph_node": "tools"}
    events: list[tuple[str, Any]] = [
        ("messages", (ToolMessage(content="Fundada en 1904", tool_call_id="c"), tools_meta)),
        ("messages", (AIMessageChunk(content="ruido", id="r"), {"langgraph_node": "recall"})),
        *_agent_step("Hola, ¿en qué te ayudo?", "m1"),
    ]
    runner = ToolAgentRunner(_StubGraph(events), Settings())

    # Act
    out = list(runner.stream("dueno-1", "hilo-1", "Hola", []))

    # Assert
    assert _tokens(out) == ["Hola, ¿en qué te ayudo?"]


# --- Contratos -----------------------------------------------------------------------------


def test_los_runners_cumplen_sus_contratos() -> None:
    # Arrange
    tool_runner = ToolAgentRunner(_StubGraph([]), Settings())
    graph_runner = GraphAgentRunner(object(), Settings())

    # Act / Assert
    assert isinstance(tool_runner, ToolStreamRunner)
    assert isinstance(graph_runner, AgentRunner)


@pytest.mark.parametrize("question", ["", "   "])
def test_la_pregunta_vacia_se_rechaza(question: str) -> None:
    # Arrange
    runner, chat = _runner([])

    # Act / Assert
    with pytest.raises(ValueError):
        _stream(runner, question)
    assert chat.calls == []


# --- Revisión de la entrega US1+US2 --------------------------------------------------------


class SlowChat(ScriptedToolChat):
    """Chat guionizado que tarda en cada trozo y cuenta los trozos que llegó a producir."""

    delay: float = 0.05
    produced: int = 0

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        for chunk in super()._stream(*args, **kwargs):
            time.sleep(self.delay)
            self.produced += 1
            yield chunk


class SlowEmbedder:
    """Embedder que tarda y cuenta las llamadas (una por ``buscar_corpus``)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        time.sleep(self.delay)
        return [0.1] * 8


LONG_ANSWER = "Carvajal se fundó en Cali en 1904 [1]. " * 12


def test_cancelar_durante_el_texto_detiene_al_modelo() -> None:
    # Arrange: 60 trozos de 50 ms; cerrar no debe esperar los ~3 s que faltan.
    chat = SlowChat(
        steps=[
            ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
            ScriptStep(text=LONG_ANSWER),
        ]
    )
    runner, _ = _runner([], store=QueueStore([FUNDACION]), chat=chat)
    events = cast(Generator[Any], runner.stream("dueno-1", "hilo-1", QUESTION, []))
    next(e for e in events if isinstance(e, ToolAgentToken))
    before = chat.produced

    # Act
    started = time.perf_counter()
    events.close()
    elapsed = time.perf_counter() - started

    # Assert
    assert elapsed < 1.0
    assert chat.produced <= before + 2
    assert chat.produced < len(LONG_ANSWER) // chat.chunk_size


def test_cancelar_durante_las_herramientas_no_ejecuta_las_pendientes() -> None:
    # Arrange: tres búsquedas de 300 ms en un mismo paso.
    embedder = SlowEmbedder(0.3)
    steps = [
        ScriptStep(
            tool_calls=[
                _call("buscar_corpus", consulta=f"{tema} de Carvajal")
                for tema in ("fundación", "historia", "presencia")
            ]
        ),
        ScriptStep(text="Listo [1]."),
    ]
    runner, chat = _runner(steps, store=QueueStore([FUNDACION]), embedder=embedder)
    events = cast(Generator[Any], runner.stream("dueno-1", "hilo-1", QUESTION, []))
    next(e for e in events if isinstance(e, ToolAgentStatus))

    # Act
    started = time.perf_counter()
    events.close()
    elapsed = time.perf_counter() - started

    # Assert
    assert elapsed < 0.6
    assert embedder.calls == 1
    assert chat.remaining_steps == 1


def test_tras_cancelar_el_hilo_admite_otro_turno() -> None:
    # Arrange
    chat = SlowChat(
        steps=[
            ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
            ScriptStep(text=LONG_ANSWER),
            ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
            ScriptStep(text="De nada, aquí estoy."),
        ],
        delay=0.0,
    )
    runner, _ = _runner([], store=QueueStore([FUNDACION]), chat=chat)
    cancelled = cast(Generator[Any], runner.stream("dueno-1", "hilo-1", QUESTION, []))
    next(e for e in cancelled if isinstance(e, ToolAgentToken))
    cancelled.close()

    # Act
    events = list(runner.stream("dueno-1", "hilo-1", "Perfecto, eso era", []))

    # Assert
    assert _tokens(events) == ["De nada, aquí estoy."]
    assert [r["tool"] for r in _final(events).results] == ["sin_consulta"]


def test_las_fuentes_salen_antes_del_primer_token() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
        ScriptStep(text=ANSWER),
    ]
    runner, _ = _runner(steps, store=QueueStore([FUNDACION]))

    # Act
    events = _stream(runner)

    # Assert: el cliente sella los párrafos con las fuentes que conoce al recibirlos.
    first_token = next(i for i, e in enumerate(events) if isinstance(e, ToolAgentToken))
    announced = [e for e in events if isinstance(e, ToolAgentSources)]
    assert len(announced) == 1
    assert events.index(announced[0]) == first_token - 1
    assert announced[0].sources == _final(events).sources
    assert announced[0].sources[0].chunk_id == "historia:3"


def test_la_abstencion_anuncia_cero_fuentes_antes_del_texto() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="sede en Tokio")]),
        ScriptStep(text="No."),
    ]
    runner, _ = _runner(steps, store=QueueStore([]))

    # Act
    events = _stream(runner, "¿Carvajal tiene sede en Tokio?")

    # Assert
    kinds = [type(e).__name__ for e in events]
    assert kinds[-3:] == ["ToolAgentSources", "ToolAgentToken", "ToolAgentFinal"]
    assert events[-3] == ToolAgentSources(sources=())


def test_la_abstencion_no_espera_a_que_el_modelo_redacte_todo() -> None:
    # Arrange: sin evidencia tras buscar_corpus, el paso final se corta al pasar la retención.
    chat = SlowChat(
        steps=[
            ScriptStep(tool_calls=[_call("buscar_corpus", consulta="sede en Tokio")]),
            ScriptStep(text=LONG_ANSWER),
        ],
        delay=0.01,
    )
    runner, _ = _runner([], store=QueueStore([]), chat=chat)

    # Act
    events = _stream(runner, "¿Carvajal tiene sede en Tokio?")

    # Assert
    assert _tokens(events) == [NO_CONTEXT_ANSWER]
    assert _final(events).gate == "abstain"
    final_step_chunks = chat.produced - 2  # el primer paso son dos trozos de la llamada
    assert final_step_chunks <= 48 // chat.chunk_size + 2


def test_una_pregunta_compuesta_conserva_la_peticion_del_dato_que_falta() -> None:
    # Arrange: la búsqueda no encuentra nada y la cotización pide la temperatura.
    text = "No encontré quién la fundó. Para cotizar, dime si la bebida es caliente o fría."
    steps = [
        ScriptStep(
            tool_calls=[
                _call("buscar_corpus", consulta="fundador de Carvajal"),
                _call("cotizar", family="vaso", quantity=2000, city="Bogotá"),
            ]
        ),
        ScriptStep(text=text),
    ]
    runner, _ = _runner(steps, store=QueueStore([]))

    # Act
    events = _stream(runner, "¿Quién fundó Carvajal? Y cotízame 2000 vasos en Bogotá")

    # Assert: la parte documental declara su ausencia y la petición del dato sale neutra,
    # con los campos de ``missing`` de la herramienta, no con la redacción del modelo.
    final = _final(events)
    assert [r["status"] for r in final.results] == ["empty", "needs_input"]
    assert final.gate == "no_query"
    [token] = _tokens(events)
    assert token.startswith(NO_CONTEXT_ANSWER)
    assert token.endswith(
        "Para seguir me falta un dato: si el contenido va caliente, frío o a temperatura "
        "ambiente. ¿Me lo indicas?"
    )
    assert final.abstained is False


def test_corpus_vacio_y_needs_input_no_liberan_afirmaciones_cualitativas_del_modelo() -> None:
    # Arrange: revisión PR 236 (C1). El corpus no responde, ``cotizar`` pide la temperatura y
    # el modelo afirma en minúscula un hecho del corpus que el filtro no marca.
    claim = (
        "Carvajal recicla todos sus vasos usados y los convierte en papel nuevo. "
        "Para cotizar, dime si la bebida es caliente o fría."
    )
    steps = [
        ScriptStep(
            tool_calls=[
                _call("buscar_corpus", consulta="programa de reciclaje de Carvajal"),
                _call("cotizar", family="vaso", quantity=2000, city="Bogotá"),
            ]
        ),
        ScriptStep(text=claim),
    ]
    runner, _ = _runner(steps, store=QueueStore([]))

    # Act
    events = _stream(runner, "¿Carvajal recicla los vasos? Y cotízame 2000 vasos en Bogotá")

    # Assert: el ``needs_input`` respalda la petición del dato, no lo dicho sobre el corpus.
    final = _final(events)
    assert [r["status"] for r in final.results] == ["empty", "needs_input"]
    assert final.gate == "no_query"
    [token] = _tokens(events)
    assert "recicla" not in token
    assert token.startswith(NO_CONTEXT_ANSWER)
    assert "caliente, frío" in token
    assert final.abstained is False


@pytest.mark.parametrize(
    ("call", "text"),
    [
        pytest.param(
            _call("sin_consulta", motivo="pedir_dato", dato="ciudad"),
            "¿En qué ciudad de Colombia necesitas los 2000 vasos?",
            id="sin-consulta",
        ),
        pytest.param(
            _call("cotizar", family="vaso", use="bebida_caliente", quantity=2000),
            "¿En qué ciudad de Colombia necesitas los 2000 vasos? Te doy el precio con IVA.",
            id="needs-input",
        ),
    ],
)
def test_las_aclaraciones_que_nombran_colombia_o_el_iva_no_se_abstienen(
    call: dict[str, Any], text: str
) -> None:
    # Arrange
    runner, _ = _runner([ScriptStep(tool_calls=[call]), ScriptStep(text=text)])

    # Act
    events = _stream(runner, "Necesito 2000 vasos para café")

    # Assert
    assert _tokens(events) == [text]
    assert _final(events).abstained is False


@pytest.mark.parametrize(
    ("call", "text"),
    [
        pytest.param(
            _call("indicador_financiero", nombre="ebitda", periodo="1950"),
            "En 1950 el ebitda fue récord.",
            id="indicador-vacio",
        ),
        pytest.param(
            _call(
                "buscar_producto",
                familia="vaso",
                capacidad_oz=99,
                temperatura="fria",
                nombre="Termogreen lanzado 2019 inversión 4500 millones EDEXA",
            ),
            "Termogreen se lanzó en 2019 con una inversión de 4500 millones junto a EDEXA.",
            id="buscar-producto-nombre-vacio",
        ),
        pytest.param(
            _call(
                "buscar_producto",
                familia="vaso",
                capacidad_oz=99,
                temperatura="fria",
                nombre="vaso Pando 2500 pesos",
            ),
            "El vaso Pando cuesta 2500 pesos.",
            id="buscar-producto-nombre-con-precio",
        ),
    ],
)
def test_los_argumentos_del_modelo_no_respaldan_sus_propias_cifras(
    call: dict[str, Any], text: str
) -> None:
    # Arrange: la herramienta repite el argumento en su mensaje o en el contexto comercial.
    runner, _ = _runner([ScriptStep(tool_calls=[call]), ScriptStep(text=text)])

    # Act
    events = _stream(runner, "Hola, ¿qué me cuentas?")

    # Assert: ninguna cifra ni nombre del modelo sale; la abstención declara la búsqueda
    # vacía con los datos de la herramienta y no habla del corpus, que no se consultó.
    [token] = _tokens(events)
    for term in re.findall(r"\d[\d.]*|\b[A-ZÁÉÍÓÚ]\w+", text):
        if term not in {"La", "En", "El"}:
            assert term not in token
    assert NO_CONTEXT_ANSWER not in token
    assert token.startswith("No ")
    assert _final(events).abstained is True


def test_una_ciudad_de_sedes_inventada_por_el_modelo_no_respalda_y_se_pide_la_ciudad() -> None:
    # Arrange: spec 045 (FR-013, cambio declarado sobre la 044): ``buscar_sedes`` ya no
    # busca en una ciudad que la persona no dijo; pide la ciudad en vez de buscar vacío.
    text = "La planta de Yumbo abrió en 1961 y tiene 3.500 empleados."
    call = _call("buscar_sedes", ciudad="Yumbo planta de 1961 con 3.500 empleados")
    runner, _ = _runner([ScriptStep(tool_calls=[call]), ScriptStep(text=text)])

    # Act
    events = _stream(runner, "Hola, ¿qué me cuentas?")

    # Assert
    [token] = _tokens(events)
    for term in ("Yumbo", "1961", "3.500"):
        assert term not in token
    assert "ciudad" in token
    final = _final(events)
    assert [r["status"] for r in final.results] == ["needs_input"]
    assert final.abstained is False


def test_los_argumentos_de_cotizar_no_respaldan_sus_cifras_al_pedir_un_dato() -> None:
    # Arrange: la cantidad 1904 la escribió el modelo; ``cotizar`` pide la ciudad.
    call = _call("cotizar", family="vaso", use="bebida_caliente", quantity=1904)
    runner, _ = _runner([ScriptStep(tool_calls=[call]), ScriptStep(text="Carvajal nació en 1904.")])

    # Act
    events = _stream(runner, "Hola, ¿qué me cuentas?")

    # Assert: la cifra no sale y la respuesta sigue pidiendo el dato que falta.
    [token] = _tokens(events)
    assert "1904" not in token
    assert token != NO_CONTEXT_ANSWER
    assert "ciudad" in token
    assert _final(events).abstained is False


# --- Datos de herramientas sin evidencia (simulación spec 044) -----------------------------

# Turnos reales de la simulación (``scratchpad/sim/completa``): llamadas y redacción del
# modelo tal como quedaron en el checkpoint.
VASOS_7_OZ = "Necesito 300 vasitos de 7 onzas pa una fiesta, cuánto sería?"
SHOTS = (
    "Fría, es para una fiesta de quince. Aunque pensándolo bien, mejor cotízame también los "
    "de 3,5 oz que son para los shots."
)
PIDE_CIUDAD = (
    "Me falta la ciudad para darte el precio de los vasos de 7 oz. Tengo disponibles: "
    "Barranquilla, Bogotá, Bucaramanga, Cali, Manizales, Medellín, Neiva, Pasto, Pereira y "
    "Villavicencio.\n\nPara los de 3,5 oz, ¿los shots van fríos o a temperatura ambiente? "
    "Con eso te muestro las opciones."
)
GOLD_14 = (
    "Buenas tardes. Quisiera cotizar vasos Gold de 14 oz con tapa pitillera para jugos naturales."
)
GOLD_MANIZALES = "Serían para empezar unas 2.000 unidades, y los recibiría acá en Manizales."
SEDES_MANIZALES = (
    "No hay precio publicado para vasos de 14 oz en Manizales, así que la cotización queda "
    "pendiente. Sí hay dos sedes del directorio con contacto:\n\n"
    "- Kive Andina — Parque Industrial Juanchito, terraza 3, lote 12 — 606 874 7455 [1]\n"
    "- Representaciones Lastra Eje Cafetero — misma dirección — 874 7455 [2]\n\n"
    "Un siguiente paso: llama a una de esas sedes para confirmar precio y disponibilidad, o "
    "dime si quieres que busque el vaso en otra ciudad o con otra capacidad."
)
J1_CALI = (
    "Listo, entonces cotízame una caja de 250 del J1 con el IVA ya incluido, a ver si me sirve "
    "por 200. Y los vasos de 14 oz fríos toca pedirlos por teléfono a Bonniplast, ¿cierto?"
)
# El precio de EDEXA salió de ``buscar_producto`` en el turno anterior: este turno no lo
# respalda y el texto no puede salir.
J1_SIN_RESPALDO = (
    "La cotización del J1 en Cali salió vacía: no hay precio publicado para esa búsqueda, solo "
    "las sedes del directorio [1]. El único precio que tengo del J1 es el de referencia de "
    "EDEXA, 180.166 COP por caja de 250 con impuestos incluidos [2].\n\n"
    "Sobre los vasos: sí, la vía es el teléfono. Bonniplast, Carrera 26 No. 8-37 Local 13, "
    "Galería Alameda, (602) 556 6514 [1]."
)
COTIZAR_J1_CALI = _call(
    "cotizar",
    family="contenedor",
    use="comida_caliente",
    quantity=250,
    city="Cali",
    temperature="caliente",
    microwave=False,
    capacity_oz=14,
)
COTIZAR_GOLD = {"family": "vaso", "capacity_oz": 14, "temperature": "fria", "use": "bebida_fria"}


def _two_turns(
    first: tuple[str, Sequence[ScriptStep]], second: tuple[str, Sequence[ScriptStep]]
) -> list[Any]:
    """Dos turnos en el mismo hilo: el segundo empieza con el contexto comercial del primero."""

    runner, _ = _runner([*first[1], *second[1]])
    list(runner.stream("dueno-1", "hilo-1", first[0], []))
    return list(runner.stream("dueno-1", "hilo-1", second[0], []))


def test_la_capacidad_del_turno_anterior_no_respalda_un_plazo_con_la_misma_cifra() -> None:
    # Arrange: revisión PR 236 (C2). El único «14» del turno es ``capacity_oz: 14.0`` del
    # contexto comercial; el modelo lo reutiliza para inventar un plazo de entrega.
    first = [
        ScriptStep(tool_calls=[_call("cotizar", **COTIZAR_GOLD)]),
        ScriptStep(text="¿En qué ciudad y cuántas unidades?"),
    ]
    second = [
        ScriptStep(tool_calls=[_call("cotizar", family="vaso", temperature="fria")]),
        ScriptStep(
            text="Para cotizarlos necesito la ciudad y la cantidad; la entrega tarda 14 días "
            "hábiles."
        ),
    ]

    # Act
    events = _two_turns(
        ("Necesito vasos de 14 oz para bebida fría.", first),
        ("¿Y para cuándo me llegarían?", second),
    )

    # Assert: sale el reemplazo neutro con los campos de ``missing``, sin el plazo.
    final = _final(events)
    assert final.gate == "no_query"
    [token] = _tokens(events)
    assert "14" not in token
    assert "días" not in token
    assert token.startswith("Para seguir me")
    assert final.abstained is False


def test_pedir_la_ciudad_con_la_capacidad_del_turno_anterior_no_se_abstiene() -> None:
    # Arrange: s-clientes-06 turno 1. Las dos herramientas piden un dato y el «7» viene del
    # contexto comercial (``capacity_oz: 7.0``).
    first = [
        ScriptStep(tool_calls=[_call("cotizar", family="vaso", capacity_oz=7, quantity=300)]),
        ScriptStep(text="Para cotizar los 300 vasos de 7 oz me falta un dato: ¿la bebida es fría?"),
    ]
    second = [
        ScriptStep(
            tool_calls=[
                _call("cotizar", family="vaso", capacity_oz=7, quantity=300, temperature="fria"),
                _call("buscar_producto", familia="vaso", capacidad_oz=3.5),
            ]
        ),
        ScriptStep(text=PIDE_CIUDAD),
    ]

    # Act
    events = _two_turns((VASOS_7_OZ, first), (SHOTS, second))

    # Assert
    final = _final(events)
    assert [r["status"] for r in final.results] == ["needs_input", "needs_input"]
    assert final.gate == "no_query"
    assert _tokens(events) == [PIDE_CIUDAD]
    assert final.abstained is False


def test_cotizar_vacio_deja_pasar_las_sedes_que_devolvio() -> None:
    # Arrange: s-clientes-07 turno 1. ``cotizar`` sin precio en Manizales, con dos sedes.
    first = [
        ScriptStep(tool_calls=[_call("cotizar", **COTIZAR_GOLD)]),
        ScriptStep(text="Para cotizar los vasos de 14 oz me falta la cantidad de unidades."),
    ]
    second = [
        ScriptStep(tool_calls=[_call("cotizar", quantity=2000, city="Manizales", **COTIZAR_GOLD)]),
        ScriptStep(text=SEDES_MANIZALES),
    ]

    # Act
    events = _two_turns((GOLD_14, first), (GOLD_MANIZALES, second))

    # Assert
    final = _final(events)
    assert [(r["status"], r["source_ids"]) for r in final.results] == [("empty", [1, 2])]
    assert final.gate == "no_query"
    assert _tokens(events) == [SEDES_MANIZALES]
    assert final.abstained is False
    assert [s.chunk_id[:5] for s in final.sources] == ["sede:", "sede:"]


def test_cotizar_vacio_sin_respaldo_se_reemplaza_por_las_sedes_y_no_por_el_corpus() -> None:
    # Arrange: s-clientes-26 turno 2. El texto repite un precio de otro turno: no sale, pero
    # el reemplazo muestra las sedes del directorio que sí devolvió la herramienta.
    steps = [ScriptStep(tool_calls=[COTIZAR_J1_CALI]), ScriptStep(text=J1_SIN_RESPALDO)]
    runner, _ = _runner(steps)
    # La persona dijo Cali en un turno anterior: la ciudad del argumento tiene respaldo.
    previous: list[Turn] = [
        {"question": "Estoy en Cali, ¿qué portacomidas tienen?", "answer": "…", "incomplete": False}
    ]

    # Act
    events = list(runner.stream("dueno-1", "hilo-1", J1_CALI, previous))

    # Assert
    [token] = _tokens(events)
    assert token != NO_CONTEXT_ANSWER
    assert "180.166" not in token
    assert "EDEXA" not in token
    assert "- Bonniplast — Carrera 26 No. 8-37 Local 13, Galería Alameda — (602) 556 6514 [1]" in (
        token
    )
    final = _final(events)
    assert final.gate == "no_query"
    assert final.abstained is False
    announced = next(e for e in events if isinstance(e, ToolAgentSources))
    assert announced.sources == final.sources
    assert final.sources[0].chunk_id.startswith("sede:")


def test_una_cifra_inventada_junto_a_las_sedes_sigue_sustituyendose() -> None:
    # Arrange: las sedes no respaldan una cifra que no traen.
    call = _call("cotizar", quantity=2000, city="Manizales", **COTIZAR_GOLD)
    text = "Carvajal tiene 13.000 empleados en Manizales; llama a Kive Andina [1]."
    runner, _ = _runner([ScriptStep(tool_calls=[call]), ScriptStep(text=text)])

    # Act
    events = _stream(runner, GOLD_MANIZALES)

    # Assert
    [token] = _tokens(events)
    assert "13.000" not in token
    assert "empleados" not in token
    assert "Kive Andina" in token
    assert _final(events).abstained is False


def test_un_nombre_propio_sin_respaldo_al_pedir_un_dato_se_sustituye_por_la_peticion() -> None:
    # Arrange: la cotización pide la ciudad; la marca del grupo no la trae ninguna fuente.
    call = _call("cotizar", family="vaso", use="bebida_caliente", quantity=2000)
    text = "Los vasos de Bioform son los mejores. ¿En qué ciudad los recibes?"
    runner, _ = _runner([ScriptStep(tool_calls=[call]), ScriptStep(text=text)])

    # Act
    events = _stream(runner, "Necesito 2000 vasos para café")

    # Assert
    [token] = _tokens(events)
    assert "Bioform" not in token
    assert token != NO_CONTEXT_ANSWER
    assert "ciudad" in token
    assert "Bogotá" in token  # ``ciudades_disponibles`` de la herramienta
    assert _final(events).abstained is False


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(SEDES_MANIZALES, id="texto-limpio"),
        # La «terraza 3» de la dirección respaldaría el plazo inventado en el filtro.
        pytest.param("Kive Andina [1] entrega en 3 días.", id="plazo-respaldado-por-casualidad"),
        # «Manizales» viene de la ciudad de la sede, no de un dato del corpus.
        pytest.param("Carvajal nació en Manizales. Llama a Kive Andina [1].", id="dato-del-corpus"),
    ],
)
def test_buscar_corpus_vacio_con_sedes_de_cotizar_emite_solo_las_sedes(text: str) -> None:
    # Arrange: pregunta compuesta; el corpus no responde, pero la cotización trajo sedes.
    steps = [
        ScriptStep(
            tool_calls=[
                _call("buscar_corpus", consulta="fundador de Carvajal"),
                _call("cotizar", quantity=2000, city="Manizales", **COTIZAR_GOLD),
            ]
        ),
        ScriptStep(text=text),
    ]
    runner, _ = _runner(steps, store=QueueStore([]))

    # Act
    events = _stream(runner, "¿Quién fundó Carvajal? Y cotízame 2000 vasos de 14 oz en Manizales")

    # Assert: la redacción del modelo no sale, ni filtrada; sale el aviso neutro con las
    # sedes que trajo la herramienta, con sus citas.
    final = _final(events)
    assert [r["status"] for r in final.results] == ["empty", "empty"]
    assert final.gate == "no_query"
    [token] = _tokens(events)
    assert token.startswith("No encontré un precio publicado")
    assert "- Kive Andina — Parque Industrial Juanchito, terraza 3, lote 12" in token
    assert "[1]" in token
    assert "días" not in token
    assert "nació" not in token
    assert final.abstained is False
    assert [s.chunk_id[:5] for s in final.sources] == ["sede:", "sede:"]


def test_buscar_corpus_vacio_y_pedir_dato_no_libera_afirmaciones_del_modelo() -> None:
    # Arrange: el modelo declara que pide un dato (sin ``dato``) y afirma hechos del corpus
    # en minúscula, que el filtro no marca. Ninguna herramienta pidió nada.
    text = (
        "Sí, carvajal es una empresa certificada como sostenible y la fundó don manuel "
        "sinisterra. ¿Qué producto buscas?"
    )
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="certificación")]),
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="pedir_dato")]),
        ScriptStep(text=text),
    ]
    runner, _ = _runner(steps, store=QueueStore([]))

    # Act
    events = _stream(runner, "¿Carvajal tiene certificación B Corp?")

    # Assert: sale la petición neutra, no la redacción del modelo.
    final = _final(events)
    assert final.gate == "no_query"
    [token] = _tokens(events)
    assert "certificada" not in token
    assert "sinisterra" not in token
    assert token != NO_CONTEXT_ANSWER
    assert token.rstrip().endswith("?")
    assert final.abstained is False


def test_sin_consulta_invalido_tras_buscar_corpus_vacio_se_abstiene() -> None:
    # Arrange: ``pedir_dato`` con un ``dato`` demasiado largo falla la validación.
    steps = [
        ScriptStep(
            tool_calls=[
                _call("buscar_corpus", consulta="sedes"),
                _call("sin_consulta", motivo="pedir_dato", dato="x" * 5000),
            ]
        ),
        ScriptStep(text="Carvajal tiene 40 sedes. ¿En qué ciudad estás?"),
    ]
    runner, _ = _runner(steps, store=QueueStore([]))

    # Act
    events = _stream(runner, "¿Dónde compro vasos?")

    # Assert: un ``sin_consulta`` inválido no cuenta como petición, ni en la compuerta ni en
    # el reemplazo: el turno se abstiene de forma coherente.
    final = _final(events)
    assert [r["status"] for r in final.results] == ["empty", "error"]
    assert final.gate == "abstain"
    assert _tokens(events) == [NO_CONTEXT_ANSWER]
    assert final.abstained is True


# Un ``nombre`` de ``buscar_producto`` que el modelo llena con hechos inventados.
NOMBRE_INVENTADO = "Termogreen lanzado 2019 inversión 4500 millones EDEXA"
TEXTO_INVENTADO = "Termogreen se lanzó en 2019 con una inversión de 4500 millones junto a EDEXA."


def test_el_nombre_de_buscar_producto_no_respalda_sus_cifras_al_pedir_un_dato() -> None:
    # Arrange: ``buscar_producto`` pide la temperatura (``needs_input``).
    call = _call("buscar_producto", familia="vaso", nombre=NOMBRE_INVENTADO)
    text = f"{TEXTO_INVENTADO} ¿La bebida va caliente o fría?"
    runner, _ = _runner([ScriptStep(tool_calls=[call]), ScriptStep(text=text)])

    # Act
    events = _stream(runner, "Háblame de los vasos térmicos")

    # Assert
    final = _final(events)
    assert [r["status"] for r in final.results] == ["needs_input"]
    [token] = _tokens(events)
    for term in ("2019", "4500", "EDEXA", "Termogreen"):
        assert term not in token
    assert "caliente, frío" in token
    assert final.abstained is False


def test_buscar_corpus_vacio_y_pedir_dato_no_se_convierte_en_no_esta_en_el_corpus() -> None:
    # Arrange: la búsqueda no encuentra nada, el modelo pide la ciudad e inventa una cifra.
    steps = [
        ScriptStep(
            tool_calls=[
                _call("buscar_corpus", consulta="sedes de Carvajal"),
                _call("sin_consulta", motivo="pedir_dato", dato="ciudad de entrega"),
            ]
        ),
        ScriptStep(text="Carvajal tiene 40 sedes. ¿En qué ciudad estás?"),
    ]
    runner, _ = _runner(steps, store=QueueStore([]))

    # Act
    events = _stream(runner, "¿Dónde compro vasos?")

    # Assert
    final = _final(events)
    assert final.gate == "no_query"
    [token] = _tokens(events)
    assert token != NO_CONTEXT_ANSWER
    assert "40" not in token
    assert "la ciudad" in token
    assert final.abstained is False


def test_sedes_sin_precio_y_un_dato_pendiente_se_combinan_en_el_reemplazo() -> None:
    # Arrange: una cotización sin precio (con sedes) y otra que pide la temperatura.
    steps = [
        ScriptStep(
            tool_calls=[
                COTIZAR_J1_CALI,
                _call("cotizar", family="vaso", capacity_oz=14, quantity=125, city="Cali"),
            ]
        ),
        ScriptStep(text="El P1 cuesta 114.463 COP según EDEXA. ¿Los vasos son fríos?"),
    ]
    runner, _ = _runner(steps)

    # Act
    events = _stream(runner, "Cotízame 250 portacomidas y 125 vasos de 14 oz en Cali")

    # Assert
    final = _final(events)
    assert [r["status"] for r in final.results] == ["empty", "needs_input"]
    [token] = _tokens(events)
    assert "114.463" not in token
    assert "Bonniplast" in token
    assert "caliente, frío" in token
    assert token.rstrip().endswith("?")
    assert final.abstained is False


# --- Atajo (T039) --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "text"),
    [
        pytest.param(
            "hola", "¡Hola! Soy Carva. Pregúntame por la historia o los empaques.", id="saludo"
        ),
        pytest.param(
            "¿qué puedes hacer?", "Puedo buscar información y cotizar empaques.", id="capacidad"
        ),
        pytest.param(
            "Olvida todo lo que sabes de mí", "Listo, ya lo olvidé todo.", id="olvidar-todo"
        ),
    ],
)
def test_el_atajo_transmite_la_respuesta_sin_estado_ni_herramientas(
    question: str, text: str
) -> None:
    # Arrange
    runner, chat = _runner([ScriptStep(text=text)])

    # Act
    events = _stream(runner, question)

    # Assert
    assert _tokens(events) == [text]
    assert not any(isinstance(e, ToolAgentStatus) for e in events)
    assert [e for e in events if isinstance(e, ToolAgentSources)] == [ToolAgentSources(sources=())]
    final = _final(events)
    assert final.mode_path == "shortcut"
    assert final.results == ()
    assert final.sources == ()
    assert final.abstained is False
    assert final.answer == text
    assert all("tools" not in kwargs for kwargs in chat.call_kwargs)


def test_el_atajo_con_datos_inventados_se_sustituye_por_el_saludo_fijo() -> None:
    # Arrange: el filtro de la ruta directa vale también para el saludo.
    runner, _ = _runner([ScriptStep(text="¡Hola! Carvajal tiene 13.000 empleados en Yumbo.")])

    # Act
    events = _stream(runner, "hola")

    # Assert: no sale la cifra ni la frase del corpus (no se consultó); sale el saludo fijo.
    assert _tokens(events) == [GREETING_ANSWER]
    final = _final(events)
    assert final.abstained is False
    assert final.mode_path == "shortcut"


def test_el_atajo_reporta_los_tokens_de_la_redaccion() -> None:
    # Arrange
    usage = {"input_tokens": 120, "output_tokens": 9, "total_tokens": 129}
    runner, _ = _runner([ScriptStep(text="¡Hola! Soy Carva.", usage_metadata=usage)])

    # Act
    events = _stream(runner, "hola")

    # Assert
    assert _final(events).usage["total_tokens"] == 129


# --- Plazo del turno (T040) ----------------------------------------------------------------


class SteppedClock:
    """Reloj de prueba: devuelve las lecturas dadas y repite la última."""

    def __init__(self, *readings: float) -> None:
        self.readings = list(readings)

    def __call__(self) -> float:
        if len(self.readings) > 1:
            return self.readings.pop(0)
        return self.readings[0]


def test_el_plazo_vencido_antes_del_primer_token_lanza_turn_timeout() -> None:
    # Arrange: el reloj salta más allá del plazo tras el primer evento del grafo.
    settings = Settings(agent_turn_timeout_s=45)
    chat = ScriptedToolChat(
        steps=[
            ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
            ScriptStep(text=ANSWER),
        ]
    )
    deps = ToolDeps(
        vector_store=QueueStore([FUNDACION]), embedder=FakeEmbedder(), settings=settings
    )
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver())
    runner = ToolAgentRunner(graph, settings, clock=SteppedClock(0.0, 0.0, 100.0))
    seen: list[Any] = []

    # Act
    with pytest.raises(TurnTimeout) as raised:
        for event in runner.stream("dueno-1", "hilo-1", QUESTION, []):
            seen.append(event)

    # Assert: no salió texto y el servicio puede usar el respaldo.
    assert isinstance(raised.value, TimeoutError)
    assert _tokens(seen) == []
    assert raised.value.timeout_s == 45
    assert isinstance(raised.value.final, ToolAgentFinal)
    assert raised.value.final.answer == ""
    assert chat.remaining_steps >= 1


def test_el_plazo_corta_un_paso_lento_del_modelo() -> None:
    # Arrange: 0,1 s por trozo; la retención libera el texto tras ~0,6 s, el plazo es 0,25 s.
    settings = Settings(agent_turn_timeout_s=0.25)
    chat = SlowChat(steps=[ScriptStep(text=LONG_ANSWER)], delay=0.1)
    runner, _ = _runner([], settings=settings, chat=chat)
    seen: list[Any] = []

    # Act
    started = time.perf_counter()
    with pytest.raises(TurnTimeout):
        for event in runner.stream("dueno-1", "hilo-1", "Cuéntame algo", []):
            seen.append(event)
    elapsed = time.perf_counter() - started

    # Assert
    assert elapsed < 1.0
    assert _tokens(seen) == []
    assert chat.produced < len(LONG_ANSWER) // chat.chunk_size


def test_el_plazo_no_corta_un_texto_que_ya_empezo() -> None:
    # Arrange: el texto empieza antes del plazo y termina después; se entrega completo.
    settings = Settings(agent_turn_timeout_s=0.3)
    text = "Carvajal se fundó en Cali en 1904 [1]. " * 5
    chat = SlowChat(
        steps=[
            ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
            ScriptStep(text=text),
        ],
        delay=0.02,
    )
    runner, _ = _runner([], store=QueueStore([FUNDACION]), settings=settings, chat=chat)

    # Act
    started = time.perf_counter()
    events = _stream(runner)
    elapsed = time.perf_counter() - started

    # Assert
    assert elapsed > settings.agent_turn_timeout_s
    assert "".join(_tokens(events)) == text
    assert _final(events).gate == "evidence"


def test_sin_vencer_el_plazo_el_turno_termina_normal() -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
        ScriptStep(text=ANSWER),
    ]
    settings = Settings(agent_turn_timeout_s=45)
    runner, _ = _runner(steps, store=QueueStore([FUNDACION]), settings=settings)

    # Act
    events = _stream(runner)

    # Assert
    assert "".join(_tokens(events)) == ANSWER


# --- Revisión de la entrega US3+US4 --------------------------------------------------------


class StalledChat(ScriptedToolChat):
    """Proveedor que tarda en mandar el primer trozo (latencia hasta el primer byte)."""

    stall: float = 2.0

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        time.sleep(self.stall)
        yield from super()._stream(*args, **kwargs)


def test_el_plazo_corta_un_proveedor_que_no_manda_el_primer_trozo() -> None:
    # Arrange: ningún callback corre mientras el proveedor no responde; el plazo es 0,25 s.
    settings = Settings(agent_turn_timeout_s=0.25)
    chat = StalledChat(
        steps=[
            ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
            ScriptStep(text=ANSWER),
        ]
    )
    runner, _ = _runner([], store=QueueStore([FUNDACION]), settings=settings, chat=chat)
    seen: list[Any] = []

    # Act
    started = time.perf_counter()
    with pytest.raises(TurnTimeout) as raised:
        for event in runner.stream("dueno-1", "hilo-1", QUESTION, []):
            seen.append(event)
    elapsed = time.perf_counter() - started

    # Assert: el respaldo puede entrar a tiempo, sin esperar al proveedor.
    assert elapsed < 1.0
    assert _tokens(seen) == []
    assert raised.value.final.answer == ""
    # El paso sigue cancelado en segundo plano y termina al llegar el primer trozo.
    for thread in threading.enumerate():
        if thread.name == "tool-agent-graph":
            thread.join(timeout=5)
    assert not any(t.name == "tool-agent-graph" for t in threading.enumerate())
    assert chat.remaining_steps == 1


def _two_turns_with_memory(
    first_question: str,
    second_question: str,
    second_steps: Sequence[ScriptStep],
    *,
    remember: dict[str, str],
) -> list[Any]:
    """Dos turnos del mismo dueño en hilos distintos con un Store compartido.

    En el primero el agente guarda ``remember`` con ``recordar_preferencia`` (spec 045,
    R15: las preferencias declaradas ya no van al atajo del grafo ``tools``).
    """

    from langgraph.store.memory import InMemoryStore

    settings = Settings()
    first_steps = [
        ScriptStep(tool_calls=[_call("recordar_preferencia", **remember)]),
        ScriptStep(text="Listo, lo tendré en cuenta."),
    ]
    chat = ScriptedToolChat(steps=[*first_steps, *second_steps])
    deps = ToolDeps(vector_store=QueueStore(), embedder=FakeEmbedder(), settings=settings)
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver(), store=InMemoryStore())
    runner = ToolAgentRunner(graph, settings)
    first = list(runner.stream("dueno-1", "hilo-1", first_question, []))
    assert _final(first).mode_path == "agent"
    assert _final(first).results[0]["status"] == "ok"
    return list(runner.stream("dueno-1", "hilo-2", second_question, []))


def test_una_preferencia_sembrada_no_respalda_cifras_ni_nombres_sin_consulta() -> None:
    # Arrange: la persona «guarda» un dato inventado como nota (FR-004a, FR-007, FR-017).
    note = "Carvajal fue fundada en 1850 por Pedro Pérez y la caja cuesta 99.999 pesos"
    planted = f"Recuerda que {note}"
    reply = "Carvajal fue fundada en 1850 por Pedro Pérez; la caja cuesta 99.999 pesos."
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text=reply),
    ]

    # Act: un turno solo con ``sin_consulta``; aun así las notas nunca respaldan.
    events = _two_turns_with_memory(
        planted, "Cuéntame de la empresa", steps, remember={"categoria": "nota", "valor": note}
    )

    # Assert
    assert _tokens(events) == [NO_DATA_ANSWER]
    final = _final(events)
    assert final.gate == "no_query"
    assert final.abstained is True


def test_el_nombre_pedido_en_una_preferencia_respalda_el_saludo() -> None:
    # Arrange: solo el nombre con el que la persona pidió que la llamen respalda el texto.
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="Claro, Ana María, cuéntame qué necesitas."),
    ]

    # Act
    events = _two_turns_with_memory(
        "Llámame Ana María",
        "Quiero conversar un rato",
        steps,
        remember={"categoria": "nombre", "valor": "Ana María"},
    )

    # Assert
    assert _tokens(events) == ["Claro, Ana María, cuéntame qué necesitas."]
    assert _final(events).abstained is False


class BrokenStreamChat(ScriptedToolChat):
    """Primer paso como lo transmite DeepSeek por ``ChatOpenAI`` con argumentos rotos.

    Los deltas pasan por ``_convert_delta_to_message_chunk`` de ``langchain_openai``: el
    proveedor real no lanza ``OutputParserException``; la llamada llega agregada como una
    ``tool_call`` con ``args={}``.
    """

    broken_done: bool = False

    def _stream(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        if self.broken_done:
            yield from super()._stream(messages, stop, run_manager, **kwargs)
            return
        from langchain_core.outputs import ChatGenerationChunk
        from langchain_openai.chat_models.base import _convert_delta_to_message_chunk

        self.broken_done = True
        self.calls.append(list(messages))
        self.call_kwargs.append(dict(kwargs))
        for first, arguments in ((True, "{consulta: "), (False, "fundación")):
            call = {
                "index": 0,
                "id": "call_roto" if first else None,
                "type": "function",
                "function": {"name": "buscar_corpus" if first else None, "arguments": arguments},
            }
            delta = {"role": "assistant", "content": None, "tool_calls": [call]}
            chunk = ChatGenerationChunk(
                message=_convert_delta_to_message_chunk(delta, AIMessageChunk)
            )
            if run_manager is not None:
                run_manager.on_llm_new_token("", chunk=chunk)
            yield chunk


def test_argumentos_rotos_en_streaming_no_consultan_ni_inventan() -> None:
    # Arrange: la «decisión inválida» real de FR-009 con DeepSeek (no una excepción).
    store = QueueStore([FUNDACION])
    chat = BrokenStreamChat(steps=[ScriptStep(text="Carvajal se fundó en 1850 en Bogotá.")])
    runner, _ = _runner([], store=store, chat=chat)

    # Act
    events = _stream(runner)

    # Assert: el esquema rechaza ``args={}``, no se busca nada y el texto no sale.
    final = _final(events)
    assert store.calls == 0
    assert [(r["tool"], r["status"], r["args"]) for r in final.results] == [
        ("buscar_corpus", "error", {})
    ]
    # Con argumentos inválidos no se buscó en el corpus: no se afirma que no esté en él.
    assert _tokens(events) == [NO_DATA_ANSWER]
    assert final.abstained is True


# --- Abstención ajustada a lo consultado (simulación s-clientes-08, 13 y 14) --------------

NEIVA_TURNS: list[Turn] = [
    {
        "question": "tienen copitas pa el aji? donde las consigo en neiva",
        "answer": "Representaciones Lastra Neiva, teléfono 318 409 4338 [1].",
        "incomplete": False,
    }
]
GRACIAS_NEIVA = (
    "Listo, muchas gracias. Entonces llamo a esa sede a ver si me llevan los 10 paquetes "
    "hasta la universidad. Dios le pague."
)


def test_una_despedida_que_repite_datos_del_historial_sale_como_despedida_fija() -> None:
    # Arrange: s-clientes-08 turno 2. El atajo redacta la despedida, pero repite la sede y el
    # teléfono del turno anterior, que este turno no respalda.
    text = "¡Con gusto! Suerte con Representaciones Lastra Neiva al 318 409 4338."
    runner, chat = _runner([ScriptStep(text=text)])

    # Act
    events = list(runner.stream("dueno-1", "hilo-1", GRACIAS_NEIVA, NEIVA_TURNS))

    # Assert: ni el dato sin respaldo ni la frase del corpus; una despedida.
    assert _tokens(events) == [FAREWELL_ANSWER]
    final = _final(events)
    assert final.mode_path == "shortcut"
    assert final.abstained is False
    assert NO_CONTEXT_ANSWER not in final.answer
    assert all("tools" not in kwargs for kwargs in chat.call_kwargs)


def test_una_despedida_limpia_sale_tal_cual() -> None:
    # Arrange
    text = "Con mucho gusto. Que te vaya muy bien, aquí estoy si necesitas algo más."
    runner, _ = _runner([ScriptStep(text=text)])

    # Act
    events = _stream(runner, "Perfecto, eso era todo")

    # Assert
    assert _tokens(events) == [text]
    assert _final(events).abstained is False


def test_catalogo_y_sedes_vacios_no_responden_con_la_frase_del_corpus() -> None:
    # Arrange: s-clientes-14 turno 1. Sin cubiertos en Colombia y sin sedes en Tunja; el
    # modelo redacta con datos que nada respalda.
    steps = [
        ScriptStep(
            tool_calls=[
                _call("buscar_producto", familia="cubierto", temperatura="fria", pais="CO"),
                _call("buscar_sedes", ciudad="Tunja"),
            ]
        ),
        ScriptStep(text="En Tunja, Bioform vende cubiertos de PS a 12.500 pesos."),
    ]
    runner, _ = _runner(steps)

    # Act
    events = _stream(runner, "¿Tienen cubiertos de cualquier material? En Tunja no hay sede.")

    # Assert
    [token] = _tokens(events)
    assert NO_CONTEXT_ANSWER not in token
    assert "No lo encontré en el catálogo de Colombia." in token
    assert "No hay sedes del directorio en esa ciudad." in token
    assert "Bogotá" in token  # ``ciudades_con_cobertura`` de ``buscar_sedes``
    assert "12.500" not in token
    assert "Bioform" not in token
    final = _final(events)
    assert final.gate == "no_query"
    assert final.abstained is True
    assert final.sources == ()


def test_sedes_vacias_no_se_pierden_cuando_otra_herramienta_pide_un_dato() -> None:
    # Arrange: s-clientes-13 turno 0. No hay sedes en Cartagena y falta la temperatura.
    steps = [
        ScriptStep(
            tool_calls=[
                _call("buscar_sedes", ciudad="Cartagena"),
                _call("buscar_producto", familia="vaso", pais="CO"),
            ]
        ),
        ScriptStep(text="En Cartagena está Dispapeles en el Bosque; ¿caliente o frío?"),
    ]
    runner, _ = _runner(steps)

    # Act
    events = _stream(runner, "¿Dónde compro empaques en Cartagena? Necesito vasos")

    # Assert
    [token] = _tokens(events)
    assert "Dispapeles" not in token
    assert "No hay sedes del directorio en esa ciudad." in token
    assert "Para seguir me falta un dato" in token
    assert _final(events).abstained is False


def test_corpus_y_sedes_vacios_declaran_ambas_ausencias() -> None:
    # Arrange: el turno sí buscó en el corpus; la frase del corpus vale, con las sedes.
    steps = [
        ScriptStep(
            tool_calls=[
                _call("buscar_corpus", consulta="venta directa Cartagena"),
                _call("buscar_sedes", ciudad="Cartagena"),
            ]
        ),
        ScriptStep(text="Carvajal envía a Cartagena desde su planta de Yumbo en 48 horas."),
    ]
    runner, _ = _runner(steps)

    # Act
    events = _stream(runner, "¿Carvajal me vende directo y me lo manda a Cartagena?")

    # Assert
    [token] = _tokens(events)
    assert token.startswith("No hay sedes del directorio en esa ciudad.")
    assert token.endswith(NO_CONTEXT_ANSWER)
    assert "48" not in token
    final = _final(events)
    assert final.gate == "abstain"
    assert final.abstained is True


# --- Revisión: seguimientos tras una cotización (unidad, ciudad, despedidas) --------------

VASOS_14_FRIOS: dict[str, Any] = {"family": "vaso", "capacity_oz": 14, "temperature": "fria"}
CINCO_CAJAS = "Cotízame 5 cajas de vasos de 14 oz fríos en Bogotá"
QUINIENTOS = "Cotízame 500 vasos de 14 oz fríos en Bogotá"


def _first_quote(question: str, **args: Any) -> list[ScriptStep]:
    return [
        ScriptStep(tool_calls=[_call("cotizar", city="Bogotá", **VASOS_14_FRIOS, **args)]),
        ScriptStep(text="Te sale así [1]."),
    ]


def test_el_seguimiento_que_reenvia_la_cantidad_sigue_en_cajas() -> None:
    # Arrange: el modelo reenvía todos los argumentos, la cantidad incluida, sin la unidad.
    second = [
        ScriptStep(
            tool_calls=[
                _call("cotizar", quantity=5, city="Bogotá", delivery=False, **VASOS_14_FRIOS)
            ]
        ),
        ScriptStep(text="Sin domicilio te sale así [1]."),
    ]

    # Act
    events = _two_turns(
        (CINCO_CAJAS, _first_quote(CINCO_CAJAS, quantity=5, unidad="cajas")),
        ("Sin domicilio, lo recojo", second),
    )

    # Assert: 5 cajas son 4.625 unidades, no 5.
    final = _final(events)
    assert [r["status"] for r in final.results] == ["ok"]
    assert final.quotes[-1]["primary"]["units_requested"] == 4625
    assert final.commercial_context["unidad"] == "cajas"


def test_una_ciudad_que_la_persona_no_dijo_no_se_cotiza_con_la_del_contexto() -> None:
    # Arrange: el hilo cotizó en Bogotá; «la capital antioqueña» no nombra la ciudad y el
    # modelo pide Medellín. Antes salía una cotización de Bogotá atribuida a Medellín.
    second = [
        ScriptStep(tool_calls=[_call("cotizar", city="Medellín", **VASOS_14_FRIOS)]),
        ScriptStep(text="En Medellín te sale así [1]."),
    ]

    # Act
    events = _two_turns(
        (QUINIENTOS, _first_quote(QUINIENTOS, quantity=500)),
        ("¿Y en la capital antioqueña?", second),
    )

    # Assert
    final = _final(events)
    assert [r["status"] for r in final.results] == ["needs_input"]
    assert final.quotes == ()
    assert "En Medellín te sale así" not in final.answer
    assert final.commercial_context["city"] == "Bogotá"


def test_un_agradecimiento_con_una_cantidad_nueva_va_al_agente() -> None:
    # Arrange: «Gracias, 2000 unidades» corrige la cantidad; no es una despedida.
    second = [
        ScriptStep(tool_calls=[_call("cotizar", quantity=2000, **VASOS_14_FRIOS)]),
        ScriptStep(text="Con 2000 te sale así [1]."),
    ]

    # Act
    events = _two_turns(
        (QUINIENTOS, _first_quote(QUINIENTOS, quantity=500)), ("Gracias, 2000 unidades", second)
    )

    # Assert
    final = _final(events)
    assert final.mode_path == "agent"
    assert [r["status"] for r in final.results] == ["ok"]
    assert final.commercial_context["quantity"] == 2000


def test_un_seguimiento_que_fue_al_agente_sin_respaldo_es_una_abstencion_y_no_una_despedida() -> (
    None
):
    # Arrange: «Gracias, sin domicilio» pide algo (va al agente); el modelo no consulta y
    # repite un total sin respaldo.
    second = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="Perfecto, sin domicilio te sale en 75.800 COP."),
    ]

    # Act
    events = _two_turns(
        (QUINIENTOS, _first_quote(QUINIENTOS, quantity=500)), ("Gracias, sin domicilio", second)
    )

    # Assert: no «Con gusto…» con abstained=False, sino la abstención declarada.
    final = _final(events)
    assert final.mode_path == "agent"
    assert _tokens(events) == [NO_DATA_ANSWER]
    assert final.abstained is True
    assert final.sources == ()


# --- Resumen y memoria frente a la compuerta (spec 045, T029 y T041) -------------------------


def _memory_runner(
    steps: Sequence[ScriptStep], *, memory: Any = None, store: QueueStore | None = None
) -> tuple[ToolAgentRunner, Any]:
    from langgraph.store.memory import InMemoryStore

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    chat = ScriptedToolChat(steps=list(steps))
    deps = ToolDeps(vector_store=store or QueueStore(), embedder=FakeEmbedder(), settings=settings)
    graph = build_tool_agent(
        deps,
        chat,
        checkpointer=InMemorySaver(),
        store=memory if memory is not None else InMemoryStore(),
    )
    return ToolAgentRunner(graph, settings), graph


def _long_seed() -> Any:
    from carvajal.agent.memory.summary import SeedTurns

    turns: list[Turn] = [
        {"question": f"Tema {i}, cuéntame algo", "answer": f"Respuesta {i}.", "incomplete": False}
        for i in range(14)
    ]
    return SeedTurns(turns[-10:], turn_count=14)


def _with_summary(graph: Any, **values: Any) -> None:
    from carvajal.agent.memory.summary import ConversationSummary

    summary = ConversationSummary(
        covered_turns=6, method="llm", updated_at="2026-11-24T10:00:00+00:00", **values
    )
    graph.update_state(
        {"configurable": {"thread_id": "hilo-1"}}, {"conversation_summary": summary.to_value()}
    )


def test_una_cifra_dicha_por_la_persona_en_el_turno_2_respalda_el_turno_15() -> None:
    # Arrange: la cita literal de la persona (``user_quotes``) viaja en el resumen.
    reply = "Al principio me dijiste que necesitabas 2.000 vasos para tinto."
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text=reply),
    ]
    runner, graph = _memory_runner(steps)
    _with_summary(
        graph,
        text="La persona tiene una cafetería.",
        user_quotes=("Necesito 2.000 vasos para tinto en mi cafetería",),
    )

    # Act
    events = list(
        runner.stream("dueno-1", "hilo-1", "¿Cuántos vasos te dije al principio?", _long_seed())
    )

    # Assert
    assert _tokens(events) == [reply]
    assert _final(events).abstained is False


def test_una_cifra_que_solo_esta_en_el_texto_del_resumen_no_respalda() -> None:
    # Arrange: el texto del resumen lo escribe el modelo: no respalda nada (FR-006).
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="Me dijiste que necesitabas 3.500 vasos."),
    ]
    runner, graph = _memory_runner(steps)
    _with_summary(graph, text="La persona necesita 3.500 vasos.", user_quotes=("Hola",))

    # Act
    events = list(
        runner.stream("dueno-1", "hilo-1", "¿Cuántos vasos te dije al principio?", _long_seed())
    )

    # Assert
    assert _tokens(events) == [NO_DATA_ANSWER]
    assert _final(events).abstained is True


def test_las_preguntas_de_la_semilla_respaldan_lo_que_la_persona_dijo() -> None:
    # Arrange: R4: las palabras de la persona en turnos anteriores respaldan sus cifras.
    seed: list[Turn] = [
        {"question": "Necesito 1.200 platos", "answer": "Listo.", "incomplete": False}
    ]
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="Me dijiste 1.200 platos."),
    ]
    runner, _ = _memory_runner(steps)

    # Act
    events = list(runner.stream("dueno-1", "hilo-1", "¿Cuántos te dije?", seed))

    # Assert
    assert _tokens(events) == ["Me dijiste 1.200 platos."]


def _saved_memory() -> Any:
    from langgraph.store.memory import InMemoryStore

    from carvajal.agent.memory.store import remember

    memory = InMemoryStore()
    remember(memory, "dueno-1", "nombre", "Ana")
    remember(memory, "dueno-1", "empresa", "Panadería La Espiga")
    return memory


def test_en_un_turno_solo_de_memoria_sus_valores_respaldan_el_texto() -> None:
    # Arrange
    reply = "Te llamas Ana y trabajas en Panadería La Espiga."
    steps = [ScriptStep(tool_calls=[_call("listar_memoria")]), ScriptStep(text=reply)]
    runner, _ = _memory_runner(steps, memory=_saved_memory())

    # Act
    events = list(runner.stream("dueno-1", "hilo-1", "¿Qué sabes de mí?", []))

    # Assert
    assert _tokens(events) == [reply]
    assert _final(events).gate == "no_query"


def test_con_otra_consulta_los_valores_de_memoria_no_respaldan() -> None:
    # Arrange: además de la memoria, el turno pidió cotizar (sin datos suficientes).
    steps = [
        ScriptStep(tool_calls=[_call("listar_memoria"), _call("cotizar", family="vaso")]),
        ScriptStep(text="Ana, para Panadería La Espiga te cotizo en Pereira."),
    ]
    runner, _ = _memory_runner(steps, memory=_saved_memory())

    # Act
    events = list(runner.stream("dueno-1", "hilo-1", "¿Qué sabes de mí? y cotiza vasos", []))

    # Assert: el texto se reemplaza por la petición neutra del dato.
    text = "".join(_tokens(events))
    assert "Panadería La Espiga" not in text
    assert "Pereira" not in text


def test_en_evidence_una_nota_de_memoria_no_entra_en_lo_permitido() -> None:
    # Arrange
    from apps.api.chat.agent import _TurnStream
    from carvajal.agent.memory.store import MemoryProfile

    stream = _TurnStream("¿Cuándo se fundó Carvajal?")
    profile = MemoryProfile(values={"nombre": "Ana"}, notes=("Carvajal se fundó en 1850",))
    stream.values = {
        "memory": profile.snapshot(),
        "results": [
            {
                "tool": "buscar_corpus",
                "args": {},
                "status": "ok",
                "source_ids": [1],
                "elapsed_ms": 1,
            }
        ],
    }

    # Act
    allowed = " ".join(stream._allowed())

    # Assert
    assert "1850" not in allowed
    assert "Ana" in allowed  # el nombre sigue respaldando el saludo


def test_un_turno_solo_de_memoria_rechazado_recibe_la_lista_y_no_la_abstencion() -> None:
    # Arrange: el modelo inventa datos al listar la memoria.
    steps = [
        ScriptStep(tool_calls=[_call("listar_memoria")]),
        ScriptStep(text="Eres Ana, vives en Bogotá y tienes 3 locales."),
    ]
    runner, _ = _memory_runner(steps, memory=_saved_memory())

    # Act
    events = list(runner.stream("dueno-1", "hilo-1", "¿Qué sabes de mí?", []))

    # Assert
    text = "".join(_tokens(events))
    assert text.startswith("Esto es lo que recuerdo de ti:")
    assert "- Nombre: Ana" in text
    assert "- Empresa: Panadería La Espiga" in text
    assert "Bogotá" not in text
    assert text != NO_DATA_ANSWER
    assert _final(events).abstained is False


def test_un_guardado_rechazado_por_el_filtro_recibe_la_confirmacion() -> None:
    # Arrange
    from langgraph.store.memory import InMemoryStore

    steps = [
        ScriptStep(
            tool_calls=[_call("recordar_preferencia", categoria="empresa", valor="La Espiga")]
        ),
        ScriptStep(text="Listo, guardé La Espiga en Medellín con 20 empleados."),
    ]
    runner, _ = _memory_runner(steps, memory=InMemoryStore())

    # Act
    events = list(runner.stream("dueno-1", "hilo-1", "Trabajo en La Espiga", []))

    # Assert
    assert _tokens(events) == ["Listo, lo guardé."]


# --- Compuerta de afirmaciones con Jev (spec 046, T032 y T039) --------------------------------


def _claims_runner(
    steps: Sequence[ScriptStep],
    judge: Any,
    *,
    store: QueueStore | None = None,
    settings: Settings | None = None,
    clock: Any = None,
    route_judge: Any = None,
) -> ToolAgentRunner:
    settings = settings or Settings()
    deps = ToolDeps(vector_store=store or QueueStore(), embedder=FakeEmbedder(), settings=settings)
    chat = ScriptedToolChat(steps=list(steps))
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver(), judge=route_judge)
    kwargs: dict[str, Any] = {"claims_judge": judge}
    if clock is not None:
        kwargs["clock"] = clock
    return ToolAgentRunner(graph, settings, **kwargs)


def _evidence_steps(text: str = ANSWER) -> list[ScriptStep]:
    return [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
        ScriptStep(text=text),
    ]


def test_no_respaldada_no_emite_el_texto_del_modelo_y_emite_el_sustituto() -> None:
    # Arrange
    from tests.fakes.judge import FakeJudge

    judge = FakeJudge(p=0.2)
    runner = _claims_runner(_evidence_steps(), judge, store=QueueStore([FUNDACION]))

    # Act
    events = _stream(runner)

    # Assert
    tokens = _tokens(events)
    assert all("imprenta familiar" not in token for token in tokens)
    final = _final(events)
    assert final.answer == NO_CONTEXT_ANSWER
    assert final.abstained is True
    assert final.claims["status"] == "unsupported"
    assert final.claims["substituted"] is True
    assert final.claims["p"] == 0.2
    assert len(judge.claims) == 1


def test_respaldada_emite_el_texto_completo_en_un_bloque_tras_la_revision() -> None:
    # Arrange
    from tests.fakes.judge import FakeJudge

    from carvajal.agent.toolagent.status import REVIEW_STATUS

    judge = FakeJudge(p=0.8)
    runner = _claims_runner(_evidence_steps(), judge, store=QueueStore([FUNDACION]))

    # Act
    events = _stream(runner)

    # Assert
    assert _tokens(events) == [ANSWER]
    reviews = [e for e in events if isinstance(e, ToolAgentStatus) and e.text == REVIEW_STATUS]
    assert len(reviews) == 1
    assert events.index(reviews[0]) < next(
        i for i, e in enumerate(events) if isinstance(e, ToolAgentToken)
    )
    assert reviews[0].step == 2  # siguiente a la línea de ``buscar_corpus``
    final = _final(events)
    assert final.claims["status"] == "supported"
    assert final.claims["substituted"] is False
    assert final.claims["jev"]["kind"] == "claims"


@pytest.mark.parametrize("status", ["timeout", "error", "auth", "invalid", "cap", "circuit_open"])
def test_si_jev_falla_sale_el_texto_de_la_compuerta_determinista(status: str) -> None:
    from tests.fakes.judge import FakeJudge

    judge = FakeJudge(claims_fail=cast(Any, status))
    runner = _claims_runner(_evidence_steps(), judge, store=QueueStore([FUNDACION]))

    events = _stream(runner)

    assert "".join(_tokens(events)) == ANSWER
    assert _final(events).claims["status"] == "unavailable"
    assert _final(events).claims["reason"] == status


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("off", id="compuerta-off"),
        pytest.param("no-disponible", id="juez-no-disponible"),
        pytest.param("sin-juez", id="sin-juez"),
    ],
)
def test_compuerta_apagada_conserva_la_retencion_de_48_caracteres(case: str) -> None:
    # Arrange
    from tests.fakes.judge import FakeJudge

    judge: Any = FakeJudge(p=0.1, is_available=case != "no-disponible")
    settings = Settings(jev_claims_mode="off") if case == "off" else None
    if case == "sin-juez":
        judge = None
    runner = _claims_runner(
        _evidence_steps(), judge, store=QueueStore([FUNDACION]), settings=settings
    )

    # Act
    events = _stream(runner)

    # Assert: como en la 044, el texto sale en varios trozos y sin juzgar.
    tokens = _tokens(events)
    assert len(tokens) > 1
    assert "".join(tokens) == ANSWER
    final = _final(events)
    assert final.claims["status"] == "off"
    if judge is not None:
        assert judge.claims == []


def test_no_query_rechazado_por_el_filtro_no_llama_a_jev() -> None:
    from tests.fakes.judge import FakeJudge

    judge = FakeJudge(p=0.9)
    steps = [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="Carvajal factura 3 billones desde 1904."),
    ]
    runner = _claims_runner(steps, judge)

    events = _stream(runner, "Cuéntame algo")

    assert judge.claims == []
    assert _final(events).claims["status"] == "skipped"
    assert "3 billones" not in _final(events).answer


def test_el_atajo_por_reglas_no_llama_a_jev() -> None:
    from tests.fakes.judge import FakeJudge

    judge = FakeJudge(p=0.1)
    runner = _claims_runner([ScriptStep(text="¡Hola! Soy Carva.")], judge, route_judge=judge)

    events = _stream(runner, "Hola")

    assert judge.claims == [] and judge.routes == []
    assert _final(events).claims == {
        **_final(events).claims,
        "status": "skipped",
        "reason": "atajo",
    }


def test_la_respuesta_directa_de_jev_no_se_juzga() -> None:
    from tests.fakes.judge import FakeJudge

    judge = FakeJudge(choice="respuesta_directa", confidence=0.95, p=0.1)
    runner = _claims_runner(
        [ScriptStep(text="Soy el asistente virtual de Carva.")], judge, route_judge=judge
    )

    events = _stream(runner, "¿eres una persona o un bot?")

    final = _final(events)
    assert judge.claims == []
    assert (final.claims["status"], final.claims["reason"]) == ("skipped", "directa")


def test_needs_input_rechazado_pide_el_dato_sin_llamar_a_jev() -> None:
    from tests.fakes.judge import FakeJudge

    judge = FakeJudge(p=0.1)
    call = _call("buscar_sedes", ciudad="Yumbo planta de 1961 con 3.500 empleados")
    steps = [ScriptStep(tool_calls=[call]), ScriptStep(text="La planta de Yumbo abrió en 1961.")]
    runner = _claims_runner(steps, judge)

    events = _stream(runner, "Hola, ¿qué me cuentas?")

    assert judge.claims == []
    assert "ciudad" in _final(events).answer
    assert _final(events).claims["reason"] == "reemplazo"


class _Clock:
    def __init__(self, *values: float) -> None:
        self.values = list(values)
        self.t = values[0]

    def __call__(self) -> float:
        if self.values:
            self.t = self.values.pop(0)
        return self.t


def test_sin_plazo_para_la_compuerta_no_llama_y_emite_lo_determinista() -> None:
    # Arrange: al cerrar el paso quedan 0,5 s de 45 (< 0,8 + 0,2).
    from tests.fakes.judge import FakeJudge

    judge = FakeJudge(p=0.1)
    clock = _Clock(0.0, 44.5)
    runner = _claims_runner(_evidence_steps(), judge, store=QueueStore([FUNDACION]), clock=clock)

    # Act
    events = _stream(runner)

    # Assert: no es ``TurnTimeout``; sale el texto que pasó la compuerta determinista.
    assert judge.claims == []
    assert "".join(_tokens(events)) == ANSWER
    final = _final(events)
    assert (final.claims["status"], final.claims["reason"]) == ("unavailable", "sin_plazo")


def test_con_plazo_el_texto_se_marca_antes_de_la_llamada() -> None:
    # Arrange: la llamada a Noul «tarda» hasta pasar el plazo del turno; como el texto se
    # marcó antes, el plazo no descarta la respuesta ya redactada.
    from tests.fakes.judge import FakeJudge

    clock = _Clock(0.0, 10.0)

    class SlowJudge(FakeJudge):
        def check(self, query: Any) -> Any:
            clock.t = 50.0
            return super().check(query)

    judge = SlowJudge(p=0.9)
    runner = _claims_runner(_evidence_steps(), judge, store=QueueStore([FUNDACION]), clock=clock)

    # Act
    events = _stream(runner)

    # Assert
    assert "".join(_tokens(events)) == ANSWER
    assert _final(events).claims["status"] == "supported"


def test_llm_class_se_deriva_de_lo_consultado_cuando_decide_el_llm() -> None:
    runner = _claims_runner(_evidence_steps(), None, store=QueueStore([FUNDACION]))

    final = _final(_stream(runner))

    assert final.gatekeeper["source"] == "llm"
    assert final.gatekeeper["llm_class"] == "rag"


def test_sombra_juzga_sin_sustituir_ni_retener() -> None:
    from tests.fakes.judge import FakeJudge

    judge = FakeJudge(p=0.1)
    runner = _claims_runner(
        _evidence_steps(),
        judge,
        store=QueueStore([FUNDACION]),
        settings=Settings(jev_claims_mode="shadow"),
    )

    events = _stream(runner)

    assert len(_tokens(events)) > 1
    assert "".join(_tokens(events)) == ANSWER
    final = _final(events)
    assert (final.claims["status"], final.claims["substituted"]) == ("unsupported", False)
    assert final.claims["reason"] == "sombra"


def _no_query_steps() -> list[ScriptStep]:
    return [
        ScriptStep(tool_calls=[_call("sin_consulta", motivo="conversacion")]),
        ScriptStep(text="Con gusto te ayudo con lo que necesites."),
    ]


def test_sombra_lenta_en_no_query_no_pierde_la_respuesta() -> None:
    # Arrange: en sombra el texto de ``no_query`` sigue retenido mientras se juzga; la
    # llamada «tarda» hasta pasar el plazo del turno (FR-030).
    from tests.fakes.judge import FakeJudge

    clock = _Clock(0.0, 10.0)

    class SlowJudge(FakeJudge):
        def check(self, query: Any) -> Any:
            clock.t = 50.0
            return super().check(query)

    judge = SlowJudge(p=0.9)
    runner = _claims_runner(
        _no_query_steps(), judge, settings=Settings(jev_claims_mode="shadow"), clock=clock
    )

    # Act
    events = _stream(runner, "¿me ayudas?")

    # Assert: no es ``TurnTimeout``; sale la respuesta del modelo.
    assert len(judge.claims) == 1
    assert "".join(_tokens(events)) == "Con gusto te ayudo con lo que necesites."
    assert (_final(events).claims["status"], _final(events).claims["reason"]) == (
        "supported",
        "sombra",
    )


def test_sombra_sin_plazo_en_no_query_no_llama_y_emite() -> None:
    # Arrange: al cerrar el paso quedan 0,5 s de 45 (< 0,8 + 0,2).
    from tests.fakes.judge import FakeJudge

    judge = FakeJudge(p=0.1)
    runner = _claims_runner(
        _no_query_steps(),
        judge,
        settings=Settings(jev_claims_mode="shadow"),
        clock=_Clock(0.0, 44.5),
    )

    # Act
    events = _stream(runner, "¿me ayudas?")

    # Assert
    assert judge.claims == []
    assert "".join(_tokens(events)) == "Con gusto te ayudo con lo que necesites."
    final = _final(events)
    assert (final.claims["status"], final.claims["reason"]) == ("unavailable", "sin_plazo")
