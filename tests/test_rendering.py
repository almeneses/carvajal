"""Pruebas del renderizado seguro de Markdown: sin red ni base de datos."""

import pytest

from apps.api.rendering import render_markdown


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
