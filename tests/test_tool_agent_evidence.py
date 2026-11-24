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
    FAREWELL_ANSWER,
    GATE_ABSTAIN,
    GATE_EVIDENCE,
    GATE_NO_QUERY,
    GREETING_ANSWER,
    NO_DATA_ANSWER,
    PREFERENCE_ANSWER,
    abstention,
    ask_for_missing,
    filter_unsupported,
    gate,
    has_evidence,
    missing_fields,
    names_city,
    neutral_only,
    sites_without_price,
    tool_misses,
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
            True,
            id="corpus-vacio+needs-input-de-la-herramienta",
        ),
        pytest.param(
            [_outcome("buscar_corpus", "empty"), _pedir_dato(), _outcome("cotizar", "needs_input")],
            True,
            id="pedir+needs-input",
        ),
        pytest.param([_pedir_dato()], False, id="pedir-sin-corpus"),
        pytest.param([_outcome("cotizar", "needs_input")], False, id="needs-input-sin-corpus"),
        pytest.param([_outcome("cotizar", "empty", (1,))], False, id="sedes-sin-corpus"),
        pytest.param([_outcome("buscar_corpus", "empty")], False, id="abstencion"),
        pytest.param(
            [_outcome("buscar_corpus", "ok", (1,)), _pedir_dato()], False, id="con-evidencia"
        ),
    ],
)
def test_solo_el_reemplazo_neutro_cuando_el_corpus_no_respondio(
    results: list[ToolOutcome], expected: bool
) -> None:
    # Act
    blocked = neutral_only(results)

    # Assert: ni la declaración del modelo, ni las sedes, ni un ``needs_input`` liberan su
    # redacción tras buscar en el corpus sin evidencia: solo respaldan la petición del dato
    # o las sedes, no lo que diga del corpus (revisión PR 236, C1). Sin búsqueda documental,
    # el texto pasa el filtro.
    assert blocked is expected


def test_la_capacidad_solo_respalda_su_cifra_en_onzas() -> None:
    # Arrange: revisión PR 236 (C2). La capacidad es la única fuente del «14».
    allowed = ['{"capacity_oz": 14.0}']

    # Act
    ounces = unsupported_terms("Vasos de 14 oz y de 14 onzas.", allowed=allowed)
    days = unsupported_terms("La entrega tarda 14 días.", allowed=allowed)
    mixed = unsupported_terms("Vasos de 14 oz; entrega en 14 días.", allowed=allowed)
    other = unsupported_terms("Entrega en 140 días.", allowed=allowed)

    # Assert: «14 oz» queda respaldado; el mismo «14» con otra unidad u otra cifra, no.
    assert ounces == []
    assert days == ["14"]
    assert mixed == ["14"]
    assert other == ["140"]


def test_la_capacidad_repetida_en_otro_dato_respalda_la_cifra_con_cualquier_unidad() -> None:
    # Arrange: el «14» también viene en el SKU de la herramienta, no solo en la capacidad.
    allowed = ['{"capacity_oz": 14.0, "sku": "CO-VGOLD-14"}']

    # Act
    terms = unsupported_terms("La referencia trae 14 unidades por paquete.", allowed=allowed)

    # Assert
    assert terms == []


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


# --- Abstención ajustada a lo consultado (simulación spec 044) ----------------------------

SEDES_VACIAS = (
    "buscar_sedes",
    {
        "status": "empty",
        "message": "No hay sedes del directorio en Tunja.",
        "missing": [],
        "ciudades_con_cobertura": ["Bogotá", "Cali"],
    },
)
PRODUCTO_VACIO = (
    "buscar_producto",
    {"status": "empty", "message": "Ninguna referencia.", "missing": [], "pais_catalogo": "CO"},
)
CORPUS_VACIO = ("buscar_corpus", {"status": "empty", "message": "Sin fragmentos.", "missing": []})


def test_las_busquedas_vacias_declaran_su_ausencia_con_los_datos_de_la_herramienta() -> None:
    # Act
    lines = tool_misses([PRODUCTO_VACIO, SEDES_VACIAS, PRODUCTO_VACIO])

    # Assert: una frase por búsqueda, sin repetir y sin el argumento del modelo («Tunja»).
    assert lines == [
        "No lo encontré en el catálogo de Colombia.",
        "No hay sedes del directorio en esa ciudad. Las ciudades con sedes son: Bogotá y Cali.",
    ]


