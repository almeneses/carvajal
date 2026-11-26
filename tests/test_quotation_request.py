"""Entrada estructurada del motor de cotización (spec 044, R4, T007).

``quote_request()`` recibe los campos ya interpretados (``QuoteRequest``) y no vuelve a leer
texto. Se prueba la paridad con ``quote()`` en todos los casos dorados y la regla de
``needs_input``: el motor pregunta el dato que falta en lugar de cotizar a ciegas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

import pytest

from carvajal.quotation import QuoteRequest, quote, quote_request
from carvajal.quotation.data import load_dataset
from carvajal.quotation.engine import MAX_DISCARDED
from carvajal.quotation.evaluation import load_cases

BOGOTA = ZoneInfo("America/Bogota")
HOY = date(2026, 11, 27)
MANANA = datetime(2026, 11, 27, 9, 0, tzinfo=BOGOTA)


class _Producto(TypedDict):
    """Campos de producto compartidos; tipados para que ``**CALIENTE`` conserve los tipos."""

    family: str
    use: str
    temperature: str


CALIENTE: _Producto = {"family": "vaso", "use": "bebida_caliente", "temperature": "caliente"}

# Lo que ``quote()`` interpreta en cada caso dorado, escrito a mano como lo enviaría el
# modelo al llamar la herramienta. ``None`` marca los casos que el texto resuelve antes de
# llegar al motor (saludo, pregunta institucional): no hay solicitud estructurada.
REQUESTS: dict[str, QuoteRequest | None] = {
    "20 vasos calientes en Bogotá": QuoteRequest(
        **CALIENTE, quantity=20, city="Bogotá", delivery=False
    ),
    "domicilio sin envío respaldado deja el total pendiente": QuoteRequest(
        **CALIENTE, quantity=20, city="Bogotá", delivery=True
    ),
    "100 unidades recalculan paquetes": QuoteRequest(
        **CALIENTE, quantity=100, city="Bogotá", delivery=False
    ),
    "sin ciudad pide ciudad": QuoteRequest(**CALIENTE, quantity=20, delivery=False),
    "pregunta institucional queda fuera": None,
    "Cali sin precio publicado cotiza el de referencia y da las sedes": QuoteRequest(
        **CALIENTE, quantity=100, city="Cali", delivery=False
    ),
    "talla agotada no se ofrece": QuoteRequest(
        family="vaso", capacity_oz=4.0, quantity=20, city="Bogotá", delivery=False
    ),
    "precio viejo se usa como normal": QuoteRequest(
        **CALIENTE, quantity=20, city="Bogotá", delivery=False
    ),
    "2000 unidades sin domicilio": QuoteRequest(
        **CALIENTE, quantity=2000, city="Bogotá", delivery=False
    ),
    "ciudad sin sede declara ausencia": QuoteRequest(
        **CALIENTE, quantity=20, city="Leticia", delivery=False
    ),
    "perfil recordado evita preguntar": QuoteRequest(
        **CALIENTE, quantity=20, city="Bogotá", delivery=True, prefers_renewable=False
    ),
    "saludo no repite la última cotización": None,
    "tinto de 7 onzas no cotiza el vaso de PP para frío": QuoteRequest(
        **CALIENTE, capacity_oz=7.0, quantity=2000, city="Bogotá", delivery=False
    ),
    "vasitos para tinto cotizan un vaso apto para caliente": QuoteRequest(
        **CALIENTE, quantity=2000, city="Bogotá", delivery=False
    ),
    "dos mil en letras pide la cantidad sin perder lo caliente": QuoteRequest(
        **CALIENTE, capacity_oz=7.0, city="Bogotá", delivery=False
    ),
    "aromática deduce caliente y pide la cantidad": QuoteRequest(
        **CALIENTE, city="Cali", delivery=False
    ),
    "chocolate caliente en Pasto cotiza el precio de referencia": QuoteRequest(
        **CALIENTE, quantity=200, city="Pasto", delivery=False
    ),
    "vaso sin temperatura pregunta antes de cotizar": QuoteRequest(
        family="vaso", capacity_oz=7.0, quantity=20, city="Bogotá", delivery=False
    ),
    "con precio publicado en la ciudad, la convertida va después": QuoteRequest(
        **CALIENTE, quantity=500, city="Bogotá", delivery=False
    ),
    "cubiertos: solo hay referencias convertidas": QuoteRequest(
        family="cubierto", quantity=2000, city="Bogotá", delivery=False
    ),
}
CASES = load_cases()


def _moment(case: dict[str, Any]) -> tuple[date, datetime]:
    day = date.fromisoformat(case.get("today", HOY.isoformat()))
    return day, datetime(day.year, day.month, day.day, 9, 0, tzinfo=BOGOTA)


def _comparable(result: dict[str, Any], greeting: str = "") -> dict[str, Any]:
    """Salida sin lo que depende del texto: la solicitud interpretada y el saludo."""

    comparable = {key: value for key, value in result.items() if key != "request"}
    reply = str(comparable.get("reply", ""))
    comparable["reply"] = reply.removeprefix(greeting)
    return comparable


def test_cada_caso_dorado_tiene_su_solicitud_estructurada() -> None:
    nombres = {case["name"] for case in CASES}

    faltan = nombres - set(REQUESTS)

    assert faltan == set()


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_quote_request_cotiza_igual_que_quote(case: dict[str, Any]) -> None:
    day, now = _moment(case)
    dataset = load_dataset(day.isoformat())
    request = REQUESTS[case["name"]]
    profile = case.get("profile") or {}
    greeting = f"Listo, {profile['name']}. " if profile.get("name") else ""

    esperado = quote(
        case["text"],
        questions=case.get("questions") or [],
        today=day,
        now=now,
        dataset=dataset,
        profile=case.get("profile"),
    )
    obtenido = quote_request(request or QuoteRequest(), today=day, now=now, dataset=dataset)

    if request is None:
        assert esperado["kind"] in {"aclaracion", "fuera_de_alcance"}
        assert obtenido["kind"] == "aclaracion"
        assert obtenido["missing"] == ["producto"]
    else:
        assert _comparable(obtenido) == _comparable(esperado, greeting)


@pytest.mark.parametrize(
    ("request_", "missing"),
    [
        (QuoteRequest(quantity=20, city="Bogotá", delivery=False), "producto"),
        (
            QuoteRequest(family="vaso", capacity_oz=7.0, quantity=20, city="Bogotá"),
            "temperatura",
        ),
        (QuoteRequest(**CALIENTE, city="Bogotá"), "cantidad"),
        (QuoteRequest(**CALIENTE, quantity=0, city="Bogotá"), "cantidad"),
        (QuoteRequest(**CALIENTE, quantity=20), "ciudad"),
    ],
    ids=["sin_familia_ni_uso", "sin_temperatura", "sin_cantidad", "cantidad_cero", "sin_ciudad"],
)
def test_falta_un_dato_pide_aclaracion(request_: QuoteRequest, missing: str) -> None:
    resultado = quote_request(request_, today=HOY, now=MANANA)

    assert resultado["kind"] == "aclaracion"
    assert resultado["missing"] == [missing]
    assert "primary" not in resultado
    assert "total" not in resultado


def test_sin_ciudad_ofrece_las_ciudades_disponibles() -> None:
    resultado = quote_request(QuoteRequest(**CALIENTE, quantity=20), today=HOY, now=MANANA)

    assert "Bogotá" in resultado["cities"]


def test_el_uso_que_implica_la_temperatura_no_la_pregunta() -> None:
    solicitud = QuoteRequest(family="vaso", use="bebida_caliente", quantity=20, city="Bogotá")

    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "7704918100199"


def test_sin_temperatura_nunca_cotiza_el_vaso_para_frio() -> None:
    solicitud = QuoteRequest(family="vaso", capacity_oz=7.0, quantity=2000, city="Bogotá")

    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    assert resultado["kind"] == "aclaracion"
    assert "caliente" in resultado["reply"]


def test_si_la_temperatura_no_cambia_el_material_no_se_pregunta() -> None:
    """Los vasos térmicos de 4 oz sirven igual para frío y caliente: no hay qué preguntar."""

    solicitud = QuoteRequest(family="vaso", capacity_oz=4.0, quantity=20, city="Bogotá")

    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    assert resultado["kind"] == "sin_resultados"


@pytest.mark.parametrize(("microondas", "falta"), [(True, None), (None, ["temperatura"])])
def test_el_microondas_implica_contenido_caliente(
    microondas: bool | None, falta: list[str] | None
) -> None:
    """En el vaso de 7 oz la temperatura sí cambia el material: sin microondas se pregunta."""

    solicitud = QuoteRequest(
        family="vaso", capacity_oz=7.0, microwave=microondas, quantity=20, city="Bogotá"
    )

    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    assert resultado.get("missing") == falta


def test_sin_decidir_domicilio_el_total_queda_pendiente() -> None:
    solicitud = QuoteRequest(**CALIENTE, quantity=20, city="Bogotá", delivery=None)

    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["presentation"]["subtotal"] == "3751"
    assert resultado["total"] is None
    assert resultado["commercial"]["total"] is None
    assert resultado["commercial"]["subtotal"] == "3751"
    assert resultado["commercial"]["shipping"]["status"] == "pending"
    assert resultado["delivery"]["requested"] is None
    # Spec 054 (decisión 4): el subtotal va sin envío y el envío queda para el resumen; el
    # texto no pregunta por domicilio o retiro (chocaba con la pregunta del flujo).
    assert "Subtotal: 3.751 COP, sin envío" in resultado["reply"]
    assert "domicilio" not in resultado["reply"]
    assert "1 paquete de 50 unidades a 3.751 COP por paquete" in resultado["reply"]


def test_la_ciudad_se_normaliza_contra_el_dataset() -> None:
    solicitud = QuoteRequest(**CALIENTE, quantity=20, city="bogota", delivery=False)

    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    assert resultado["kind"] == "cotizacion"
    assert resultado["city"] == "Bogotá"


def test_la_solicitud_de_la_salida_refleja_los_campos_recibidos() -> None:

    solicitud = QuoteRequest(**CALIENTE, quantity=100, city="Bogotá", delivery=False)

    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    assert resultado["primary"]["presentation"]["count"] == 2
    assert resultado["request"]["quantity"] == 100


# --- Unidad de compra (spec 044, simulación s-clientes-19 y s-clientes-24) -----------------

TIMES = "\u00d7"  # signo de multiplicar de la conversión
FRIA_14: dict[str, Any] = {"family": "vaso", "capacity_oz": 14.0, "temperature": "fria"}
CONTENEDOR_CALIENTE: dict[str, Any] = {
    "family": "contenedor",
    "use": "comida_caliente",
    "temperature": "caliente",
}


def _options(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [result["primary"], *result["alternatives"]]


def _per_box(sku: str) -> int:
    product = load_dataset(HOY.isoformat()).product(sku)
    assert product is not None
    return product.units_per_box


def test_cinco_cajas_se_convierten_con_la_caja_de_cada_referencia() -> None:
    # Arrange: «como 5 cajas» de vasos de 14 oz; el rígido trae 925 por caja y el Gold 1000.
    solicitud = QuoteRequest(**FRIA_14, quantity=5, unit="caja", city="Bogotá", delivery=False)

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert: cada opción convierte con su propia caja, no con un 5 por 1000 genérico.
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "CO-VRG-14"
    assert resultado["primary"]["conversion"]["text"] == f"5 cajas {TIMES} 925 = 4.625 unidades"
    for option in _options(resultado):
        esperado = 5 * _per_box(option["sku"])
        assert option["units_requested"] == esperado
        assert option["presentation"]["units_requested"] == esperado
        assert option["conversion"]["units"] == esperado
    assert f"5 cajas {TIMES} 925 = 4.625 unidades" in resultado["reply"]


def test_los_paquetes_usan_el_paquete_de_la_ficha() -> None:
    # Arrange
    solicitud = QuoteRequest(**FRIA_14, quantity=10, unit="paquete", city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["conversion"]["text"] == f"10 paquetes {TIMES} 25 = 250 unidades"
    assert resultado["primary"]["presentation"]["units_requested"] == 250


def test_las_pacas_usan_la_paca_declarada_en_el_nombre() -> None:
    # Arrange: los portacomidas espumados son «(paca x 200)».
    solicitud = QuoteRequest(**CONTENEDOR_CALIENTE, quantity=10, unit="paca", city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    for option in _options(resultado):
        assert "paca x 200" in option["name"]
        assert option["conversion"]["text"] == f"10 pacas {TIMES} 200 = 2.000 unidades"


def test_una_referencia_sin_esa_presentacion_declarada_no_se_cotiza() -> None:
    # Arrange: el contenedor térmico con tapa solo declara «paquete x 20»; ninguna fuente dice
    # cuántas unidades trae una caja, así que no se puede convertir con certeza.
    solicitud = QuoteRequest(**CONTENEDOR_CALIENTE, quantity=10, unit="caja", city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    cotizadas = {option["sku"] for option in _options(resultado)}
    assert not cotizadas & {"7704918101110", "CO-CTT-08", "CO-CTT-16"}
    assert any("no se sabe con certeza" in reason for reason in resultado["discarded"])


def test_sin_presentacion_convertible_pide_la_cantidad_en_vez_de_suponer() -> None:
    # Arrange: ningún vaso de 14 oz se vende por paca.
    solicitud = QuoteRequest(**FRIA_14, quantity=10, unit="paca", city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "aclaracion"
    assert resultado["missing"] == ["cantidad"]
    assert resultado["unconvertible"]
    assert "primary" not in resultado
    assert "unidades" in resultado["reply"]


@pytest.mark.parametrize("unit", [None, "unidad"])
def test_sin_unidad_la_cantidad_son_unidades(unit: str | None) -> None:
    # Act
    resultado = quote_request(
        QuoteRequest(**FRIA_14, quantity=5, unit=unit, city="Bogotá"), today=HOY, now=MANANA
    )

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["presentation"]["units_requested"] == 5
    assert "conversion" not in resultado["primary"]


def test_una_unidad_desconocida_pide_la_cantidad() -> None:
    # Act
    resultado = quote_request(
        QuoteRequest(**FRIA_14, quantity=5, unit="docena", city="Bogotá"), today=HOY, now=MANANA
    )

    # Assert
    assert resultado["kind"] == "aclaracion"
    assert resultado["missing"] == ["cantidad"]


# --- Unidad de compra: revisión (orden, presentaciones contradictorias y tope) -------------

VASO_FRIO: dict[str, Any] = {"family": "vaso", "temperature": "fria"}


def test_con_cajas_de_distinto_tamano_se_ordena_por_precio_por_unidad() -> None:
    # Arrange: cada vaso frío trae otra cantidad por caja (925, 2.750, 1.000…): el subtotal
    # compara pedidos distintos.
    solicitud = QuoteRequest(**VASO_FRIO, quantity=5, unit="caja", city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    opciones = _options(resultado)
    assert len({option["units_requested"] for option in opciones}) > 1
    # Publicadas primero y convertidas al final, cada grupo por precio por unidad.
    for kind in ("publicado", "convertido"):
        precios = [
            Decimal(option["presentation"]["unit_price"])
            for option in opciones
            if option["price_kind"] == kind
        ]
        assert precios == sorted(precios)
    kinds = [option["price_kind"] for option in opciones]
    assert kinds == sorted(kinds, key=lambda kind: kind == "convertido")
    assert "La más económica" not in resultado["reply"]
    assert "Te recomiendo" in resultado["reply"]
    assert "Cada referencia trae una cantidad distinta por caja" in resultado["reply"]
    for option in opciones:
        assert option["conversion"]["text"] in resultado["reply"]
    for alternativa in resultado["alternatives"]:
        assert alternativa["conversion"]["text"] in alternativa["difference"]


def test_con_la_misma_conversion_se_ordena_por_subtotal() -> None:
    # Arrange: en unidades todas las opciones son el mismo pedido.
    solicitud = QuoteRequest(**VASO_FRIO, quantity=500, city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    subtotales = [Decimal(option["presentation"]["subtotal"]) for option in _options(resultado)]
    publicados = [
        Decimal(option["presentation"]["subtotal"])
        for option in _options(resultado)
        if option["price_kind"] == "publicado"
    ]
    convertidos = [
        Decimal(option["presentation"]["subtotal"])
        for option in _options(resultado)
        if option["price_kind"] == "convertido"
    ]
    assert subtotales == publicados + convertidos  # los convertidos van al final
    assert publicados == sorted(publicados)
    assert convertidos == sorted(convertidos)
    # La recomendada es la más barata de todas: no hace falta «con precio publicado».
    assert "La más económica es" in resultado["reply"]


def test_una_caja_del_contrato_no_convierte_si_la_ficha_declara_paca() -> None:
    # Arrange: los portacomidas espumados son «(paca x N)» en el catálogo, pero el contrato
    # rotula esa misma oferta como «caja de 200». Una caja de pacas no son 200 unidades con
    # certeza: esas referencias no se cotizan por caja.
    solicitud = QuoteRequest(**CONTENEDOR_CALIENTE, quantity=3, unit="caja", city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    cotizadas = _options(resultado)
    assert cotizadas
    assert all("paca x" not in option["name"] for option in cotizadas)
    assert "CO-PCOM-C1" not in {option["sku"] for option in cotizadas}


def test_la_paca_de_la_ficha_sigue_convirtiendo() -> None:
    # Arrange
    solicitud = QuoteRequest(**CONTENEDOR_CALIENTE, quantity=3, unit="paca", city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "CO-PCOM-C1"
    assert resultado["primary"]["conversion"]["text"] == f"3 pacas {TIMES} 200 = 600 unidades"


def test_los_descartes_respetan_el_tope_y_explican_primero_la_conversion() -> None:
    # Arrange: hay descartes por temperatura y otros tantos por conversión.
    solicitud = QuoteRequest(**CONTENEDOR_CALIENTE, quantity=3, unit="caja", city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    descartes = resultado["discarded"]
    assert 0 < len(descartes) <= MAX_DISCARDED
    assert "no se sabe con certeza" in descartes[0]


# --- Referencia nombrada (spec 044, ronda 4: s-r3-01, 02, 05, 10 y 11) ---------------------


def _skus(result: dict[str, Any]) -> set[str]:
    return {option["sku"] for option in _options(result)}


def test_el_p1_nombrado_cotiza_el_p1_sin_pedir_la_temperatura() -> None:
    # Arrange: «2 pacas del P1»; antes cotizaba el portacomidas más barato de la familia.
    solicitud = QuoteRequest(name="P1", quantity=2, unit="paca", city="Bogotá", delivery=False)

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "CO-PCOM-P1"
    assert resultado["alternatives"] == []
    assert resultado["primary"]["conversion"]["text"] == f"2 pacas {TIMES} 200 = 400 unidades"
    assert resultado["primary"]["presentation"]["count"] == 2
    assert resultado["commercial"]["subtotal"] == "228926"
    assert resultado["total"] == "228926"
    assert resultado["request"]["sku"] == "CO-PCOM-P1"
    assert "Portacomidas espumado P1" in resultado["reply"]


@pytest.mark.parametrize("nombre", ["portacomidas J2", "vaso J2"])
def test_el_j2_nombrado_gana_a_una_familia_mal_pasada(nombre: str) -> None:
    # Arrange: el J2 es un contenedor; la familia «vaso» no lo esconde.
    solicitud = QuoteRequest(name=nombre, family="vaso", quantity=600, city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert _skus(resultado) == {"CO-PCOM-J2"}
    assert resultado["primary"]["presentation"]["count"] == 3
    assert resultado["commercial"]["subtotal"] == "284766"
    assert resultado["total"] is None
    assert resultado["commercial"]["shipping"]["status"] == "pending"
    assert resultado["request"]["family"] == "contenedor"


def test_el_sku_fija_la_referencia_y_convierte_sus_paquetes() -> None:
    # Arrange: «10 paquetes del CO-CTT-16» = 10 por 20.793 (s-r3-11).
    solicitud = QuoteRequest(
        sku="CO-CTT-16", quantity=10, unit="paquete", city="Bogotá", delivery=False
    )

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert _skus(resultado) == {"CO-CTT-16"}
    assert resultado["primary"]["conversion"]["text"] == f"10 paquetes {TIMES} 20 = 200 unidades"
    assert resultado["primary"]["tax"]["status"]
    assert resultado["total"] == "207930"


@pytest.mark.parametrize("temperatura", [None, "fria"])
def test_el_vaso_gold_de_14_oz_nombrado_no_pide_temperatura(temperatura: str | None) -> None:
    # Arrange: sin nombre, los vasos de 14 oz piden la temperatura (el Gold es solo frío).
    solicitud = QuoteRequest(
        name="Vaso Gold de 14 oz", temperature=temperatura, quantity=1000, city="Bogotá"
    )

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert _skus(resultado) == {"CO-VGOLD-14"}
    assert resultado["primary"]["presentation"]["count"] == 20
    assert resultado["commercial"]["subtotal"] == "396180"


def test_una_referencia_nombrada_no_apta_para_el_uso_dicho_no_se_cotiza() -> None:
    # Arrange: el vaso Gold es de PP declarado solo para bebida fría.
    solicitud = QuoteRequest(
        name="Vaso Gold de 14 oz", use="bebida_caliente", quantity=1000, city="Bogotá"
    )

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "sin_resultados"
    assert resultado["reason"] == "referencia_no_apta"
    assert resultado["sku"] == "CO-VGOLD-14"
    assert "fría" in resultado["discarded"][0]
    assert "primary" not in resultado
    assert "total" not in resultado


def test_un_nombre_ambiguo_pide_cual_con_las_candidatas() -> None:
    # Arrange: hay un J1 espumado y un J1 de pulpa de caña.
    solicitud = QuoteRequest(name="portacomidas J1", quantity=200, city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "aclaracion"
    assert resultado["missing"] == ["referencia"]
    candidatas = {item["sku"] for item in resultado["candidates"]}
    assert {"CO-PCOM-J1", "7704918105071"} <= candidatas
    assert "primary" not in resultado


def test_un_termino_mas_del_nombre_acota_el_j1_ambiguo() -> None:
    # Arrange: «icopor» deja solo el J1 espumado.
    solicitud = QuoteRequest(name="portacomidas J1 de icopor", quantity=200, city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert _skus(resultado) == {"CO-PCOM-J1"}


def test_un_nombre_que_no_existe_no_cotiza_otra_referencia() -> None:
    # Act
    resultado = quote_request(
        QuoteRequest(name="vaso XZ-900", quantity=100, city="Bogotá"), today=HOY, now=MANANA
    )

    # Assert
    assert resultado["kind"] == "sin_resultados"
    assert resultado["reason"] == "referencia_no_encontrada"
    assert "primary" not in resultado


def test_una_referencia_sin_precio_en_la_ciudad_no_se_sustituye() -> None:
    # Arrange: el J1 de pulpa tiene precio publicado en Bogotá, no en Pereira (s-r3-01).
    solicitud = QuoteRequest(
        name="portacomidas de pulpa de caña J1 de Bioform",
        use="comida_caliente",
        quantity=1,
        unit="caja",
        city="Pereira",
    )

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "sin_resultados"
    assert resultado["reason"] == "sin_precio"
    assert resultado["sku"] == "7704918105071"
    assert resultado["providers"]
    assert {item["city"] for item in resultado["providers"]} == {"Pereira"}
    assert resultado["priced_cities"] == ["Bogotá"]
    assert "primary" not in resultado


def test_una_referencia_sin_ningun_precio_publicado_no_pide_mas_datos() -> None:
    # Arrange: el vaso Gold de 16 oz no tiene oferta publicada en ninguna ciudad.
    solicitud = QuoteRequest(name="vaso Gold 16 oz", city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "sin_resultados"
    assert resultado["reason"] == "sin_precio"
    assert resultado["sku"] == "CO-VGOLD-16"
    assert resultado["priced_cities"] == []
    assert resultado["providers"]


# --- Revisión de la referencia nombrada (ronda 4) ------------------------------------------


def test_el_vaso_p1_cotiza_el_p1_y_avisa_que_es_un_contenedor() -> None:
    # Act
    resultado = quote_request(
        QuoteRequest(name="vaso P1", quantity=400, city="Bogotá"), today=HOY, now=MANANA
    )

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "CO-PCOM-P1"
    assert resultado["family_mismatch"] == {"asked": "vaso", "family": "contenedor"}
    assert "es de contenedores y no de vasos" in resultado["reply"]
    assert "Cotizo la referencia que pediste" not in resultado["reply"]


def test_una_familia_pedida_que_contradice_el_sku_se_avisa() -> None:
    # Act: el modelo pasa el SKU del P1 con familia «vaso».
    resultado = quote_request(
        QuoteRequest(sku="CO-PCOM-P1", family="vaso", quantity=400, city="Bogotá"),
        today=HOY,
        now=MANANA,
    )

    # Assert: no se cotiza en silencio como si fuera un vaso.
    assert resultado["kind"] == "cotizacion"
    assert resultado["family_mismatch"] == {"asked": "vaso", "family": "contenedor"}


def test_el_portacomidas_j2_con_familia_mal_pasada_no_avisa_otra_familia() -> None:
    # Act: el nombre dice portacomidas y el J2 lo es; la familia «vaso» es del modelo.
    resultado = quote_request(
        QuoteRequest(name="portacomidas J2", family="vaso", quantity=600, city="Bogotá"),
        today=HOY,
        now=MANANA,
    )

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert "family_mismatch" not in resultado
    assert "Cotizo la referencia que pediste" in resultado["reply"]


def test_un_sku_inexacto_no_tapa_el_nombre() -> None:
    # Act: «CO-PCOM-P1S» no es un SKU; antes traía cinco portacomidas «parecidos».
    resultado = quote_request(
        QuoteRequest(sku="CO-PCOM-P1S", name="J2", quantity=400, city="Bogotá"),
        today=HOY,
        now=MANANA,
    )

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "CO-PCOM-J2"


def test_un_sku_inexacto_sin_nombre_se_busca_como_nombre() -> None:
    # Act
    resultado = quote_request(
        QuoteRequest(sku="P1", quantity=400, city="Bogotá"), today=HOY, now=MANANA
    )

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "CO-PCOM-P1"


def test_sku_y_nombre_de_referencias_distintas_piden_cual() -> None:
    # Act
    resultado = quote_request(
        QuoteRequest(sku="CO-PCOM-P1", name="J2", quantity=400, city="Bogotá"),
        today=HOY,
        now=MANANA,
    )

    # Assert
    assert resultado["kind"] == "aclaracion"
    assert resultado["missing"] == ["referencia"]
    assert resultado["conflict"] is True
    assert {item["sku"] for item in resultado["candidates"]} == {"CO-PCOM-P1", "CO-PCOM-J2"}
    assert "primary" not in resultado


def test_sku_y_nombre_de_la_misma_referencia_la_cotizan() -> None:
    # Act
    resultado = quote_request(
        QuoteRequest(sku="CO-VGOLD-14", name="Vaso Gold de 14 oz", quantity=1000, city="Bogotá"),
        today=HOY,
        now=MANANA,
    )

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "CO-VGOLD-14"


def test_una_referencia_no_apta_no_queda_fijada_en_la_solicitud() -> None:
    # Act
    resultado = quote_request(
        QuoteRequest(name="Vaso Gold de 14 oz", use="bebida_caliente", quantity=1000),
        today=HOY,
        now=MANANA,
    )

    # Assert: el SKU solo va en el motivo, no en la solicitud que se reutiliza.
    assert resultado["reason"] == "referencia_no_apta"
    assert "sku" not in resultado["request"]


def test_un_nombre_generico_se_cotiza_como_busqueda_por_familia() -> None:
    # Act: «portacomidas de icopor» coincide con cinco referencias, ninguna nombrada.
    resultado = quote_request(
        QuoteRequest(name="portacomidas de icopor", quantity=400, city="Bogotá"),
        today=HOY,
        now=MANANA,
    )

    # Assert: la más económica y alternativas, solo de EPS, sin pedir cuál.
    assert resultado["kind"] == "cotizacion"
    assert resultado["alternatives"]
    assert {option["material"] for option in _options(resultado)} == {"EPS"}
    assert resultado["reply"].startswith("Cotización para 400 contenedores")
    assert "La más económica es" in resultado["reply"]
    assert "sku" not in resultado["request"]


def test_un_nombre_generico_pregunta_la_temperatura_si_cambia_el_material() -> None:
    # Act: «vaso» solo es un tipo de producto; entre los vasos la temperatura importa.
    resultado = quote_request(
        QuoteRequest(name="vaso", quantity=400, city="Bogotá"), today=HOY, now=MANANA
    )

    # Assert
    assert resultado["kind"] == "aclaracion"
    assert resultado["missing"] == ["temperatura"]


def test_un_nombre_generico_respeta_la_guia_de_materiales() -> None:
    # Act: con microondas, el EPS no es apto.
    resultado = quote_request(
        QuoteRequest(name="portacomidas de icopor", microwave=True, quantity=400, city="Bogotá"),
        today=HOY,
        now=MANANA,
    )

    # Assert
    assert resultado["kind"] == "sin_resultados"
    assert "primary" not in resultado
    assert resultado["discarded"]


def test_la_referencia_nombrada_sin_conversion_la_nombra_con_su_presentacion() -> None:
    # Act: «2 cajas del P1» (s-r3-05); la ficha declara «paca x 200», no la caja.
    resultado = quote_request(
        QuoteRequest(name="P1", quantity=2, unit="caja", city="Bogotá"), today=HOY, now=MANANA
    )

    # Assert
    assert resultado["kind"] == "aclaracion"
    assert resultado["missing"] == ["cantidad"]
    assert resultado["presentations"] == ["paca de 200"]
    assert resultado["unconvertible"] == ["CO-PCOM-P1"]
    assert "Portacomidas espumado P1" in resultado["reply"]
    assert "en unidades o en pacas" in resultado["reply"]
    assert "esas cajas" not in resultado["reply"]


# --- Funciones públicas para convertir_presentacion (spec 045, T021) --------------------------


def test_conversion_publica_de_cajas_a_unidades() -> None:
    """3 cajas del CO-VTER-04 (20 paquetes de 50 = 1000 por caja) son 3000 unidades."""

    # Arrange
    from carvajal.quotation import engine

    dataset = load_dataset(HOY.isoformat())
    vaso = dataset.product("CO-VTER-04")
    assert vaso is not None

    # Act
    converted = engine.conversion(vaso, 3, "caja", dataset)

    # Assert
    assert converted is not None
    assert converted.units_per == 1000
    assert converted.quantity * converted.units_per == 3000
    assert engine.declared_sizes(vaso, "caja", dataset) == {1000: ["catálogo"]}


def test_la_paca_del_nombre_contra_la_caja_del_contrato_no_se_convierte() -> None:
    """Portacomidas J1: «paca x 200» en la ficha y «caja de 200» en el contrato."""

    # Arrange
    from carvajal.quotation import engine

    dataset = load_dataset(HOY.isoformat())
    j1 = dataset.product("CO-PCOM-J1")
    assert j1 is not None

    # Act / Assert
    assert engine.declared_sizes(j1, "caja", dataset) == {}
    assert engine.conversion(j1, 3, "caja", dataset) is None
    assert engine.published_presentations(j1, dataset) == ["paca de 200"]


def test_presentaciones_publicadas_sin_ciudad_cuentan_todas_las_ofertas() -> None:
    # Arrange
    from carvajal.quotation import engine

    dataset = load_dataset(HOY.isoformat())
    with_offers = next(
        product
        for product in (dataset.product(offer.sku) for offer in dataset.offers)
        if product is not None and not engine._NAME_PRESENTATION.search(product.name)
    )
    cities = {offer.city for offer in dataset.offers_for(with_offers.sku)}

    # Act
    every = engine.published_presentations(with_offers, dataset)
    per_city = [engine.published_presentations(with_offers, dataset, city) for city in cities]

    # Assert
    assert every
    assert set(every) == {item for items in per_city for item in items}


def test_un_nombre_con_una_familia_no_ofrece_referencias_de_otra() -> None:
    # Arrange: «vaso Wau 7 oz» también coincide con «Tapa … para vaso … 7 oz Wau» (ensayo).
    solicitud = QuoteRequest(name="vaso Wau 7 oz", quantity=500, city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["family"] == "vaso"


def test_un_nombre_que_nombra_dos_familias_sigue_preguntando() -> None:
    # Arrange
    solicitud = QuoteRequest(name="tapa vaso 7 oz", quantity=500, city="Bogotá")

    # Act
    resultado = quote_request(solicitud, today=HOY, now=MANANA)

    # Assert
    assert resultado["kind"] == "aclaracion"
    assert {c["family"] for c in resultado["candidates"]} == {"tapa"}
