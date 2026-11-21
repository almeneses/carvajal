"""Pruebas de caracterización de la spec 043: comportamiento que no puede cambiar.

Se escriben **antes** de retirar `_safe_rollback`, el embedder sin uso de
``ChatService`` y la configuración no usada de ``GraphAgentRunner``. Fijan el
contrato observable del servicio de chat, del runner del agente y de la CLI
histórica, de modo que la refactorización se demuestre como cambio sin
comportamiento nuevo.

Las pruebas se escribieron y se ejecutaron verdes en ``origin/dev`` antes de la
refactorización; en la rama solo cambia la construcción del servicio (sin el
embedder retirado), no las aserciones de comportamiento.
"""

from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apps.api import agent_runtime as agent_runtime_module
from apps.api import runtime as runtime_module
from apps.api.chat.agent import AgentRunner, AgentTurn, GraphAgentRunner, default_agent_runner
from apps.api.chat.service import ChatService, default_chat_service
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.agent.state import Turn
from carvajal.application.ask import NO_CONTEXT_ANSWER, Answer, Source
from carvajal.cli import build_parser, main
from carvajal.infrastructure.settings import Settings

PREGUNTA = "¿En qué ciudad fue fundada Carvajal?"

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


class FakeLLM:
    """LLM de doble que emite los fragmentos indicados."""

    def __init__(self, fragmentos: Sequence[str] = ("Hola, ", "soy Carvajal.")) -> None:
        self.fragmentos = fragmentos
        self.llamadas = 0

    def complete(self, *, system: str, prompt: str) -> str:
        self.llamadas += 1
        return "".join(self.fragmentos)

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.llamadas += 1
        yield from self.fragmentos


class FakeAgent:
    """Runner determinista con el contrato ``AgentRunner``."""

    def __init__(self, turn: AgentTurn) -> None:
        self.turn = turn
        self.prepares = 0
        self.records: list[dict[str, Any]] = []

    def prepare(
        self, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Turn]
    ) -> AgentTurn:
        self.prepares += 1
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


