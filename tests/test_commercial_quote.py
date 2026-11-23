"""Pruebas de cajas, cotización y distribuidores de la ruta comercial (T021-T027)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from carvajal.agent.commercial import (
    BULK_BOXES,
    NoPriceError,
    boxes_for,
    find_distributors,
    quote,
)
from carvajal.catalog.loader import DEFAULT_DIR, Catalog, load_catalog
from carvajal.catalog.models import Distributor, Reference
from carvajal.quotation import quote as quote_engine

CATALOGO_REAL = Path(__file__).resolve().parents[1] / DEFAULT_DIR
HOY = date(2026, 11, 16)
HOY_COTIZADOR = date(2026, 11, 27)


def _ref(**extra: object) -> Reference:
    datos: dict[str, object] = {
        "sku": "VASO-8",
        "name": "Vaso papel 8 oz",
        "family": "vaso",
        "use": "bebida_caliente",
        "material": "PAPEL",
        "units_per_pack": 50,
        "packs_per_box": 20,
        "units_per_box": 1000,
        "price": "1061.00",
        "currency": "MXN",
        "tax_included": True,
        "price_valid_until": "2027-12-31",
        "country": "MX",
        "source_url": "https://ejemplo.test/vaso-8",
        "captured_at": "2026-10-23",
    }
    datos.update(extra)
    return Reference.model_validate(datos)


def _distribuidor(nombre: str, ciudad: str) -> Distributor:
    return Distributor.model_validate(
        {
            "name": nombre,
            "city": ciudad,
            "source_url": "https://ejemplo.test/directorio",
            "captured_at": "2026-10-23",
        }
    )


DIRECTORIO = Catalog(
    references=(),
    materials={},
    distributors=(
        _distribuidor("Uno", "Bogotá"),
        _distribuidor("Dos", "Medellín"),
        _distribuidor("Tres", "Medellín"),
    ),
)


def test_cajas_redondean_hacia_arriba_con_excedente() -> None:
    """Escenario US2.1: 5.000 unidades son 5 cajas exactas; 5.001, 6 cajas y 999 de sobra."""

    exacto = boxes_for(_ref(), 5000)
    redondeo = boxes_for(_ref(), 5001)

    assert (exacto.boxes, exacto.surplus) == (5, 0)
    assert (redondeo.boxes, redondeo.surplus) == (6, 999)


def test_cajas_exactas_con_cantidades_grandes() -> None:
    """El techo se calcula con enteros: nada de redondear hacia abajo por coma flotante."""

    unidades = 10**16 + 1
    resultado = boxes_for(_ref(), unidades)

    assert resultado.boxes == unidades // 1000 + 1
    assert resultado.surplus == resultado.boxes * 1000 - unidades
    assert resultado.surplus >= 0


def test_cantidad_invalida() -> None:
    """Cero o negativo no es una cantidad."""

    with pytest.raises(ValueError, match="positivo"):
        boxes_for(_ref(), 0)


def test_cotiza_cajas_por_precio_con_fecha_y_aviso() -> None:
    """Escenarios US2.2 y US2.4: total exacto, moneda, impuestos, fecha y aviso."""

    cotizacion = quote(_ref(), 5, today=HOY)

    assert Decimal(cotizacion.total) == Decimal("5305.00")
    assert cotizacion.currency == "MXN"
    assert cotizacion.tax_included is True
    assert cotizacion.captured_at == "2026-10-23"
    assert cotizacion.valid_until == "2027-12-31"
    assert "Precio de referencia con impuestos incluidos" in cotizacion.notice
    assert "no confirma existencias" in cotizacion.notice


def test_sin_precio_no_se_estima() -> None:
    """Escenario US2.3: sin precio se declara la ausencia con un error explícito."""

    sin_precio = _ref(price=None, currency=None, tax_included=None, country="CO")

    with pytest.raises(NoPriceError, match="no se estima"):
        quote(sin_precio, 2, today=HOY)


def test_oferta_vencida_se_advierte() -> None:
    """Si la vigencia publicada ya pasó, el aviso lo dice."""

    vencida = _ref(price_valid_until="2026-10-31")

    cotizacion = quote(vencida, 1, today=HOY)

    assert "terminó el 2026-10-31" in cotizacion.notice


def test_volumen_grande_sugiere_cotizacion_comercial() -> None:
    """Borde de la spec: más de 100 cajas se calcula, pero se sugiere contacto por volumen."""

    grande = quote(_ref(), BULK_BOXES + 1, today=HOY)
    normal = quote(_ref(), BULK_BOXES, today=HOY)

    assert "por volumen" in grande.notice
    assert "por volumen" not in normal.notice


def test_distribuidores_de_la_ciudad_sin_tildes_ni_mayusculas() -> None:
    """Escenario US3.2: «medellin» encuentra los dos de Medellín."""

    encontrados, ciudades = find_distributors("  medellin ", catalog=DIRECTORIO)

    assert [d.name for d in encontrados] == ["Dos", "Tres"]
    assert ciudades == ["Bogotá", "Medellín"]


def test_ciudad_sin_distribuidores_ofrece_las_que_tienen() -> None:
    """Escenario US3.3: sin distribuidores, la lista de ciudades permite ofrecer alternativas."""

    encontrados, ciudades = find_distributors("Cartagena", catalog=DIRECTORIO)

    assert encontrados == []
    assert ciudades == ["Bogotá", "Medellín"]


def test_catalogo_real_de_punta_a_punta() -> None:
    """Con los datos versionados: 5.000 vasos en México se cotizan y Cali tiene distribuidores."""

    load_catalog.cache_clear()
    real = load_catalog(str(CATALOGO_REAL))
    vaso = next(
        ref
        for ref in real.references
        if ref.country == "MX" and ref.family == "vaso" and ref.units_per_box == 1000
    )

    cajas = boxes_for(vaso, 5000)
    cotizacion = quote(vaso, cajas.boxes, today=HOY)
    en_cali, _ = find_distributors("cali", catalog=real)

    assert cajas.boxes == 5
    assert vaso.price is not None
    assert Decimal(cotizacion.total) == vaso.price * 5
    assert cotizacion.currency == "MXN"
    assert len(en_cali) >= 1


def test_trescientos_vasos_publicados_son_seis_paquetes() -> None:
    resultado = quote_engine("cotiza 300 vasos para bebida caliente en Bogotá", today=HOY_COTIZADOR)

    presentation = resultado["primary"]["presentation"]
    assert presentation["basis"] == "paquete"
    assert presentation["units"] == 50
    assert presentation["units_bought"] == 300
    assert presentation["units_surplus"] == 0
    assert presentation["price_per_presentation"] == "3751"


def test_trescientos_uno_vasos_publicados_sobran_49() -> None:
    resultado = quote_engine("cotiza 301 vasos para bebida caliente en Bogotá", today=HOY_COTIZADOR)

    presentation = resultado["primary"]["presentation"]
    assert presentation["count"] == 7
    assert presentation["units_bought"] == 350
    assert presentation["units_surplus"] == 49
    assert presentation["subtotal"] == "26257"


def test_la_presentacion_publicada_manda_sobre_la_vista_a_caja() -> None:
    resultado = quote_engine("cotiza 100 vasos para bebida caliente en Bogotá", today=HOY_COTIZADOR)

    assert resultado["commercial"]["presentation"]["units"] == 50
    assert resultado["commercial"]["presentation"]["price_per_presentation"] == "3751"
    assert resultado["commercial"]["source"]["name"] == "EDEXA"


def test_sin_vigencia_publicada_el_resultado_no_inventa_una_fecha() -> None:
    resultado = quote_engine("cotiza 100 vasos para bebida caliente en Bogotá", today=HOY_COTIZADOR)

    assert resultado["commercial"]["valid_until"] is None
    assert resultado["primary"]["commercial_valid_until"] is None
