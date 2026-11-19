"""Pruebas de la traza del turno de chat (spec 038, US1 y US2).

El servicio real se ejecuta contra SQLite en memoria, sin Postgres ni red: el
tracer se inyecta como doble y, cuando hace falta el camino real de LangSmith,
se usa un cliente falso que captura los runs enviados.
"""

from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from time import perf_counter, sleep
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from apps.api.chat.agent import AgentTurn
from apps.api.chat.service import ChatService
from apps.api.chat.streaming import ChatEvent
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.agent.state import Turn
from carvajal.application.ask import NO_CONTEXT_ANSWER, Source
from carvajal.infrastructure.monitoring import build_tracer, monitoring_status
from carvajal.infrastructure.settings import Settings

PREGUNTA = "¿Qué vasos hay para café de 12 oz en México?"
PREGUNTA_CON_PII = "Soy Ana Pérez, mi teléfono es 300 123 4567, ¿tienen vasos de 12 oz?"

SOURCE = Source(
    "vasos-12",
    "vasos-12oz",
    "vasos-12oz",
    "core",
    0.91,
    "Vaso de 12 oz para café caliente.",
    "https://www.carvajal.com/empaques/vasos/",
    "Vasos",
    "oficial",
)


class EmbedderFalso:
    """El servicio no usa el embedder en estas pruebas."""

    def embed(self, text: str) -> list[float]:
        return [0.0] * 8


class LLMFalso:
    """LLM que emite dos fragmentos y registra las llamadas."""

    def __init__(self, fragmentos: Sequence[str] = ("Carvajal ", "tiene vasos [1].")) -> None:
        self.fragmentos = fragmentos
        self.llamadas = 0

    def complete(self, *, system: str, prompt: str) -> str:
        self.llamadas += 1
        return "".join(self.fragmentos)

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.llamadas += 1
        yield from self.fragmentos


class AgenteFalso:
    """Runner determinista: devuelve el turno indicado o falla."""

    def __init__(self, turn: AgentTurn, error: Exception | None = None) -> None:
        self.turn = turn
        self.error = error
        self.registros: list[dict[str, Any]] = []

    def prepare(
        self, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Turn]
    ) -> AgentTurn:
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
        self.registros.append({"question": question, "answer": answer, "incomplete": incomplete})


def turno(
    route: str = "rag",
    *,
    prompt: str | None = "contexto",
    intent: str = "comercial",
    tool: str = "",
    tool_period: str = "",
    preferences: tuple[str, ...] = (),
    products: dict[str, Any] | None = None,
) -> AgentTurn:
    return AgentTurn(
        route=route,
        intent=intent,
        prompt=prompt,
        system="sistema",
        sources=(SOURCE,) if prompt else (),
        turns=(),
        preferences=preferences,
        tool=tool,
        tool_period=tool_period,
        products=products or {},
    )


