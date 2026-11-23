"""Pruebas del motor de cotización (spec 042, US1-US5)."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from carvajal.quotation import quote

HOY = date(2026, 11, 27)
MANANA = datetime(2026, 11, 27, 9, 0, tzinfo=ZoneInfo("America/Bogota"))
HISTORIAL = ["cotízame 20 vasos para bebida caliente"]


def test_sin_ciudad_pide_la_ciudad_y_no_cotiza() -> None:
    resultado = quote("cotízame 20 vasos para bebida caliente", today=HOY, now=MANANA)

    assert resultado["kind"] == "aclaracion"
    assert "ciudad" in resultado["reply"]
    assert "primary" not in resultado
    assert "Bogotá" in resultado["cities"]


def test_cotiza_20_vasos_con_presentacion_y_sobrantes() -> None:
    resultado = quote("en Bogotá", questions=HISTORIAL, today=HOY, now=MANANA)

    assert resultado["kind"] == "cotizacion"
    principal = resultado["primary"]
    assert principal["sku"] == "7704918100199"
    assert principal["presentation"] == {
        "basis": "paquete",
        "units": 50,
        "count": 1,
        "units_bought": 50,
        "units_surplus": 30,
        "subtotal": "3751",
        "unit_price": "75.02",
    }
    assert principal["provider"]["name"] == "EDEXA"
    assert principal["price_simulated"] is False
    assert principal["stock"]["simulated"] is True


def test_domicilio_suma_envio_y_ventana_habil() -> None:
    resultado = quote("con domicilio", questions=[*HISTORIAL, "en Bogotá"], today=HOY, now=MANANA)

    entrega = resultado["delivery"]
    assert entrega["requested"] is True
    assert entrega["ship_cost"] == "12000"
    assert entrega["window"] == "30 nov y 1 dic"
    assert resultado["total"] == str(3751 + 12000)


def test_el_envio_es_gratis_sobre_el_umbral() -> None:
    resultado = quote("cotiza 2000 vasos para bebida caliente en Bogotá", today=HOY, now=MANANA)

    assert resultado["kind"] == "cotizacion"
    assert resultado["delivery"]["requested"] is False


def test_cantidad_seguida_recalcula_presentaciones() -> None:
    resultado = quote("mejor 100", questions=[*HISTORIAL, "en Bogotá"], today=HOY, now=MANANA)

    principal = resultado["primary"]
    assert principal["presentation"]["count"] == 2
    assert principal["presentation"]["units_bought"] == 100
    assert principal["presentation"]["units_surplus"] == 0
    assert principal["presentation"]["subtotal"] == "7502"


def test_producto_agotado_no_se_ofrece() -> None:
    resultado = quote("cotiza 20 vasos térmicos de 4 oz en Bogotá", today=HOY, now=MANANA)

    if resultado["kind"] == "cotizacion":
        assert resultado["primary"]["sku"] != "CO-VTER-04"


def test_referencia_simulada_se_senala() -> None:
    resultado = quote("cotiza 100 cucharas en Cali", today=HOY, now=MANANA)

    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["simulated"] is True
    assert resultado["primary"]["price_simulated"] is True


def test_pregunta_ajena_al_catalogo_queda_fuera_de_alcance() -> None:
    resultado = quote("¿cómo nació Carvajal?", today=HOY, now=MANANA)

    assert resultado["kind"] == "fuera_de_alcance"
    assert "chat general" in resultado["reply"]


@pytest.mark.parametrize("texto", ["cotiza 0 vasos en Bogotá", "cotiza -5 vasos en Bogotá"])
def test_cantidades_invalidas_no_producen_total(texto: str) -> None:
    resultado = quote(texto, today=HOY, now=MANANA)

    assert resultado["kind"] in {"aclaracion", "sin_resultados"}


def test_los_proveedores_llevan_coordenadas_para_el_mapa() -> None:
    resultado = quote("en Bogotá", questions=HISTORIAL, today=HOY, now=MANANA)

    assert resultado["providers"]
    assert all(sede["lat"] and sede["lon"] for sede in resultado["providers"])
    assert Decimal(resultado["primary"]["presentation"]["subtotal"]) == Decimal("3751")


def test_sin_modo_demo_solo_cotiza_lo_publicado(monkeypatch: pytest.MonkeyPatch) -> None:
    resultado = quote("cotiza 100 cucharas en Cali", today=HOY, now=MANANA, allow_demo=False)

    assert resultado["kind"] == "sin_resultados"


def test_el_precio_viejo_se_usa_como_normal() -> None:
    from datetime import date as fecha

    resultado = quote(
        "en Bogotá",
        questions=HISTORIAL,
        today=fecha(2027, 4, 1),
        now=datetime(2027, 4, 1, 9, 0, tzinfo=ZoneInfo("America/Bogota")),
    )

    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["sku"] == "7704918100199"
    assert resultado["primary"]["captured_at"] == "2026-11-18"
    assert "stale" not in resultado["primary"]
    assert resultado["primary"]["presentation"]["subtotal"] == "3751"


def test_el_perfil_recuerda_ciudad_domicilio_y_nombre() -> None:
    perfil = {"name": "Ana", "city": "Bogotá", "delivery": True, "preferences": []}

    resultado = quote("cotiza 20 vasos para bebida caliente", today=HOY, now=MANANA, profile=perfil)

    assert resultado["kind"] == "cotizacion"
    assert resultado["city"] == "Bogotá"
    assert resultado["delivery"]["requested"] is True
    assert resultado["reply"].startswith("Listo, Ana.")


def test_la_ciudad_dicha_gana_sobre_la_recordada() -> None:
    perfil = {"name": None, "city": "Bogotá", "delivery": False, "preferences": []}

    resultado = quote("cotiza 20 vasos calientes en Cali", today=HOY, now=MANANA, profile=perfil)

    assert resultado["city"] == "Cali"


def test_la_preferencia_renovable_se_aplica() -> None:
    perfil = {
        "name": None,
        "city": "Bogotá",
        "delivery": False,
        "preferences": ["materiales renovables"],
    }

    resultado = quote("cotiza 20 vasos calientes", today=HOY, now=MANANA, profile=perfil)

    assert resultado["request"]["prefers_renewable"] is True


def test_la_oferta_apunta_a_su_ficha_citable() -> None:
    resultado = quote("en Bogotá", questions=HISTORIAL, today=HOY, now=MANANA)

    assert resultado["primary"]["provider"]["ficha"] == "7704918100199-p06"
    assert all("ficha" in sede for sede in resultado["providers"])


def test_un_saludo_no_repite_la_ultima_cotizacion() -> None:
    resultado = quote(
        "hola", questions=["cotiza 20 vasos calientes en Bogotá"], today=HOY, now=MANANA
    )

    assert resultado["kind"] == "aclaracion"
    assert resultado["reply"].startswith("Hola.")
    assert "primary" not in resultado


def test_un_agradecimiento_no_repite_la_cotizacion() -> None:
    resultado = quote(
        "gracias", questions=["cotiza 20 vasos calientes en Bogotá"], today=HOY, now=MANANA
    )

    assert resultado["kind"] == "aclaracion"
    assert "primary" not in resultado


def test_un_seguimiento_sigue_cotizando() -> None:
    for text in ("mejor 100", "con domicilio", "en Cali", "100"):
        resultado = quote(
            text, questions=["cotiza 20 vasos calientes en Bogotá"], today=HOY, now=MANANA
        )
        assert resultado["kind"] == "cotizacion", text
