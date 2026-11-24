"""Minimización de lo que viaja a Jev (spec 046, T010 y T040; research R8).

Unidad: ``outgoing_question``, ``outgoing_answer``, ``sensitive_spans`` y ``context_line``.
Extremo a extremo (T040, al final): un juez que registra cada carga recorre turnos web y de
WhatsApp con memoria, resumen, contexto comercial, frases sensibles y una cotización
guardada, y ninguna carga lleva identidad, memoria, historial ni datos de la persona.
"""

import pytest
from tests.test_memory_sensitive import SENSITIVE, VALID

from carvajal.agent.gatekeeper.privacy import (
    FOLIO_MARK,
    PERSON_MARK,
    context_line,
    outgoing_answer,
    outgoing_question,
    sensitive_spans,
)
from carvajal.agent.memory.sensitive import REDACTED
from carvajal.agent.memory.store import MemoryProfile, classify
from carvajal.agent.state import Turn

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
