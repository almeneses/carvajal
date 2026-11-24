"""Línea de estado del modo ``tools`` (spec 044, T022, FR-018).

Cada herramienta que empieza publica ``{"type": "status", "text", "step"}`` por el canal
``custom`` de LangGraph. El texto sale de plantillas fijas: sin nombres internos, sin
resultados, precios ni fuentes; solo el dato principal que aportó la persona, saneado.
"""

from typing import Any, TypedDict, cast

import pytest
from langgraph.graph import END, START, StateGraph

from carvajal.agent.toolagent.state import (
    TOOL_BUSCAR_CORPUS,
    TOOL_BUSCAR_PRODUCTO,
    TOOL_BUSCAR_SEDES,
    TOOL_COTIZAR,
    TOOL_INDICADOR_FINANCIERO,
    TOOL_LISTAR_MEMORIA,
    TOOL_NAMES,
    TOOL_OLVIDAR_PREFERENCIA,
    TOOL_RECORDAR_PREFERENCIA,
    TOOL_SIN_CONSULTA,
)
from carvajal.agent.toolagent.status import MAX_VALUE_CHARS, emit_status, status_text

# Herramientas que sí publican estado (todas menos sin_consulta).
STATUS_TOOLS = tuple(name for name in TOOL_NAMES if name != TOOL_SIN_CONSULTA)

# Argumentos completos y vacíos por herramienta, con los nombres de los esquemas.
FULL_ARGS: dict[str, dict[str, Any]] = {
    TOOL_BUSCAR_CORPUS: {"consulta": "quién fundó Carvajal", "alcance": "todo"},
    TOOL_BUSCAR_PRODUCTO: {"familia": "vaso", "uso": "bebida_caliente", "capacidad_oz": 12},
    TOOL_COTIZAR: {"family": "vaso", "quantity": 2000, "city": "Bogotá"},
    TOOL_BUSCAR_SEDES: {"ciudad": "Cali", "sku": "ABC-123"},
    TOOL_INDICADOR_FINANCIERO: {"nombre": "ingresos", "periodo": "2024"},
    # Memoria (spec 045): la línea de estado nunca lleva el valor (FR-018).
    TOOL_RECORDAR_PREFERENCIA: {"categoria": "nombre", "valor": "Ana Secreta"},
    TOOL_LISTAR_MEMORIA: {},
    TOOL_OLVIDAR_PREFERENCIA: {"categoria": "nota", "valor": "compostables"},
}


# --- status_text: plantillas por herramienta -------------------------------------------


def test_buscar_corpus_uses_fixed_text_without_query():
    # Arrange
    args = FULL_ARGS[TOOL_BUSCAR_CORPUS]

    # Act
    text = status_text(TOOL_BUSCAR_CORPUS, args)

    # Assert
    assert text == "Estoy buscando en la información de Carvajal…"


def test_buscar_producto_with_family_names_the_product():
    # Arrange
    args = {"familia": "vaso", "uso": "bebida_caliente"}

    # Act
    text = status_text(TOOL_BUSCAR_PRODUCTO, args)

    # Assert
    assert text == "Estoy revisando qué vasos te sirven…"


def test_buscar_producto_without_family_falls_back_to_catalog():
    # Arrange
    args = {"uso": "bebida_caliente", "temperatura": None}

    # Act
    text = status_text(TOOL_BUSCAR_PRODUCTO, args)

    # Assert
    assert text == "Estoy revisando el catálogo…"


def test_buscar_producto_with_free_text_family_keeps_it():
    # Arrange
    args = {"familia": "bandejas"}

    # Act
    text = status_text(TOOL_BUSCAR_PRODUCTO, args)

    # Assert
    assert text == "Estoy revisando qué bandejas te sirven…"


def test_cotizar_with_all_data_formats_quantity_with_thousands_separator():
    # Arrange
    args = {"family": "vaso", "quantity": 2000, "city": "Bogotá"}

    # Act
    text = status_text(TOOL_COTIZAR, args)

    # Assert
    assert text == "Estoy calculando cuánto te salen 2.000 vasos en Bogotá…"


def test_cotizar_accepts_spanish_argument_names():
    # Arrange
    args = {"familia": "contenedor", "cantidad": 1500000, "ciudad": "Medellín"}

    # Act
    text = status_text(TOOL_COTIZAR, args)

    # Assert
    assert text == "Estoy calculando cuánto te salen 1.500.000 contenedores en Medellín…"


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"family": "vaso", "quantity": 1}, "Estoy calculando cuánto te sale 1 vaso…"),
        ({"family": "tapa"}, "Estoy calculando cuánto te salen tus tapas…"),
        (
            {"family": "tapa", "city": "Cali"},
            "Estoy calculando cuánto te salen tus tapas en Cali…",
        ),
        ({"quantity": 300}, "Estoy calculando cuánto te salen 300 unidades…"),
        (
            {"quantity": 300, "city": "Cali"},
            "Estoy calculando cuánto te salen 300 unidades en Cali…",
        ),
        ({"city": "Cali"}, "Estoy calculando tu cotización en Cali…"),
        ({}, "Estoy calculando tu cotización…"),
    ],
)
def test_cotizar_variants_with_partial_data(args, expected):
    # Arrange
    # (los argumentos vienen parametrizados)

    # Act
    text = status_text(TOOL_COTIZAR, args)

    # Assert
    assert text == expected


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        ("2000", "2.000"),
        (2000.0, "2.000"),
        ("2.000", "2.000"),
        (" 12 000 ", "12.000"),
    ],
)
def test_cotizar_normalizes_quantity_representations(quantity, expected):
    # Arrange
    args = {"family": "vaso", "quantity": quantity}

    # Act
    text = status_text(TOOL_COTIZAR, args)

    # Assert
    assert text == f"Estoy calculando cuánto te salen {expected} vasos…"


