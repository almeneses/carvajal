"""Pruebas del resumen hablado y de la normalización de transcripciones (spec 048).

Funciones puras de ``carvajal.speech``, sin red ni LLM:

- ``spoken_summary``: extractivo (solo frases y cifras de la respuesta), con presupuesto de
  palabras, lista en una frase, pregunta final, fuentes en una frase y aviso por canal;
- el contrato de ``voz=true`` con la sección escrita (``TEXT_HEADER``) y su inversa
  ``parse_voice_reply``, compatible con ``split_voice_reply``;
- ``normalize_transcript``: miles al estilo de EE. UU. → estilo colombiano.
"""

import random
import re
from typing import Any, cast

import pytest

from carvajal.speech import (
    LINKS_HEADER,
    SUMMARY_POINTERS,
    SUMMARY_WORDS,
    TEXT_HEADER,
    SpeechLink,
    SpokenReply,
    combine,
    normalize_transcript,
    parse_voice_reply,
    split_voice_reply,
    spoken_summary,
    to_speech,
)

RAG_ANSWER = (
    "Para bebidas calientes, Carvajal Empaques ofrece vasos de papel de pared sencilla y "
    "doble pared [1]. Son aptos para café, té y chocolate, y se venden en cajas de 1.000 "
    "unidades.\n\n"
    "Estas son las opciones:\n"
    "- **Vaso de papel 8 oz**: pared sencilla, caja x 1.000 und, ideal para tinto.\n"
    "- **Vaso de papel 12 oz**: doble pared, caja x 500 und, aprox. $185.000 por caja.\n"
    "- **Vaso de papel 16 oz** — doble pared, para capuchino y bebidas grandes.\n\n"
    "Todos se pueden combinar con tapas de 80 mm y portavasos. Revisa la ficha en "
    "https://colombia.carvajalempaques.com/vasos para más detalles.\n\n"
    "¿Cuántas unidades necesitas y en qué ciudad?"
)
SOURCES = [("Vasos de papel", "https://colombia.carvajalempaques.com/vasos")]
LONG_PROSE = (
    "Carvajal Empaques fabrica empaques desechables para alimentos y bebidas desde hace más "
    "de cincuenta años en Colombia [1]. Tiene plantas en Ginebra, Tenjo y Cali, y exporta a "
    "más de diez países de la región. Su portafolio incluye vasos, contenedores, cubiertos, "
    "bolsas y láminas para la industria. También ofrece líneas compostables hechas con fibra "
    "de caña de azúcar y vasos de papel con certificación forestal.\n\n"
    "¿Te interesa alguna línea en particular?"
)
# Frases que el resumen agrega por su cuenta (no son de la respuesta).
_OWN_PHRASES = re.compile(
    r"^(?:Te muestro|Te dejo|Según el sitio|Lo saqué|El detalle completo)", re.IGNORECASE
)


# Tokens numéricos completos: «1.234.500» es uno solo, nunca «234.500» ni «1.234».
_NUMBER = re.compile(r"(?<![\d.,])\d[\d.,]*\d(?![\d.,]\d)|(?<![\d.,])\d(?![\d.,]\d)")
_ITEM = re.compile(r"^\s*(?:[-*+•]|\d{1,2}[.)])\s+(.+)$")
_LIST_PHRASE = re.compile(
    r"^(?:Te muestro (?:\S+ ){1,2}en pantalla|Te dejo por escrito (?:\S+ ){0,1}\S+): (.+)\.$"
)
# Una cifra justo tras el corte de la cabeza: el corte habría partido una enumeración o
# un rango («Vasos de 7» de «Vasos de 7, 9 y 12 onzas»).
_CUT_FIGURE = re.compile(r"\s*(?:[,\u2013\u2014-]|\s(?:y|a|e))?\s*\$?\d")


def _words(text: str) -> int:
    return len(text.split())


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text))


