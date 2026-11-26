"""Pruebas del renderizado seguro de Markdown: sin red ni base de datos."""

import pytest

from apps.api.rendering import render_answer, render_markdown


def test_renderiza_tablas_de_markdown() -> None:
    # Arrange
    text = "| Sección | Año |\n| --- | --- |\n| Historia | 1904 |"

    # Act
    html = render_markdown(text)

    # Assert
    assert "<table>" in html
    assert "<th>Sección</th>" in html
    assert "<td>Historia</td>" in html


def test_renderiza_listas_de_markdown() -> None:
    # Arrange
    text = "- Empaques\n- Educación"

    # Act
    html = render_markdown(text)

    # Assert
    assert "<ul>" in html
    assert "<li>Empaques</li>" in html
    assert "<li>Educación</li>" in html


def test_renderiza_listas_ordenadas() -> None:
    # Arrange
    text = "1. Primero\n2. Segundo"

    # Act
    html = render_markdown(text)

    # Assert
    assert "<ol>" in html
    assert "<li>Primero</li>" in html
    assert "<li>Segundo</li>" in html


def test_renderiza_bloques_de_codigo_cercados() -> None:
    # Arrange
    text = "```python\nprint('hola')\n```"

    # Act
    html = render_markdown(text)

    # Assert
    assert "<pre>" in html
    assert '<code class="language-python">' in html
    assert "print('hola')" in html


def test_elimina_etiquetas_script_y_su_contenido() -> None:
    # Arrange
    text = '<script>alert("xss")</script>\n\nTexto legítimo.'

    # Act
    html = render_markdown(text)

    # Assert
    assert "<script" not in html
    assert 'alert("xss")' not in html
    assert "Texto legítimo." in html


def test_elimina_manejadores_de_eventos() -> None:
    # Arrange
    text = '<p onerror="alert(1)">Texto con atributo peligroso.</p>'

    # Act
    html = render_markdown(text)

    # Assert
    assert "onerror" not in html
    assert "<p>Texto con atributo peligroso.</p>" in html


def test_elimina_enlaces_con_esquema_javascript() -> None:
    # Arrange
    text = "[Haz clic](javascript:alert(1))"

    # Act
    html = render_markdown(text)

    # Assert
    assert "javascript:" not in html
    assert "href=" not in html
    assert "Haz clic" in html


def test_elimina_iframes() -> None:
    # Arrange
    text = '<iframe src="https://example.com/incrustado"></iframe>'

    # Act
    html = render_markdown(text)

    # Assert
    assert "<iframe" not in html
    assert "example.com/incrustado" not in html


@pytest.mark.parametrize(
    "href",
    [
        "http://example.com/ruta",
        "https://example.com/ruta",
        "mailto:alguien@example.com",
    ],
)
def test_conserva_enlaces_seguros_con_rel(href: str) -> None:
    # Arrange
    text = f"[Enlace seguro]({href})"

    # Act
    html = render_markdown(text)

    # Assert
    assert f'href="{href}"' in html
    assert 'rel="noopener noreferrer"' in html


def test_render_answer_formatea_titulos_negritas_y_cursivas() -> None:
    # Arrange
    text = "# Carvajal\n\n**Importante** y *cursiva*."

    # Act
    html = str(render_answer(text, 0))

    # Assert
    assert "<h1>Carvajal</h1>" in html
    assert "<strong>Importante</strong>" in html
    assert "<em>cursiva</em>" in html


def test_render_answer_oculta_referencias_validas_sin_estropear_la_puntuacion() -> None:
    # Arrange
    text = "El dato aparece en **la historia [1]** y en prensa [2]."

    # Act
    html = str(render_answer(text, 2))

    # Assert
    assert html == "<p>El dato aparece en <strong>la historia</strong> y en prensa.</p>"


def test_render_answer_conserva_numeros_entre_corchetes_ajenos_a_las_fuentes() -> None:
    # Arrange
    text = "Una referencia inventada [5]."

    # Act
    html = str(render_answer(text, 2))

    # Assert
    assert 'href="#cita-5"' not in html
    assert "[5]" in html


def test_render_answer_autoenlaza_urls_sueltas() -> None:
    # Arrange
    text = "Consulta https://www.carvajal.com/historia/ para más detalle."

    # Act
    html = str(render_answer(text, 0))

    # Assert
    assert 'href="https://www.carvajal.com/historia/"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


