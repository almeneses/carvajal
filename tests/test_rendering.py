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


def test_render_answer_convierte_referencias_en_enlaces_de_fuente() -> None:
    # Arrange
    text = "El dato aparece en la historia [1] y en prensa [2]."

    # Act
    html = str(render_answer(text, 2))

    # Assert
    assert 'class="cita-inline" href="#cita-1"' in html
    assert 'class="cita-inline" href="#cita-2"' in html
    assert 'href="#cita-3"' not in html


def test_render_answer_no_enlaza_referencias_fuera_de_rango() -> None:
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
