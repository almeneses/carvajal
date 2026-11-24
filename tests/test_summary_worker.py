"""``SummaryWorker``: resumen en segundo plano tras persistir el turno (spec 045, T026).

SQLite en memoria con el esquema real, ``InMemoryStore`` y un resumidor falso: sin red ni
LLM. Cubren FR-003 (``submit`` no bloquea), la cola acotada, el plazo vencido, el Store
caído, la idempotencia, la lápida de «olvidar todo» y la degradación de ``llm`` sin
DeepSeek.
"""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from apps.api.chat.summary_worker import SummaryWorker
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.chat import SqlChatRepository
from carvajal.adapters.storage.database import build_session_factory
from carvajal.agent.memory.summary import TOMBSTONE_KEY, ConversationSummary, summary_namespace
from carvajal.infrastructure.settings import Settings

OWNER = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"


@pytest.fixture()
def session_factory() -> Iterator[Any]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        connection.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    try:
        yield build_session_factory(engine)
    finally:
        tables.metadata.drop_all(engine)
        engine.dispose()


def _conversation(session_factory: Any, turns: int, start: int = 0) -> str:
    with session_factory() as session:
        repository = SqlChatRepository(session)
        conversation = repository.create(OWNER)
        for index in range(start, start + turns):
            repository.append_turn(
                conversation.id, question=f"pregunta {index}", answer=f"respuesta {index}"
            )
        session.commit()
        return conversation.id


def _append(session_factory: Any, conversation_id: str, turns: int, start: int) -> None:
    with session_factory() as session:
        repository = SqlChatRepository(session)
        for index in range(start, start + turns):
            repository.append_turn(
                conversation_id, question=f"pregunta {index}", answer=f"respuesta {index}"
            )
        session.commit()


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, agent_history_turns=8, **overrides)  # type: ignore[call-arg]


