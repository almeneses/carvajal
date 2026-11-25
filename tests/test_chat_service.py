"""Pruebas de integración del servicio de chat contra un Postgres real."""

from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any, cast

import pytest
from sqlalchemy import Engine, delete, func, select

from apps.api.chat.agent import AgentTurn
from apps.api.chat.service import ChatFailure, ChatService
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.agent.state import Turn
from carvajal.application.ask import NO_CONTEXT_ANSWER, Source
from carvajal.infrastructure.settings import Settings

pytestmark = pytest.mark.integration


def routes_settings() -> Settings:
    """Estas pruebas cubren el modo ``routes``; el valor por defecto es ``tools``."""

    return Settings(_env_file=None, agent_mode="routes")  # type: ignore[call-arg]


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


class FakeEmbedder:
    """Embedder mínimo; el servicio no lo usa directamente en estas pruebas."""

    def embed(self, text: str) -> list[float]:
        return [0.0] * 768


class FakeLLM:
    """LLM que no debería invocarse sin contexto."""

    def complete(self, *, system: str, prompt: str) -> str:
        raise AssertionError("El LLM no debería invocarse sin contexto.")

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        raise AssertionError("El LLM no debería invocarse sin contexto.")


class FakeAgent:
    """Runner determinista: fija la ruta y registra lo que el servicio guarda."""

    def __init__(self, turn: AgentTurn, error: Exception | None = None) -> None:
        self.turn = turn
        self.error = error
        self.prepares = 0
        self.records: list[dict[str, Any]] = []

    def prepare(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
    ) -> AgentTurn:
        self.prepares += 1
        if self.error is not None:
            raise self.error
        return self.turn

    def record(
        self,
        thread_id: str,
        *,
        question: str,
        answer: str,
        incomplete: bool,
        turns: Sequence[Turn],
    ) -> None:
        self.records.append(
            {
                "thread_id": thread_id,
                "question": question,
                "answer": answer,
                "incomplete": incomplete,
            }
        )


def no_context_agent() -> FakeAgent:
    return FakeAgent(
        AgentTurn(
            route="rag",
            intent="documental",
            prompt=None,
            system="sistema",
            sources=(),
            turns=(),
        )
    )


def _delete_owner(engine: Engine, owner_key: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            delete(tables.conversations).where(tables.conversations.c.owner_key == owner_key)
        )


def test_servicio_persiste_usuario_y_asistente_al_terminar_bien(engine: Engine) -> None:
    # Arrange
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        agent=no_context_agent(),
        settings=routes_settings(),
    )
    owner_key = "servicio-ok"

    try:
        # Act
        answer = service.ask(owner_key, "¿Dato del corpus?")
        history = list(service.history(owner_key))

        # Assert
        assert answer.text == NO_CONTEXT_ANSWER
        assert [message.role for message in history] == ["user", "assistant"]
        assert history[0].content == "¿Dato del corpus?"
        assert history[1].content == NO_CONTEXT_ANSWER
        assert history[1].sources == []
        assert history[1].status == "complete"
    finally:
        _delete_owner(engine, owner_key)


def test_stream_persiste_un_solo_turno_y_emite_trace(engine: Engine) -> None:
    # Arrange
    agent = no_context_agent()
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        agent=agent,
        settings=routes_settings(),
    )
    owner_key = "stream-ok"

    try:
        # Act
        events = list(service.stream(owner_key, "¿Dato del corpus?"))
        history = list(service.history(owner_key))

        # Assert
        assert [event.name for event in events] == ["open", "trace", "meta", "token", "done"]
        assert events[1].data["route"] == "rag"
        assert events[1].data["thread_id"] == history[0].conversation_id
        assert events[2].data == {"sources": []}
        assert events[3].data == {"text": NO_CONTEXT_ANSWER}
        done = events[4].data
        assert done["conversation_id"] == history[0].conversation_id
        assert done["message_id"] == history[1].id
        assert agent.records[0]["answer"] == NO_CONTEXT_ANSWER
    finally:
        _delete_owner(engine, owner_key)


class StreamingLLM:
    """Emite tres fragmentos y no debe llamar a ``complete``."""

    def __init__(self) -> None:
        self.stream_calls = 0
        self.complete_calls = 0
        self.prompt = ""

    def complete(self, *, system: str, prompt: str) -> str:
        self.complete_calls += 1
        raise AssertionError("No debe completarse un stream exitoso.")

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.stream_calls += 1
        self.prompt = prompt
        yield "Carvajal "
        yield "nació "
        yield "en 1904 [1]."


def rag_agent() -> FakeAgent:
    return FakeAgent(
        AgentTurn(
            route="rag",
            intent="documental",
            prompt="Contexto con [1].",
            system="sistema",
            sources=(SOURCE,),
            turns=(),
        )
    )


