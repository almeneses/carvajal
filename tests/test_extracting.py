"""Pruebas de extracción de Markdown desde HTML."""

from carvajal.extracting import html_to_markdown


def test_html_a_markdown_devuelve_encabezado_y_parrafo() -> None:
    """Convierte encabezado y párrafo a Markdown."""

    markdown = html_to_markdown("<h1>Título</h1><p>Cuerpo editorial</p>")

    assert markdown.startswith("# Título")
    assert "Cuerpo editorial" in markdown


def test_html_a_markdown_ignora_script_y_estilos() -> None:
    """Descarta tags no editoriales antes de convertir."""

    html = "<html><body><script>alert(1)</script><p>Contenido</p></body></html>"
    markdown = html_to_markdown(html)

    assert "alert(1)" not in markdown
    assert "Contenido" in markdown
