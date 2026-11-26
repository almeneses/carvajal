"""Compuerta de afirmaciones ante los casos del ensayo del 2026-11-26 (spec 046, §10.7).

El ensayo sustituyó más de un tercio de las respuestas juzgadas (web 18 de 47, WhatsApp 17
de 45), 24 de 35 con p entre 0,2 y 0,5. Estas pruebas usan un juez falso que devuelve el p
observado y comprueban tres cosas sin llamar a Jev:

- con el umbral de 0,3 los falsos positivos de la franja de 0,3 a 0,5 ya no se sustituyen y lo
  que la compuerta debe atrapar (valoraciones sin fuente, cifras inventadas) sigue atrapado;
- lo que la persona dijo en turnos anteriores y el borrador de la cotización viajan como
  evidencia;
- un turno que solo presentaba datos del catálogo o del directorio recibe esos datos, no
  «No tengo un dato verificado…».
"""

from collections.abc import Sequence
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from tests.fakes.judge import FakeJudge
from tests.fakes.tool_chat import ScriptedToolChat, ScriptStep

from apps.api.chat.agent import ToolAgentFinal, ToolAgentRunner
from carvajal.agent.gatekeeper.claims import DRAFT_EXTRACT, PERSON_EXTRACT
from carvajal.agent.gatekeeper.stats import ClaimsStats
from carvajal.agent.state import Turn
from carvajal.agent.toolagent.evidence import NO_DATA_ANSWER, presented_data
from carvajal.agent.toolagent.graph import build_tool_agent
from carvajal.agent.toolagent.tools import ToolDeps
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings
from carvajal.quotation.draft import QuoteDraft

MICROONDAS = "Necesito 500 contenedores que se puedan meter al microondas en Bogotá"
BUSCAR_CAZUELAS = {"familia": "contenedor", "microondas": True, "pais": "CO"}
# Texto de la forma de los falsos positivos auditados: tabla de opciones citadas.
TABLA = (
    "Para microondas te sirven estas cazuelas de PP:\n\n"
    "| Referencia | Capacidad |\n|---|---|\n"
    "| Cazuela negra 12 oz Wau Maestro [1] | 12 oz |\n"
    "| Cazuela negra 16 oz Wau Maestro [2] | 16 oz |"
)


class FakeEmbedder:
    model = "fake"

    def embed(self, text: str) -> list[float]:
        return [0.1] * 8


class EmptyStore:
    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise AssertionError("El adaptador no indexa.")

    def search(self, embedding: Sequence[float], **_: Any) -> Sequence[ScoredChunk]:
        return []

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("El adaptador no borra.")


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args}


def _run(
    call: dict[str, Any],
    text: str,
    judge: FakeJudge,
    *,
    question: str = MICROONDAS,
    seed: Sequence[Turn] = (),
    draft: QuoteDraft | None = None,
) -> ToolAgentFinal:
    settings = Settings(_env_file=None)
    deps = ToolDeps(vector_store=EmptyStore(), embedder=FakeEmbedder(), settings=settings)
    chat = ScriptedToolChat(steps=[ScriptStep(tool_calls=[call]), ScriptStep(text=text)])
    graph = build_tool_agent(deps, chat, checkpointer=InMemorySaver())
    runner = ToolAgentRunner(graph, settings, claims_judge=judge, claims_stats=ClaimsStats())
    events = list(runner.stream("dueno-1", "hilo-1", question, list(seed), draft=draft))
    final = events[-1]
    assert isinstance(final, ToolAgentFinal)
    return final


# --- Umbral (evidencia §10.3 y §10.7) -----------------------------------------------------


def test_el_umbral_por_defecto_es_0_3() -> None:
    assert Settings(_env_file=None).jev_claims_threshold == 0.3


