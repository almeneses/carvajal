"""Del perfil del alimento al empaque (spec 051, US1-US3, FR-007 a FR-016, SC-002, SC-003).

Contra el catálogo publicado (``data/catalog/empaques``): las sugerencias deben ser
referencias reales del país, con ficha, y ninguna de un material que la guía declare no
apto para el uso o para la temperatura.
"""

from dataclasses import replace
from typing import Any, Literal

import pytest

from apps.api.chat.products import has_cards
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.food.profile import FOOD_KINDS, FoodKind, FoodProfile, Temperature
from carvajal.food.recommend import (
    KIND_RULES,
    MAX_ALTERNATIVES,
    application_level,
    read_note,
    recommend,
)
from carvajal.food.render import (
    PHOTO_QUESTION,
    photo_question,
    recommendation_cards,
    render_recommendation,
)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


def _profile(kind: FoodKind = "sopas", **changes: Any) -> FoodProfile:
    base = FoodProfile(
        food_p=0.95,
        kind=kind,
        kind_confidence=0.9,
        kind_runner_up=None,
        consistency="solido",
        consistency_confidence=0.9,
        temperature="caliente",
        temperature_confidence=0.95,
        portion=2.0,
        portion_confidence=0.8,
    )
    return replace(base, **changes)


def test_sopa_caliente_sugiere_contenedores_aptos_de_colombia(catalog: Catalog) -> None:
    # Act
    rec = recommend(_profile("sopas", consistency="liquido"), catalog=catalog)

    # Assert
    assert rec.status == "ok" and rec.country == "CO"
    assert 1 <= len(rec.references) <= 1 + MAX_ALTERNATIVES
    for reference in rec.references:
        rule = catalog.materials.get(reference.material)
        assert reference.country == "CO"
        assert reference.family in KIND_RULES["sopas"].families
        assert reference.source_url
        assert rule is None or rule.hot_content
        assert application_level(rule, "sopas") != "no_apto"
    assert rec.requirements is not None and rec.requirements.closure


def test_bebida_fria_solo_sugiere_vasos(catalog: Catalog) -> None:
    # Act
    rec = recommend(
        _profile("bebida_fria", temperature="fria", consistency="liquido"), catalog=catalog
    )

    # Assert
    assert rec.status == "ok"
    assert {reference.family for reference in rec.references} == {"vaso"}


@pytest.mark.parametrize("country", ["CO", "MX"])
@pytest.mark.parametrize("kind", [kind for kind in FOOD_KINDS if kind != "no_es_comida"])
@pytest.mark.parametrize("temperature", ["caliente", "fria", "ambiente"])
def test_ninguna_sugerencia_usa_un_material_no_apto(
    catalog: Catalog, country: Literal["CO", "MX"], kind: FoodKind, temperature: Temperature
) -> None:
    # Act
    rec = recommend(_profile(kind, temperature=temperature), catalog=catalog, country=country)

    # Assert (SC-002, SC-003)
    assert rec.requirements is not None
    known = {reference.sku for reference in catalog.references}
    for reference in rec.references:
        rule = catalog.materials.get(reference.material)
        assert reference.sku in known and reference.country == country
        assert application_level(rule, rec.requirements.use) != "no_apto"
        if rec.requirements.temperature == "caliente" and rule is not None:
            assert rule.hot_content


def test_un_postre_solido_no_va_en_vaso(catalog: Catalog) -> None:
    # Arrange: la torta de la evaluación (postres_helados, sólida, fría).
    torta = _profile("postres_helados", consistency="solido", temperature="fria")

    # Act
    rec = recommend(torta, catalog=catalog)

    # Assert
    assert rec.status == "ok"
    assert "vaso" not in {reference.family for reference in rec.references}


def test_la_sopa_no_recibe_una_salsera(catalog: Catalog) -> None:
    # Act
    rec = recommend(_profile("sopas", consistency="liquido"), catalog=catalog)

    # Assert: la capacidad declarada no es mucho menor que la porción.
    assert all(
        reference.capacity_oz is None or reference.capacity_oz >= 6 for reference in rec.references
    )


@pytest.mark.parametrize(
    "profile",
    [_profile(food_p=0.05), _profile("no_es_comida", food_p=0.9)],
)
def test_lo_que_no_es_comida_no_recibe_sugerencias(catalog: Catalog, profile: FoodProfile) -> None:
    # Act
    rec = recommend(profile, catalog=catalog)
    text = render_recommendation(rec, catalog)

    # Assert
    assert rec.status == "not_food" and not rec.references
    assert "No veo comida" in text and "ref." not in text
    assert recommendation_cards(rec, catalog) is None


