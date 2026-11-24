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


def _words(text: str) -> int:
    return len(text.split())


def _sentences(text: str) -> list[str]:
    return [
        piece.strip()
        for piece in re.split(r"(?<=[.!?])\s+(?=[¿A-ZÁÉÍÓÚÑ0-9])", text)
        if piece.strip()
    ]


# --- respuesta típica de RAG ---------------------------------------------------------------


def test_resumen_web_de_una_respuesta_con_lista_y_pregunta() -> None:
    # Act
    summary = spoken_summary(RAG_ANSWER, channel="web", sources=SOURCES)

    # Assert
    assert summary.text == (
        "Para bebidas calientes, Carvajal Empaques ofrece vasos de papel de pared sencilla y "
        "doble pared. Te muestro tres opciones en pantalla: Vaso de papel 8 onzas, Vaso de "
        "papel 12 onzas y Vaso de papel 16 onzas.\n\n"
        "Según el sitio de Carvajal Empaques.\n\n"
        "¿Cuántas unidades necesitas y en qué ciudad?"
    )
    assert summary.omitted is True
    assert _words(summary.text) <= SUMMARY_WORDS
    assert _words(to_speech(RAG_ANSWER).text) > 2 * _words(summary.text)


def test_resumen_de_whatsapp_remite_al_texto_escrito() -> None:
    # Act
    summary = spoken_summary(RAG_ANSWER, channel="whatsapp", sources=SOURCES)

    # Assert
    assert "Te dejo por escrito tres opciones: Vaso de papel 8 onzas" in summary.text
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
    full = to_speech(text).text
    for number in re.findall(r"\d[\d.,]*\d|\d", summary.text):
        assert number in full, number
    assert summary.text.endswith("¿Quieres que te cotice?")


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

    blocks = [" ".join(sentence() for _ in range(rng.randint(1, 5)))]
    if rng.random() < 0.7:
        items = [f"- {sentence()}" for _ in range(rng.randint(1, 8))]
        blocks.append("\n".join(items))
    if rng.random() < 0.5:
        blocks.append(" ".join(sentence() for _ in range(rng.randint(1, 3))))
    if rng.random() < 0.6:
        blocks.append("¿" + sentence().rstrip(".") + "?")
    return "\n\n".join(blocks)


@pytest.mark.parametrize("budget", [20, 35, SUMMARY_WORDS])
@pytest.mark.parametrize("seed", range(40))
def test_nunca_pasa_del_presupuesto_en_mas_de_una_frase(seed: int, budget: int) -> None:
    # Arrange
    text = _random_answer(seed)

    # Act
    summary = spoken_summary(text, channel="web", sources=SOURCES, budget=budget)

    # Assert
    longest = max(_words(sentence) for sentence in _sentences(summary.text))
    assert _words(summary.text) <= budget + longest


@pytest.mark.parametrize("seed", range(40))
def test_el_resumen_solo_reutiliza_frases_de_la_respuesta(seed: int) -> None:
    # Arrange
    text = _random_answer(seed)
    full = to_speech(text).text

    # Act
    summary = spoken_summary(text, channel="whatsapp", budget=35)

    # Assert: toda frase propia de la respuesta está en su lectura completa, sin cambios.
    for sentence in _sentences(summary.text):
        if _OWN_PHRASES.match(sentence):
            continue
        assert sentence in full, sentence


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