@pytest.mark.parametrize("p", [0.38, 0.46, 0.49])
def test_una_tabla_citada_con_el_p_observado_ya_no_se_sustituye(p: float) -> None:
    # Arrange: las seis tablas citadas auditadas como falsos positivos tenían p de 0,38 a 0,49.
    judge = FakeJudge(p=p)

    # Act
    final = _run(_call("buscar_producto", **BUSCAR_CAZUELAS), TABLA, judge)

    # Assert
    assert final.answer == TABLA
    assert (final.claims["status"], final.claims["substituted"]) == ("supported", False)
    assert final.claims["threshold"] == 0.3


@pytest.mark.parametrize(
    ("text", "p"),
    [
        # Las dos bien atrapadas de la auditoría: valoración presentada como hecho.
        ("La Cazuela negra 12 oz [1] sirve para porciones pequeñas y es la más pedida.", 0.14),
        ("La Cazuela negra 16 oz [2] es la preferida de los restaurantes.", 0.23),
        # Cifras inventadas: el conjunto congelado da p entre 0,03 y 0,15.
        ("La Cazuela negra 12 oz [1] cuesta $1.200 y llega en 24 horas.", 0.06),
    ],
)
def test_lo_que_la_compuerta_debe_atrapar_sigue_atrapado(text: str, p: float) -> None:
    # Arrange
    judge = FakeJudge(p=p)

    # Act
    final = _run(_call("buscar_producto", **BUSCAR_CAZUELAS), text, judge)

    # Assert: el texto no sale y el reemplazo solo trae datos de la herramienta.
    assert final.claims["status"] == "unsupported"
    assert final.claims["substituted"] is True
    assert text not in final.answer
    for invented in ("porciones pequeñas", "preferida", "$1.200", "24 horas", "más pedida"):
        assert invented not in final.answer


# --- Reemplazo con los datos del turno (punto b) ------------------------------------------


def test_microondas_sustituida_presenta_las_cazuelas_y_no_el_texto_generico() -> None:
    # Arrange: microondas t1 del ensayo; p por debajo del umbral para forzar la sustitución.
    judge = FakeJudge(p=0.25)

    # Act
    final = _run(_call("buscar_producto", **BUSCAR_CAZUELAS), TABLA, judge)

    # Assert
    assert final.claims["substituted"] is True
    assert final.answer != NO_DATA_ANSWER
    assert final.answer.startswith("Estas referencias del catálogo son aptas para")
    assert "uso en microondas" in final.answer
    assert "1. Cazuela negra 12 oz Wau Maestro (SKU CO-CAZ-12) · PP, 12 oz [1]" in final.answer
    assert final.answer.endswith("¿Quieres que te cotice alguna?")
    assert final.abstained is False
    assert final.sources  # las tarjetas y las citas conservan sus fuentes


def test_la_evidencia_dice_para_que_son_aptas_las_opciones() -> None:
    # Arrange
    judge = FakeJudge(p=0.9)

    # Act
    _run(_call("buscar_producto", **BUSCAR_CAZUELAS), TABLA, judge)

    # Assert: sin el criterio, «se pueden meter al microondas» no tenía respaldo.
    evidence = dict(judge.claims[0].evidence)
    assert (
        '"aptas_para":{"temperatura":"caliente","microondas":true}' in evidence["buscar_producto"]
    )


def test_las_sedes_sustituidas_se_presentan_con_sus_contactos() -> None:
    # Arrange
    judge = FakeJudge(p=0.1)

    # Act
    final = _run(
        _call("buscar_sedes", ciudad="Bogotá"),
        "En Bogotá la mejor sede es la del norte, abierta 24 horas.",
        judge,
        question="¿Dónde compro en Bogotá?",
    )

    # Assert
    assert final.claims["substituted"] is True
    assert final.answer.startswith("Estas sedes del directorio tienen contacto")
    assert "24 horas" not in final.answer
    assert "[1]" in final.answer


# --- Lo dicho antes y el borrador viajan a Jev (punto a) ----------------------------------


