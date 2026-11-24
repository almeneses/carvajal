"""Pruebas del texto apto para voz (``carvajal.speech``).

Funciones puras, sin red ni LLM. Cubren las reglas de la respuesta hablada por WhatsApp:
citas, Markdown y formato de WhatsApp, URL separadas como enlaces, unidades, importes,
fechas, abreviaturas, folios, listas, fuentes en una frase y el contrato de
``SpokenReply.render`` / ``split_voice_reply``.
"""

import pytest

from carvajal.speech import (
    LINKS_HEADER,
    SpeechLink,
    SpokenReply,
    combine,
    speak_folio,
    speak_folio_tail,
    speak_money,
    split_voice_reply,
    spoken_sources,
    to_speech,
)

# --- citas y formato -----------------------------------------------------------------------


def test_quita_las_citas_numeradas() -> None:
    # Arrange
    text = "Carvajal nació en 1904 [1][2] y tiene sede en Cali [3, 4]."

    # Act
    spoken = to_speech(text)

    # Assert
    assert spoken.text == "Carvajal nació en 1904 y tiene sede en Cali."


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("## Historia\n**Carvajal** nació en 1904.", "Historia. Carvajal nació en 1904."),
        ("*Total:* $370.000", "Total: 370.000 pesos."),
        ("_válida hasta el 1 dic 2026_", "Válida hasta el 1 de diciembre de 2026."),
        ("Usa el código `VP12` ~ya no~ hoy", "Usa el código VP12 ya no hoy."),
        ("> Una cita textual", "Una cita textual."),
        ("a\n---\nb", "A.\n\nB."),
    ],
)
def test_quita_markdown_y_formato_de_whatsapp(markdown: str, expected: str) -> None:
    # Act
    spoken = to_speech(markdown)

    # Assert
    assert spoken.text == expected


def test_quita_los_emojis() -> None:
    # Act
    spoken = to_speech("🧾 *Cotización lista* 💰")

    # Assert
    assert spoken.text == "Cotización lista."


# --- enlaces -------------------------------------------------------------------------------


def test_url_en_una_frase_se_nombra_y_se_separa() -> None:
    # Act
    spoken = to_speech("Más detalles en https://www.carvajal.com/historia.")

    # Assert
    assert spoken.text == "Más detalles en el enlace."
    assert spoken.links == (SpeechLink("Enlace", "https://www.carvajal.com/historia"),)
    assert "http" not in spoken.text


def test_linea_que_rotula_un_enlace_no_se_lee() -> None:
    # Act
    spoken = to_speech("Listo.\n📷 Foto: https://x.example/catalogo/imagenes/VP12.jpg")

    # Assert
    assert spoken.text == "Listo."
    assert spoken.links == (SpeechLink("Foto", "https://x.example/catalogo/imagenes/VP12.jpg"),)


def test_enlace_markdown_conserva_su_texto() -> None:
    # Act
    spoken = to_speech("Revisa [el catálogo de vasos](https://x.example/vasos) cuando puedas.")

    # Assert
    assert spoken.text == "Revisa el catálogo de vasos cuando puedas."
    assert spoken.links == (SpeechLink("el catálogo de vasos", "https://x.example/vasos"),)


def test_enlace_entre_parentesis_de_whatsapp_se_separa() -> None:
    # Act
    spoken = to_speech("Mira el catálogo completo de vasos (https://x.example/vasos) y elige.")

    # Assert
    assert spoken.text == "Mira el catálogo completo de vasos y elige."
    assert [link.url for link in spoken.links] == ["https://x.example/vasos"]


def test_url_sola_toma_el_rotulo_de_la_linea_anterior() -> None:
    # Arrange
    text = (
        "📄 Aquí está tu cotización #a3f09c12b7e4 en PDF:\n"
        "https://x.example/cotizaciones/a3f09c12b7e4.pdf?e=1&t=2\n"
        "_El enlace funciona hasta el 1 dic 2026._"
    )

    # Act
    spoken = to_speech(text)

    # Assert
    assert spoken.text == (
        "Aquí está tu cotización terminada en B 7 E 4 en PDF. "
        "El enlace funciona hasta el 1 de diciembre de 2026."
    )
    assert spoken.links == (
        SpeechLink(
            "Aquí está tu cotización #a3f09c12b7e4 en PDF",
            "https://x.example/cotizaciones/a3f09c12b7e4.pdf?e=1&t=2",
        ),
    )


