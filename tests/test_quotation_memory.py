"""Pruebas de la memoria silenciosa del Cotizador (spec 042, FR-022)."""

from datetime import UTC, datetime, timedelta

from carvajal.quotation.memory import (
    EMPTY,
    MEMORY_DAYS,
    extract,
    forget_requested,
    is_fresh,
    merge,
    normalize,
)


def test_extrae_nombre_trabajo_ciudad_y_domicilio() -> None:
    datos = extract(
        "Hola, me llamo Ana, trabajo en una cafetería en Bogotá y siempre pido a domicilio"
    )

    assert datos == {
        "name": "Ana",
        "workplace": "cafetería en Bogotá",
        "city": "Bogotá",
        "delivery": True,
    }


def test_extrae_negocio_y_gustos_sin_confundir_la_ciudad() -> None:
    datos = extract("Tengo una panadería en Cali y me gustan los vasos de papel")

    assert datos["workplace"] == "panadería en Cali"
    assert datos["city"] == "Cali"
    assert datos["preferences"] == ["los vasos de papel"]


def test_no_guarda_lo_que_no_coincide_con_un_patron() -> None:
    assert extract("cotiza 20 vasos para bebida caliente en Bogotá") == {}
    assert extract("soy de Bogotá") == {}


def test_merge_conserva_preferencias_y_recorta_el_perfil() -> None:
    perfil = normalize(
        {
            "name": "Ana",
            "city": "Bogotá",
            "preferences": [f"gusto {indice}" for indice in range(6)],
        }
    )
    fusionado = merge(perfil, {"name": "Ana María", "preferences": ["gusto 0", "nuevo"]})

    assert fusionado["name"] == "Ana María"
    assert fusionado["city"] == "Bogotá"
    assert len(fusionado["preferences"]) == 5
    assert "nuevo" in fusionado["preferences"]
    assert "gusto 0" in fusionado["preferences"]


def test_olvidar_se_reconoce_en_varias_formas() -> None:
    assert forget_requested("olvida mis datos")
    assert forget_requested("Olvídame, por favor")
    assert forget_requested("borra mi memoria")
    assert not forget_requested("cotiza 20 vasos")


def test_el_perfil_caduca() -> None:
    ahora = datetime(2026, 11, 21, tzinfo=UTC)

    assert is_fresh(ahora - timedelta(days=MEMORY_DAYS - 1), now=ahora)
    assert not is_fresh(ahora - timedelta(days=MEMORY_DAYS + 1), now=ahora)
    assert not is_fresh(None, now=ahora)


def test_el_perfil_vacio_es_estable() -> None:
    assert normalize(dict(EMPTY)) == {
        "name": None,
        "workplace": None,
        "city": None,
        "delivery": False,
        "preferences": [],
    }
