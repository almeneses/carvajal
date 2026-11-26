"""Tarjetas solo para las opciones que la respuesta nombra (``presented_options``).

En el modo ``tools``, ``buscar_producto`` deja hasta ocho opciones en el resultado aunque el
texto presente menos; el chat mostraba tarjetas de más («necesito 2000 platos desechables»
con cuatro opciones en el texto y más tarjetas debajo). Los textos de estas pruebas imitan
respuestas reales guardadas: listas en markdown, tablas y nombres con tildes.
"""

from typing import Any

from apps.api.chat.products import presented_options, render_products
from carvajal.agent.commercial_flow import MAX_OPTIONS
from carvajal.agent.toolagent.tools.builtin import MAX_PRESENTED_OPTIONS


def _option(sku: str, name: str) -> dict[str, Any]:
    return {"sku": sku, "name": name, "material": "EPS", "units_per_box": 480}


PLATOS = [
    _option("7704918101288", "Plato espumado hondo 12 oz Wau"),
    _option("CO-PHON-05", "Plato espumado hondo 5 oz Wau"),
    _option("CO-PPAN-23", "Plato espumado pando 23 cm Wau"),
    _option("CO-PPAN-26", "Plato espumado pando 26 cm Wau"),
    _option("CO-P3DIV", "Plato rígido rectangular negro 3 divisiones Wau Maestro"),
    _option("CO-PHON-112", "Plato espumado hondo 112 oz Wau"),
    _option("CO-PPAN-18", "Plato espumado pando 18 cm Wau"),
    _option("CO-PPAN-20", "Plato espumado pando 20 cm Wau"),
]


def _result(options: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"options": list(PLATOS if options is None else options), **extra}


def _skus(result: dict[str, Any] | None) -> list[str]:
    assert result is not None
    return [option["sku"] for option in result["options"]]


def test_conserva_las_opciones_nombradas_por_su_sku() -> None:
    # Arrange
    answer = "Te sirven el de 12 oz (7704918101288) y el pando de 23 cm (CO-PPAN-23) [1]."

    # Act
    filtered = presented_options(_result(), answer)

    # Assert
    assert _skus(filtered) == ["7704918101288", "CO-PPAN-23"]


def test_el_nombre_coincide_sin_tildes_mayusculas_ni_markdown() -> None:
    # Arrange: negrita, otra capitalización y sin tildes.
    answer = (
        "Para 2000 platos tienes:\n\n"
        "- **PLATO ESPUMADO PANDO 26 CM WAU**, caja x 480 [3]\n"
        "- *Plato rigido rectangular negro 3 divisiones Wau Maestro* [4]\n"
    )

    # Act
    filtered = presented_options(_result(), answer)

    # Assert
    assert _skus(filtered) == ["CO-PPAN-26", "CO-P3DIV"]


def test_el_nombre_coincide_dentro_de_una_tabla() -> None:
    # Arrange
    answer = (
        "| Referencia | Capacidad |\n|---|---|\n"
        "| Plato espumado hondo 5 oz Wau | 5 oz [1] |\n"
        "|Plato espumado pando 18 cm Wau|18 cm [2]|\n"
    )

    # Act
    filtered = presented_options(_result(), answer)

    # Assert
    assert _skus(filtered) == ["CO-PHON-05", "CO-PPAN-18"]


def test_doce_onzas_no_coincide_dentro_de_ciento_doce() -> None:
    # Arrange: solo se nombra la de 112 oz; la de 12 oz no debe colarse.
    answer = "La más grande es el **Plato espumado hondo 112 oz Wau** [6]."

    # Act
    filtered = presented_options(_result(), answer)

    # Assert
    assert _skus(filtered) == ["CO-PHON-112"]