def test_enlaces_repetidos_se_envian_una_vez() -> None:
    # Act
    spoken = to_speech("Ver https://x.example/a y otra vez https://x.example/a.")

    # Assert
    assert [link.url for link in spoken.links] == ["https://x.example/a"]


# --- unidades, importes, fechas y abreviaturas ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vaso de 12 oz", "Vaso de 12 onzas."),
        ("Vaso de 1 oz", "Vaso de 1 onza."),
        ("Tapa x 200 und", "Tapa de 200 unidades."),
        ("Paquete x200", "Paquete de 200 unidades."),
        ("Caja x 1.000 und. a buen precio", "Caja de 1.000 unidades a buen precio."),
        ("Compras 2.000 und.", "Compras 2.000 unidades."),
        ("Bandeja de 10x20 cm", "Bandeja de 10 por 20 centímetros."),
        ("Envase de 250 ml y 1,5 L", "Envase de 250 mililitros y 1,5 litros."),
        ("IVA incluido (19 %)", "IVA incluido (19 por ciento)."),
        ("Cuesta aprox. 20 pesos", "Cuesta aproximadamente 20 pesos."),
        ("Son 300 aprox. Te sirve?", "Son 300 aproximadamente. Te sirve?"),
        ("Vasos, platos, etc.", "Vasos, platos, etcétera."),
        ("Llama al Tel. 601 555", "Llama al teléfono 601 555."),
        ("Válida hasta 18 nov 2026", "Válida hasta 18 de noviembre de 2026."),
    ],
)
def test_unidades_y_abreviaturas_en_palabras(text: str, expected: str) -> None:
    # Act
    spoken = to_speech(text)

    # Assert
    assert spoken.text == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Total: $1.234.500", "Total: 1.234.500 pesos."),
        ("Total: $370.000 COP", "Total: 370.000 pesos."),
        ("Son 370.000 COP", "Son 370.000 pesos."),
        ("Precio: $12,50 USD", "Precio: 12 dólares con 50 centavos."),
        ("Precio: $185.000,00", "Precio: 185.000 pesos."),
    ],
)
def test_importes_conservan_los_digitos(text: str, expected: str) -> None:
    # Act
    spoken = to_speech(text)

    # Assert
    assert spoken.text == expected


def test_speak_money() -> None:
    # Act / Assert
    assert speak_money("$1.234.500") == "1.234.500 pesos"
    assert speak_money("$99", "MXN") == "99 pesos mexicanos"


def test_codigos_tecnicos_y_sku_no_se_leen() -> None:
    # Act
    spoken = to_speech("El vaso SKU: VP-12OZ es igual al CJ-1200.")

    # Assert
    assert "VP" not in spoken.text and "CJ" not in spoken.text and "SKU" not in spoken.text


# --- folio ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("folio", "expected"),
    [
        ("C-2026-0042", "C, 2026, 0 0 4 2"),
        ("a3f09c12b7e4", "A 3 F 0, 9 C 1 2, B 7 E 4"),
        ("#Q-7F3A2C", "Q, 7 F 3 A 2 C"),
    ],
)
def test_speak_folio_deletrea_por_grupos(folio: str, expected: str) -> None:
    # Act / Assert
    assert speak_folio(folio) == expected


@pytest.mark.parametrize(
    ("folio", "expected"),
    [
        ("a3f09c12b7e4", "B 7 E 4"),
        ("C-2026-0042", "0 0 4 2"),
        ("#Q-7F3A2C", "3 A 2 C"),
        ("X12", "X 1 2"),
    ],
)
def test_speak_folio_tail_deletrea_solo_los_ultimos_cuatro(folio: str, expected: str) -> None:
    # Act / Assert
    assert speak_folio_tail(folio) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tu folio: C-2026-0042.", "Tu folio terminado en 0 0 4 2."),
        ("Folio #a3f09c12b7e4 emitido", "Folio terminado en B 7 E 4 emitido."),
        ("Cotización #a3f09c12b7e4", "Cotización terminada en B 7 E 4."),
        ("Guardé a3f09c12b7e4", "Guardé el folio terminado en B 7 E 4."),
        ("La opción #2", "La opción número 2."),
    ],
)
def test_folio_en_el_texto_se_lee_solo_por_su_final(text: str, expected: str) -> None:
    # Act
    spoken = to_speech(text)

    # Assert
    assert spoken.text == expected


# --- listas --------------------------------------------------------------------------------


def test_vinetas_cortas_pasan_a_una_frase() -> None:
    # Act
    spoken = to_speech("Tenemos sedes en:\n- Bogotá\n- Cali\n- Medellín")

    # Assert
    assert spoken.text == "Tenemos sedes en: Bogotá, Cali y Medellín."