def _sentences(text: str) -> list[str]:
    """Frases del resumen, partidas sin mirar cifras (un punto de miles no lleva espacio)."""

    return [
        piece.strip()
        for paragraph in text.split("\n\n")
        for piece in re.split(r"(?<=[.!?])\s+", paragraph)
        if piece.strip()
    ]


def _spoken_items(text: str) -> list[str]:
    """Ítems de la primera lista de la respuesta, pasados a voz uno a uno y sin el punto."""

    items: list[str] = []
    for line in text.splitlines():
        match = _ITEM.match(line)
        if match:
            items.append(to_speech(match[1]).text.rstrip(" ."))
        elif items:
            break
    return items


def _assert_heads_are_whole(listed: str, items: list[str]) -> None:
    """Cada cabeza dicha es un prefijo de su ítem que no corta una cifra, un rango ni una
    enumeración y cuyas cifras son tokens completos del ítem."""

    rest = listed
    separators = [", "] * max(0, len(items) - 2) + [" y "] * (len(items) > 1) + [""]
    for item, separator in zip(items, separators, strict=True):
        head = next(
            (
                item[:end]
                for end in range(len(item), 0, -1)
                if rest.casefold().startswith(item[:end].casefold())
                and (rest[end:].startswith(separator) if separator else rest[end:] == "")
            ),
            None,
        )
        assert head, (listed, item)
        if head[-1].isdigit():
            assert not _CUT_FIGURE.match(item[len(head) :]), (head, item)
        assert _numbers(head) <= _numbers(item), (head, item)
        rest = rest[len(head) + len(separator) :]


# --- respuesta típica de RAG ---------------------------------------------------------------


def test_resumen_web_de_una_respuesta_con_lista_y_pregunta() -> None:
    # Act
    summary = spoken_summary(RAG_ANSWER, channel="web", sources=SOURCES)

    # Assert: el párrafo de las tapas queda fuera, así que se avisa aunque haya lista.
    assert summary.text == (
        "Para bebidas calientes, Carvajal Empaques ofrece vasos de papel de pared sencilla y "
        "doble pared. Son aptos para café, té y chocolate, y se venden en cajas de 1.000 "
        "unidades. Te dejo tres opciones en pantalla.\n\n"
        "Según el sitio de Carvajal Empaques.\n\n"
        f"{SUMMARY_POINTERS['web']}\n\n"
        "¿Cuántas unidades necesitas y en qué ciudad?"
    )
    assert summary.omitted is True
    assert _words(summary.text) <= SUMMARY_WORDS
    assert _words(to_speech(RAG_ANSWER).text) > 1.8 * _words(summary.text)


def test_si_solo_se_omite_el_detalle_de_la_lista_nombra_las_cabezas_sin_aviso() -> None:
    # Arrange: la introducción corta de la lista va en su propio párrafo.
    text = RAG_ANSWER.replace(
        " Son aptos para café, té y chocolate, y se venden en cajas de 1.000 unidades.", ""
    ).replace(
        "Todos se pueden combinar con tapas de 80 mm y portavasos. Revisa la ficha en "
        "https://colombia.carvajalempaques.com/vasos para más detalles.\n\n",
        "",
    )

    # Act
    summary = spoken_summary(text, channel="web", sources=SOURCES)

    # Assert
    assert summary.text == (
        "Para bebidas calientes, Carvajal Empaques ofrece vasos de papel de pared sencilla y "
        "doble pared. Te muestro tres opciones en pantalla: Vaso de papel 8 onzas, Vaso de "
        "papel 12 onzas y Vaso de papel 16 onzas.\n\n"
        "Según el sitio de Carvajal Empaques.\n\n"
        "¿Cuántas unidades necesitas y en qué ciudad?"
    )
    assert summary.omitted is True