def test_render_answer_convierte_citas_textuales_en_blockquote() -> None:
    # Arrange
    text = '"El proyecto familiar comenzó en Cali."'

    # Act
    html = str(render_answer(text, 0))

    # Assert
    assert "<blockquote>" in html
    assert "El proyecto familiar comenzó en Cali." in html


def test_render_answer_escapa_html_del_modelo() -> None:
    # Arrange
    text = '<script>alert("xss")</script> y <b>texto</b>.'

    # Act
    html = str(render_answer(text, 0))

    # Assert
    assert "<script" not in html
    assert "<b>" not in html
    assert "&lt;script&gt;alert" in html
    assert "&lt;b&gt;texto&lt;/b&gt;" in html


def test_render_answer_elimina_enlaces_javascript() -> None:
    # Arrange
    text = "[Clic](javascript:alert(1))"

    # Act
    html = str(render_answer(text, 0))

    # Assert
    assert "javascript:" not in html
    assert "href=" not in html
    assert "Clic" in html


def test_render_answer_preserva_literales_dentro_de_codigo_y_enlaces() -> None:
    # Arrange
    text = "El código `[1]` y el [1](https://example.com) no son citas [1]."

    # Act
    html = str(render_answer(text, 1))

    # Assert
    assert "<code>[1]</code>" in html
    assert 'href="https://example.com" rel="noopener noreferrer">1</a>' in html
    assert html.endswith(" no son citas.</p>")


def test_render_answer_no_quita_corchetes_de_una_url_suelta() -> None:
    # Arrange
    text = "Consulta https://example.com/?ref=[1]&lang=es [1]."

    # Act
    html = render_answer(text, source_count=1)

    # Assert
    assert 'href="https://example.com/?ref=[1]&amp;lang=es"' in html
    assert html.endswith("</a>.</p>")


def test_render_answer_no_anida_enlaces_markdown_y_urls() -> None:
    # Arrange
    text = "[Documento](https://example.com/a) y https://example.com/b."

    # Act
    html = str(render_answer(text, 0))

    # Assert
    assert html.count("<a ") == 2
    assert '<a href="https://example.com/a" rel="noopener noreferrer">Documento</a>' in html
    assert 'href="https://example.com/b"' in html


def test_render_answer_escapa_entidades_escritas_por_el_modelo() -> None:
    # Arrange
    text = "Texto con &lt;script&gt; y un ampersand & suelto."

    # Act
    html = str(render_answer(text, 0))

    # Assert
    assert "&amp;lt;script&amp;gt;" in html
    assert "&amp; suelto" in html
    assert "<script" not in html


def test_render_answer_escapa_la_url_del_enlace_automatico() -> None:
    # Arrange
    text = "Consulta https://example.com/buscar?q=a&b=c."

    # Act
    html = str(render_answer(text, 0))

    # Assert
    assert 'href="https://example.com/buscar?q=a&amp;b=c"' in html
    assert 'rel="noopener noreferrer"' in html


def test_la_lista_pegada_al_titulo_y_los_saltos_se_conservan_al_recargar() -> None:
    # Arrange: el texto del flujo de cotización tal como se guarda (spec 054).
    text = (
        "**Alternativas**\n"
        "1. Vaso traslúcido 3,5 oz · $55.880\n"
        "2. Vaso blanco 3,5 oz · $56.160\n\n"
        "• **500** → 3 cajas x 200 · $166.644\n"
        "• **1.000** → 5 cajas x 200 · $277.740"
    )

    # Act
    html = render_answer(text)

    # Assert: la lista es lista y las viñetas «•» van en líneas separadas, como en el stream.
    assert "<p><strong>Alternativas</strong></p>\n<ol>" in html
    assert "<li>Vaso traslúcido 3,5 oz · $55.880</li>" in html
    assert "$166.644<br" in html


def test_una_linea_sangrada_no_parte_la_lista() -> None:
    # Arrange: continuación sangrada de un ítem.
    text = "**Opciones**\n1. Vaso 7 oz\n   PP, 7 oz\n2. Vaso 9 oz"

    # Act
    html = render_answer(text)

    # Assert
    assert html.count("<ol>") == 1
    assert html.count("<li>") == 2