def test_tipo_dudoso_pregunta_por_las_dos_opciones(catalog: Catalog) -> None:
    # Act
    rec = recommend(
        _profile("sopas", kind_confidence=0.45, kind_runner_up="salsas"), catalog=catalog
    )
    text = render_recommendation(rec, catalog)

    # Assert
    assert rec.status == "ask_kind"
    assert "una sopa o una salsa" in text and "ref." not in text


def test_temperatura_dudosa_pregunta_y_el_texto_la_resuelve(catalog: Catalog) -> None:
    # Arrange: el guacamole de la prueba previa (ambiente 0,46 frente a fría 0,36).
    guacamole = _profile(
        "salsas", consistency="semiliquido", temperature="ambiente", temperature_confidence=0.46
    )

    # Act
    asked = recommend(guacamole, catalog=catalog)
    answered = recommend(guacamole, catalog=catalog, note="lo entrego frío")

    # Assert
    assert asked.status == "ask_temperature"
    assert "¿Se entrega caliente, frío o a temperatura ambiente?" in render_recommendation(
        asked, catalog
    )
    assert answered.status == "ok"
    assert answered.requirements is not None and answered.requirements.temperature == "fria"


def test_las_bebidas_no_preguntan_la_temperatura(catalog: Catalog) -> None:
    # Act
    rec = recommend(
        _profile("bebida_caliente", temperature="fria", temperature_confidence=0.3),
        catalog=catalog,
    )

    # Assert
    assert rec.status == "ok"
    assert rec.requirements is not None and rec.requirements.temperature == "caliente"


def test_el_texto_elige_el_pais(catalog: Catalog) -> None:
    # Act
    rec = recommend(_profile("frituras"), catalog=catalog, note="vendo en México")

    # Assert
    assert rec.country == "MX"
    assert {reference.country for reference in rec.references} == {"MX"}


def test_sin_empaque_apto_lo_dice_y_no_presenta_nada_como_ideal(catalog: Catalog) -> None:
    # Arrange: un catálogo solo con PET (no apto para caliente) no sirve para sopa caliente.
    pet = replace(
        catalog,
        references=tuple(
            reference for reference in catalog.references if reference.material == "PET"
        ),
    )

    # Act
    rec = recommend(_profile("sopas", consistency="liquido"), catalog=pet, country="MX")
    text = render_recommendation(rec, pet)

    # Assert
    assert rec.status == "no_match" and not rec.references and rec.alternative is None
    assert "no tenemos un empaque específico para sopas" in text


def test_el_texto_cita_referencia_ficha_y_guia(catalog: Catalog) -> None:
    # Act
    rec = recommend(_profile("sopas", consistency="liquido"), catalog=catalog)
    text = render_recommendation(rec, catalog)

    # Assert (FR-014, FR-017)
    for reference in rec.references:
        assert f"(ref. {reference.sku})" in text
        assert reference.source_url in text
    assert "Según la guía de materiales" in text
    assert "pide también la tapa" in text
    assert "¿Te cotizo alguna?" in text


def test_las_tarjetas_tienen_el_formato_de_buscar_producto(catalog: Catalog) -> None:
    # Act
    rec = recommend(_profile("bebida_caliente", consistency="liquido"), catalog=catalog)
    cards = recommendation_cards(rec, catalog)

    # Assert
    assert cards is not None and has_cards(cards)
    assert [option["sku"] for option in cards["options"]] == [r.sku for r in rec.references]
    assert cards["request"]["country"] == "CO" and cards["request"]["quantity"] is None


@pytest.mark.parametrize(
    ("note", "temperature", "country"),
    [
        ("lo entrego caliente", "caliente", None),
        ("bien frío, para Colombia", "fria", "CO"),
        ("a temperatura ambiente", "ambiente", None),
        ("caliente o frío, depende", None, None),
        ("", None, None),
    ],
)
def test_lee_temperatura_y_pais_del_texto(
    note: str, temperature: str | None, country: str | None
) -> None:
    # Act
    hints = read_note(note)

    # Assert
    assert hints.temperature == temperature and hints.country == country


def test_la_marca_del_turno_guarda_el_texto_sin_la_foto() -> None:
    # Act / Assert
    assert photo_question("") == PHOTO_QUESTION
    assert photo_question("  para  domicilio ") == f"{PHOTO_QUESTION}: para domicilio"
