"""Minimización de lo que viaja a Jev (spec 046, T010 y T040; research R8).

Unidad: ``outgoing_question``, ``outgoing_answer``, ``sensitive_spans`` y ``context_line``.
Extremo a extremo (T040, al final): un juez que registra cada carga recorre turnos web y de
WhatsApp con memoria, resumen, contexto comercial, frases sensibles y una cotización
guardada, y ninguna carga lleva identidad, memoria, historial ni datos de la persona.
"""

import json
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from tests.fakes.judge import FakeJudge
from tests.fakes.tool_chat import ScriptedToolChat, ScriptStep
from tests.test_memory_sensitive import SENSITIVE, VALID
from tests.test_tool_agent_stream import FUNDACION, FakeEmbedder, QueueStore

from apps.api.chat.agent import ToolAgentRunner
from carvajal.agent.gatekeeper.claims import build_claims_query
from carvajal.agent.gatekeeper.decide import decide_gate
from carvajal.agent.gatekeeper.privacy import (
    FOLIO_MARK,
    PERSON_MARK,
    address_spans,
    context_line,
    outgoing_answer,
    outgoing_question,
    sensitive_spans,
)
from carvajal.agent.memory.sensitive import REDACTED
from carvajal.agent.memory.store import MemoryProfile, classify, remember
from carvajal.agent.memory.summary import ConversationSummary
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.graph import build_tool_agent
from carvajal.agent.toolagent.tools import ToolDeps
from carvajal.infrastructure.settings import Settings

MEMORY = MemoryProfile(
    values={"nombre": "Ana", "empresa": "Panadería La Espiga", "ciudad_habitual": "Cali"},
    notes=("mi socio se llama Ramiro",),
).snapshot()


@pytest.mark.parametrize(("phrase", "kind"), SENSITIVE)
def test_la_pregunta_redacta_las_frases_sensibles(phrase: str, kind: str) -> None:
    # Act
    sent = outgoing_question(f"¿Cuánto valen 500 vasos? Te dejo esto: {phrase}")

    # Assert
    assert REDACTED in sent
    assert "500 vasos" in sent


@pytest.mark.parametrize("phrase", [p for p in VALID if not classify(p)])
def test_la_pregunta_no_toca_las_frases_validas(phrase: str) -> None:
    assert outgoing_question(phrase) == phrase


@pytest.mark.parametrize(
    ("text", "hidden"),
    [
        ("soy Ana, trabajo en Panadería La Espiga y necesito vasos", ["Ana", "La Espiga"]),
        ("llámame Ana", ["Ana"]),
        ("mi empresa es Distribuciones Pérez, ¿qué vasos tienen?", ["Distribuciones Pérez"]),
        ("trabajo en Panadería La Espiga en Cali", ["La Espiga"]),
        ("tengo una panadería llamada El Trigal, ¿qué bolsas hay?", ["El Trigal"]),
    ],
)
def test_enmascara_nombre_y_empresa_declarados(text: str, hidden: list[str]) -> None:
    # Act
    sent = outgoing_question(text)

    # Assert
    assert PERSON_MARK in sent
    for value in hidden:
        assert value not in sent


@pytest.mark.parametrize(
    ("text", "hidden"),
    [
        (
            "Hola, habla Ana Gómez de Plásticos Ríos, ¿tienen vasos de 9 oz?",
            ["Ana", "Gómez", "Plásticos Ríos"],
        ),
        ("Le habla el doctor Ramírez del Hospital San José", ["Ramírez", "Hospital San José"]),
        (
            "Cotiza para Restaurante El Fogón, a nombre de Pedro",
            ["Restaurante El Fogón", "Pedro"],
        ),
        (
            "Soy Carlos, de la Panadería Doña Rosa en Cali, ¿precio de bolsas?",
            ["Carlos", "Doña Rosa"],
        ),
        ("Necesito factura a nombre de Inversiones Gómez SAS", ["Inversiones Gómez"]),
        ("Buenas, de parte de la señora Martínez, ¿tienen tapas?", ["Martínez"]),
    ],
)
def test_enmascara_las_presentaciones_habituales(text: str, hidden: list[str]) -> None:
    # Act
    sent = outgoing_question(text)

    # Assert
    assert PERSON_MARK in sent
    for value in hidden:
        assert value not in sent, value