def test_con_lista_y_un_parrafo_omitido_avisa_que_hay_mas_detalle() -> None:
    # Arrange
    text = (
        "Para tu cafetería te recomiendo estos vasos de papel.\n"
        "- Vaso de papel 8 oz\n- Vaso de papel 12 oz\n- Vaso de papel 16 oz\n\n"
        "El pedido mínimo es de 1.000 unidades por referencia y los precios no incluyen IVA. "
        "El despacho tarda de 3 a 5 días hábiles y hay descuentos desde 10.000 unidades."
    )

    # Act
    web = spoken_summary(text, channel="web", budget=40)
    whatsapp = spoken_summary(text, channel="whatsapp", budget=40)

    # Assert
    assert web.text == (
        "Para tu cafetería te recomiendo estos vasos de papel. Te muestro tres opciones en "
        "pantalla: Vaso de papel 8 onzas, Vaso de papel 12 onzas y Vaso de papel 16 onzas."
        f"\n\n{SUMMARY_POINTERS['web']}"
    )
    assert whatsapp.text.endswith(SUMMARY_POINTERS["whatsapp"])
    assert web.omitted is True
    assert _words(web.text) <= 40


def test_con_lista_si_la_pregunta_no_cabe_se_avisa() -> None:
    # Arrange
    question = (
        "¿Me confirmas cuántas unidades necesitas, en qué ciudad recibes el pedido y si "
        "quieres tapa?"
    )
    text = (
        "Para tu cafetería te recomiendo estos vasos de papel de doble pared.\n"
        "- Vaso 8 oz\n- Vaso 12 oz\n\n"
        f"{question}"
    )

    # Act
    summary = spoken_summary(text, budget=25)

    # Assert
    assert question not in summary.text
    assert summary.text.endswith(SUMMARY_POINTERS["web"])
    assert _words(summary.text) <= 25


def test_resumen_de_whatsapp_remite_al_texto_escrito() -> None:
    # Act
    summary = spoken_summary(RAG_ANSWER, channel="whatsapp", sources=SOURCES)

    # Assert
    assert "Te dejo tres opciones por escrito." in summary.text
    assert SUMMARY_POINTERS["whatsapp"] in summary.text
    assert "en pantalla" not in summary.text
    assert summary.text.endswith("¿Cuántas unidades necesitas y en qué ciudad?")


def test_el_resumen_no_lee_url_y_conserva_los_enlaces() -> None:
    # Act
    summary = spoken_summary(RAG_ANSWER, channel="whatsapp", sources=SOURCES)

    # Assert
    assert "http" not in summary.text and "enlace" not in summary.text
    assert summary.links == (SpeechLink("Enlace", "https://colombia.carvajalempaques.com/vasos"),)


def test_sin_lista_avisa_lo_omitido_antes_de_la_pregunta() -> None:
    # Act
    web = spoken_summary(LONG_PROSE, channel="web")
    whatsapp = spoken_summary(LONG_PROSE, channel="whatsapp")

    # Assert
    assert web.text.startswith("Carvajal Empaques fabrica empaques desechables")
    assert web.text.endswith(
        f"{SUMMARY_POINTERS['web']}\n\n¿Te interesa alguna línea en particular?"
    )
    assert whatsapp.text.endswith(
        f"{SUMMARY_POINTERS['whatsapp']}\n\n¿Te interesa alguna línea en particular?"
    )
    assert "fibra de caña" not in web.text
    assert web.omitted is True


# --- respuestas cortas y abstenciones -------------------------------------------------------


def test_una_respuesta_corta_se_lee_completa_sin_aviso() -> None:
    # Arrange
    text = "Carvajal nació en **1904** en Cali [1]."

    # Act
    summary = spoken_summary(text, sources=[("Historia", "https://www.carvajal.com/historia")])

    # Assert
    assert summary.text == "Carvajal nació en 1904 en Cali.\n\nSegún el sitio de Carvajal."
    assert summary.omitted is False


def test_una_abstencion_se_lee_tal_cual() -> None:
    # Arrange
    text = (
        "No encontré información sobre ese tema en las fuentes de Carvajal. ¿Puedo ayudarte "
        "con algo más sobre sus empaques?"
    )

    # Act
    summary = spoken_summary(text, channel="whatsapp")

    # Assert
    assert summary == SpokenReply(text)