def test_vinetas_largas_pasan_a_frases_sueltas() -> None:
    # Arrange
    text = "* Presentación: caja de 1.000 unidades\n* Subtotal: $370.000"

    # Act
    spoken = to_speech(text)

    # Assert
    assert spoken.text == "Presentación: caja de 1.000 unidades. Subtotal: 370.000 pesos."


def test_lista_numerada_pasa_a_primero_luego() -> None:
    # Act
    spoken = to_speech("Pasos:\n1. Elige el producto\n2. Indica la ciudad\n3. Confirma")

    # Assert
    assert spoken.text == (
        "Pasos: Primero, Elige el producto. Luego, Indica la ciudad. Por último, Confirma."
    )


def test_conjuncion_e_antes_de_i() -> None:
    # Act
    spoken = to_speech("- Vasos\n- Platos\n- Individuales")

    # Assert
    assert spoken.text == "Vasos, Platos e Individuales."


# --- fuentes -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        ([], ""),
        (
            [("Vasos", "https://colombia.carvajalempaques.com/vasos")],
            "Según el sitio de Carvajal Empaques.",
        ),
        (
            [("Historia", "https://www.carvajal.com/h"), ("Hitos", "https://www.carvajal.com/i")],
            "Lo saqué de dos páginas del sitio de Carvajal.",
        ),
        ([("Informe 2024", "https://valora.example/x")], "Lo saqué del documento «Informe 2024»."),
        (
            [("A", "https://a.example"), ("B", ""), ("C", "https://www.carvajal.com/c")],
            "Lo saqué de tres documentos.",
        ),
    ],
)
def test_fuentes_en_una_sola_frase(sources: list[tuple[str, str]], expected: str) -> None:
    # Act / Assert
    assert spoken_sources(sources) == expected


def test_bloque_de_fuentes_de_whatsapp_pasa_a_una_frase_y_enlaces() -> None:
    # Arrange
    text = (
        "Carvajal nació en 1904 [1].\n\n*Fuentes:*\n"
        "[1] Historia de Carvajal — https://www.carvajal.com/historia\n"
        "[2] Hitos — https://www.carvajal.com/hitos"
    )

    # Act
    spoken = to_speech(text)

    # Assert
    assert spoken.text == (
        "Carvajal nació en 1904.\n\nLo saqué de dos páginas del sitio de Carvajal."
    )
    assert [link.label for link in spoken.links] == ["Historia de Carvajal", "Hitos"]


# --- contrato de la respuesta --------------------------------------------------------------


def test_render_sin_enlaces_es_solo_el_texto() -> None:
    # Act / Assert
    assert SpokenReply("Hola.").render() == "Hola."


def test_render_y_split_son_inversos() -> None:
    # Arrange
    reply = SpokenReply(
        "Te envío el PDF.", (SpeechLink("Cotización C-1 en PDF", "https://x.example/c.pdf"),)
    )

    # Act
    rendered = reply.render()
    spoken, links = split_voice_reply(rendered)

    # Assert
    assert rendered == (
        f"Te envío el PDF.\n\n{LINKS_HEADER}\nCotización C-1 en PDF: https://x.example/c.pdf"
    )
    assert spoken == "Te envío el PDF."
    assert links == ["Cotización C-1 en PDF: https://x.example/c.pdf"]


def test_split_sin_seccion_de_enlaces() -> None:
    # Act / Assert
    assert split_voice_reply("Hola.") == ("Hola.", [])
    assert split_voice_reply(f"{LINKS_HEADER}\nhttps://x.example") == ("", ["https://x.example"])


def test_combine_une_parrafos_y_no_repite_enlaces() -> None:
    # Arrange
    link = SpeechLink("Foto", "https://x.example/f.jpg")

    # Act
    joined = combine([SpokenReply("Uno.", (link,)), SpokenReply(""), SpokenReply("Dos.", (link,))])

    # Assert
    assert joined == SpokenReply("Uno.\n\nDos.", (link,))


def test_el_texto_hablado_no_trae_url_ni_marcas() -> None:
    # Arrange
    text = (
        "**Hola** [1]. Visita https://x.example/a\n- *uno*\n- __dos__\n\n"
        "*Fuentes:*\n[1] Doc — https://x.example/doc"
    )

    # Act
    spoken = to_speech(text).text

    # Assert
    for mark in ("http", "*", "_", "[", "]", "#", "`"):
        assert mark not in spoken