@pytest.mark.parametrize(
    "text",
    [
        "¿De qué habla el Informe de Sostenibilidad?",
        "cotízame para Bogotá 500 vasos",
        "Panadería La Espiga",
    ],
)
def test_sin_presentacion_no_enmascara(text: str) -> None:
    assert outgoing_question(text) == text


@pytest.mark.parametrize(
    "address",
    ["Calle 5 # 38-25 apto 301", "Cra 7 No. 45-10", "Av. 3N #12-40 oficina 502", "cl 10 #5"],
)
def test_redacta_las_direcciones_de_la_persona(address: str) -> None:
    # Act
    sent = outgoing_question(f"Dirección de entrega: {address}, Cali")
    context = context_line(
        "¿y en Bogotá?",
        [{"question": f"Envíen a {address}, ¿tienen vasos?", "answer": "x", "incomplete": False}],
    )

    # Assert
    assert sent == f"Dirección de entrega: {REDACTED}, Cali"
    assert address not in context
    assert REDACTED in context


def test_la_respuesta_conserva_la_direccion_del_directorio() -> None:
    answer = "La sede queda en la Calle 13 # 100-00 de Cali."

    assert outgoing_answer(answer, "¿Dónde queda la sede de Cali?") == answer
    assert address_spans("Envíen a Calle 5 # 38-25 apto 301") == ["Calle 5 # 38-25 apto 301"]
    sent = outgoing_answer(
        "Te enviamos a Calle 5 # 38-25 apto 301.", "Envíen a Calle 5 # 38-25 apto 301"
    )
    assert "38-25" not in sent


def test_la_respuesta_redacta_lo_que_la_persona_dio_en_turnos_anteriores() -> None:
    # Arrange
    prior = ["Mi celular es 3001234567, llámenme", "Soy Ana Gómez, de la Panadería Doña Rosa"]

    # Act
    sent = outgoing_answer(
        "Ana Gómez, de Panadería Doña Rosa: tu celular es 300 123 4567.",
        "¿Cuál es mi celular?",
        prior=prior,
    )

    # Assert
    assert "3001234567" not in sent and "300 123 4567" not in sent
    assert "Ana" not in sent and "Doña Rosa" not in sent
    assert REDACTED in sent and PERSON_MARK in sent


def test_el_telefono_de_un_turno_anterior_no_viaja_en_la_compuerta() -> None:
    # Arrange: el modelo repite un celular que la persona dio en un turno anterior.
    judge = FakeJudge(choice="mixta", confidence=0.95, p=0.9)
    settings = Settings(_env_file=None, agent_mode="tools", jev_claims_mode="enforce")
    chat = ScriptedToolChat(
        steps=[
            ScriptStep(tool_calls=[{"name": "sin_consulta", "args": {"motivo": "conversacion"}}]),
            ScriptStep(text="Tu celular es 3001234567."),
        ]
    )
    deps = ToolDeps(vector_store=QueueStore(), embedder=FakeEmbedder(), settings=settings)
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver(), judge=judge)
    runner = ToolAgentRunner(graph, settings, claims_judge=judge)
    seed: list[Turn] = [
        {
            "question": "Mi celular es 3001234567, llámenme",
            "answer": "Anotado.",
            "incomplete": False,
        }
    ]

    # Act
    list(runner.stream("d", "h", "¿Cuál es mi celular?", seed))

    # Assert
    assert judge.claims
    assert "3001234567" not in _payloads(judge)


def test_no_enmascara_ciudades_ni_el_tipo_de_negocio() -> None:
    # Arrange
    text = "tengo una cafetería en Cali, ¿qué vasos me sirven?"
    assert classify(text) == {"empresa": "cafetería en Cali"}

    # Act / Assert
    assert outgoing_question(text) == text
    assert "Cali" in outgoing_question("trabajo en Panadería La Espiga en Cali")
    assert outgoing_question("trabajo en Cali") == "trabajo en Cali"


def test_enmascara_los_valores_de_la_memoria() -> None:
    # Act
    sent = outgoing_question(
        "Para la panadería la espiga, la de Ana: mi socio se llama Ramiro. Vasos en Cali",
        MEMORY,
    )

    # Assert
    assert "espiga" not in sent.lower()
    assert "Ana" not in sent
    assert "Ramiro" not in sent
    assert "Cali" in sent  # la ciudad habitual no identifica