def test_stream_exitoso_emite_tokens_y_persiste_el_turno(engine: Engine) -> None:
    # Arrange
    llm = StreamingLLM()
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        llm,
        agent=rag_agent(),
        settings=routes_settings(),
    )
    owner_key = "stream-tokens"

    try:
        # Act
        events = list(service.stream(owner_key, "¿Cuándo nació?"))
        history = list(service.history(owner_key))

        # Assert
        assert [event.name for event in events] == [
            "open",
            "trace",
            "meta",
            "token",
            "token",
            "token",
            "done",
        ]
        assert events[2].data == {"sources": [asdict(SOURCE)]}
        assert [event.data["text"] for event in events[3:6]] == [
            "Carvajal ",
            "nació ",
            "en 1904 [1].",
        ]
        assert llm.stream_calls == 1
        assert llm.complete_calls == 0
        assert llm.prompt == "Contexto con [1]."
        assert len(history) == 2
        assert history[1].content == "Carvajal nació en 1904 [1]."
        assert history[1].sources == [asdict(SOURCE)]
    finally:
        _delete_owner(engine, owner_key)


def test_stream_comercial_emite_las_tarjetas_antes_de_los_tokens(engine: Engine) -> None:
    # Arrange: resultado mínimo de la ruta comercial (spec 036, T029).
    resultado = {
        "options": [
            {
                "sku": "SKU-1",
                "name": "Vaso de prueba",
                "material": "PAPEL",
                "units_per_box": 1000,
                "country": "MX",
                "source_url": "https://ejemplo.test/vaso",
                "captured_at": "2026-10-23",
                "price_per_box": "1450.00 MXN",
                "price_notice": "Precio de referencia.",
            }
        ],
        "channel": {},
    }
    agent = FakeAgent(
        AgentTurn(
            route="commercial",
            intent="commercial",
            prompt="Contexto con [1].",
            system="sistema",
            sources=(SOURCE,),
            turns=(),
            products=resultado,
        )
    )
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        StreamingLLM(),
        agent=agent,
        settings=routes_settings(),
    )
    owner_key = "stream-tarjetas"

    try:
        # Act
        events = list(service.stream(owner_key, "Necesito vasos para café en México"))

        # Assert
        nombres = [event.name for event in events]
        assert nombres[:4] == ["open", "trace", "meta", "products"]
        assert "<code>SKU-1</code>" in events[3].data["html"]
        assert nombres[-1] == "done"
    finally:
        _delete_owner(engine, owner_key)


class FailingStreamLLM:
    """El stream falla antes del primer token; ``complete`` entrega el respaldo."""

    def __init__(self) -> None:
        self.complete_calls = 0

    def complete(self, *, system: str, prompt: str) -> str:
        self.complete_calls += 1
        return "Respuesta completa de respaldo [1]."

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        raise RuntimeError("el stream se cayó")
        yield ""  # pragma: no cover


def test_stream_falla_antes_del_primer_token_y_persiste_el_fallback(engine: Engine) -> None:
    # Arrange
    llm = FailingStreamLLM()
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        llm,
        agent=rag_agent(),
        settings=routes_settings(),
    )
    owner_key = "stream-fallback"

    try:
        # Act
        events = list(service.stream(owner_key, "¿Dato del corpus?"))
        history = list(service.history(owner_key))

        # Assert
        assert [event.name for event in events] == ["open", "trace", "meta", "fallback", "done"]
        assert events[3].data == {
            "text": "Respuesta completa de respaldo [1].",
            "sources": [asdict(SOURCE)],
        }
        assert llm.complete_calls == 1
        assert history[1].content == "Respuesta completa de respaldo [1]."
    finally:
        _delete_owner(engine, owner_key)


class BrokenAfterTokenLLM:
    """Falla después de entregar el primer fragmento."""

    def complete(self, *, system: str, prompt: str) -> str:
        raise AssertionError("No debe completarse una respuesta ya empezada.")

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        yield "Carvajal"
        raise RuntimeError("corte a mitad del stream")


def test_stream_falla_tras_un_token_persiste_incompleto(engine: Engine) -> None:
    # Arrange
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        BrokenAfterTokenLLM(),
        agent=rag_agent(),
        settings=routes_settings(),
    )
    owner_key = "stream-incompleto"

    try:
        # Act
        events = list(service.stream(owner_key, "¿Dato del corpus?"))
        history = list(service.history(owner_key))

        # Assert
        assert [event.name for event in events] == [
            "open",
            "trace",
            "meta",
            "token",
            "error",
        ]
        assert events[-1].data["incomplete"] is True
        assert [message.role for message in history] == ["user", "assistant"]
        assert history[1].content == "Carvajal"
        assert history[1].status == "incomplete"
    finally:
        _delete_owner(engine, owner_key)