def test_el_catalogo_de_mexico_y_las_coincidencias_por_nombre_se_nombran() -> None:
    # Arrange
    data = {
        "status": "empty",
        "pais_catalogo": "MX",
        "fuera_de_filtros": [
            {"sku": "MX-1", "nombre": "Vaso térmico 8 oz", "familia": "vaso", "apto": True},
            {"sku": "MX-2", "familia": "tapa", "apto": False, "motivo": "PET"},
        ],
    }

    # Act
    [line] = tool_misses([("buscar_producto", data)])

    # Assert: solo la apta se ofrece.
    assert line.startswith("No lo encontré en el catálogo de México.")
    assert "Vaso térmico 8 oz (MX-1)" in line
    assert "MX-2" not in line


@pytest.mark.parametrize(
    "payloads",
    [
        pytest.param([("buscar_producto", {"status": "ok"})], id="ok"),
        pytest.param([("cotizar", {"status": "needs_input"})], id="needs-input"),
        pytest.param([("buscar_sedes", {"status": "error"})], id="error"),
        pytest.param([("cotizar", {"status": "empty", "sedes": [{"n": 1}]})], id="con-sedes"),
    ],
)
def test_solo_las_busquedas_vacias_sin_datos_declaran_ausencia(
    payloads: list[tuple[str, dict[str, Any]]],
) -> None:
    # Act / Assert
    assert tool_misses(payloads) == []


def test_sin_buscar_en_el_corpus_la_abstencion_no_lo_nombra() -> None:
    # Act
    text, abstained = abstention([PRODUCTO_VACIO, SEDES_VACIAS])

    # Assert
    assert NO_CONTEXT_ANSWER not in text
    assert text.startswith("No lo encontré en el catálogo de Colombia.")
    assert abstained is True


def test_con_busqueda_en_el_corpus_la_frase_del_corpus_va_al_final() -> None:
    # Act
    text, abstained = abstention([SEDES_VACIAS, CORPUS_VACIO])

    # Assert
    assert text.startswith("No hay sedes del directorio en esa ciudad.")
    assert text.endswith(NO_CONTEXT_ANSWER)
    assert abstained is True


def test_una_busqueda_documental_con_error_no_cuenta_como_consulta_al_corpus() -> None:
    # Act
    text, abstained = abstention([("buscar_corpus", {"status": "error"})])

    # Assert
    assert text == NO_DATA_ANSWER
    assert abstained is True


@pytest.mark.parametrize(
    ("conversation", "abstained"),
    [
        (FAREWELL_ANSWER, False),
        (GREETING_ANSWER, False),
        (PREFERENCE_ANSWER, False),
        (NO_DATA_ANSWER, True),
    ],
)
def test_sin_busquedas_sale_la_respuesta_de_conversacion(
    conversation: str, abstained: bool
) -> None:
    # Act
    text, flagged = abstention([("sin_consulta", {"status": "ok"})], conversation=conversation)

    # Assert
    assert text == conversation
    assert flagged is abstained


@pytest.mark.parametrize(
    "payloads",
    [
        pytest.param([], id="sin-herramientas"),
        pytest.param([PRODUCTO_VACIO, SEDES_VACIAS], id="catalogo-y-sedes"),
        pytest.param([SEDES_VACIAS, CORPUS_VACIO], id="sedes-y-corpus"),
    ],
)
def test_la_abstencion_no_afirma_nada_que_las_herramientas_no_respalden(
    payloads: list[tuple[str, dict[str, Any]]],
) -> None:
    # Arrange: el respaldo son solo los datos de las herramientas (sin ``message``).
    allowed = [
        json.dumps({k: v for k, v in data.items() if k != "message"}, ensure_ascii=False)
        for _, data in payloads
    ]

    # Act
    text, _ = abstention(payloads)

    # Assert
    assert unsupported_terms(text, allowed=allowed) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Gracias, en Cali", True), ("nos vemos en medellin", True), ("gracias, muy amable", False)],
)
def test_names_city_detecta_ciudades_en_minuscula_y_sin_tildes(text: str, expected: bool) -> None:
    # Act / Assert
    assert names_city(text) is expected


# --- Papeles desde el registro (spec 045, T013) -----------------------------------------------