def test_recorta_pregunta_y_contexto() -> None:
    long = "vasos " * 400

    assert len(outgoing_question(long)) <= 1000
    assert len(outgoing_question(long, limit=160)) <= 160


def test_la_respuesta_conserva_los_telefonos_del_directorio() -> None:
    # Arrange
    answer = "Llama a la sede al (601) 555 1234 o al +57 1 555 1234."

    # Act
    sent = outgoing_answer(answer, "¿Dónde compro vasos en Bogotá?")

    # Assert
    assert sent == answer


def test_la_respuesta_redacta_solo_lo_que_dio_la_persona() -> None:
    # Arrange
    question = "Soy Ana, mi celular es 300 123 4567. ¿Me cotizas 500 vasos?"
    answer = "Ana, te escribo al 3001234567. La sede atiende al (601) 555 1234."

    # Act
    sent = outgoing_answer(answer, question, MEMORY)

    # Assert
    assert "3001234567" not in sent
    assert "Ana" not in sent
    assert REDACTED in sent
    assert PERSON_MARK in sent
    assert "(601) 555 1234" in sent


def test_la_respuesta_sustituye_los_folios_de_la_persona() -> None:
    # Arrange
    payloads = [("consultar_cotizacion", {"status": "ok", "cotizaciones": [{"folio": "CV-7F3A9"}]})]

    # Act
    sent = outgoing_answer(
        "Tu cotización CV-7F3A9 sigue vigente.", "¿y mi cotización?", None, payloads
    )

    # Assert
    assert sent == f"Tu cotización {FOLIO_MARK} sigue vigente."


def test_sensitive_spans_usa_los_patrones_de_la_045() -> None:
    assert sensitive_spans("mi celular es 300 123 4567 y quiero 500 vasos") == ["300 123 4567"]
    assert sensitive_spans("500 vasos de 12 oz") == []


def test_context_line_solo_con_pregunta_eliptica_o_corta() -> None:
    # Arrange
    turns: list[Turn] = [
        {
            "question": "Cotízame 500 vasos de 9 oz, mi celular es 3001234567",
            "answer": "x",
            "incomplete": False,
        }
    ]

    # Act
    short = context_line("¿y en Cali?", turns)
    long = context_line("¿Cuáles fueron los ingresos consolidados de Carvajal en 2024?", turns)
    without = context_line("¿y en Cali?", [])

    # Assert
    assert short.startswith("Cotízame 500 vasos de 9 oz")
    assert "3001234567" not in short
    assert len(short) <= 160
    assert long == ""
    assert without == ""


# --- Extremo a extremo (T040): ninguna carga lleva identidad, memoria ni historial -------------

WEB_OWNER = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
WA_OWNER = "wa_5f2d7c1e9a8b4c3d"
PHONE = "+573001234567"
PREVIOUS_ANSWER = "Respuesta anterior: te recomendé la referencia RA-PRIVADA-77."
SUMMARY_TEXT = "La persona habló de su panadería y de su socio."
FOLIO = "CV-7F3A9-PRIV"


def _payloads(judge: Any) -> str:
    """Todo lo que el doble recibió, como texto, para buscar fugas."""

    routes = [[q.question, q.context] for q in judge.routes]
    claims = [[q.answer, list(q.evidence)] for q in judge.claims]
    return json.dumps([routes, claims], ensure_ascii=False)


def _store_with_memory(owner: str) -> Any:
    store = InMemoryStore()
    remember(store, owner, "nombre", "Ana", origin="shortcut")
    remember(store, owner, "empresa", "Panadería La Espiga", origin="shortcut")
    remember(store, owner, "nota", "mi socio se llama Ramiro", origin="shortcut")
    return store