def test_una_lista_corta_que_cabe_se_lee_entera() -> None:
    # Arrange
    text = "Tenemos tres tamaños:\n- 8 oz\n- 12 oz\n- 16 oz"

    # Act
    summary = spoken_summary(text)

    # Assert
    assert (
        summary.text
        == to_speech(text).text
        == "Tenemos tres tamaños: 8 onzas, 12 onzas y 16 onzas."
    )
    assert summary.omitted is False


def test_una_sola_frase_larga_se_lee_completa() -> None:
    # Arrange: no hay nada que omitir, aunque pase del presupuesto.
    sentence = " ".join(["palabra"] * 70) + "."

    # Act
    summary = spoken_summary(sentence, budget=30)

    # Assert
    assert summary.text == to_speech(sentence).text
    assert summary.omitted is False


# --- listas ---------------------------------------------------------------------------------


def test_con_mas_de_cuatro_items_solo_dice_cuantos_hay() -> None:
    # Arrange
    items = "\n".join(f"- Vaso de {size} oz, caja x 1.000 und" for size in (7, 9, 12, 16, 20))
    text = f"Para bebidas frías y calientes tenemos vasos de varios tamaños.\n{items}"

    # Act
    web = spoken_summary(text, budget=20)
    whatsapp = spoken_summary(text, channel="whatsapp", budget=20)

    # Assert
    assert web.text == (
        "Para bebidas frías y calientes tenemos vasos de varios tamaños. "
        "Te dejo cinco opciones en pantalla."
    )
    assert whatsapp.text.endswith("Te dejo cinco opciones por escrito.")


def test_items_con_cabezas_largas_no_se_enumeran() -> None:
    # Arrange
    text = (
        "Te recomiendo estos contenedores térmicos para domicilios de almuerzo.\n"
        "- Portacomidas espumado de una división con tapa abatible y cierre seguro\n"
        "- Portacomidas espumado de tres divisiones con tapa abatible y cierre seguro"
    )

    # Act
    summary = spoken_summary(text, budget=20)

    # Assert
    assert summary.text.endswith("Te dejo dos opciones en pantalla.")


_INTRO = (
    "Para tu cafetería tenemos varias referencias de vasos de papel con despacho nacional y "
    "tapas compatibles."
)


@pytest.mark.parametrize(
    ("items", "never"),
    [
        ("- Vasos de 7, 9 y 12 oz en papel\n- Vaso 16 oz, doble pared", "Vasos de 7"),
        ("- Entre 1.000 - 5.000 unidades: $120\n- Más de 5.000 unidades: $100", "Entre 1.000"),
        (
            "- Caja x 1.000 und, vaso 8 oz\n- Caja x 1.000 und, vaso 12 oz",
            "Caja de 1.000 unidades y Caja",
        ),
        ("- Precio: $1.200 por unidad\n- Mínimo: 5.000 unidades", "Precio y Mínimo"),
    ],
    ids=["enumeracion", "rango", "cabezas-repetidas", "clave-valor"],
)
def test_no_nombra_cabezas_que_cortan_cifras_o_no_distinguen_opciones(
    items: str, never: str
) -> None:
    # Arrange
    text = f"{_INTRO}\n{items}\n\n¿Cuántas unidades necesitas?"

    # Act
    summary = spoken_summary(text, budget=25)

    # Assert: solo dice cuántas hay; ninguna cifra dicha es distinta de la respuesta.
    assert "Te dejo dos opciones en pantalla." in summary.text
    assert never not in summary.text
    assert _numbers(summary.text) <= _numbers(to_speech(text).text)


