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
    "ciudad sin precio conserva sedes": QuoteRequest(
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
    "chocolate caliente en Pasto sin precio publicado": QuoteRequest(
        **CALIENTE, quantity=200, city="Pasto", delivery=False
    ),
    "vaso sin temperatura pregunta antes de cotizar": QuoteRequest(
        family="vaso", capacity_oz=7.0, quantity=20, city="Bogotá", delivery=False
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
    assert "pendiente" in resultado["reply"]


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
    precios = [Decimal(option["presentation"]["unit_price"]) for option in opciones]
    assert precios == sorted(precios)
    assert "La más económica" not in resultado["reply"]
    assert "La de menor precio por unidad es" in resultado["reply"]
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
    assert subtotales == sorted(subtotales)
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