@pytest.mark.parametrize("owner", [WEB_OWNER, WA_OWNER])
@pytest.mark.parametrize(
    "question",
    [
        "¿Cuándo se fundó Carvajal? Te escribe Ana de Panadería La Espiga",
        "mi socio se llama Ramiro, ¿qué vasos de 9 oz tienen?",
        f"Escríbeme al {PHONE}, ¿cuándo se fundó Carvajal?",
        "¿y en Cali?",
        "¿Quién fundó Carvajal? mi cédula es 1.234.567.890",
    ],
)
def test_turnos_web_y_whatsapp_con_memoria_no_filtran_datos(owner: str, question: str) -> None:
    # Arrange
    judge = FakeJudge(choice="mixta", confidence=0.9, p=0.9)
    store = _store_with_memory(owner)
    echo = f" Te escribo al {PHONE}." if PHONE in question else ""
    settings = Settings()
    chat = ScriptedToolChat(
        steps=[
            ScriptStep(tool_calls=[{"name": "buscar_corpus", "args": {"consulta": "fundación"}}]),
            # El modelo solo puede repetir el número si la persona lo dio en el mensaje: por
            # WhatsApp el número nunca llega al grafo (``owner_key`` es un HMAC).
            ScriptStep(text=f"Ana, Carvajal fue fundada en Cali en 1904 [1].{echo}"),
        ]
    )
    deps = ToolDeps(
        vector_store=QueueStore([FUNDACION]), embedder=FakeEmbedder(), settings=settings
    )
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver(), store=store, judge=judge)
    runner = ToolAgentRunner(graph, settings, claims_judge=judge)
    seed: list[Turn] = [
        {
            "question": "Hola, cotízame 500 vasos de 9 oz en Cali",
            "answer": PREVIOUS_ANSWER,
            "incomplete": False,
        }
    ]
    thread = f"hilo-{owner[-6:]}"

    # Act
    events = list(runner.stream(owner, thread, question, seed))

    # Assert
    assert judge.routes
    sent = _payloads(judge)
    for leaked in (owner, thread, PHONE, "3001234567", "Ramiro", "Espiga", "1.234.567.890"):
        assert leaked not in sent, leaked
    assert "Ana" not in sent
    assert PREVIOUS_ANSWER not in sent and "RA-PRIVADA-77" not in sent
    for query in judge.claims:
        assert question not in json.dumps([query.answer, list(query.evidence)], ensure_ascii=False)
    assert events


@pytest.mark.parametrize(("phrase", "kind"), SENSITIVE)
def test_ninguna_frase_sensible_viaja_sin_redactar(phrase: str, kind: str) -> None:
    # Arrange
    judge = FakeJudge(choice="rag", confidence=0.9, p=0.9)
    question = f"¿Cuándo se fundó Carvajal? {phrase}"
    payloads = [
        ("buscar_corpus", {"status": "ok", "fragmentos": [{"n": 1, "texto": "Se fundó en 1904."}]}),
        ("consultar_cotizacion", {"status": "ok", "cotizaciones": [{"folio": FOLIO}]}),
    ]

    # Act
    decide_gate(question, judge=judge, settings=Settings(), memory=MEMORY)
    judge.check(
        build_claims_query(
            f"Se fundó en 1904 [1]. Repito tu dato: {phrase}. Folio {FOLIO}.",
            question,
            payloads,
            MEMORY,
        )
    )

    # Assert
    sent = _payloads(judge)
    for span in sensitive_spans(phrase):
        assert span not in sent, (phrase, span)
    assert FOLIO not in sent
    assert question not in json.dumps(list(judge.claims[0].evidence), ensure_ascii=False)


def test_el_resumen_y_el_contexto_comercial_no_viajan() -> None:
    # Arrange: un turno con resumen acumulado y contexto comercial en el estado del hilo.
    judge = FakeJudge(choice="rag", confidence=0.9, p=0.9)
    settings = Settings()
    chat = ScriptedToolChat(
        steps=[
            ScriptStep(tool_calls=[{"name": "buscar_corpus", "args": {"consulta": "fundación"}}]),
            ScriptStep(text="Carvajal fue fundada en Cali en 1904 [1]."),
        ]
    )
    deps = ToolDeps(
        vector_store=QueueStore([FUNDACION]), embedder=FakeEmbedder(), settings=settings
    )
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver(), judge=judge)
    summary = ConversationSummary(text=SUMMARY_TEXT, covered_turns=0).to_value()

    # Act
    graph.invoke(
        {
            "question": "¿Cuándo se fundó Carvajal?",
            "owner_key": WEB_OWNER,
            "thread_id": "hilo-resumen",
            "conversation_summary": summary,
            "commercial_context": {"city": "Pasto", "quantity": 777},
        },
        {"configurable": {"thread_id": "hilo-resumen"}, "recursion_limit": 20},
    )

    # Assert
    sent = _payloads(judge)
    assert SUMMARY_TEXT not in sent
    assert "Pasto" not in sent and "777" not in sent
