"""Pruebas de la compuerta de evidencia del modo ``tools`` (spec 044, T029; research R2, R3).

- ``gate`` decide el camino del turno sobre ``ToolOutcome.status`` y ``source_ids``:
  ``evidence`` (alguna herramienta ``ok`` con citas), ``abstain`` (sin evidencia y hubo
  ``buscar_corpus``) o ``no_query`` (el resto: solo ``sin_consulta``, herramientas sin
  evidencia que no buscan en el corpus o algún ``needs_input``).
- ``filter_unsupported`` aplica el filtro de la ruta directa al texto del camino
  ``no_query``: cifras, fechas, montos o nombres propios de Carvajal que no vengan de la
  pregunta ni de los resultados de las herramientas obligan a abstenerse.
"""

from typing import Any

import pytest

from carvajal.agent.toolagent.evidence import (
    GATE_ABSTAIN,
    GATE_EVIDENCE,
    GATE_NO_QUERY,
    filter_unsupported,
    gate,
    has_evidence,
    unsupported_terms,
)
from carvajal.agent.toolagent.state import ToolOutcome, make_tool_outcome


def _outcome(tool: str, status: str = "ok", source_ids: tuple[int, ...] = ()) -> ToolOutcome:
    args: dict[str, Any] = {"motivo": "conversacion"} if tool == "sin_consulta" else {}
    return make_tool_outcome(tool=tool, args=args, status=status, source_ids=source_ids)


# --- gate ----------------------------------------------------------------------------------


def test_hay_evidencia_con_una_herramienta_ok_con_citas() -> None:
    # Arrange
    results = [_outcome("buscar_corpus", "empty"), _outcome("buscar_corpus", "ok", (1, 2))]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_EVIDENCE == "evidence"
    assert has_evidence(results)


@pytest.mark.parametrize(
    "tool",
    ["buscar_producto", "cotizar", "buscar_sedes", "indicador_financiero"],
)
def test_cualquier_herramienta_con_citas_aporta_evidencia(tool: str) -> None:
    # Arrange
    results = [_outcome("sin_consulta"), _outcome(tool, "ok", (3,))]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_EVIDENCE


def test_sin_evidencia_tras_buscar_corpus_se_abstiene() -> None:
    # Arrange
    results = [_outcome("buscar_corpus", "empty"), _outcome("buscar_corpus", "empty")]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_ABSTAIN == "abstain"
    assert not has_evidence(results)


def test_buscar_corpus_mezclado_con_otras_sin_evidencia_se_abstiene() -> None:
    # Arrange
    results = [
        _outcome("sin_consulta"),
        _outcome("buscar_producto", "empty"),
        _outcome("buscar_corpus", "error"),
    ]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_ABSTAIN


@pytest.mark.parametrize(
    ("status", "expected"),
    [("needs_input", GATE_NO_QUERY), ("empty", GATE_ABSTAIN), ("error", GATE_ABSTAIN)],
)
def test_las_citas_sin_estado_ok_no_son_evidencia(status: str, expected: str) -> None:
    # Arrange
    results = [_outcome("buscar_corpus", status, (1,))]

    # Act
    path = gate(results)

    # Assert
    assert not has_evidence(results)
    assert path == expected


def test_ok_sin_citas_no_es_evidencia() -> None:
    # Arrange
    results = [_outcome("buscar_corpus", "ok")]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_ABSTAIN


def test_solo_sin_consulta_es_camino_sin_consulta() -> None:
    # Arrange
    results = [_outcome("sin_consulta")]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_NO_QUERY == "no_query"


def test_herramientas_sin_evidencia_que_no_buscan_en_el_corpus_son_sin_consulta() -> None:
    # Arrange
    results = [_outcome("buscar_producto", "needs_input"), _outcome("cotizar", "needs_input")]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_NO_QUERY


def test_turno_sin_herramientas_es_camino_sin_consulta() -> None:
    # Arrange
    results: list[ToolOutcome] = []

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_NO_QUERY


# --- filter_unsupported --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Hola, soy Carva, el asistente de Carvajal. ¿En qué te ayudo hoy?",
        "Puedo contarte la historia de Carvajal, sus cifras o ayudarte con empaques.",
        "¿Me dices la ciudad de entrega? Así te cotizo.",
        "Ese tema está fuera de lo que puedo responder. Pregúntame por Carvajal.",
        "**Claro que sí.** Dime qué producto buscas.",
        "",
    ],
)
def test_texto_conversacional_limpio_pasa(text: str) -> None:
    # Act
    blocked = filter_unsupported(text)

    # Assert
    assert blocked is False
    assert unsupported_terms(text) == []


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("Carvajal se fundó en 1904.", "1904"),
        ("Los ingresos fueron de 3,2 billones.", "3,2"),
        ("La sede abre el 15 de marzo.", "15"),
        ("La junta se reúne en marzo.", "marzo"),
        ("Vende cerca de $ en vasos.", "$"),
        ("Creció un 12% el año pasado.", "12"),
        ("Factura millones de pesos al año.", "millones"),
        ("Carvajal Empaques fabrica vasos.", "Empaques"),
        ("Su marca de vajillas es Bioform y la usan hoteles.", "Bioform"),
        ("Tiene una planta en yumbo desde hace años.", "yumbo"),
    ],
)
def test_cifras_fechas_montos_y_nombres_propios_se_bloquean(text: str, term: str) -> None:
    # Act
    blocked = filter_unsupported(text)
    terms = unsupported_terms(text)

    # Assert
    assert blocked is True
    assert term in terms