class FakeSummarizer:
    """Resumidor falso: espera a ``release`` (si se da) y responde un texto fijo."""

    def __init__(self, reply: str = "Resumen del modelo.", *, release: Any = None) -> None:
        self.reply = reply
        self.release = release
        self.calls = 0
        self.started = threading.Event()
        self.on_call: Any = None

    def invoke(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        self.started.set()
        if self.on_call is not None:
            self.on_call()
        if self.release is not None:
            self.release.wait(timeout=5)
        return AIMessage(content=self.reply)


def _draft(store: Any, conversation_id: str) -> ConversationSummary | None:
    item = store.get(summary_namespace(OWNER), conversation_id)
    return ConversationSummary.from_value(item.value) if item is not None else None


def test_submit_no_bloquea_aunque_el_resumidor_tarde(session_factory: Any) -> None:
    # Arrange: el resumidor no responde hasta que se le libera.
    release = threading.Event()
    model = FakeSummarizer(release=release)
    store = InMemoryStore()
    conversation = _conversation(session_factory, 10)
    worker = SummaryWorker(
        session_factory, _settings(agent_summary_mode="llm"), store=store, model=model
    )

    # Act
    started = time.perf_counter()
    accepted = worker.submit(OWNER, conversation)
    elapsed = time.perf_counter() - started

    # Assert
    assert accepted
    assert elapsed < 0.05
    assert model.started.wait(timeout=5)
    release.set()
    worker.close(wait=True)
    summary = _draft(store, conversation)
    assert summary is not None
    assert summary.method == "llm"
    assert summary.covered_turns == 2


def test_la_cola_llena_descarta_y_lo_registra(session_factory: Any) -> None:
    # Arrange
    release = threading.Event()
    model = FakeSummarizer(release=release)
    conversation = _conversation(session_factory, 10)
    worker = SummaryWorker(
        session_factory,
        _settings(agent_summary_mode="llm"),
        store=InMemoryStore(),
        model=model,
        max_queue=1,
    )

    # Act
    with capture_logs() as logs:
        first = worker.submit(OWNER, conversation)
        second = worker.submit(OWNER, conversation)
    release.set()
    worker.close(wait=True)

    # Assert
    assert first and not second
    dropped = [log for log in logs if log["event"] == "agent.summary_dropped"]
    assert len(dropped) == 1
    assert "pregunta" not in str(logs)


def test_el_plazo_vencido_usa_el_determinista(session_factory: Any) -> None:
    # Arrange
    release = threading.Event()
    store = InMemoryStore()
    conversation = _conversation(session_factory, 10)
    worker = SummaryWorker(
        session_factory,
        _settings(agent_summary_mode="llm"),
        store=store,
        model=FakeSummarizer(release=release),
        timeout_s=0.05,
    )

    # Act
    with capture_logs() as logs:
        summary = worker.summarize_now(OWNER, conversation)
    release.set()

    # Assert
    assert summary is not None
    assert summary.method == "deterministic"
    assert summary.text.splitlines()[0] == "Turno 1 — la persona: «pregunta 0»"
    failed = [log for log in logs if log["event"] == "agent.summary_failed"]
    assert failed and failed[0]["cause"] == "timeout"


class BrokenStore(InMemoryStore):
    def get(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("store caído")


def test_un_store_caido_solo_deja_una_advertencia(session_factory: Any) -> None:
    # Arrange
    conversation = _conversation(session_factory, 10)
    worker = SummaryWorker(session_factory, _settings(), store=BrokenStore())

    # Act
    with capture_logs() as logs:
        summary = worker.summarize_now(OWNER, conversation)

    # Assert
    assert summary is None
    assert any(log["event"] == "agent.summary_store_failed" for log in logs)


def test_un_submit_repetido_no_llama_otra_vez_al_modelo(session_factory: Any) -> None:
    # Arrange
    model = FakeSummarizer()
    store = InMemoryStore()
    conversation = _conversation(session_factory, 10)
    worker = SummaryWorker(
        session_factory, _settings(agent_summary_mode="llm"), store=store, model=model
    )

    # Act
    first = worker.summarize_now(OWNER, conversation)
    second = worker.summarize_now(OWNER, conversation)

    # Assert
    assert model.calls == 1
    assert first == second


def test_sin_turnos_fuera_de_la_ventana_no_hay_trabajo(session_factory: Any) -> None:
    # Arrange
    model = FakeSummarizer()
    store = InMemoryStore()
    conversation = _conversation(session_factory, 8)
    worker = SummaryWorker(
        session_factory, _settings(agent_summary_mode="llm"), store=store, model=model
    )

    # Act
    summary = worker.summarize_now(OWNER, conversation)

    # Assert
    assert summary is None
    assert model.calls == 0
    assert _draft(store, conversation) is None


def test_el_resumen_avanza_de_forma_incremental(session_factory: Any) -> None:
    # Arrange
    store = InMemoryStore()
    conversation = _conversation(session_factory, 10)
    worker = SummaryWorker(session_factory, _settings(), store=store)
    worker.summarize_now(OWNER, conversation)
    _append(session_factory, conversation, 3, start=10)

    # Act
    summary = worker.summarize_now(OWNER, conversation)

    # Assert
    assert summary is not None
    assert summary.covered_turns == 5
    assert summary.text.splitlines()[-1] == "Turno 5 — la persona: «pregunta 4»"
    assert summary.last_message_id


def test_una_lapida_posterior_a_la_lectura_descarta_el_resultado(session_factory: Any) -> None:
    # Arrange: «olvidar todo» llega mientras el resumidor espera al modelo.
    store = InMemoryStore()
    model = FakeSummarizer()
    model.on_call = lambda: store.put(
        summary_namespace(OWNER), TOMBSTONE_KEY, {"v": 1, "at": datetime.now(UTC).isoformat()}
    )
    conversation = _conversation(session_factory, 10)
    worker = SummaryWorker(
        session_factory, _settings(agent_summary_mode="llm"), store=store, model=model
    )

    # Act
    with capture_logs() as logs:
        summary = worker.summarize_now(OWNER, conversation)

    # Assert
    assert summary is None
    assert _draft(store, conversation) is None
    assert any(log["event"] == "agent.summary_discarded" for log in logs)


def test_los_turnos_anteriores_a_la_lapida_no_se_resumen(session_factory: Any) -> None:
    # Arrange: tres turnos, «olvidar todo», y diez turnos más.
    store = InMemoryStore()
    conversation = _conversation(session_factory, 3)
    time.sleep(0.01)
    store.put(
        summary_namespace(OWNER), TOMBSTONE_KEY, {"v": 1, "at": datetime.now(UTC).isoformat()}
    )
    time.sleep(0.01)
    _append(session_factory, conversation, 10, start=3)
    worker = SummaryWorker(session_factory, _settings(), store=store)

    # Act
    summary = worker.summarize_now(OWNER, conversation)

    # Assert: 13 turnos, ventana 8 → cubre 5; los 3 primeros quedaron olvidados.
    assert summary is not None
    assert summary.forgotten_through == 3
    assert summary.covered_turns == 5
    assert "pregunta 0" not in summary.text
    assert summary.text.splitlines() == [
        "Turno 4 — la persona: «pregunta 3»",
        "Turno 5 — la persona: «pregunta 4»",
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"llm_provider": "ollama"}, id="ollama"),
        pytest.param({"llm_provider": "deepseek", "deepseek_api_key": ""}, id="sin-clave"),
        pytest.param(
            {"llm_provider": "deepseek", "deepseek_api_key": "k", "agent_mode": "routes"},
            id="routes",
        ),
    ],
)
def test_llm_sin_deepseek_se_degrada_a_determinista(
    session_factory: Any, overrides: dict[str, Any]
) -> None:
    # Act
    with capture_logs() as logs:
        worker = SummaryWorker(
            session_factory,
            _settings(agent_summary_mode="llm", **overrides),
            store=InMemoryStore(),
        )

    # Assert
    assert worker.mode == "deterministic"
    assert any(log["event"] == "agent.summary_mode_degraded" for log in logs)


def test_con_el_modo_off_no_se_encola_nada(session_factory: Any) -> None:
    # Arrange
    conversation = _conversation(session_factory, 10)
    store = InMemoryStore()
    worker = SummaryWorker(session_factory, _settings(agent_summary_mode="off"), store=store)

    # Act
    accepted = worker.submit(OWNER, conversation)
    worker.close(wait=True)

    # Assert
    assert not accepted
    assert _draft(store, conversation) is None