def test_servicio_no_crea_hilo_si_la_preparacion_falla(engine: Engine) -> None:
    # Arrange
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        agent=FakeAgent(
            AgentTurn("rag", "", None, "", (), ()),
            error=EmbeddingError("Ollama no responde."),
        ),
        settings=routes_settings(),
    )
    owner_key = "servicio-fallo"

    # Act / Assert
    with pytest.raises(ChatFailure):
        service.ask(owner_key, "¿Dato del corpus?")
    assert list(service.history(owner_key)) == []


def test_reintento_con_el_mismo_turno_no_duplica_ni_vuelve_al_llm(engine: Engine) -> None:
    # Arrange
    agent = rag_agent()
    llm = StreamingLLM()
    service = ChatService(
        build_session_factory(engine), FakeEmbedder(), llm, agent=agent, settings=routes_settings()
    )
    owner_key = "stream-idempotente"

    try:
        # Act
        primero = list(service.stream(owner_key, "¿Cuándo nació?", client_turn_id="turno-1"))
        segundo = list(service.stream(owner_key, "¿Cuándo nació?", client_turn_id="turno-1"))
        history = list(service.history(owner_key))

        # Assert
        assert primero[-1].name == "done"
        assert segundo[1].data["route"] == "replay"
        assert segundo[-1].name == "done"
        assert len(history) == 2
        assert agent.prepares == 1
        assert llm.stream_calls == 1
    finally:
        _delete_owner(engine, owner_key)


def test_un_hilo_ajeno_no_se_puede_usar(engine: Engine) -> None:
    # Arrange
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        agent=no_context_agent(),
        settings=routes_settings(),
    )
    dueno = "dueno-a"
    intruso = "dueno-b"

    try:
        hilo = service.new_conversation(dueno)
        # Act
        events = list(service.stream(intruso, "¿Dato?", thread_id=hilo.id))

        # Assert
        assert events[-1].name == "error"
        assert events[-1].data["incomplete"] is False
        assert list(service.history(intruso)) == []
    finally:
        _delete_owner(engine, dueno)
        _delete_owner(engine, intruso)


