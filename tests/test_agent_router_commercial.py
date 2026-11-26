"""Pruebas de la ruta comercial del router (spec 036, T016-T018 y T020)."""

import pytest

from carvajal.agent.commercial import CommercialRequest
from carvajal.agent.commercial_intent import (
    COMMERCIAL_KEYS,
    USE_FAMILIES,
    extract_commercial,
    family_for_use,
)
from carvajal.agent.router import decide_route
from carvajal.agent.state import Turn
from carvajal.catalog.loader import load_catalog


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


def test_una_ciudad_conocida_se_detecta_aunque_vaya_en_minuscula() -> None:
    decision = decide_route("necesito vasos para café en cali")

    assert decision.route == "commercial"
    assert decision.extra["city"] == "Cali"
    assert decision.extra["country"] == "CO"


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


def test_dos_aclaraciones_seguidas_conservan_lo_anterior() -> None:
    turnos = [_turno("busco vasos de 16 oz"), _turno("caliente")]

    decision = decide_route("en Cali", turnos)

    assert decision.route == "commercial"
    assert decision.extra["capacity_oz"] == 16.0
    assert decision.extra["temperature"] == "caliente"
    assert decision.extra["city"] == "Cali"
    assert decision.extra["country"] == "CO"


def test_la_ciudad_heredada_no_sustituye_la_temperatura_obligatoria() -> None:
    turnos = [_turno("busco vasos de 16 oz")]

    decision = decide_route("en Cali", turnos)

    assert decision.intent == "commercial_clarify"
    assert decision.extra["family"] == "vaso"
    assert decision.extra["city"] == "Cali"


def test_una_pregunta_intermedia_con_senal_corta_la_herencia() -> None:
    turnos = [_turno("busco vasos de 16 oz"), _turno("¿cuánto cuesta?")]

    assert decide_route("caliente", turnos).route == "rag"


def test_la_temperatura_tras_la_ciudad_cierra_la_aclaracion() -> None:
    turnos = [_turno("busco vasos de 16 oz"), _turno("en Cali")]

    decision = decide_route("caliente", turnos)

    assert decision.route == "commercial"
    assert decision.extra["city"] == "Cali"
    assert decision.extra["temperature"] == "caliente"


def test_una_pregunta_institucional_con_producto_y_ciudad_no_es_comercial() -> None:
    assert decide_route("¿Qué vasos vende Carvajal en Cali?").route == "rag"
    assert decide_route("¿Los vasos de 12 oz de Carvajal son compostables?").route == "rag"


def test_la_temperatura_mantiene_comercial_la_pregunta_sobre_carvajal() -> None:
    assert decide_route("¿Carvajal tiene vasos para café caliente?").route == "commercial"


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


def test_una_necesidad_a_la_medida_se_deriva_sin_recomendar() -> None:
    decision = decide_route("Necesito empaques a la medida para exportar flores")

    assert decision.route == "commercial"
    assert decision.intent == "commercial_custom"
    assert set(decision.extra) == set(COMMERCIAL_KEYS)


@pytest.mark.parametrize(
    "texto",
    ["¿Carvajal Empaques produce contenedores de pulpa?", "¿Qué platos y cubiertos hace Carvajal?"],
)
def test_una_pregunta_institucional_sobre_productos_va_a_rag(texto: str) -> None:
    assert decide_route(texto).route == "rag"


@pytest.mark.parametrize(
    "texto",
    [
        "¿Qué empaques flexibles fabrica Carvajal Empaques?",
        "¿Qué bolsas industriales produce Carvajal?",
    ],
)
def test_la_regla_institucional_gana_sobre_la_necesidad_a_la_medida(texto: str) -> None:
    assert decide_route(texto).route == "rag"


def test_un_precio_sin_datos_sigue_siendo_comercial() -> None:
    decision = decide_route("¿Carvajal tiene precios de vasos?")

    assert decision.intent.startswith("commercial")


def test_una_duda_de_entrega_es_parte_de_la_compra() -> None:
    decision = decide_route("Necesito vasos para café de 12 oz. ¿Cuál es el tiempo de entrega?")

    assert decision.intent == "commercial"


def test_la_parte_mixta_se_detecta_sin_puntuacion() -> None:
    decision = decide_route("Necesito vasos para café de 12 oz y quién fundó Carvajal")

    assert decision.intent == "commercial_mixed"
    assert decision.query == "quién fundó Carvajal"


def test_una_respuesta_corta_heredada_no_se_marca_como_mixta() -> None:
    turnos = [_turno("necesito vasos para café de 12 oz")]

    decision = decide_route("caliente, es para la empresa", turnos)

    assert decision.intent == "commercial"
    assert decision.extra["temperature"] == "caliente"


