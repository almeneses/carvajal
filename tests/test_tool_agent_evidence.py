"""Pruebas de la compuerta de evidencia del modo ``tools`` (spec 044, T029; research R2, R3).

- ``gate`` decide el camino del turno sobre ``ToolOutcome.status`` y ``source_ids``:
  ``evidence`` (alguna herramienta ``ok`` con citas), ``abstain`` (sin evidencia y hubo
  ``buscar_corpus``) o ``no_query`` (el resto: solo ``sin_consulta``, herramientas sin
  evidencia que no buscan en el corpus o algún ``needs_input``).
- ``filter_unsupported`` aplica el filtro de la ruta directa al texto del camino
  ``no_query``: cifras, fechas, montos o nombres propios de Carvajal que no vengan de la
  pregunta ni de los resultados de las herramientas obligan a abstenerse.
"""

import json
from typing import Any

import pytest

from carvajal.agent.toolagent.evidence import (
    GATE_ABSTAIN,
    GATE_EVIDENCE,
    GATE_NO_QUERY,
    ask_for_missing,
    filter_unsupported,
    gate,
    has_evidence,
    missing_fields,
    neutral_only,
    sites_without_price,
    unsupported_terms,
)
from carvajal.agent.toolagent.state import ToolOutcome, make_tool_outcome
from carvajal.prompts import NO_CONTEXT_ANSWER


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


# --- Datos de herramientas sin evidencia (simulación spec 044: s-clientes-06, 07 y 26) -----

# Lo que devolvió ``cotizar`` en s-clientes-07 turno 1 (``empty`` con sedes citadas) y el
# contexto comercial con el que empezó el turno (``capacity_oz`` llega como ``14.0``).
SEDES_MANIZALES = (
    '{"status": "empty", "missing": [], "reutilizado": [], "sedes": [{"n": 1, "nombre": '
    '"Kive Andina", "direccion": "Parque Industrial Juanchito, terraza 3, lote 12", '
    '"telefono": "606 874 7455", "ciudad": "Manizales"}], "ciudades_disponibles": '
    '["Bogotá", "Manizales", "Pereira"]}'
)
CONTEXTO_14_OZ = '{"family": "vaso", "use": "bebida_fria", "capacity_oz": 14.0, "country": "CO"}'


def test_cotizar_vacio_con_sedes_citadas_no_cae_en_la_abstencion_del_corpus() -> None:
    # Arrange: la búsqueda documental no encontró nada, pero la cotización trajo sedes.
    results = [_outcome("buscar_corpus", "empty"), _outcome("cotizar", "empty", (1, 2))]

    # Act
    path = gate(results)

    # Assert: las sedes respaldan la respuesta; se filtra en vez de abstenerse.
    assert not has_evidence(results)
    assert path == GATE_NO_QUERY


def test_pedir_un_dato_con_sin_consulta_tampoco_cae_en_la_abstencion_del_corpus() -> None:
    # Arrange: el modelo busca en el corpus y además pide un dato a la persona.
    pedir = make_tool_outcome(
        tool="sin_consulta", args={"motivo": "pedir_dato", "dato": "ciudad"}, status="ok"
    )
    results = [_outcome("buscar_corpus", "empty"), pedir]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_NO_QUERY


def test_sin_datos_de_otras_herramientas_buscar_corpus_vacio_sigue_absteniendose() -> None:
    # Arrange: herramientas sin citas ni petición de datos no salvan la abstención.
    results = [_outcome("buscar_corpus", "empty"), _outcome("buscar_producto", "empty")]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_ABSTAIN


def test_un_sin_consulta_invalido_no_saca_el_turno_de_la_abstencion() -> None:
    # Arrange: ``pedir_dato`` con argumentos inválidos (``status == "error"``) no declara nada.
    pedir = make_tool_outcome(tool="sin_consulta", args={"motivo": "pedir_dato"}, status="error")
    results = [_outcome("buscar_corpus", "empty"), pedir]

    # Act
    path = gate(results)

    # Assert
    assert path == GATE_ABSTAIN
    assert neutral_only(results) is False


