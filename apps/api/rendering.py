"""Renderizado seguro de Markdown a HTML para las pantallas del shell.

El Markdown del corpus puede contener HTML incrustado; primero se convierte con
Python-Markdown y después se sanea con ``nh3``. El resultado es el único HTML
que las plantillas pueden insertar sin escapar.
"""

import markdown
import nh3

__all__ = ["render_markdown"]

# Lista blanca mínima para el contenido editorial del corpus: estructura,
# énfasis, código, tablas y enlaces. Nada de formularios, medios ni scripts.
_ALLOWED_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "strong",
    "em",
    "code",
    "pre",
    "blockquote",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "a",
    "br",
    "hr",
}

# ``nh3`` prohíbe declarar ``rel`` para ``a`` cuando ``link_rel`` está activo:
# el valor de ``link_rel`` es el que se impone y el Markdown no puede
# sobrescribirlo. Por eso ``rel`` no aparece en la lista de atributos.
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "code": {"class"},
}

_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def render_markdown(text: str) -> str:
    """Convierte Markdown a HTML saneado y listo para insertar en una plantilla.

    Los enlaces conservan solo ``http``, ``https`` y ``mailto``; cualquier
    etiqueta o atributo fuera de la lista blanca se elimina.
    """

    html = markdown.markdown(text, extensions=["tables", "fenced_code"])
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        clean_content_tags={"script", "style"},
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )
