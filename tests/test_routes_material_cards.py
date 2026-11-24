"""Material apto en las tarjetas comerciales: microondas (evaluación spec 044, caso g-003).

En la evaluación, «Quiero contenedores para sopa que se puedan calentar en microondas, 500
unidades en Medellín, con domicilio» mostró en el modo ``routes`` la ficha del contenedor
térmico Wau (EPS, 7704918101110), y el EPS no es apto para microondas según
``data/catalog/empaques/materiales.csv``. El texto del modelo lo descartaba, pero la ficha
del contrato (``contract_result``) se elegía sin pasar por ``check_material`` y la vista la
pinta en lugar de las opciones válidas. Estas pruebas cubren ese camino, las variantes de
redacción del microondas (y los falsos positivos de «micro»), que la ficha salga de las
opciones visibles en su orden y el modo ``tools`` (``buscar_producto`` y ``cotizar``, con la
red de seguridad que lee el microondas del texto de la persona).

Las frases nuevas se redactaron para estas pruebas: no salen del conjunto de generalización.

Sin red: catálogo y libro comercial versionados en el repositorio.
"""

import json
from datetime import date
from typing import Any

import pytest
from tests.test_tool_agent_tools import _deps as _tool_deps

from apps.api.chat.products import render_products
from carvajal.agent.commercial import RENEWABLE
from carvajal.agent.commercial_flow import commercial_sources, contract_result, run_commercial
from carvajal.agent.commercial_intent import extract_commercial, mentions_microwave, microwave_for
from carvajal.agent.router import decide_route
from carvajal.agent.toolagent.tools import ToolContext, ToolDeps, run_tool
from carvajal.catalog.loader import load_catalog

CATALOG = load_catalog()
MATERIALS = {reference.sku: reference.material for reference in CATALOG.references}
EPS_WAU = "7704918101110"
HOY = date(2026, 11, 27)
G003 = (
    "Quiero contenedores para sopa que se puedan calentar en microondas, 500 unidades en "
    "Medellín, con domicilio"
)
VARIANTES = (
    G003,
    "Necesito contenedores para sopa para calentar en el microondas, 500 unidades en Medellín",
    "Necesito contenedores para sopa aptos para micro, 500 unidades en Medellín",
    "Quiero contenedores para sopa que aguanten microondas, 500 unidades en Medellín",
)
# Redacciones del microondas que el detector debe reconocer, con producto y ciudad propios.
REDACCIONES_MICROONDAS = (
    "Necesito contenedores para sopa microondables, 500 unidades en Medellín",
    "Busco un recipiente microondable para caldo, 300 unidades en Bogotá",
    "Quiero cazuelas para sopa que vayan a las microndas, 200 unidades en Cali",
    "Necesito contenedores para caldo para el mircoondas, 400 unidades en Medellín",
    "Busco contenedores para sopa microwave safe, 250 unidades en Bogotá",
    "Quiero contenedores para consomé que se puedan microondear, 600 unidades en Cali",
    "Necesito contenedores para sopa aptos para MW, 500 unidades en Medellín",
    "Busco contenedores para caldo que vayan al micro, 150 unidades en Bogotá",
)
# «micro» como prefijo de otra palabra, separado o con guion, no es microondas.
FALSOS_MICRO = (
    "Vasos para un micro negocio de jugos, 500 unidades en Cali",
    "Necesito vasos para jugo para mi micro-empresa, 300 unidades en Bogotá",
    "Contenedores para ensalada, 500 unidades en Cali, para mi micro negocio",
    "Quiero vasos para limonada para un micro emprendimiento, 200 unidades en Medellín",
    "Busco vasos para gaseosa para micro y pequeñas empresas, 400 unidades en Cali",
    "Necesito charolas de cartón microondulado para frutas, 100 unidades en Bogotá",
    "Vasos para jugo, 500 unidades en Cali, no necesito que sean para microondas",
)
# Preguntas institucionales que mencionan la «micro empresa»: no son una compra.
INSTITUCIONALES_MICRO = (
    "Historia de Carvajal con los vasos y la micro empresa",
    "¿Qué vasos vende Carvajal para micro empresas en Cali?",
    "¿Carvajal ofrece programas para la micro empresa colombiana?",
)
CIUDADES = ("Medellín", "Bogotá", "Cali")
FAMILIAS = ("vasos", "contenedores", "tapas", "charolas", "platos")
USOS = ("café", "jugo", "sopa", "ensalada", "frituras")
MODIFICADORES = ("", " compostables", " para microondas")


def _apto_microondas(material: str) -> bool:
    """Solo un material con regla en la guía que lo declare apto va al microondas."""

    rule = CATALOG.materials.get(material)
    return rule is not None and rule.microwave


def _materiales_mostrados(result: dict[str, Any]) -> set[str]:
    """Materiales de todo lo que el turno recomienda: opciones, ficha, ofertas y citas."""

    shown = {option["material"] for option in result.get("options") or []}
    if result.get("ficha"):
        shown.add(result["ficha"]["material"])
    shown.update(MATERIALS[sku] for sku in _skus_citados(result) if sku in MATERIALS)
    return shown


def _skus_citados(result: dict[str, Any]) -> set[str]:
    skus = {
        source["slug"]
        for source in commercial_sources(result)
        if source["chunk_id"].startswith("catalogo:")
    }
    offers = result.get("offers") or []
    ficha = result.get("ficha") or {}
    if offers and ficha:
        skus.add(ficha["sku"])
    return skus


def _rutas(question: str) -> dict[str, Any]:
    decision = decide_route(question, None)
    assert decision.route == "commercial"
    return run_commercial(decision.extra, today=HOY, question=question, questions=[question])


def _deps() -> ToolDeps:
    """Dependencias de las herramientas con el catálogo y el libro reales del repositorio."""

    return _tool_deps(catalog=None, today=HOY)


# --- Extracción ----------------------------------------------------------------------------


@pytest.mark.parametrize("question", VARIANTES)
def test_extraccion_marca_microondas_en_todas_las_variantes(question: str) -> None:
    # Act
    request = extract_commercial(question)

    # Assert
    assert request is not None
    assert request["microwave"] is True
    assert request["temperature"] == "caliente"
    assert request["family"] == "contenedor"


@pytest.mark.parametrize(
    "question",
    [
        "Necesito vasos para mi microempresa, 500 unidades en Cali",
        "Quiero contenedores para un microemprendimiento de sopas, 500 unidades en Cali",
    ],
)
def test_palabras_que_empiezan_por_micro_no_son_microondas(question: str) -> None:
    # Act
    request = extract_commercial(question)

    # Assert
    assert request is not None
    assert request["microwave"] is None


@pytest.mark.parametrize("question", REDACCIONES_MICROONDAS)
def test_extraccion_reconoce_otras_redacciones_del_microondas(question: str) -> None:
    # Act
    request = extract_commercial(question)

    # Assert
    assert request is not None
    assert request["microwave"] is True
    assert mentions_microwave(question)


@pytest.mark.parametrize("question", FALSOS_MICRO)
def test_micro_separado_o_negado_no_es_microondas(question: str) -> None:
    # Act
    request = extract_commercial(question)

    # Assert
    assert request is not None
    assert request["microwave"] is None
    assert not mentions_microwave(question)


def test_micro_negocio_no_cambia_la_temperatura_pedida() -> None:
    # Act
    request = extract_commercial("Vasos para un micro negocio de jugos, 500 unidades en Cali")

    # Assert: antes «micro» suelto forzaba caliente aunque el uso era bebida fría.
    assert request is not None
    assert request["temperature"] == "fria"
    assert request["use"] == "bebida_fria"


@pytest.mark.parametrize("question", INSTITUCIONALES_MICRO)
def test_micro_empresa_no_lleva_una_pregunta_institucional_a_la_ruta_comercial(
    question: str,
) -> None:
    # Act
    decision = decide_route(question, None)

    # Assert
    assert decision.route != "commercial"


def test_microwave_for_no_deduce_nada_si_la_pregunta_nombra_otra_familia() -> None:
    # Arrange
    question = "Vasos para café y contenedores para microondas, 300 unidades en Cali"

    # Act
    para_vasos = microwave_for(question, "vaso")
    para_contenedores = microwave_for("Contenedores para microondas en Cali", "contenedor")

    # Assert
    assert para_vasos is None
    assert para_contenedores is True


# --- Modo routes ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", REDACCIONES_MICROONDAS)
def test_routes_descarta_el_eps_con_otras_redacciones(question: str) -> None:
    # Act
    result = _rutas(question)

    # Assert
    shown = _materiales_mostrados(result)
    assert all(_apto_microondas(material) for material in shown), shown


def _barrido() -> list[str]:
    return [
        f"Necesito {familia} para {uso}{modificador}, 500 unidades en {ciudad}"
        for familia in FAMILIAS
        for uso in USOS
        for modificador in MODIFICADORES
        for ciudad in ("Bogotá", "Medellín")
    ]


def test_routes_la_ficha_es_una_opcion_visible_con_oferta() -> None:
    # Arrange
    preguntas = [q for q in _barrido() if decide_route(q, None).route == "commercial"]
    assert len(preguntas) > 50

    # Act
    resultados = {q: _rutas(q) for q in preguntas}

    # Assert: la ficha oculta las opciones, así que debe ser una de ellas y traer precio;
    # sin ficha, las opciones quedan a la vista.
    for question, result in resultados.items():
        skus = [option["sku"] for option in result["options"]]
        ficha = result.get("ficha")
        if ficha is None:
            assert not result.get("offers"), question
            continue
        assert ficha["sku"] in skus, question
        assert result["offers"], question


def test_routes_la_ficha_respeta_la_preferencia_renovable() -> None:
    # Arrange
    preguntas = [
        q for q in _barrido() if "compostables" in q and decide_route(q, None).route == "commercial"
    ]

    # Act
    resultados = [_rutas(q) for q in preguntas]

    # Assert
    con_renovable = [r for r in resultados if r.get("renewable_available")]
    assert con_renovable
    for result in con_renovable:
        ficha = result.get("ficha")
        assert ficha is None or ficha["material"] in RENEWABLE, ficha
        assert result["options"][0]["material"] in RENEWABLE


def test_routes_vasos_compostables_no_muestran_una_ficha_de_plastico() -> None:
    # Act
    result = _rutas("Quiero vasos compostables para limonada, 400 unidades en Bogotá")

    # Assert: antes la ficha era un vaso de PP con oferta y ocultaba los de papel.
    ficha = result.get("ficha")
    assert ficha is not None
    assert ficha["material"] in RENEWABLE
    assert ficha["sku"] in {option["sku"] for option in result["options"]}
    assert "Vaso rígido" not in render_products(result)


def test_routes_sin_oferta_muestra_todas_las_opciones() -> None:
    # Arrange: Medellín no tiene ofertas publicadas para las cazuelas.
    question = "Busco contenedores para caldo microondables, 300 unidades en Medellín"

    # Act
    result = _rutas(question)
    html = render_products(result)

    # Assert
    assert result["ficha"] is None
    assert len(result["options"]) > 1
    for option in result["options"]:
        assert option["name"] in html


@pytest.mark.parametrize("question", VARIANTES)
def test_routes_no_recomienda_material_no_apto_para_microondas(question: str) -> None:
    # Act
    result = _rutas(question)

    # Assert
    assert result["options"]
    shown = _materiales_mostrados(result)
    assert all(_apto_microondas(material) for material in shown), shown
    assert EPS_WAU in {item["sku"] for item in result["discarded"]}


@pytest.mark.parametrize("city", CIUDADES)
def test_routes_la_ficha_del_contrato_respeta_el_material(city: str) -> None:
    # Arrange
    question = G003.replace("Medellín", city)

    # Act
    result = _rutas(question)

    # Assert
    ficha = result.get("ficha")
    assert ficha is None or _apto_microondas(ficha["material"])
    assert ficha is None or ficha["sku"] != EPS_WAU
    # Las ofertas del contrato son las de la ficha: sin ficha apta no hay ofertas.
    assert ficha is not None or not result["offers"]


def test_routes_la_tarjeta_g003_no_muestra_el_eps() -> None:
    # Act
    html = render_products(_rutas(G003))

    # Assert: el EPS solo aparece en la lista de descartes, con su motivo.
    recommended, _, discards = html.partition('class="productos-descartes"')
    assert "Contenedor térmico" not in recommended
    assert "Cazuela negra 12 oz" in recommended
    assert "Cazuela negra 16 oz" in recommended
    assert "Cazuela negra 26 oz" in recommended
    assert "no es apto para microondas" in discards


def test_contract_result_descarta_la_ficha_que_no_va_al_microondas() -> None:
    # Arrange
    extra = extract_commercial(G003)
    assert extra is not None

    # Act
    contract = contract_result(G003, extra, today=HOY, questions=[G003])

    # Assert
    ficha = contract["ficha"]
    assert ficha is None or _apto_microondas(ficha["material"])


def test_contract_result_sin_microondas_sigue_aceptando_el_eps_para_sopa() -> None:
    # Arrange: el EPS sí es apto para sopa caliente fuera del microondas (guía).
    question = "Quiero contenedores para sopa caliente, 500 unidades en Bogotá"
    extra = extract_commercial(question)
    assert extra is not None and extra["microwave"] is None

    # Act
    contract = contract_result(question, extra, today=HOY, questions=[question])

    # Assert
    assert contract["ficha"] is not None
    assert contract["ficha"]["sku"] == EPS_WAU


# --- Modo tools ----------------------------------------------------------------------------


def test_tools_buscar_producto_no_recomienda_eps_para_microondas_en_colombia() -> None:
    # Act
    result = run_tool(
        "buscar_producto",
        {"familia": "contenedor", "uso": "sopas", "microondas": True, "pais": "CO"},
        _deps(),
        ToolContext(),
    )

    # Assert
    assert result.status == "ok"
    options = result.payload["opciones"]
    assert options
    assert all(_apto_microondas(MATERIALS[item["sku"]]) for item in options)
    assert all(_apto_microondas(item["material"]) for item in result.cards["options"])
    assert "ficha" not in result.cards
    assert EPS_WAU in {item["sku"] for item in result.payload["descartadas"]}


@pytest.mark.parametrize("city", CIUDADES)
def test_tools_cotizar_no_cotiza_eps_para_microondas(city: str) -> None:
    # Act
    result = run_tool(
        "cotizar",
        {
            "family": "contenedor",
            "use": "sopas",
            "microwave": True,
            "quantity": 500,
            "city": city,
            "delivery": True,
        },
        _deps(),
        ToolContext(),
    )

    # Assert
    quoted = result.quote or {}
    primary = quoted.get("primary") or {}
    assert primary.get("sku") is None or _apto_microondas(MATERIALS[primary["sku"]])
    assert EPS_WAU not in json.dumps(result.cards, ensure_ascii=False)


def test_tools_buscar_producto_lee_el_microondas_del_texto_si_el_modelo_lo_omite() -> None:
    # Arrange: el modelo no pasó microondas, pero la persona lo pidió.
    context = ToolContext(question="Busco contenedores microondables para caldo en Bogotá")

    # Act
    result = run_tool(
        "buscar_producto",
        {"familia": "contenedor", "uso": "sopas", "pais": "CO"},
        _deps(),
        context,
    )

    # Assert
    assert result.status == "ok"
    assert all(_apto_microondas(item["material"]) for item in result.cards["options"])
    assert EPS_WAU in {item["sku"] for item in result.payload["descartadas"]}


def test_tools_buscar_producto_respeta_microondas_false_explicito() -> None:
    # Arrange
    context = ToolContext(question="Contenedores para sopa, ¿alguno sirve para microondas?")

    # Act
    result = run_tool(
        "buscar_producto",
        {"familia": "contenedor", "uso": "sopas", "microondas": False, "pais": "CO"},
        _deps(),
        context,
    )

    # Assert: lo explícito del modelo manda; la red solo completa lo ausente.
    assert EPS_WAU not in {item["sku"] for item in result.payload.get("descartadas", [])}


def test_tools_la_red_no_aplica_el_microondas_a_otra_familia() -> None:
    # Arrange
    context = ToolContext(
        question="Vasos para café y contenedores para microondas, 300 unidades en Bogotá"
    )

    # Act
    result = run_tool(
        "buscar_producto",
        {"familia": "vaso", "uso": "bebida_caliente", "pais": "CO"},
        _deps(),
        context,
    )

    # Assert
    motivos = " ".join(item["motivo"] for item in result.payload.get("descartadas", []))
    assert "microondas" not in motivos


def test_tools_cotizar_lee_el_microondas_del_texto_si_el_modelo_lo_omite() -> None:
    # Arrange
    context = ToolContext(question="Cotiza 500 contenedores microondables para caldo en Bogotá")

    # Act
    result = run_tool(
        "cotizar",
        {"family": "contenedor", "use": "sopas", "quantity": 500, "city": "Bogotá"},
        _deps(),
        context,
    )

    # Assert
    quoted = result.quote or {}
    primary = quoted.get("primary") or {}
    assert primary.get("sku") != EPS_WAU
    assert EPS_WAU not in json.dumps(result.cards, ensure_ascii=False)