class ConnectionCheckingLLM:
    """Falla si el pool sigue ocupado cuando el LLM responde."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.calls = 0

    def complete(self, *, system: str, prompt: str) -> str:
        raise AssertionError("El stream no se usa en este caso.")

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.calls += 1
        assert cast(Any, self._engine.pool).checkedout() == 0
        yield "Carvajal nació en 1904 [1]."


def test_el_stream_libera_la_conexion_antes_de_llamar_al_llm(engine: Engine) -> None:
    # Arrange
    llm = ConnectionCheckingLLM(engine)
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        llm,
        agent=rag_agent(),
        settings=routes_settings(),
    )
    owner_key = "stream-sin-transaccion"

    try:
        # Act
        list(service.stream(owner_key, "¿Cuándo nació?"))

        # Assert
        assert llm.calls == 1
        assert len(service.history(owner_key)) == 2
    finally:
        _delete_owner(engine, owner_key)


def test_el_primer_envio_concurrente_comparte_un_solo_hilo(engine: Engine) -> None:
    """Dos peticiones sin hilo no abren dos conversaciones para el mismo dueño."""

    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        agent=no_context_agent(),
        settings=routes_settings(),
    )
    owner_key = "primer-hilo-concurrente"

    def enviar(turn_id: str) -> list[str]:
        return [event.name for event in service.stream(owner_key, "¿Dato?", client_turn_id=turn_id)]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(enviar, ["turno-a", "turno-b"]))

        assert all(nombres[-1] == "done" for nombres in resultados)
        with engine.connect() as connection:
            conversaciones = connection.execute(
                select(func.count())
                .select_from(tables.conversations)
                .where(tables.conversations.c.owner_key == owner_key)
            ).scalar_one()
        assert conversaciones == 1
        assert len(list(service.history(owner_key))) == 4
    finally:
        _delete_owner(engine, owner_key)


def test_dos_reenvios_simultaneos_del_mismo_turno_persisten_uno(engine: Engine) -> None:
    """El mismo ``client_turn_id`` concurrente no duplica mensajes."""

    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        agent=no_context_agent(),
        settings=routes_settings(),
    )
    owner_key = "mismo-turno-concurrente"

    def enviar(_: int) -> list[str]:
        return [
            event.name for event in service.stream(owner_key, "¿Dato?", client_turn_id="turno-1")
        ]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(enviar, [0, 1]))

        assert all(nombres[-1] in {"done", "error"} for nombres in resultados)
        assert len([item for item in service.history(owner_key) if item.role == "user"]) == 1
    finally:
        _delete_owner(engine, owner_key)


def _quote_payload() -> dict[str, Any]:
    return {
        "kind": "cotizacion",
        "version": 2,
        "city": "Bogotá",
        "reply": "Son 3.751 COP.",
        "primary": {
            "sku": "7704918100199",
            "name": "Vaso rígido",
            "currency": "COP",
            "units_requested": 20,
            "presentation": {
                "basis": "paquete",
                "units": 50,
                "count": 1,
                "units_bought": 50,
                "units_surplus": 30,
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


def _commercial_turn() -> AgentTurn:
    return AgentTurn(
        route="commercial",
        intent="commercial",
        prompt="Contexto comercial.",
        system="sistema",
        sources=(SOURCE,),
        turns=(),
        products={
            "ficha": {
                "sku": "7704918100199",
                "name": "Vaso rígido",
                "material": "PP",
                "units_per_box": 2750,
                "source_url": "https://example.com/ficha",
                "captured_at": "2026-11-18",
            },
            "quote": _quote_payload(),
            "cities": ["Bogotá"],
        },
    )


def test_el_turno_comercial_guarda_cotizacion_y_resultado_junto(engine: Engine) -> None:
    """Mensajes, resultado y cotización se confirman en una transacción (al cerrar con «sí»)."""

    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        StreamingLLM(),
        agent=FakeAgent(_commercial_turn()),
        settings=routes_settings(),
    )
    owner_key = "comercial-cotiza"

    try:
        proposal = list(service.stream(owner_key, "cotiza 20 vasos", client_turn_id="coti-1"))
        assert proposal[-1].data["quote_id"] is None
        events = list(service.stream(owner_key, "No, confírmala", client_turn_id="coti-1-si"))
        done = events[-1].data
        folio = done["quote_id"]

        with engine.connect() as connection:
            filas = (
                connection.execute(
                    select(tables.quotes).where(tables.quotes.c.owner_key == owner_key)
                )
                .mappings()
                .all()
            )
        history = list(service.history(owner_key))

        assert folio and len(filas) == 1
        assert filas[0]["id"] == folio
        assert filas[0]["conversation_id"] == history[0].conversation_id
        fuentes = history[3].sources or []
        assert fuentes[-1]["result_type"] == "commercial"
        assert fuentes[-1]["result"]["quote"]["quote"]["id"] == folio

        replay = list(service.stream(owner_key, "No, confírmala", client_turn_id="coti-1-si"))
        assert [event.name for event in replay[1:5]] == ["trace", "meta", "products", "token"]
        assert replay[-1].data["quote_id"] == folio
    finally:
        _delete_owner(engine, owner_key)


def test_si_falla_la_cotizacion_no_quedan_mensajes(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La cotización y el turno comparten transacción; sin una, no queda el otro."""

    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        StreamingLLM(),
        agent=FakeAgent(_commercial_turn()),
        settings=routes_settings(),
    )
    owner_key = "comercial-rollback"

    def fallar(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("no se pudo guardar la cotización")

    monkeypatch.setattr("apps.api.chat.service._record_quote", fallar)

    try:
        list(service.stream(owner_key, "cotiza 20 vasos", client_turn_id="coti-2"))
        events = list(service.stream(owner_key, "No, confírmala", client_turn_id="coti-2-si"))

        assert events[-1].name == "error"
        assert len(list(service.history(owner_key))) == 2  # solo el turno del carrito
        with engine.connect() as connection:
            assert (
                connection.execute(
                    select(func.count())
                    .select_from(tables.quotes)
                    .where(tables.quotes.c.owner_key == owner_key)
                ).scalar_one()
                == 0
            )
    finally:
        _delete_owner(engine, owner_key)


def test_el_mismo_turno_html_no_duplica_ni_vuelve_al_llm(engine: Engine) -> None:
    """El POST HTML propaga ``client_turn_id`` y reutiliza el turno ya guardado."""

    agent = rag_agent()
    llm = StreamingLLM()
    service = ChatService(
        build_session_factory(engine), FakeEmbedder(), llm, agent=agent, settings=routes_settings()
    )
    owner_key = "html-idempotente"

    try:
        service.ask(owner_key, "¿Cuándo nació?", client_turn_id="html-1")
        service.ask(owner_key, "¿Cuándo nació?", client_turn_id="html-1")

        assert agent.prepares == 1
        assert llm.stream_calls == 1
        assert len(list(service.history(owner_key))) == 2
    finally:
        _delete_owner(engine, owner_key)