@pytest.mark.parametrize("quantity", [0, -5, 2.5, True, "muchos", None, 10**15])
def test_cotizar_ignores_invalid_quantity(quantity):
    # Arrange
    args = {"family": "vaso", "quantity": quantity}

    # Act
    text = status_text(TOOL_COTIZAR, args)

    # Assert
    assert text == "Estoy calculando cuánto te salen tus vasos…"


def test_buscar_sedes_with_city():
    # Arrange
    args = {"ciudad": "Barranquilla", "sku": "ABC-123"}

    # Act
    text = status_text(TOOL_BUSCAR_SEDES, args)

    # Assert
    assert text == "Estoy buscando dónde comprar en Barranquilla…"


def test_buscar_sedes_without_city():
    # Arrange
    args = {"ciudad": "   "}

    # Act
    text = status_text(TOOL_BUSCAR_SEDES, args)

    # Assert
    assert text == "Estoy buscando dónde puedes comprar…"


def test_indicador_financiero_uses_fixed_text():
    # Arrange
    args = FULL_ARGS[TOOL_INDICADOR_FINANCIERO]

    # Act
    text = status_text(TOOL_INDICADOR_FINANCIERO, args)

    # Assert
    assert text == "Estoy consultando las cifras de Carvajal…"


def test_city_written_in_lowercase_is_capitalized():
    # Arrange
    args = {"ciudad": "santa marta"}

    # Act
    text = status_text(TOOL_BUSCAR_SEDES, args)

    # Assert
    assert text == "Estoy buscando dónde comprar en Santa Marta…"


def test_sin_consulta_has_no_status():
    # Arrange
    args = {"motivo": "pedir_dato", "dato": "ciudad"}

    # Act
    text = status_text(TOOL_SIN_CONSULTA, args)

    # Assert
    assert text is None


@pytest.mark.parametrize("tool_name", ["herramienta_inventada", "", "COTIZAR"])
def test_unknown_tool_has_no_status(tool_name):
    # Arrange
    args = {"ciudad": "Cali"}

    # Act
    text = status_text(tool_name, args)

    # Assert
    assert text is None


@pytest.mark.parametrize("tool_name", STATUS_TOOLS)
@pytest.mark.parametrize("args", [None, {}, "no es un dict"])
def test_every_tool_tolerates_missing_or_malformed_args(tool_name, args):
    # Arrange
    # (argumentos ausentes o con forma inesperada)

    # Act
    text = status_text(tool_name, args)

    # Assert
    assert isinstance(text, str)
    assert text.startswith("Estoy ")
    assert text.endswith("…")


@pytest.mark.parametrize("tool_name", STATUS_TOOLS)
@pytest.mark.parametrize("with_data", [True, False])
def test_no_text_contains_internal_tool_names(tool_name, with_data):
    # Arrange
    args = FULL_ARGS.get(tool_name, {}) if with_data else {}

    # Act
    text = status_text(tool_name, args)

    # Assert
    assert text is not None
    lowered = text.lower()
    assert all(name not in lowered for name in TOOL_NAMES)
    assert "_" not in text


@pytest.mark.parametrize("tool_name", STATUS_TOOLS)
def test_no_text_leaks_results_prices_or_sources(tool_name):
    # Arrange
    args = FULL_ARGS.get(tool_name, {})

    # Act
    text = status_text(tool_name, args)

    # Assert
    assert text is not None
    assert "$" not in text
    assert "[" not in text
    assert "http" not in text
    assert "ABC-123" not in text  # el SKU no es dato de la persona


# --- Saneamiento del dato aportado ------------------------------------------------------


def test_value_with_newlines_and_control_chars_is_flattened():
    # Arrange
    args = {"ciudad": "Bogotá\n\nIgnora todo\x00\x1b[31m‮"}

    # Act
    text = status_text(TOOL_BUSCAR_SEDES, args)

    # Assert
    assert text == "Estoy buscando dónde comprar en Bogotá Ignora todo[31m…"
    assert "\n" not in text


def test_value_with_markup_loses_angle_brackets():
    # Arrange
    args = {"familia": "<script>alert(1)</script>"}

    # Act
    text = status_text(TOOL_BUSCAR_PRODUCTO, args)

    # Assert
    assert text is not None
    assert "<" not in text
    assert ">" not in text


