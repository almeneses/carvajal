"""Pruebas de la ruta comercial del router (spec 036, T016-T018 y T020)."""

import pytest

from carvajal.agent.commercial import CommercialRequest
from carvajal.agent.commercial_intent import COMMERCIAL_KEYS, extract_commercial
from carvajal.agent.router import decide_route
from carvajal.agent.state import Turn


def _turno(pregunta: str) -> Turn:
    return {"question": pregunta, "answer": "…", "incomplete": False}


def test_las_claves_extraidas_son_las_del_contrato() -> None:
    decision = decide_route("necesito vasos para café caliente de 12 oz")

    assert set(decision.extra) == set(COMMERCIAL_KEYS)
    CommercialRequest(**decision.extra)  # el nodo construye la solicitud sin traducir claves


def test_una_solicitud_completa_va_a_commercial_con_sus_datos() -> None:
    decision = decide_route("Necesito 5.000 vasos de 12 oz para café en México")

    assert decision.route == "commercial"
    assert decision.extra["family"] == "vaso"
    assert decision.extra["use"] == "bebida_caliente"
    assert decision.extra["temperature"] == "caliente"
    assert decision.extra["capacity_oz"] == 12.0
    assert decision.extra["quantity"] == 5000
    assert decision.extra["country"] == "MX"
    assert "vaso" in decision.reason  # FR-012: la razón registra los datos extraídos


def test_microondas_implica_contenido_caliente() -> None:
    decision = decide_route("contenedores para sopa que se puedan calentar en microondas")

    assert decision.route == "commercial"
    assert decision.extra["use"] == "sopas"
    assert decision.extra["temperature"] == "caliente"
    assert decision.extra["microwave"] is True


@pytest.mark.parametrize(
    ("texto", "onzas"),
    [("charolas para ensalada de 500 ml", 16.91), ("vasos para jugo de 1 litro", 33.81)],
)
def test_convierte_mililitros_y_litros_a_onzas(texto: str, onzas: float) -> None:
    assert decide_route(texto).extra["capacity_oz"] == onzas


@pytest.mark.parametrize(
    ("texto", "cantidad"),
    [
        ("necesito 5.000 vasos de 12 oz para café", 5000),
        ("cubiertos para comida caliente, 3 mil", 3000),
        ("vasos para café, unas 2000", 2000),
        ("200 contenedores para comida caliente", 200),
    ],
)
def test_extrae_la_cantidad_sin_confundirla_con_la_capacidad(texto: str, cantidad: int) -> None:
    assert decide_route(texto).extra["quantity"] == cantidad


def test_cafe_frio_no_se_lee_como_bebida_caliente() -> None:
    decision = decide_route("vasos para café frío de 16 oz")

    assert decision.extra["use"] == "bebida_fria"
    assert decision.extra["temperature"] == "fria"


def test_la_familia_mencionada_primero_es_la_pedida() -> None:
    assert decide_route("tapas para vasos de bebida caliente").extra["family"] == "tapa"


def test_una_ciudad_conocida_implica_el_pais() -> None:
    decision = decide_route("vasos de papel para café en Cali")

    assert decision.extra["city"] == "Cali"
    assert decision.extra["country"] == "CO"
    assert decision.extra["prefers_renewable"] is True


def test_una_ciudad_sin_distribuidor_se_conserva_para_listar_alternativas() -> None:
    decision = decide_route("necesito vasos para jugo en Tuluá")

    assert decision.extra["city"] == "Tuluá"
    assert decision.extra["country"] is None


def test_sin_temperatura_ni_uso_pide_aclaracion_en_direct() -> None:
    decision = decide_route("busco vasos de 16 oz")

    assert decision.route == "direct"
    assert decision.intent == "commercial_clarify"
    assert decision.extra["capacity_oz"] == 16.0


def test_la_respuesta_a_la_aclaracion_hereda_la_solicitud() -> None:
    turnos = [_turno("busco vasos de 16 oz")]

    decision = decide_route("caliente, unas 3000", turnos)

    assert decision.route == "commercial"
    assert decision.extra["family"] == "vaso"
    assert decision.extra["capacity_oz"] == 16.0
    assert decision.extra["use"] == "bebida_caliente"
    assert decision.extra["quantity"] == 3000


def test_el_seguimiento_conserva_lo_dicho_y_actualiza_lo_nuevo() -> None:
    turnos = [_turno("necesito vasos para café de 12 oz"), _turno("en Colombia")]

    decision = decide_route("en Medellín", turnos)

    assert decision.route == "commercial"
    assert decision.extra["capacity_oz"] == 12.0
    assert decision.extra["city"] == "Medellín"
    assert decision.extra["country"] == "CO"


def test_una_pregunta_institucional_tras_un_turno_comercial_no_se_hereda() -> None:
    turnos = [_turno("necesito vasos para café de 12 oz")]

    assert decide_route("¿Qué presencia tiene Carvajal en Cali?", turnos).route == "rag"


def test_una_pregunta_corta_no_hereda_la_aclaración_comercial() -> None:
    turnos = [_turno("necesito vasos para café de 12 oz")]

    decision = decide_route("¿Qué presencia tiene en Cali?", turnos)

    assert decision.route == "rag"
    assert decision.extra == {}


def test_la_pregunta_corta_del_revisor_no_activa_la_ruta_comercial() -> None:
    turnos = [_turno("necesito vasos para café de 12 oz")]

    decision = decide_route("¿Cuántos empleados hay en Cali?", turnos)

    assert decision.route != "commercial"
    assert decision.extra == {}


def test_la_respuesta_a_la_aclaración_puede_ser_solo_la_temperatura() -> None:
    turnos = [_turno("busco vasos de 16 oz")]

    decision = decide_route("caliente", turnos)

    assert decision.route == "commercial"
    assert decision.extra["family"] == "vaso"
    assert decision.extra["temperature"] == "caliente"


def test_un_saludo_intermedio_no_pierde_la_aclaración() -> None:
    turnos = [_turno("busco vasos de 16 oz"), _turno("hola")]

    decision = decide_route("caliente", turnos)

    assert decision.route == "commercial"
    assert decision.extra["capacity_oz"] == 16.0


@pytest.mark.parametrize(
    "texto",
    [
        "¿Qué vasos fabrica Carvajal Empaques?",
        "¿Qué vasos de papel fabrica Carvajal Empaques?",
        "¿Qué empresas conforman el sector de Papel y Empaques?",
        "¿Qué te puedo preguntar?",
    ],
)
def test_sin_senal_de_compra_ni_datos_no_es_comercial(texto: str) -> None:
    assert extract_commercial(texto) is None
    assert decide_route(texto).route == "rag"


def test_las_rutas_existentes_conservan_su_decision() -> None:
    assert decide_route("hola").route == "direct"
    assert decide_route("¿Cuáles fueron los ingresos de Carvajal en 2024?").route == "tool"
    assert decide_route("recuerda que prefiero respuestas cortas").intent == "memory_write"