def test_trescientas_tapas_lleva_a_jev_lo_dicho_en_los_turnos_anteriores() -> None:
    # Arrange: «300 tapas» (si_ante_la_pregunta t3) tras pedir 500 vasos de 9 oz en Bogotá.
    seed: list[Turn] = [
        {
            "question": "Cotízame 500 vasos de 9 oz Wau en Bogotá",
            "answer": "Hay dos referencias.",
            "incomplete": False,
        },
        {"question": "sí", "answer": "¿Cuál te cotizo?", "incomplete": False},
    ]
    judge = FakeJudge(p=0.9)

    # Act
    _run(
        _call("buscar_producto", familia="tapa", temperatura="caliente", pais="CO"),
        "Para tus 500 vasos de 9 oz en Bogotá, estas tapas encajan.",
        judge,
        question="300 tapas",
        seed=seed,
    )

    # Assert: los valores, no el texto de los mensajes.
    said = dict(judge.claims[0].evidence)[PERSON_EXTRACT]
    assert said.startswith("cantidad: 300; familia: tapa")
    assert "en mensajes anteriores: cantidad: 500; familia: vaso; capacidad: 9 oz" in said
    assert "ciudad: Bogotá" in said
    assert "Cotízame" not in said and "Wau" not in said


def test_el_borrador_de_la_cotizacion_viaja_a_jev() -> None:
    # Arrange
    line = {
        "primary": {
            "name": "Plato espumado pando 12 cm Wau",
            "sku": "CO-PPAN-12",
            "currency": "COP",
            "presentation": {"units_requested": 500, "subtotal": 43700},
        },
        "city": "Bogotá",
        "folio": "CV-SECRETO",
    }
    draft = QuoteDraft(lines=(line,), state="carrito", awaiting="algo_mas")
    judge = FakeJudge(p=0.9)

    # Act
    _run(
        _call("buscar_producto", **BUSCAR_CAZUELAS),
        TABLA,
        judge,
        question="¿Y algo para microondas?",
        draft=draft,
    )

    # Assert
    lines = dict(judge.claims[0].evidence)[DRAFT_EXTRACT]
    assert lines == "1. Plato espumado pando 12 cm Wau · 500 und · Bogotá · subtotal 43700 COP"
    assert "CV-SECRETO" not in lines


# --- presented_data: cuándo no aplica -----------------------------------------------------


OPTIONS = ("buscar_producto", {"status": "ok", "opciones": [{"n": 1, "sku": "A", "nombre": "A"}]})


@pytest.mark.parametrize(
    "extra",
    [
        ("buscar_corpus", {"status": "ok", "fragmentos": [{"n": 2, "texto": "Historia."}]}),
        ("cotizar", {"status": "ok", "opciones": []}),
        ("historia_carvajal", {"status": "ok", "hitos": []}),
    ],
)
def test_presented_data_no_aplica_si_el_turno_consulto_otra_cosa(
    extra: tuple[str, dict[str, Any]],
) -> None:
    # La parte del corpus se perdería; con ``cotizar`` responde su plantilla (spec 054).
    assert presented_data([OPTIONS, extra]) == ""


def test_presented_data_ignora_declaraciones_y_memoria() -> None:
    # Arrange
    payloads = [
        OPTIONS,
        ("sin_consulta", {"status": "ok", "motivo": "pedir_dato", "dato": "ciudad"}),
        ("listar_memoria", {"status": "ok", "memoria": {}}),
    ]

    # Act
    text = presented_data(payloads)

    # Assert
    assert text.startswith("Esta referencia del catálogo es apta para lo que pides:")
    assert "1. A (SKU A) [1]" in text


def test_presented_data_sin_opciones_ni_sedes_devuelve_vacio() -> None:
    assert presented_data([("buscar_producto", {"status": "ok", "opciones": []})]) == ""
    assert presented_data([]) == ""