def test_un_nombre_contenido_en_otro_mas_largo_no_cuenta() -> None:
    # Arrange: nombrar la cazuela «Maestro» no nombra la referencia de nombre más corto.
    options = [
        _option("CO-A", "Cazuela negra 16 oz Wau"),
        _option("CO-B", "Cazuela negra 16 oz Wau Maestro"),
    ]
    answer = "Sí, la tenemos: **Cazuela negra 16 oz Wau Maestro**, en PP [1]."

    # Act
    filtered = presented_options(_result(options), answer)

    # Assert
    assert _skus(filtered) == ["CO-B"]


def test_las_cifras_pegadas_a_la_unidad_coinciden_con_las_separadas() -> None:
    # Arrange: el catálogo de México escribe «16oz 90mm».
    options = [
        _option("2864632125", "Vasos de papel 32oz 105mm"),
        _option("2864620001", "Vasos de papel 20oz 90mm"),
    ]
    answer = "- Vasos de papel 20 oz 90 mm, 600 por caja: 1.242,00 MXN [3]"

    # Act
    filtered = presented_options(_result(options), answer)

    # Assert
    assert _skus(filtered) == ["2864620001"]


def test_si_la_respuesta_no_nombra_ninguna_quedan_las_primeras() -> None:
    # Arrange
    answer = "Dime qué capacidad te sirve y te paso el contacto de la sede."

    # Act
    filtered = presented_options(_result(), answer)

    # Assert
    assert _skus(filtered) == [option["sku"] for option in PLATOS[:MAX_OPTIONS]]


def test_si_las_nombra_todas_devuelve_el_mismo_resultado() -> None:
    # Arrange
    result = _result(PLATOS[:2])
    answer = "Plato espumado hondo 12 oz Wau y Plato espumado hondo 5 oz Wau."

    # Act
    filtered = presented_options(result, answer)

    # Assert
    assert filtered is result


def test_si_lista_las_ocho_quedan_como_mucho_cuatro_tarjetas() -> None:
    # Arrange: caso real del 2026-11-25 («necesito 400 vasos para bebida»): el texto listó
    # las ocho opciones que vio el modelo.
    answer = "\n".join(f"- {option['name']} (SKU {option['sku']})" for option in PLATOS)

    # Act
    filtered = presented_options(_result(), answer)

    # Assert
    assert len(_skus(filtered)) == MAX_PRESENTED_OPTIONS
    assert _skus(filtered) == [option["sku"] for option in PLATOS[:MAX_PRESENTED_OPTIONS]]


def test_las_tarjetas_siguen_el_orden_del_texto() -> None:
    # Arrange: el texto nombra primero la última opción de la herramienta.
    answer = (
        "- Plato espumado pando 20 cm Wau (SKU CO-PPAN-20)\n"
        "- Plato espumado hondo 12 oz Wau (SKU 7704918101288)\n"
        "- Plato espumado pando 23 cm Wau\n"
    )

    # Act
    filtered = presented_options(_result(), answer)

    # Assert
    assert _skus(filtered) == ["CO-PPAN-20", "7704918101288", "CO-PPAN-23"]


def test_la_ficha_y_la_foto_no_se_filtran() -> None:
    # Arrange
    ficha = _result(ficha=PLATOS[0])
    foto = _result(origin="foto")
    answer = "Te recomiendo el Plato espumado hondo 12 oz Wau."

    # Act
    filtered_ficha = presented_options(ficha, answer)
    filtered_foto = presented_options(foto, answer)

    # Assert
    assert filtered_ficha is ficha
    assert filtered_foto is foto
    assert len(filtered_foto["options"]) == len(PLATOS)


def test_sin_opciones_o_sin_resultado_no_cambia_nada() -> None:
    # Arrange
    quote_only = {"quote": {"total": "100"}}

    # Act
    filtered = presented_options(quote_only, "Son 100 COP.")

    # Assert
    assert filtered is quote_only
    assert presented_options(None, "Hola") is None