def test_una_lista_numerada_son_pasos_y_no_opciones() -> None:
    # Arrange
    text = (
        "Para comprar en línea, sigue estos pasos con tu usuario de cliente:\n"
        "1. Regístrate en el portal\n2. Agrega los productos\n3. Paga con tarjeta\n\n"
        "El pedido llega en tres días hábiles a cualquier ciudad del país con guía de envío."
    )

    # Act
    web = spoken_summary(text, budget=25)
    whatsapp = spoken_summary(text, channel="whatsapp", budget=25)

    # Assert
    assert "Te dejo tres pasos en pantalla." in web.text
    assert "Te dejo tres pasos por escrito." in whatsapp.text
    assert "opciones" not in web.text
    assert "Regístrate" not in web.text


def test_la_introduccion_corta_de_la_lista_se_omite_y_la_larga_se_dice() -> None:
    # Arrange
    items = "\n- Vaso 8 oz, pared sencilla y caja de mil\n- Vaso 12 oz, doble pared y caja de mil"
    short = f"Estas son las opciones:{items}\n\nTodos los vasos traen tapa compatible de 80 mm."
    long = (
        f"Para café caliente en tu cafetería de Medellín tenemos estos vasos:{items}\n\n"
        "Todos los vasos traen tapa compatible de 80 mm."
    )

    # Act
    without = spoken_summary(short, budget=20)
    kept = spoken_summary(long, budget=30)

    # Assert
    assert without.text.startswith("Te muestro dos opciones en pantalla: Vaso 8 onzas y Vaso 12")
    assert "Estas son las opciones" not in without.text
    assert kept.text.startswith(
        "Para café caliente en tu cafetería de Medellín tenemos estos vasos. Te muestro dos"
    )


def test_una_lista_al_inicio_va_antes_que_la_prosa() -> None:
    # Arrange
    text = (
        "- Vaso 8 oz, pared sencilla\n- Vaso 12 oz, doble pared\n\n"
        "Los dos vienen en cajas de 1.000 unidades y se despachan desde Cali o Bogotá. "
        "Pídelos con tu distribuidor más cercano para que te entregue en dos días."
    )

    # Act
    summary = spoken_summary(text, budget=35)

    # Assert
    assert summary.text.startswith(
        "Te muestro dos opciones en pantalla: Vaso 8 onzas y Vaso 12 onzas. Los dos vienen"
    )


def test_los_encabezados_no_abren_el_resumen() -> None:
    # Arrange
    text = (
        "## Vasos para café\n"
        "Tenemos vasos de papel de doble pared para café caliente. Todos llevan tapa "
        "compatible y se pueden personalizar con tu logo desde 10.000 unidades por pedido.\n\n"
        "- 8 oz\n- 12 oz\n- 16 oz\n\n"
        "Todos vienen en cajas de 1.000 unidades con despacho a todo el país."
    )

    # Act
    summary = spoken_summary(text, budget=30)

    # Assert
    assert summary.text.startswith("Tenemos vasos de papel de doble pared para café caliente.")
    assert "Vasos para café." not in summary.text
    assert "Te muestro tres opciones en pantalla: 8 onzas, 12 onzas y 16 onzas." in summary.text


# --- cifras, presupuesto y extracción --------------------------------------------------------


def test_nunca_parte_una_cifra() -> None:
    # Arrange
    text = (
        "El precio de referencia es $1.234.500 por caja de 1.000 unidades, con IVA del 19 % "
        "incluido. La caja pesa 12,5 kg y mide 40 x 30 x 50 cm. El pedido mínimo es de 5 "
        "cajas y la entrega tarda de 3 a 5 días hábiles después de confirmar el pago. El "
        "envío a Cali cuesta aprox. $45.000 y a Bogotá $52.500. ¿Quieres que te cotice?"
    )

    # Act
    summary = spoken_summary(text, budget=30)

    # Assert
    assert summary.text.startswith(
        "El precio de referencia es 1.234.500 pesos por caja de 1.000 unidades, con IVA del "
        "19 por ciento incluido."
    )
    assert _numbers(summary.text) <= _numbers(to_speech(text).text)
    assert summary.text.endswith("¿Quieres que te cotice?")