def test_la_compuerta_lee_el_papel_corpus_del_registro(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una herramienta nueva con ``evidence_role="corpus"`` se abstiene como ``buscar_corpus``.

    ``gate`` y ``neutral_only`` ya no nombran ``buscar_corpus``: leen ``CORPUS_TOOLS`` del
    registro (``preguntas_frecuentes`` lo usará en la US4).
    """

    # Arrange
    from carvajal.agent.toolagent import evidence

    monkeypatch.setattr(evidence, "CORPUS_TOOLS", frozenset({"buscar_corpus", "faq_falsa"}))
    vacia = {"tool": "faq_falsa", "args": {}, "status": "empty", "source_ids": [], "elapsed_ms": 1}
    pide = {**vacia, "tool": "cotizar", "status": "needs_input"}

    # Act / Assert
    assert evidence.gate([vacia]) == evidence.GATE_ABSTAIN
    assert evidence.neutral_only([vacia, pide])
    assert evidence.abstention([("faq_falsa", {"status": "empty"})])[0].endswith(NO_CONTEXT_ANSWER)


# --- Herramientas institucionales (spec 045, US4) --------------------------------------------


def test_preguntas_frecuentes_vacia_se_abstiene_como_buscar_corpus() -> None:
    # Arrange: ninguna entrada generada pasó la verificación contra su pasaje.
    from carvajal.agent.toolagent.evidence import GATE_ABSTAIN, abstention, gate

    results = [
        make_tool_outcome(
            tool="preguntas_frecuentes", args={}, status="empty", source_ids=[], elapsed_ms=3
        )
    ]

    # Act
    path = gate(results)
    text, abstained = abstention([("preguntas_frecuentes", {"status": "empty"})])

    # Assert
    assert path == GATE_ABSTAIN
    assert abstained
    assert NO_CONTEXT_ANSWER in text


def test_de_preguntas_frecuentes_solo_respalda_el_fragmento() -> None:
    # Arrange: la pregunta y la respuesta las redactó un LLM.
    from carvajal.agent.toolagent.evidence import backing_data

    data = {
        "status": "ok",
        "preguntas": [
            {
                "n": 1,
                "tema": "Historia",
                "pregunta": "¿Cuándo se fundó?",
                "respuesta": "En 1850, en Bogotá.",
                "fragmento": "El sábado 29 de octubre de 1904 se fundó en Cali.",
            }
        ],
    }

    # Act
    backing = json.dumps(backing_data("preguntas_frecuentes", data), ensure_ascii=False)

    # Assert
    assert "1904" in backing
    assert "1850" not in backing
    assert "Bogotá" not in backing
    assert backing_data("historia_carvajal", data) is data


def test_el_servicio_no_respalda_con_la_respuesta_generada_de_la_faq() -> None:
    # Arrange
    from apps.api.chat.agent import _tool_backing

    payload = {
        "status": "ok",
        "preguntas": [{"respuesta": "Se fundó en 1850.", "fragmento": "Se fundó en 1904."}],
    }

    # Act
    texts = _tool_backing([("preguntas_frecuentes", payload)])

    # Assert
    assert len(texts) == 1
    assert "1904" in texts[0] and "1850" not in texts[0]


def test_un_anio_sin_hito_se_declara_con_los_anios_cercanos() -> None:
    # Arrange
    from carvajal.agent.toolagent.evidence import tool_misses

    payloads = [("historia_carvajal", {"status": "empty", "anios_cercanos": [1961, 1964]})]

    # Act
    lines = tool_misses(payloads)

    # Assert
    assert lines == [
        "La historia oficial no registra un hito en ese año; no lo interpolo. Los años más "
        "cercanos con hitos son: 1961 y 1964."
    ]


def test_la_sugerencia_de_ciudad_de_la_memoria_no_respalda() -> None:
    # Arrange: cotizar sin ciudad añade la ciudad habitual de la memoria (FR-013).
    from carvajal.agent.toolagent.evidence import ask_for_missing, backing_data

    data = {"status": "needs_input", "missing": ["ciudad"], "sugerencia_ciudad": "Yumbo"}

    # Act
    backing = json.dumps(backing_data("cotizar", data), ensure_ascii=False)
    ask = ask_for_missing(["ciudad"], suggestion="Yumbo")

    # Assert: no respalda nada (FR-017); solo se ofrece en la pregunta de confirmación.
    assert "Yumbo" not in backing
    assert ask.endswith("¿O lo hago en Yumbo, tu ciudad habitual?")