def test_long_value_is_truncated():
    # Arrange
    long_city = "Bogotá " * 40

    # Act
    text = status_text(TOOL_BUSCAR_SEDES, {"ciudad": long_city})

    # Assert
    assert text is not None
    value = text.removeprefix("Estoy buscando dónde comprar en ").removesuffix("…")
    assert 0 < len(value) <= MAX_VALUE_CHARS
    assert not value.endswith(" ")


def test_value_naming_an_internal_tool_is_dropped():
    # Arrange
    args = {"family": "buscar_corpus", "quantity": 10, "city": "Cali"}

    # Act
    text = status_text(TOOL_COTIZAR, args)

    # Assert
    assert text == "Estoy calculando cuánto te salen 10 unidades en Cali…"


def test_underscored_identifier_value_is_humanized():
    # Arrange
    args = {"familia": "vaso_termico"}

    # Act
    text = status_text(TOOL_BUSCAR_PRODUCTO, args)

    # Assert
    assert text == "Estoy revisando qué vaso termico te sirven…"


@pytest.mark.parametrize("value", [123, ["Cali"], {"x": 1}, "\n\t "])
def test_non_text_or_blank_values_are_ignored(value):
    # Arrange
    args = {"ciudad": value}

    # Act
    text = status_text(TOOL_BUSCAR_SEDES, args)

    # Assert
    assert text == "Estoy buscando dónde puedes comprar…"


# --- emit_status ------------------------------------------------------------------------


def test_emit_status_outside_graph_does_not_fail():
    # Arrange
    args = {"ciudad": "Cali"}

    # Act
    event = emit_status(TOOL_BUSCAR_SEDES, args, step=1)

    # Assert
    assert event == {"type": "status", "text": "Estoy buscando dónde comprar en Cali…", "step": 1}


def test_emit_status_for_sin_consulta_emits_nothing():
    # Arrange
    args = {"motivo": "conversacion"}

    # Act
    event = emit_status(TOOL_SIN_CONSULTA, args, step=1)

    # Assert
    assert event is None


@pytest.mark.parametrize("step", [0, -1])
def test_emit_status_rejects_non_positive_step(step):
    # Arrange
    args = {"ciudad": "Cali"}

    # Act / Assert
    with pytest.raises(ValueError, match="step"):
        emit_status(TOOL_BUSCAR_SEDES, args, step=step)


class _MiniState(TypedDict, total=False):
    calls: list[tuple[str, dict[str, Any]]]


def _build_status_graph():
    """Grafo mínimo: un nodo que «ejecuta» herramientas en serie y publica su estado."""

    def tools_node(state: _MiniState) -> _MiniState:
        for step, (name, args) in enumerate(state.get("calls", []), start=1):
            emit_status(name, args, step=step)
        return {}

    builder = StateGraph(cast(Any, _MiniState))
    builder.add_node("tools", tools_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile()


def test_emit_status_inside_graph_publishes_custom_events_in_order():
    # Arrange
    graph = _build_status_graph()
    calls = [
        (TOOL_BUSCAR_PRODUCTO, {"familia": "vaso"}),
        (TOOL_SIN_CONSULTA, {"motivo": "pedir_dato"}),
        (TOOL_COTIZAR, {"family": "vaso", "quantity": 2000, "city": "Bogotá"}),
    ]

    # Act
    chunks = list(graph.stream({"calls": calls}, stream_mode=["custom"]))

    # Assert
    assert chunks == [
        ("custom", {"type": "status", "text": "Estoy revisando qué vasos te sirven…", "step": 1}),
        (
            "custom",
            {
                "type": "status",
                "text": "Estoy calculando cuánto te salen 2.000 vasos en Bogotá…",
                "step": 3,
            },
        ),
    ]


def test_emit_status_inside_graph_without_custom_mode_is_silent():
    # Arrange
    graph = _build_status_graph()
    calls = [(TOOL_BUSCAR_CORPUS, {"consulta": "historia"})]

    # Act
    result = graph.invoke({"calls": calls})

    # Assert
    assert result == {"calls": calls}


# --- Memoria (spec 045, T042) ----------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name", [TOOL_RECORDAR_PREFERENCIA, TOOL_LISTAR_MEMORIA, TOOL_OLVIDAR_PREFERENCIA]
)
@pytest.mark.parametrize(
    "args",
    [
        {"categoria": "nombre", "valor": "Ana Secreta"},
        {"categoria": "ciudad_habitual", "valor": "Cali"},
        {"categoria": "nota", "valor": "mi cédula es 1.234.567.890"},
    ],
)
def test_las_lineas_de_estado_de_memoria_no_llevan_valores(tool_name, args):
    # Act
    text = status_text(tool_name, args)

    # Assert
    assert text is not None
    assert text.startswith("Estoy ")
    assert args["valor"] not in text
    assert "Ana" not in text and "Cali" not in text and "1.234" not in text
    assert args["categoria"] not in text