def test_los_tokens_numericos_detectan_una_cifra_cortada() -> None:
    # Arrange: una subcadena pasaría; un token completo, no.
    full = "Cuesta 1.234.500 pesos, para 1.000 - 5.000 unidades."

    # Act / Assert
    assert "234.500" in full and "234.500" not in _numbers(full)
    assert _numbers("Cuesta 1.234.500 pesos.") <= _numbers(full)
    assert not _numbers("Cuesta 234.500 pesos.") <= _numbers(full)


def test_el_oraculo_de_cabezas_detecta_una_enumeracion_cortada() -> None:
    # Arrange
    items = ["Vasos de 7, 9 y 12 onzas en papel", "Vaso 16 onzas, doble pared"]

    # Act / Assert
    _assert_heads_are_whole("Vasos de 7, 9 y 12 onzas en papel y Vaso 16 onzas", items)
    with pytest.raises(AssertionError):
        _assert_heads_are_whole("Vasos de 7 y Vaso 16 onzas", items)
    with pytest.raises(AssertionError):
        _assert_heads_are_whole("Entre 1.000 y Más", ["Entre 1.000 - 5.000 unidades", "Más"])


def test_la_lista_con_rango_o_enumeracion_no_parte_cifras() -> None:
    # Arrange
    text = (
        "Te comparto los precios por volumen de los vasos de papel para tu pedido.\n"
        "- Vasos de 7, 9 y 12 oz: desde 1.000 - 5.000 unidades\n"
        "- Vasos de 16 oz: más de 5.000 unidades"
    )

    # Act
    summary = spoken_summary(text, budget=30)

    # Assert
    assert _numbers(summary.text) <= _numbers(to_speech(text).text)
    match = next(
        (found for sentence in _sentences(summary.text) if (found := _LIST_PHRASE.match(sentence))),
        None,
    )
    if match:
        _assert_heads_are_whole(match[1], _spoken_items(text))


def _random_answer(seed: int) -> str:
    rng = random.Random(seed)
    vocabulary = [
        "vaso",
        "papel",
        "caja",
        "unidades",
        "Cali",
        "Bogotá",
        "precio",
        "entrega",
        "tapa",
        "doble",
        "pared",
        "café",
        "contenedor",
        "icopor",
        "bandeja",
        "cubiertos",
        "compostable",
        "caña",
        "pedido",
        "mínimo",
        "distribuidor",
    ]

    def sentence() -> str:
        words = [rng.choice(vocabulary) for _ in range(rng.randint(3, 40))]
        words.insert(rng.randint(0, len(words)), f"{rng.randint(1, 999)}.{rng.randint(100, 999)}")
        return " ".join(words).capitalize() + "."

    def item() -> str:
        a, b, c = sorted(rng.sample(range(4, 33), 3))
        low = rng.randint(1, 9)
        choices = [
            sentence,
            lambda: f"Vasos de {a}, {b} y {c} oz en papel",
            lambda: f"Entre {low}.000 - {low + 4}.000 unidades: ${rng.randint(50, 999)}",
            lambda: f"{rng.choice(['Precio', 'Mínimo', 'Entrega'])}: ${rng.randint(1, 99)}.000",
            lambda: f"Caja x 1.000 und, vaso {a} oz",
            lambda: f"Vaso de papel {b} oz, doble pared",
            lambda: f"Vaso de papel {a} oz",
            lambda: f"Tapa {c} mm (compatible)",
            lambda: f"Contenedor de {a} oz - {b} unidades",
        ]
        return rng.choice(choices)()

    opening = [sentence() for _ in range(rng.randint(1, 5))]
    if rng.random() < 0.4:
        opening = ["Tenemos estas referencias de vasos."]
    blocks = [" ".join(opening)]
    if rng.random() < 0.7:
        items = [f"- {item()}" for _ in range(rng.randint(1, rng.choice([3, 8])))]
        blocks.append("\n".join(items))
    if rng.random() < 0.5:
        blocks.append(" ".join(sentence() for _ in range(rng.randint(1, 3))))
    if rng.random() < 0.6:
        blocks.append("¿" + sentence().rstrip(".") + "?")
    return "\n\n".join(blocks)


