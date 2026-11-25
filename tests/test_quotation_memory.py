"""Pruebas de la memoria silenciosa del Cotizador (spec 042, FR-022)."""

from datetime import UTC, datetime, timedelta

from carvajal.quotation.memory import EMPTY, MEMORY_DAYS, is_fresh, normalize


def test_normalize_recorta_y_deduplica_las_preferencias() -> None:
    perfil = normalize(
        {
            "name": "A" * 200,
            "preferences": ["vasos de papel", "Vasos de papel", *[f"p{i}" for i in range(9)]],
        }
    )

    assert perfil["name"] is not None and len(perfil["name"]) <= 60
    assert perfil["preferences"][0] == "vasos de papel"
    assert len(perfil["preferences"]) == 5


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
