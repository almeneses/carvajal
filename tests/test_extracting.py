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


def test_html_a_markdown_ignora_navegacion_y_menus() -> None:
    """Descarta la navegación y los contenedores con clases de menú."""

    html = (
        "<html><body>"
        "<nav><a href='/'>Inicio</a></nav>"
        "<header>Enlaces del encabezado</header>"
        "<div class='main-menu'><a href='/empaques/'>Empaques</a></div>"
        "<main><p>Contenido real</p></main>"
        "<footer>Pie institucional</footer>"
        "</body></html>"
    )
    markdown = html_to_markdown(html)

    assert "Contenido real" in markdown
    assert "Inicio" not in markdown
    assert "Empaques" not in markdown
    assert "Pie institucional" not in markdown


def test_html_a_markdown_no_duplica_contenedores() -> None:
    """Emite el bloque una sola vez aunque haya contenedores anidados."""

    html = "<html><body><div><section><p>Párrafo único</p></section></div></body></html>"
    markdown = html_to_markdown(html)

    assert markdown.count("Párrafo único") == 1


def test_html_a_markdown_ignora_regiones_elementor() -> None:
    """Descarta las plantillas de header/footer de Elementor."""

    html = (
        "<html><body>"
        "<div data-elementor-type='header'>Menú superior</div>"
        "<div data-elementor-type='wp-page'><p>Contenido editorial</p></div>"
        "<div data-elementor-type='footer'>Pie con enlaces</div>"
        "</body></html>"
    )
    markdown = html_to_markdown(html)

    assert "Contenido editorial" in markdown
    assert "Menú superior" not in markdown
    assert "Pie con enlaces" not in markdown