@pytest.mark.parametrize("budget", [20, 35, SUMMARY_WORDS])
@pytest.mark.parametrize("seed", range(60))
def test_solo_las_piezas_obligatorias_pasan_del_presupuesto(seed: int, budget: int) -> None:
    # Arrange
    text = _random_answer(seed)

    # Act
    summary = spoken_summary(text, channel="web", sources=SOURCES, budget=budget)

    # Assert: solo la primera frase, la frase de la lista, la de fuentes y el aviso entran
    # aunque no quepan; todo lo demás, solo si cabe.
    sentences = _sentences(summary.text)
    forced = sum(_words(sentence) for sentence in sentences if _OWN_PHRASES.match(sentence))
    first = next((s for s in sentences if not _OWN_PHRASES.match(s)), "")
    assert _words(summary.text) <= max(budget, forced + _words(first))


@pytest.mark.parametrize("budget", [35, 80])
@pytest.mark.parametrize("seed", range(60))
def test_el_resumen_solo_reutiliza_frases_de_la_respuesta(seed: int, budget: int) -> None:
    # Arrange
    text = _random_answer(seed)
    full = to_speech(text).text

    # Act
    summary = spoken_summary(text, channel="whatsapp", budget=budget)

    # Assert: toda frase propia de la respuesta está en su lectura completa, sin cambios;
    # las cabezas de la lista son prefijos enteros de sus ítems y ninguna cifra cambia.
    assert _numbers(summary.text) <= _numbers(full)
    for sentence in _sentences(summary.text):
        listed = _LIST_PHRASE.match(sentence)
        if listed:
            _assert_heads_are_whole(listed[1], _spoken_items(text))
            continue
        if _OWN_PHRASES.match(sentence):
            continue
        assert sentence in full, sentence


def test_las_fuentes_cuentan_para_leer_la_respuesta_completa() -> None:
    # Arrange: 51 palabras de prosa y 5 de fuentes pasan de 55.
    sentence = "Los vasos de papel de doble pared sirven para café, té y chocolate caliente."
    text = " ".join([sentence] * 3) + " Llegan en cajas de mil unidades con tapa incluida."
    sources = [("Vasos", "https://www.carvajal.com/vasos")]
    assert _words(to_speech(text).text) <= SUMMARY_WORDS

    # Act
    summary = spoken_summary(text, sources=sources)

    # Assert
    assert summary.omitted is True
    assert _words(summary.text) <= SUMMARY_WORDS
    assert summary.text.endswith(SUMMARY_POINTERS["web"])


def test_un_canal_desconocido_falla() -> None:
    # Act / Assert
    with pytest.raises(ValueError, match="Canal"):
        spoken_summary("Hola.", channel=cast(Any, "sms"))


# --- contrato de voz=true con texto escrito -------------------------------------------------


def test_render_con_texto_escrito_usa_la_seccion_de_texto() -> None:
    # Arrange
    reply = SpokenReply(
        "Resumen corto.", (SpeechLink("Ficha", "https://x.example/f"),), omitted=True
    )
    written = "*Respuesta completa* con la ficha (https://x.example/f)."

    # Act
    rendered = reply.render(written=written)

    # Assert: el enlace ya va en el texto escrito; la sección de enlaces sobra.
    assert rendered == f"Resumen corto.\n\n{TEXT_HEADER}\n{written}"
    assert LINKS_HEADER not in rendered


def test_render_agrega_los_enlaces_que_no_estan_en_el_texto_escrito() -> None:
    # Arrange
    reply = SpokenReply(
        "Resumen.",
        (SpeechLink("Ficha", "https://x.example/f"), SpeechLink("Foto", "https://x.example/p")),
    )

    # Act
    rendered = reply.render(written="Texto con https://x.example/f")
    parts = parse_voice_reply(rendered)

    # Assert
    assert parts.spoken == "Resumen."
    assert parts.written == "Texto con https://x.example/f"
    assert parts.links == ("Foto: https://x.example/p",)


