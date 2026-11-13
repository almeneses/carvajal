"""Pruebas del router determinista, la herramienta y sus parámetros."""

import pytest
from pydantic import ValidationError

from carvajal.agent.router import decide_route, extract_period
from carvajal.agent.state import Turn
from carvajal.agent.tools import IndicatorQuery, find_indicator, indicator_lookup

TURNS: list[Turn] = [
    {
        "question": "¿En qué ciudad fue fundada Carvajal?",
        "answer": "Carvajal fue fundada en Cali [1].",
        "incomplete": False,
    }
]


def test_un_saludo_va_a_direct_sin_busqueda() -> None:
    # Act
    decision = decide_route("Hola, ¿qué puedes hacer?")

    # Assert
    assert decision.route == "direct"
    assert decision.intent == "greeting"
    assert decision.tool_name == ""


def test_una_consulta_de_indicador_va_a_tool_con_periodo() -> None:
    # Act
    decision = decide_route("¿Cuál fue el EBITDA consolidado a septiembre de 2025?")

    # Assert
    assert decision.route == "tool"
    assert decision.tool_name == "ebitda"
    assert decision.tool_period == "2025-09"


def test_indicador_sin_periodo_pide_aclaracion_en_direct() -> None:
    # Act
    decision = decide_route("¿Cuántos empleados tiene Carvajal?")

    # Assert
    assert decision.route == "direct"
    assert decision.intent == "missing_period"


def test_un_saludo_con_pregunta_documental_no_es_directo() -> None:
    # Act
    decision = decide_route("Hola, ¿cuál es la misión de Carvajal?")

    # Assert
    assert decision.route == "rag"


def test_una_pregunta_corta_sin_marca_anafórica_no_es_seguimiento() -> None:
    # Act
    decision = decide_route("¿Qué es LiquidezYa?", TURNS)

    # Assert
    assert decision.route == "rag"
    assert decision.intent == "documental"


def test_un_seguimiento_eliptico_reescribe_con_el_turno_anterior() -> None:
    # Act
    decision = decide_route("¿Y en qué año ocurrió eso?", TURNS)

    # Assert
    assert decision.route == "rag"
    assert decision.intent == "follow_up"
    assert "ciudad fue fundada" in decision.query
    assert "año ocurrió eso" in decision.query


def test_una_consulta_documental_va_a_rag() -> None:
    # Act
    decision = decide_route("¿Qué iniciativas de sostenibilidad adelanta Carvajal?")

    # Assert
    assert decision.route == "rag"
    assert decision.intent == "documental"


def test_la_consulta_de_fundacion_se_expande_con_datos_del_corpus() -> None:
    # Act
    decision = decide_route("¿En qué ciudad fue fundada Carvajal?")

    # Assert
    assert decision.route == "rag"
    assert "1904 Cali Colombia" in decision.query


def test_una_preferencia_se_marca_para_guardar() -> None:
    # Act
    decision = decide_route("Recuerda que prefiero respuestas breves")

    # Assert
    assert decision.route == "direct"
    assert decision.intent == "memory_write"
    assert "respuestas breves" in decision.preference


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("en 2024", "2024"),
        ("a septiembre de 2025", "2025-09"),
        ("tercer trimestre de 2024", "2024-09"),
        ("2025-09", "2025-09"),
        ("sin año", ""),
    ],
)
def test_extrae_periodos_explicitos(texto: str, esperado: str) -> None:
    assert extract_period(texto) == esperado


def test_herramienta_devuelve_un_registro_verificable() -> None:
    # Act
    result = find_indicator("ingresos", "2024")

    # Assert
    assert result.found is True
    assert result.value == 3.96
    assert result.source_url.startswith("https://caliescribe.com/")
    assert "3.96 billones" in result.quote


def test_herramienta_devuelve_ausencia_estable() -> None:
    # Act
    result = find_indicator("ebitda", "2024")

    # Assert
    assert result.found is False
    assert result.value is None
    assert "No hay registro" in result.message


def test_parametros_invalidos_fallan_antes_de_leer() -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        IndicatorQuery(name="indicador-inventado", period="2024")
    with pytest.raises(ValidationError):
        IndicatorQuery(name="ingresos", period="2024-13")
    with pytest.raises(ValidationError):
        indicator_lookup.invoke({"name": "ingresos", "period": "no-es-un-periodo"})