class TurnoFalso:
    """Handle de traza que registra metadatos y cierres."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.outputs: dict[str, Any] = {}
        self.outcomes: list[str] = []

    def add_metadata(self, **values: Any) -> None:
        self.metadata.update(values)

    def add_outputs(self, **values: Any) -> None:
        self.outputs.update(values)

    def finish(self, *, outcome: str = "ok", error: BaseException | None = None) -> None:
        self.outcomes.append(outcome)


class TracerFalso:
    """Tracer que abre siempre (o nunca) y guarda lo que recibe."""

    def __init__(self, *, abrir: bool = True, retraso_al_cerrar: float = 0.0) -> None:
        self.abrir = abrir
        self.retraso = retraso_al_cerrar
        self.llamadas: list[dict[str, Any]] = []
        self.turnos: list[TurnoFalso] = []

    @contextmanager
    def turn(
        self,
        *,
        request_id: str,
        thread_hash: str,
        router: str,
        question: str = "",
    ) -> Generator[TurnoFalso]:
        self.llamadas.append(
            {
                "request_id": request_id,
                "thread_hash": thread_hash,
                "router": router,
                "question": question,
            }
        )
        turno_actual = TurnoFalso()
        self.turnos.append(turno_actual)
        try:
            yield turno_actual
        except GeneratorExit:
            turno_actual.finish(outcome="cancelled")
            raise
        except BaseException as error:
            turno_actual.finish(outcome="failed", error=error)
            raise
        else:
            turno_actual.finish(outcome="ok")
        finally:
            if self.retraso:
                sleep(self.retraso)


class ClienteFalso:
    """Doble del cliente de LangSmith: guarda los runs en memoria."""

    def __init__(self, *, falla: bool = False) -> None:
        self.runs: list[dict[str, Any]] = []
        self.actualizaciones: list[dict[str, Any]] = []
        self.falla = falla

    def create_run(self, **kwargs: Any) -> None:
        if self.falla:
            raise RuntimeError("servicio de monitoreo caído")
        self.runs.append(kwargs)

    def update_run(self, **kwargs: Any) -> None:
        if self.falla:
            raise RuntimeError("servicio de monitoreo caído")
        self.actualizaciones.append(kwargs)


@pytest.fixture()
def engine() -> Iterator[Any]:
    """Base SQLite en memoria con el esquema real, sin Docker."""

    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    # SQLite no soporta índices parciales: los dos índices de ``client_turn_id``
    # quedan como únicos plenos y el turno (usuario + asistente) chocaría
    # consigo mismo. Se retiran en las pruebas; Postgres los conserva parciales.
    with motor.begin() as conexion:
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    try:
        yield motor
    finally:
        tables.metadata.drop_all(motor)
        motor.dispose()


def servicio(
    engine: Any,
    agente: Any,
    tracer: Any,
    *,
    llm: LLMFalso | None = None,
    settings: Settings | None = None,
) -> ChatService:
    return ChatService(
        build_session_factory(engine),
        EmbedderFalso(),
        llm or LLMFalso(),
        agent=agente,
        tracer=tracer,
        settings=settings or Settings(_env_file=None),
    )


def eventos(chat: ChatService, owner_key: str, question: str = PREGUNTA) -> list[ChatEvent]:
    return list(chat.stream(owner_key, question))


def con_nombre(eventos_recibidos: Sequence[ChatEvent], nombre: str) -> ChatEvent:
    return next(evento for evento in eventos_recibidos if evento.name == nombre)


@pytest.mark.parametrize(
    ("route", "prompt"),
    [("direct", None), ("rag", "contexto"), ("tool", "contexto"), ("commercial", "contexto")],
)
def test_las_cuatro_rutas_abren_una_raiz(engine: Any, route: str, prompt: str | None) -> None:
    """Cada ruta deja una raíz con request_id, router, hilo seudónimo y longitud."""

    tracer = TracerFalso()
    chat = servicio(engine, AgenteFalso(turno(route, prompt=prompt)), tracer)

    recibidos = eventos(chat, f"traza-{route}")

    assert len(tracer.llamadas) == 1
    llamada = tracer.llamadas[0]
    assert llamada["router"] == "rules"
    assert llamada["question"] == PREGUNTA
    assert len(llamada["thread_hash"]) == 16
    assert llamada["thread_hash"] not in repr(recibidos[1].data)
    assert llamada["request_id"]

    handle = tracer.turnos[0]
    assert handle.metadata["route"] == route
    assert handle.metadata["question_chars"] == len(PREGUNTA)
    assert handle.metadata["answer_chars"] > 0
    assert handle.outcomes == ["ok"]


def test_la_raiz_lleva_los_datos_extraidos(engine: Any) -> None:
    """Intención, herramienta, período y preferencias viajan como metadatos (FR-002)."""

    tracer = TracerFalso()
    chat = servicio(
        engine,
        AgenteFalso(
            turno(
                "tool",
                intent="financiero",
                tool="indicator_lookup",
                tool_period="2025",
                preferences=("sin plástico", "12 oz"),
            )
        ),
        tracer,
    )

    eventos(chat, "traza-datos")

    metadata = tracer.turnos[0].metadata
    assert metadata["intent"] == "financiero"
    assert metadata["tool"] == "indicator_lookup"
    assert metadata["tool_period"] == "2025"
    assert metadata["preferences_count"] == 2


def test_el_done_publica_el_request_id_de_la_traza(engine: Any) -> None:
    """El navegador recibe el mismo identificador que la traza y los logs (FR-004)."""

    tracer = TracerFalso()
    chat = servicio(engine, AgenteFalso(turno()), tracer)

    recibidos = eventos(chat, "traza-done")

    assert con_nombre(recibidos, "done").data["request_id"] == tracer.llamadas[0]["request_id"]


def test_commercial_clarify_se_traza_sin_llamar_al_modelo(engine: Any) -> None:
    """La aclaración comercial es un turno completo aunque no haya prompt."""

    tracer = TracerFalso()
    llm = LLMFalso()
    chat = servicio(engine, AgenteFalso(turno("commercial_clarify", prompt=None)), tracer, llm=llm)

    recibidos = eventos(chat, "traza-clarify")

    assert llm.llamadas == 0
    assert tracer.turnos[0].metadata["route"] == "commercial_clarify"
    assert tracer.turnos[0].outcomes == ["ok"]
    assert con_nombre(recibidos, "token").data["text"] == NO_CONTEXT_ANSWER


def test_rag_con_abstencion_no_llama_al_modelo_y_se_traza(engine: Any) -> None:
    """Sin contexto no hay llamada al modelo; la traza lo muestra como completa."""

    tracer = TracerFalso()
    llm = LLMFalso()
    chat = servicio(engine, AgenteFalso(turno("rag", prompt=None)), tracer, llm=llm)

    eventos(chat, "traza-abstencion")

    assert llm.llamadas == 0
    assert tracer.turnos[0].outcomes == ["ok"]


def test_la_cancelacion_cierra_la_traza_como_cancelada(engine: Any) -> None:
    """Cortar el stream a mitad deja ``cancelled`` y cierra una sola vez."""

    tracer = TracerFalso()
    chat = servicio(engine, AgenteFalso(turno()), tracer)

    generador = cast(Generator[ChatEvent], chat.stream("traza-cancelada", PREGUNTA))
    primeros = [next(generador) for _ in range(2)]
    assert [evento.name for evento in primeros] == ["open", "trace"]
    generador.close()

    assert tracer.turnos[0].outcomes == ["cancelled"]


def test_un_error_cierra_la_traza_una_sola_vez(engine: Any) -> None:
    """El fallo del grafo se propaga como evento y la traza queda ``failed``."""

    tracer = TracerFalso()
    chat = servicio(engine, AgenteFalso(turno(), error=RuntimeError("grafo roto")), tracer)

    recibidos = eventos(chat, "traza-error")

    assert con_nombre(recibidos, "error")
    assert tracer.turnos[0].outcomes == ["failed"]


def test_el_primer_fragmento_no_espera_al_cierre_de_la_traza(engine: Any) -> None:
    """El envío de la traza no retrasa el primer fragmento (SC-003, T013)."""

    tracer = TracerFalso(retraso_al_cerrar=1.0)
    chat = servicio(engine, AgenteFalso(turno()), tracer)
    # Calentamiento: la primera petición crea el hilo y compila el stream.
    eventos(chat, "traza-caliente")

    inicio = perf_counter()
    generador = cast(Generator[ChatEvent], chat.stream("traza-latencia", PREGUNTA))
    while True:
        evento = next(generador)
        if evento.name == "token":
            break
    transcurrido = perf_counter() - inicio
    generador.close()

    assert transcurrido < 0.1, f"el primer fragmento tardó {transcurrido:.3f}s"


def test_el_replay_no_abre_traza(engine: Any) -> None:
    """Repetir un turno guardado no llama al modelo ni crea una traza nueva."""

    tracer = TracerFalso()
    chat = servicio(engine, AgenteFalso(turno()), tracer)

    primero = list(chat.stream("traza-replay", PREGUNTA, client_turn_id="turno-1"))
    hilo = con_nombre(primero, "done").data["thread_id"]
    segundo = list(chat.stream("traza-replay", PREGUNTA, client_turn_id="turno-1", thread_id=hilo))

    assert len(tracer.llamadas) == 1
    assert [evento.name for evento in segundo][-1] == "done"
    assert (
        con_nombre(primero, "done").data["message_id"]
        == con_nombre(segundo, "done").data["message_id"]
    )


class AgenteConLangChain:
    """Runner que ejecuta un paso de LangChain dentro del turno (T011)."""

    def __init__(self) -> None:
        self.turn = turno()

    def prepare(
        self, owner_key: str, thread_id: str, question: str, seed_turns: Sequence[Turn]
    ) -> AgentTurn:
        from langchain_core.runnables import RunnableLambda

        RunnableLambda(lambda texto: texto.upper()).invoke(question)
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
        return None


def test_los_pasos_de_langchain_aparecen_como_hijos(engine: Any) -> None:
    """La raíz abierta por el tracer recibe los runs de LangChain como hijos (T011)."""

    cliente = ClienteFalso()
    tracer = build_tracer(
        Settings(_env_file=None, monitoring_enabled=True, langsmith_api_key="lsv2_pt_x"),
        client=cliente,
        random_fn=lambda: 0.0,
    )
    chat = servicio(engine, AgenteConLangChain(), tracer)

    eventos(chat, "traza-hijos")

    nombres = [run.get("name") for run in cliente.runs]
    assert "chat_turn" in nombres
    assert "RunnableLambda" in nombres
    raiz = next(run for run in cliente.runs if run["name"] == "chat_turn")
    hijo = next(run for run in cliente.runs if run["name"] == "RunnableLambda")
    assert hijo["parent_run_id"] == raiz["id"]


def test_la_raiz_no_lleva_el_texto_en_produccion(engine: Any) -> None:
    """Con contenido desactivado solo viajan longitudes y metadatos (FR-006)."""

    cliente = ClienteFalso()
    tracer = build_tracer(
        Settings(_env_file=None, monitoring_enabled=True, langsmith_api_key="lsv2_pt_x"),
        client=cliente,
        random_fn=lambda: 0.0,
    )
    chat = servicio(engine, AgenteFalso(turno()), tracer)

    eventos(chat, "traza-pii", PREGUNTA_CON_PII)

    enviado = repr(cliente.runs) + repr(cliente.actualizaciones)
    assert "300 123 4567" not in enviado
    assert "Ana Pérez" not in enviado
    raiz = cliente.runs[0]
    assert raiz["inputs"] == {}
    assert raiz["extra"]["metadata"]["question_chars"] == len(PREGUNTA_CON_PII)


def test_el_cliente_redacta_entradas_salidas_metadatos_y_errores() -> None:
    """El anonimizador del cliente cubre los cuatro ganchos (FR-005, FR-006)."""

    from carvajal.infrastructure.monitoring import build_client

    cliente = build_client(
        Settings(
            _env_file=None,
            monitoring_enabled=True,
            langsmith_api_key="lsv2_pt_x",
            langsmith_endpoint="https://langsmith.interno/",
        )
    )
    assert cliente is not None
    pii = {"question": PREGUNTA_CON_PII, "score": 0.9, "ip": "192.0.2.1"}

    assert cliente._anonymizer(pii) == {"score": 0.9}
    assert cliente._anonymizer({"answer": "Ana, te recomiendo…"}) == {}
    assert cliente._anonymizer({"ls_provider": "openai", "prompt": "texto"}) == {
        "ls_provider": "openai"
    }
    assert cliente._anonymizer({"error": "ValueError: Ana, 300 123 4567"}) == {
        "error": "ValueError"
    }


def test_un_monitoreo_caido_no_rompe_el_turno_ni_la_persistencia(engine: Any) -> None:
    """El cliente que lanza en post/end no altera la respuesta ni lo guardado (FR-008)."""

    cliente = ClienteFalso(falla=True)
    tracer = build_tracer(
        Settings(_env_file=None, monitoring_enabled=True, langsmith_api_key="lsv2_pt_x"),
        client=cliente,
        random_fn=lambda: 0.0,
    )
    chat = servicio(engine, AgenteFalso(turno()), tracer)

    with capture_logs() as registros:
        recibidos = eventos(chat, "traza-caida")

    assert con_nombre(recibidos, "done").data["route"] == "rag"
    assert "".join(e.data["text"] for e in recibidos if e.name == "token").startswith("Carvajal")
    historial = list(chat.history("traza-caida"))
    assert [mensaje.role for mensaje in historial] == ["user", "assistant"]
    eventos_registrados = [r["event"] for r in registros]
    assert "monitoring.failed" in eventos_registrados
    assert "chat.turn_completed" in eventos_registrados


def test_la_salud_del_monitoreo_no_expone_credenciales() -> None:
    """``/health`` publica enabled, sample_rate y content, nunca la llave (FR-011)."""

    salud = monitoring_status(
        Settings(
            _env_file=None,
            monitoring_enabled=True,
            langsmith_api_key="lsv2_pt_secreta",
            monitoring_sample_rate=0.2,
        )
    ).as_health()

    assert salud == {"enabled": True, "sample_rate": 0.2, "content": "metadata"}
    assert "lsv2_pt_secreta" not in repr(salud)


class CuotaContada:
    """Cuota inyectada que cuenta las consultas del endpoint."""

    def __init__(self) -> None:
        self.llamadas = 0

    def __call__(self, owner_key: str) -> Any:
        from carvajal.application.quota import QuotaResult

        self.llamadas += 1
        return QuotaResult(allowed=True)


def test_la_cuota_se_consume_igual_con_el_monitoreo_caido(engine: Any) -> None:
    """La cuota se cobra antes del turno y no depende del servicio de trazas (FR-008)."""

    from fastapi.testclient import TestClient

    from apps.api.main import create_app
    from apps.api.security import CSRF_COOKIE, CSRF_FIELD

    cliente_traza = ClienteFalso(falla=True)
    tracer = build_tracer(
        Settings(_env_file=None, monitoring_enabled=True, langsmith_api_key="lsv2_pt_x"),
        client=cliente_traza,
        random_fn=lambda: 0.0,
    )
    cuota = CuotaContada()
    app = create_app()
    app.state.chat = servicio(engine, AgenteFalso(turno()), tracer)
    app.state.quota = cuota
    cliente_http = TestClient(app)

    cliente_http.get("/faq")
    datos = {"question": PREGUNTA, CSRF_FIELD: cliente_http.cookies[CSRF_COOKIE]}
    respuesta = cliente_http.post("/chat/stream", data=datos)

    assert respuesta.status_code == 200
    assert cuota.llamadas == 1
    assert "event: done" in respuesta.text