def test_render_compara_url_completas_y_no_prefijos() -> None:
    # Arrange: «…/vasos» es prefijo de «…/vasos-12», pero no está en el texto escrito.
    reply = SpokenReply("Resumen.", (SpeechLink("Ficha", "https://c.com/vasos"),))

    # Act
    rendered = reply.render(written="Mira https://c.com/vasos-12 y (https://c.com/tapas).")
    kept = SpokenReply("R.", (SpeechLink("Tapas", "https://c.com/tapas"),)).render(
        written="Mira (https://c.com/tapas)."
    )

    # Assert
    assert parse_voice_reply(rendered).links == ("Ficha: https://c.com/vasos",)
    assert LINKS_HEADER not in kept


def test_parse_es_inverso_de_render_y_split_ignora_el_texto_escrito() -> None:
    # Arrange
    written = "Línea uno.\n\nLínea dos con *negrita*.\n- ítem"
    rendered = SpokenReply("Hola.\n\nAdiós.").render(written=written)

    # Act
    parts = parse_voice_reply(rendered)
    spoken, links = split_voice_reply(rendered)

    # Assert
    assert (parts.spoken, parts.written, parts.links) == ("Hola.\n\nAdiós.", written, ())
    assert (spoken, links) == ("Hola.\n\nAdiós.", [])


def test_render_sin_texto_escrito_no_cambia_el_contrato_anterior() -> None:
    # Arrange
    reply = SpokenReply("Te envío el PDF.", (SpeechLink("PDF", "https://x.example/c.pdf"),))

    # Act / Assert
    assert reply.render() == f"Te envío el PDF.\n\n{LINKS_HEADER}\nPDF: https://x.example/c.pdf"
    assert parse_voice_reply("Solo voz.").written == ""


def test_combine_conserva_que_algo_se_omitio() -> None:
    # Act
    joined = combine([SpokenReply("Uno."), SpokenReply("Dos.", omitted=True)])

    # Assert
    assert joined.omitted is True
    assert combine([SpokenReply("Uno.")]).omitted is False


# --- transcripciones ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        ("Cotiza 1,800 vasos en Cali", "Cotiza 1.800 vasos en Cali"),
        ("necesito 12,500,000 pesos", "necesito 12.500.000 pesos"),
        ("son 2,500.50 pesos", "son 2.500,50 pesos"),
        ("$1,800", "$1.800"),
        ("quiero 1,000 unidades.", "quiero 1.000 unidades."),
        ("entre 1,200 y 3,400 cajas", "entre 1.200 y 3.400 cajas"),
        ("1,800,", "1.800,"),
        ("(1,800)", "(1.800)"),
    ],
)
def test_normaliza_los_miles_al_estilo_colombiano(heard: str, expected: str) -> None:
    # Act / Assert
    assert normalize_transcript(heard) == expected


@pytest.mark.parametrize(
    "heard",
    [
        "1,5 litros",
        "0,75",
        "12,5 onzas",
        "a las 10:30",
        "tallas 2, 3 y 4",
        "1.800 vasos",
        "12.500.000 pesos",
        "en 2026",
        "0,750 kilos",
        "1,8000",
        "1,80",
        "versión 1.2.3",
        "",
    ],
)
def test_no_toca_decimales_horas_ni_listas(heard: str) -> None:
    # Act / Assert
    assert normalize_transcript(heard) == heard


@pytest.mark.parametrize(
    ("heard", "expected"),
    [("son 1,500 kg", "son 1.500 kg"), ("1,250 litros", "1.250 litros")],
)
def test_tres_cifras_tras_la_coma_se_toman_como_miles_aunque_haya_unidades(
    heard: str, expected: str
) -> None:
    # Decisión documentada en normalize_transcript: el STT escribe así los miles.
    # Act / Assert
    assert normalize_transcript(heard) == expected