class GrafoFalso:
    """Grafo doble que captura la invocación y las actualizaciones de estado."""

    def __init__(
        self,
        estado: dict[str, Any] | None = None,
        *,
        error_update: Exception | None = None,
    ) -> None:
        self.estado = estado if estado is not None else {}
        self.error_update = error_update
        self.invocaciones: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.actualizaciones: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def invoke(self, entrada: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.invocaciones.append((entrada, config))
        return self.estado

    def update_state(self, config: dict[str, Any], valores: dict[str, Any]) -> None:
        if self.error_update is not None:
            raise self.error_update
        self.actualizaciones.append((config, valores))


def _ajustes() -> Settings:
    """Configuración determinista: sin monitoreo ni lectura de ``.env``."""

    return Settings(_env_file=None, monitoring_enabled=False, langsmith_api_key="")


def _nuevo_servicio(factory: Any, llm: Any, agent: Any) -> ChatService:
    """Construye ``ChatService`` con los dobles y la configuración determinista."""

    return ChatService(factory, llm, agent=agent, settings=_ajustes())


@pytest.fixture()
def fabrica() -> Iterator[Any]:
    """Fábrica de sesiones sobre SQLite en memoria con el esquema creado."""

    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    try:
        yield build_session_factory(motor)
    finally:
        motor.dispose()


def _agente_sin_contexto() -> FakeAgent:
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


# --- Contrato de ChatService -------------------------------------------------


def test_stream_emite_los_eventos_del_contrato_y_persiste_el_turno(fabrica: Any) -> None:
    # Arrange
    servicio = _nuevo_servicio(fabrica, FakeLLM(), _agente_sin_contexto())

    # Act
    eventos = list(servicio.stream("dueno-caracterizacion", PREGUNTA))

    # Assert
    assert [evento.name for evento in eventos] == ["open", "trace", "meta", "token", "done"]
    assert eventos[3].data == {"text": NO_CONTEXT_ANSWER}
    assert eventos[4].data["route"] == "rag"
    assert eventos[4].data["conversation_id"] == eventos[4].data["thread_id"]
    historial = list(servicio.history("dueno-caracterizacion"))
    assert [(m.role, m.status) for m in historial] == [
        ("user", "complete"),
        ("assistant", "complete"),
    ]
    assert historial[1].content == NO_CONTEXT_ANSWER


def test_ask_recompone_el_texto_y_las_citas(fabrica: Any) -> None:
    # Arrange
    agente = FakeAgent(
        AgentTurn(
            route="rag",
            intent="documental",
            prompt="contexto",
            system="sistema",
            sources=(SOURCE,),
            turns=(),
        )
    )
    servicio = _nuevo_servicio(fabrica, FakeLLM(("Hola, ", "soy Carvajal.")), agente)

    # Act
    respuesta = servicio.ask("dueno-ask", PREGUNTA)

    # Assert
    assert respuesta == Answer(text="Hola, soy Carvajal.", sources=(SOURCE,))


def test_el_servicio_expone_su_contrato_publico(fabrica: Any) -> None:
    # Act
    servicio = _nuevo_servicio(fabrica, FakeLLM(), _agente_sin_contexto())

    # Assert
    for nombre in ("history", "conversations", "ask", "stream", "new_conversation"):
        assert callable(getattr(servicio, nombre))
    hilo = servicio.new_conversation("dueno-contrato")
    assert hilo.owner_key == "dueno-contrato"


def test_default_chat_service_es_cacheado_y_difiere_los_clientes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: la fábrica y los clientes se sustituyen por dobles.
    llamadas: list[int] = []

    def componentes_falsos() -> tuple[object, object]:
        llamadas.append(1)
        return object(), object()

    monkeypatch.setattr(runtime_module, "session_factory", lambda: object())
    monkeypatch.setattr(runtime_module, "ask_components", componentes_falsos)
    default_chat_service.cache_clear()

    try:
        # Act
        primero = default_chat_service()
        segundo = default_chat_service()
    finally:
        default_chat_service.cache_clear()

    # Assert
    assert isinstance(primero, ChatService)
    assert primero is segundo
    assert llamadas == []


# --- Contrato de GraphAgentRunner -------------------------------------------


def _grafo_con_turno() -> GrafoFalso:
    return GrafoFalso(
        {
            "route": "rag",
            "intent": "documental",
            "prompt": "contexto",
            "system": "sistema",
            "sources": [
                {
                    "chunk_id": "historia-1",
                    "slug": "historia",
                    "section": "historia",
                    "layer": "core",
                    "score": 0.9,
                    "excerpt": "Inició en 1904.",
                    "url": "https://www.carvajal.com/historia/",
                    "title": "Historia",
                    "source_type": "oficial",
                    "origin": "",
                    "category": "",
                    "entity": "",
                }
            ],
            "turns": [{"question": PREGUNTA, "answer": "Cali.", "incomplete": False}],
            "preferences": ["prefiere respuestas breves"],
            "tool_name": "indicator_lookup",
            "tool_period": "2024",
            "reason": "hay indicadores",
            "query": "fundación de Carvajal",
        }
    )


def _runner_desde_grafo(monkeypatch: pytest.MonkeyPatch, grafo: GrafoFalso) -> GraphAgentRunner:
    monkeypatch.setattr(agent_runtime_module, "agent_runtime", lambda: SimpleNamespace(graph=grafo))
    return default_agent_runner()


def test_default_agent_runner_devuelve_el_runner_del_contrato(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Act
    runner = _runner_desde_grafo(monkeypatch, _grafo_con_turno())

    # Assert
    assert isinstance(runner, GraphAgentRunner)
    assert isinstance(runner, AgentRunner)


def test_prepare_conserva_los_campos_de_agent_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    grafo = _grafo_con_turno()
    runner = _runner_desde_grafo(monkeypatch, grafo)

    # Act
    turno = runner.prepare("dueno-1", "hilo-1", PREGUNTA, [])

    # Assert
    assert turno.route == "rag"
    assert turno.intent == "documental"
    assert turno.prompt == "contexto"
    assert turno.system == "sistema"
    assert turno.sources == (SOURCE,)
    assert turno.turns == ({"question": PREGUNTA, "answer": "Cali.", "incomplete": False},)
    assert turno.preferences == ("prefiere respuestas breves",)
    assert turno.tool == "indicator_lookup"
    assert turno.tool_period == "2024"
    assert turno.reason == "hay indicadores"
    assert turno.query == "fundación de Carvajal"
    assert turno.products == {}
    entrada, config = grafo.invocaciones[0]
    assert entrada["question"] == PREGUNTA
    assert config == {"configurable": {"thread_id": "hilo-1"}}


def test_record_añade_el_turno_sin_duplicar_los_previos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: primer hilo sin turnos previos.
    grafo = _grafo_con_turno()
    runner = _runner_desde_grafo(monkeypatch, grafo)

    # Act
    runner.record(
        "hilo-1",
        question=PREGUNTA,
        answer="Cali.",
        incomplete=False,
        turns=(),
    )

    # Assert: el turno nuevo se añade al estado.
    assert len(grafo.actualizaciones) == 1
    _, valores = grafo.actualizaciones[0]
    assert valores["turns"] == [{"question": PREGUNTA, "answer": "Cali.", "incomplete": False}]
    assert valores["answer"] == "Cali."
    assert valores["incomplete"] is False
    assert grafo.actualizaciones[0][0] == {"configurable": {"thread_id": "hilo-1"}}

    # Arrange: el mismo turno ya viene en la secuencia previa.
    ya_presente: Turn = {"question": PREGUNTA, "answer": "Cali.", "incomplete": False}
    otra_grafo = GrafoFalso({"turns": [ya_presente]})
    otro_runner = _runner_desde_grafo(monkeypatch, otra_grafo)

    # Act
    otro_runner.record(
        "hilo-1",
        question=PREGUNTA,
        answer="Cali.",
        incomplete=False,
        turns=(ya_presente,),
    )

    # Assert: no se duplica.
    assert len(otra_grafo.actualizaciones) == 1
    assert otra_grafo.actualizaciones[0][1]["turns"] == [ya_presente]


def test_record_no_rompe_la_respuesta_si_el_checkpointer_falla(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    grafo = GrafoFalso(error_update=RuntimeError("checkpointer caído"))
    runner = _runner_desde_grafo(monkeypatch, grafo)

    # Act / Assert: el fallo se registra y no se propaga.
    runner.record(
        "hilo-1",
        question=PREGUNTA,
        answer="Cali.",
        incomplete=False,
        turns=(),
    )


# --- Contrato de la CLI histórica -------------------------------------------


def test_el_parser_de_la_cli_conserva_su_contrato() -> None:
    # Act
    parser = build_parser()

    # Assert
    assert parser.prog == "carvajal"
    assert parser.description == "Pipeline de captura y curaduría del corpus web de Carvajal."
    pregunta = parser.parse_args(["ask", PREGUNTA])
    assert pregunta.command == "ask"
    assert pregunta.question == PREGUNTA
    faq = parser.parse_args(["faq"])
    assert faq.command == "faq"
    assert faq.output == Path("docs") / "faq.md"


@pytest.mark.parametrize(
    ("subcomando", "texto"),
    [
        ("ask", "Pregunta del usuario."),
        ("faq", "Archivo Markdown donde se escribe la FAQ."),
    ],
)
def test_la_ayuda_de_los_subcomandos_sale_con_cero(
    subcomando: str,
    texto: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Act
    with pytest.raises(SystemExit) as salida:
        main([subcomando, "--help"])

    # Assert
    assert salida.value.code == 0
    assert texto in capsys.readouterr().out


def test_la_cli_historica_importa_su_modulo_de_rag() -> None:
    # Act: el módulo histórico solo se importa dentro de ``main``.
    from carvajal import rag

    # Assert
    for nombre in ("ask", "build_retriever", "build_system_prompt", "generate_faq", "summarize"):
        assert callable(getattr(rag, nombre))


def test_la_configuracion_lee_el_archivo_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    archivo = tmp_path / ".env"
    archivo.write_text("OLLAMA_MODEL=modelo-desde-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    # Act
    ajustes = Settings(_env_file=archivo)

    # Assert
    assert ajustes.ollama_model == "modelo-desde-dotenv"