def test_las_gemelas_se_resuelven_por_el_sku_nombrado() -> None:
    # Arrange: tres referencias con el mismo nombre; el texto da el SKU de la segunda.
    name = "Platos de plástico PS 7 Bio - Caja con 1000 pzs"
    options = [
        _option("CO-MX-2864903323", name),
        _option("CO-MX-2864903324", name),
        _option("CO-MX-2864903325", name),
        _option("CO-PPAN-23", "Plato espumado pando 23 cm Wau"),
    ]
    answer = f"Te sirve **{name}** (CO-MX-2864903324) [2]."

    # Act
    filtered = presented_options(_result(options), answer)

    # Assert
    assert _skus(filtered) == ["CO-MX-2864903324"]


def test_las_gemelas_sin_sku_quedan_tantas_como_veces_se_nombran() -> None:
    # Arrange: el nombre aparece una vez y sin SKU → una sola tarjeta, la primera.
    name = "Platos de plástico PS 7 Bio - Caja con 1000 pzs"
    options = [
        _option("CO-MX-2864903323", name),
        _option("CO-MX-2864903324", name),
        _option("CO-MX-2864903325", name),
    ]
    once = f"Te sirven los {name} [1]."
    twice = f"- {name} [1]\n- {name} [2]"

    # Act
    filtered_once = presented_options(_result(options), once)
    filtered_twice = presented_options(_result(options), twice)

    # Assert
    assert _skus(filtered_once) == ["CO-MX-2864903323"]
    assert _skus(filtered_twice) == ["CO-MX-2864903323", "CO-MX-2864903324"]


GEMELAS = "Platos de plástico PS 7 Bio - Caja con 1000 pzs"


def test_el_sku_en_el_formato_del_prompt_coincide() -> None:
    # Arrange: el prompt pide «(SKU 2864108000)» para que la voz no lea el código.
    options = [
        _option("2864108000", "Vasos térmicos 8oz - Caja con 1000 pzs"),
        _option("2864632125", "Vasos de papel 32oz 105mm"),
    ]
    answer = "Para café te recomiendo un vaso térmico de 8 oz (SKU 2864108000) [1]."

    # Act
    filtered = presented_options(_result(options), answer)

    # Assert
    assert _skus(filtered) == ["2864108000"]


def test_las_gemelas_suman_por_sku_y_por_nombre() -> None:
    # Arrange: un ítem trae el SKU de la primera y otro solo el nombre → dos tarjetas.
    options = [
        _option("CO-MX-2864903323", GEMELAS),
        _option("CO-MX-2864903324", GEMELAS),
        _option("CO-MX-2864903325", GEMELAS),
    ]
    answer = f"- {GEMELAS} (CO-MX-2864903323)\n- {GEMELAS} [2]"

    # Act
    filtered = presented_options(_result(options), answer)

    # Assert
    assert _skus(filtered) == ["CO-MX-2864903323", "CO-MX-2864903324"]


def test_repetir_el_nombre_en_el_mismo_parrafo_no_suma_gemelas() -> None:
    # Arrange: dos menciones en la misma línea cuentan como una.
    options = [
        _option("CO-MX-2864903323", GEMELAS),
        _option("CO-MX-2864903324", GEMELAS),
    ]
    answer = f"Te recomiendo **{GEMELAS}**. Los {GEMELAS} vienen por caja."

    # Act
    filtered = presented_options(_result(options), answer)

    # Assert
    assert _skus(filtered) == ["CO-MX-2864903323"]


def test_las_tarjetas_renderizadas_son_las_nombradas() -> None:
    # Arrange
    answer = "Opciones: Plato espumado pando 23 cm Wau (CO-PPAN-23) y CO-P3DIV."

    # Act
    html = render_products(presented_options(_result(), answer) or {})

    # Assert
    assert html.count('data-campo="producto-nombre"') == 2
    assert "Plato espumado pando 23 cm Wau" in html
    assert "Plato espumado hondo 12 oz Wau" not in html