def _pedir_dato() -> ToolOutcome:
    return make_tool_outcome(
        tool="sin_consulta", args={"motivo": "pedir_dato", "dato": "ciudad"}, status="ok"
    )


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        pytest.param(
            [_outcome("buscar_corpus", "empty"), _pedir_dato()], True, id="corpus-vacio+pedir"
        ),
        pytest.param(
            [_outcome("buscar_corpus", "empty"), _outcome("cotizar", "empty", (1, 2))],
            True,
            id="corpus-vacio+sedes",
        ),
        pytest.param(
            [_outcome("buscar_corpus", "empty"), _outcome("cotizar", "needs_input")],
            False,
            id="corpus-vacio+needs-input-de-la-herramienta",
        ),
        pytest.param(
            [_outcome("buscar_corpus", "empty"), _pedir_dato(), _outcome("cotizar", "needs_input")],
            False,
            id="pedir+needs-input",
        ),
        pytest.param([_pedir_dato()], False, id="pedir-sin-corpus"),
        pytest.param([_outcome("cotizar", "empty", (1,))], False, id="sedes-sin-corpus"),
        pytest.param([_outcome("buscar_corpus", "empty")], False, id="abstencion"),
        pytest.param(
            [_outcome("buscar_corpus", "ok", (1,)), _pedir_dato()], False, id="con-evidencia"
        ),
    ],
)
def test_solo_el_reemplazo_neutro_cuando_el_corpus_no_respondio_y_nadie_pidio_el_dato(
    results: list[ToolOutcome], expected: bool
) -> None:
    # Act
    blocked = neutral_only(results)

    # Assert: la declaración del modelo o las sedes no liberan su redacción tras buscar en
    # el corpus sin evidencia; un ``needs_input`` (lo decide la herramienta) sí la filtra.
    assert blocked is expected


def test_riesgo_conocido_la_capacidad_respalda_la_misma_cifra_con_otra_unidad() -> None:
    # Arrange: el filtro compara cifras sin su unidad (docstring de ``_canonical``).
    allowed = ['{"capacity_oz": 14.0}']

    # Act
    same = unsupported_terms("Vasos de 14 oz; entrega en 14 días.", allowed=allowed)
    other = unsupported_terms("Entrega en 140 días.", allowed=allowed)

    # Assert: «14» queda respaldado con cualquier unidad (limitación aceptada); otra cifra no.
    assert same == []
    assert other == ["140"]


def test_una_cifra_decimal_entera_de_los_datos_respalda_la_cifra_sin_decimales() -> None:
    # Arrange: s-clientes-07 turno 1; «14.0» del contexto se bloqueaba como «140».
    text = (
        "No hay precio publicado para vasos de 14 oz en Manizales. Sí hay una sede:\n"
        "- Kive Andina — Parque Industrial Juanchito, terraza 3, lote 12 — 606 874 7455 [1]"
    )
    allowed = ["Serían unas 2.000 unidades en Manizales.", SEDES_MANIZALES, CONTEXTO_14_OZ]

    # Act
    terms = unsupported_terms(text, allowed=allowed)

    # Assert
    assert terms == []


@pytest.mark.parametrize(
    ("allowed", "text", "term"),
    [
        pytest.param(
            '{"capacity_oz": 14.0}', "Tenemos vasos de 140 oz.", "140", id="14.0-no-es-140"
        ),
        pytest.param('{"quantity": 2.000}', "Te cotizo 2 vasos.", "2", id="miles-no-se-recortan"),
        pytest.param('{"capacity_oz": 3.5}', "El vaso de 35 oz.", "35", id="3.5-no-es-35-entero"),
    ],
)
def test_la_cifra_canonica_no_respalda_otras_cifras(allowed: str, text: str, term: str) -> None:
    # Act
    terms = unsupported_terms(text, allowed=[allowed])

    # Assert
    assert term in terms


def test_una_referencia_que_trae_la_herramienta_no_se_bloquea() -> None:
    # Arrange: el SKU compuesto venía en los datos; antes se marcaba como nombre propio.
    text = "Pregunta en la sede por el CO-VGOLD-14 y la tapa compatible."
    allowed = ['{"opciones": [{"sku": "CO-VGOLD-14", "nombre": "Vaso Gold 14 oz"}]}']

    # Act
    terms = unsupported_terms(text, allowed=allowed)

    # Assert
    assert terms == []