def test_nombrar_a_carvajal_con_datos_de_compra_sigue_siendo_comercial() -> None:
    decision = decide_route("vasos de 12 oz para café de Carvajal en Cali")

    assert decision.route == "commercial"
    assert decision.extra["city"] == "Cali"


def test_una_pregunta_mixta_atiende_lo_comercial_y_guarda_la_otra_parte() -> None:
    decision = decide_route("Necesito vasos para café de 12 oz. ¿Quién fundó Carvajal?")

    assert decision.route == "commercial"
    assert decision.intent == "commercial_mixed"
    assert decision.query == "Quién fundó Carvajal"
    assert decision.extra["capacity_oz"] == 12.0


def test_un_indicador_dentro_de_una_compra_es_mixta() -> None:
    decision = decide_route("necesito vasos de café de 12 oz y los ingresos de Carvajal en 2024")

    assert decision.intent == "commercial_mixed"
    assert decision.query == "el indicador ingresos consolidados de 2024"


def test_una_pregunta_corta_de_precio_no_es_mixta() -> None:
    decision = decide_route("Necesito vasos para café de 12 oz. ¿Cuánto cuestan?")

    assert decision.intent == "commercial"


def test_un_seguimiento_que_nombra_el_producto_hereda_la_solicitud() -> None:
    turnos = [_turno("Necesito 5.000 vasos de 12 oz para café en México")]

    decision = decide_route("¿y si son 4.500 vasos?", turnos)

    assert (decision.route, decision.intent) == ("commercial", "commercial")
    assert decision.extra["quantity"] == 4500
    assert decision.extra["country"] == "MX"
    assert decision.extra["use"] == "bebida_caliente"


def test_un_numero_suelto_en_un_seguimiento_es_la_cantidad() -> None:
    turnos = [_turno("Necesito 5.000 vasos de 12 oz para café en México")]

    decision = decide_route("¿y si son 4.500?", turnos)

    assert (decision.route, decision.intent) == ("commercial", "commercial")
    assert decision.extra["quantity"] == 4500


def test_un_anio_en_un_seguimiento_no_es_una_cantidad() -> None:
    turnos = [_turno("Necesito 5.000 vasos de 12 oz para café en México")]

    assert decide_route("¿y en 2025?", turnos).route == "rag"
    assert decide_route("¿y para 2025?", turnos).route == "rag"


def test_un_seguimiento_hereda_la_cantidad_del_seguimiento_anterior() -> None:
    # «¿y si son 4.500?» respondió la aclaración: el turno siguiente no vuelve a 5.000.
    turnos = [
        _turno("Necesito 5.000 vasos de 12 oz para café en México"),
        _turno("¿y si son 4.500?"),
    ]

    decision = decide_route("¿y en Colombia?", turnos)

    assert (decision.route, decision.intent) == ("commercial", "commercial")
    assert decision.extra["quantity"] == 4500


def test_una_solicitud_nueva_no_hereda_de_una_pregunta_institucional() -> None:
    turnos = [_turno("¿Quién fundó Carvajal?")]

    assert decide_route("busco vasos de 16 oz", turnos).intent == "commercial_clarify"


# --- Cambio de familia por uso (mejora 4 del tercer ensayo, guion «solo_mirar») -----------


def test_un_uso_de_otra_familia_no_hereda_los_vasos() -> None:
    # Arrange
    turnos = [_turno("¿Qué tipos de vasos tienen?")]

    # Act
    solicitud = extract_commercial("¿y para sopa caliente?", turnos)

    # Assert
    assert solicitud is not None
    assert solicitud["family"] == "contenedor"
    assert solicitud["use"] == "sopas"
    assert solicitud["temperature"] == "caliente"


def test_un_uso_de_la_misma_familia_sigue_en_vasos() -> None:
    # Arrange
    turnos = [_turno("¿Qué tipos de vasos tienen?")]

    # Act
    solicitud = extract_commercial("¿y para café?", turnos)

    # Assert
    assert solicitud is not None
    assert solicitud["family"] == "vaso"
    assert solicitud["use"] == "bebida_caliente"


def test_al_cambiar_de_familia_no_se_heredan_capacidad_ni_temperatura_del_vaso() -> None:
    # Arrange: el vaso era de 12 oz para jugo (frío); la cantidad y la ciudad sí siguen.
    turnos = [_turno("necesito 500 vasos de 12 oz para jugo en Cali")]

    # Act
    solicitud = extract_commercial("¿y para sopa?", turnos)

    # Assert
    assert solicitud is not None
    assert (solicitud["family"], solicitud["use"]) == ("contenedor", "sopas")
    assert solicitud["capacity_oz"] is None
    assert solicitud["temperature"] == "caliente"
    assert (solicitud["quantity"], solicitud["city"]) == (500, "Cali")