def test_las_mayusculas_al_inicio_de_cada_oracion_no_son_nombres_propios() -> None:
    # Arrange
    text = "Entiendo. Necesito un dato más.\n- Ciudad de entrega\n- Producto\nGracias."

    # Act
    terms = unsupported_terms(text)

    # Assert
    assert terms == []


def test_los_marcadores_de_lista_numerada_no_son_cifras() -> None:
    # Arrange
    text = "Te pido dos datos:\n1. La ciudad.\n2) El producto."

    # Act
    blocked = filter_unsupported(text)

    # Assert
    assert blocked is False


def test_lo_que_aporta_la_pregunta_o_una_herramienta_no_se_bloquea() -> None:
    # Arrange
    text = "¿En qué ciudad necesitas los 2.000 vasos? Hay entrega en Bogotá y Medellín."
    allowed = ["Necesito 2000 vasos", '{"ciudades_disponibles": ["Bogotá", "Medellín"]}']

    # Act
    blocked = filter_unsupported(text, allowed=allowed)

    # Assert
    assert blocked is False


def test_lo_permitido_no_cubre_otras_cifras_ni_nombres() -> None:
    # Arrange
    text = "Los 2.000 vasos llegan el 3 de mayo a Cali."
    allowed = ["Necesito 2000 vasos"]

    # Act
    terms = unsupported_terms(text, allowed=allowed)

    # Assert
    assert "2.000" not in terms
    assert {"3", "mayo", "Cali"} <= set(terms)


# --- Revisión de la entrega US1+US2 --------------------------------------------------------


def test_un_needs_input_sin_evidencia_toma_el_camino_filtrado_aunque_haya_buscar_corpus() -> None:
    # Arrange: pregunta compuesta; la búsqueda no encontró nada y la cotización pide un dato.
    results = [_outcome("buscar_corpus", "empty"), _outcome("cotizar", "needs_input")]

    # Act
    path = gate(results)

    # Assert: la petición del dato no se pierde tras la abstención (FR-005).
    assert path == GATE_NO_QUERY


@pytest.mark.parametrize(
    "text",
    [
        "¿Cuántas unidades necesitas y en qué ciudad de Colombia?",
        "¿Lo quieres en Colombia o en México?",
        "Te doy el precio con IVA cuando me digas la ciudad.",
        "¿Tienes el SKU del producto?",
        "Para cotizar en Colombia o México necesito la ciudad.",
    ],
)
def test_las_preguntas_de_aclaracion_del_asistente_no_se_bloquean(text: str) -> None:
    # Arrange
    allowed = ["Necesito vasos para café"]

    # Act
    terms = unsupported_terms(text, allowed=allowed)

    # Assert
    assert terms == []


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("Carvajal tiene más de veinte mil empleados.", "veinte"),
        ("Su ebitda creció un doce por ciento.", "doce"),
        ("Su ebitda creció un doce por ciento.", "ciento"),
        ("Abrió la planta en dos mil veinticuatro.", "veinticuatro"),
        ("Factura cien veces más que antes.", "cien"),
        ("Fue la primera empresa del país en hacerlo.", "primera"),
        ("Nació a comienzos del siglo XX.", "XX"),
        ("Nació a comienzos del siglo pasado.", "siglo"),
        ("Propal es una empresa del grupo.", "Propal"),
        ("Mepal hace vajillas.", "Mepal"),
        ("Tenemos sedes en bogotá, medellín y barranquilla.", "bogotá"),
        ("Medellín tiene una planta.", "Medellín"),
    ],
)
def test_cifras_en_letras_empresas_y_ciudades_tambien_se_bloquean(text: str, term: str) -> None:
    # Act
    terms = unsupported_terms(text, allowed=["hola, ¿quién eres?"])

    # Assert
    assert term in terms


def test_una_cifra_en_letras_que_trae_la_pregunta_no_se_bloquea() -> None:
    # Arrange
    text = "¿En qué ciudad necesitas los dos mil vasos?"

    # Act
    terms = unsupported_terms(text, allowed=["Necesito dos mil vasos"])

    # Assert
    assert terms == []