@pytest.mark.parametrize(
    ("text", "term"),
    [
        pytest.param("Pregunta por el CO-VGOLD-99 en la sede.", "CO-VGOLD-99", id="sku-inventado"),
        pytest.param("Carvajal tiene 13.000 empleados en Manizales.", "13.000", id="cifra"),
        pytest.param("La sede de Kive Andina vende la línea Bioform.", "Bioform", id="marca"),
        pytest.param("Kive Andina abrió en marzo, dice Pulpak.", "Pulpak", id="empresa"),
    ],
)
def test_los_datos_de_la_herramienta_no_respaldan_lo_que_no_traen(text: str, term: str) -> None:
    # Act
    terms = unsupported_terms(text, allowed=[SEDES_MANIZALES, CONTEXTO_14_OZ])

    # Assert
    assert term in terms


# --- Reemplazos neutros ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        pytest.param(["ciudad"], "la ciudad", id="ciudad"),
        pytest.param(["cantidad"], "cuántas unidades", id="cantidad"),
        pytest.param(["temperatura"], "caliente, frío", id="temperatura"),
        pytest.param(["periodo"], "el período", id="periodo"),
        pytest.param(["cantidad", "ciudad"], "cuántas unidades", id="varios"),
    ],
)
def test_la_peticion_neutra_nombra_el_dato_que_falta(fields: list[str], expected: str) -> None:
    # Act
    text = ask_for_missing(fields)

    # Assert
    assert expected in text
    assert NO_CONTEXT_ANSWER not in text
    assert text.rstrip().endswith("?")
    assert unsupported_terms(text) == []


def test_la_peticion_de_ciudad_lista_las_ciudades_que_trajo_la_herramienta() -> None:
    # Arrange
    cities = ["Bogotá", "Manizales", "Pereira"]

    # Act
    text = ask_for_missing(["ciudad"], cities=cities)

    # Assert
    assert "Bogotá, Manizales y Pereira" in text
    assert unsupported_terms(text, allowed=[SEDES_MANIZALES]) == []


def test_sin_campo_conocido_la_peticion_es_generica_y_no_repite_el_dato_del_modelo() -> None:
    # Act
    text = ask_for_missing(["desde 1904"])

    # Assert
    assert "1904" not in text
    assert text.rstrip().endswith("?")
    assert unsupported_terms(text) == []


@pytest.mark.parametrize(
    ("dato", "expected"),
    [
        ("ciudad de entrega", ["ciudad"]),
        ("cantidad de unidades y ciudad", ["cantidad", "ciudad"]),
        ("si la bebida es fría o caliente", ["temperatura"]),
        ("desde 1904", []),
        ("", []),
    ],
)
def test_el_dato_de_sin_consulta_se_traduce_a_campos_conocidos(
    dato: str, expected: list[str]
) -> None:
    # Act
    fields = missing_fields(dato)

    # Assert
    assert fields == expected


def test_las_sedes_sin_precio_se_listan_con_su_cita() -> None:
    # Arrange
    sites = [
        {
            "n": 1,
            "nombre": "Bonniplast",
            "direccion": "Carrera 26 No. 8-37 Local 13, Galería Alameda",
            "telefono": "(602) 556 6514",
            "ciudad": "Cali",
        },
        {"n": 2, "nombre": "Disdecol", "direccion": None, "telefono": "310 451 4938"},
    ]
    allowed = [json.dumps({"sedes": sites}, ensure_ascii=False)]

    # Act
    text = sites_without_price(sites)

    # Assert
    assert "- Bonniplast — Carrera 26 No. 8-37 Local 13, Galería Alameda — (602) 556 6514 [1]" in (
        text
    )
    assert "- Disdecol — 310 451 4938 [2]" in text
    assert "None" not in text
    assert NO_CONTEXT_ANSWER not in text
    assert unsupported_terms(text, allowed=allowed) == []