def test_un_uso_de_bebida_tras_contenedores_vuelve_a_vasos() -> None:
    # Arrange
    turnos = [_turno("necesito contenedores para sopa que vayan al microondas")]

    # Act
    solicitud = extract_commercial("¿y para café?", turnos)

    # Assert
    assert solicitud is not None
    assert solicitud["family"] == "vaso"
    assert solicitud["microwave"] is None


def test_un_plato_hondo_sigue_sirviendo_para_sopa() -> None:
    # Arrange: dentro de la comida no se cambia de familia (el catálogo declara platos
    # hondos para sopa).
    turnos = [_turno("busco platos desechables")]

    # Act
    solicitud = extract_commercial("¿y para sopa?", turnos)

    # Assert
    assert solicitud is not None
    assert solicitud["family"] == "plato"


def test_el_uso_de_un_turno_intermedio_no_devuelve_la_familia_vieja() -> None:
    # Arrange: el turno actual no nombra uso; la sopa llegó en el turno intermedio.
    turnos = [_turno("necesito vasos"), _turno("para sopa")]

    # Act
    solicitud = extract_commercial("500 unidades en Cali", turnos)

    # Assert
    assert solicitud is not None
    assert (solicitud["family"], solicitud["use"]) == ("contenedor", "sopas")
    assert (solicitud["quantity"], solicitud["city"]) == (500, "Cali")


def test_un_seguimiento_sin_uso_conserva_los_vasos_para_comida_caliente() -> None:
    # Arrange: la persona eligió vasos para comida caliente (el catálogo de México los
    # declara); el seguimiento de capacidad no nombra uso ni familia.
    turnos = [_turno("¿tienen vasos para comida caliente en México?")]

    # Act
    solicitud = extract_commercial("¿y de 16 onzas?", turnos)

    # Assert
    assert solicitud is not None
    assert (solicitud["family"], solicitud["use"]) == ("vaso", "comida_caliente")
    assert solicitud["capacity_oz"] == 16.0


@pytest.mark.parametrize(
    ("pregunta", "esperada"),
    [
        # Diseño declarado de la mejora 4: un uso de comida sin familia deja los vasos. La
        # capacidad que la persona dice en el mismo turno sí vale para el producto nuevo.
        ("¿y de 16 oz para el almuerzo?", ("contenedor", "comida_caliente", 16.0)),
        ("¿y para una cafetería que vende postres?", ("contenedor", "panaderia", None)),
        ("¿y para el evento con pasteles?", ("contenedor", "panaderia", None)),
    ],
)
def test_un_uso_de_comida_tras_vasos_cambia_de_familia(
    pregunta: str, esperada: tuple[str, str, float | None]
) -> None:
    # Arrange
    turnos = [_turno("necesito 500 vasos de 12 oz en Cali")]

    # Act
    solicitud = extract_commercial(pregunta, turnos)

    # Assert
    assert solicitud is not None
    assert (solicitud["family"], solicitud["use"], solicitud["capacity_oz"]) == esperada
    assert solicitud["quantity"] == 500


@pytest.mark.parametrize(
    ("familia", "uso", "esperada"),
    [
        ("vaso", "sopas", "contenedor"),
        ("vaso", "ensaladas", "contenedor"),
        ("vaso", "comida_caliente", "contenedor"),
        ("vaso", "salsas", "envase"),
        ("contenedor", "bebida_fria", "vaso"),
        ("vaso", "bebida_caliente", "vaso"),
        ("tapa", "sopas", "tapa"),
        ("cubierto", "comida_caliente", "cubierto"),
        ("charola", "frituras", "charola"),
        ("vaso", None, "vaso"),
        (None, "sopas", None),
    ],
)
def test_family_for_use(familia: str | None, uso: str | None, esperada: str | None) -> None:
    # Act
    resultado = family_for_use(familia, uso)

    # Assert
    assert resultado == esperada


def test_la_familia_de_cada_uso_esta_en_el_catalogo() -> None:
    # Arrange: usos que el catálogo declara en sus fichas → familias que los declaran.
    declaradas: dict[str, set[str]] = {}
    for referencia in load_catalog().references:
        for uso in referencia.uses:
            declaradas.setdefault(uso, set()).add(referencia.family)

    # Act / Assert: la familia preferida de un uso declarado es una de las que lo declaran.
    # «comida_fria» solo la declaran las bandejas espumadas de exhibición (charolas): para
    # un seguimiento como «¿y para comida fría?» se prefiere el contenedor.
    for uso, familias in USE_FAMILIES.items():
        if uso in declaradas and uso != "comida_fria":
            assert familias[0] in declaradas[uso], uso
    assert USE_FAMILIES["sopas"][0] == "contenedor"
