"""Renderizado seguro de Markdown a HTML para las pantallas del shell.

El Markdown del corpus puede contener HTML incrustado; primero se convierte con
Python-Markdown y después se sanea con ``nh3``. El resultado es el único HTML
que las plantillas pueden insertar sin escapar.

``render_answer`` aplica el mismo saneamiento al texto generado por el modelo:
admite énfasis, títulos, listas, citas textuales, enlaces y referencias ``[n]``
convertidas en enlaces a la lista de fuentes.
"""

import re

import markdown
import nh3
from markupsafe import Markup

__all__ = ["render_answer", "render_markdown"]

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

# El texto del asistente no necesita tablas ni bloques de código: se limita a
# lo que puede aparecer en una respuesta conversacional.
_ANSWER_TAGS = {
    "p",
    "br",
    "strong",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "ul",
    "ol",
    "li",
    "a",
    "sup",
    "code",
}

_ANSWER_ATTRIBUTES = {
    "a": {"href", "title", "target", "class"},
    "sup": set(),
}

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_URL_RE = re.compile(r"(?<![\w\"'=(<])(https?://[^\s<>\"']+)")
_TRAILING_PUNCTUATION = ".,;:!?)]}"


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


def _cita(numero: int, source_count: int | None) -> str:
    """Enlace de cita inline si el número corresponde a una fuente conocida."""

    if numero < 1:
        return f"[{numero}]"
    if source_count is not None and numero > source_count:
        return f"[{numero}]"
    return (
        f'<sup><a class="cita-inline" href="#cita-{numero}" '
        f'aria-label="Abrir la fuente {numero}">[{numero}]</a></sup>'
    )


def _enlazar_url(match: re.Match[str]) -> str:
    """Envuelve una URL suelta en un enlace y conserva la puntuación final."""

    url = match.group(1)
    sufijo = ""
    while url and url[-1] in _TRAILING_PUNCTUATION:
        sufijo = url[-1] + sufijo
        url = url[:-1]
    if not url:
        return match.group(0)
    return f'<a href="{url}" target="_blank">{url}</a>{sufijo}'


def _citas_enteras(texto: str) -> str:
    """Convierte líneas enteras entre comillas en citas textuales Markdown."""

    lineas: list[str] = []
    for linea in texto.splitlines():
        limpia = linea.strip()
        if len(limpia) > 2 and limpia[0] in {'"', "“"} and limpia[-1] in {'"', "”"}:
            lineas.append(f"> {limpia}")
        else:
            lineas.append(linea)
    return "\n".join(lineas)


def _escapar_html(text: str) -> str:
    """Escapa HTML del modelo sin romper los marcadores ``>`` de cita."""

    lineas: list[str] = []
    for linea in text.splitlines():
        contenido = linea.lstrip()
        sangria = linea[: len(linea) - len(contenido)]
        if contenido.startswith(">"):
            resto = contenido[1:].replace("<", "&lt;").replace(">", "&gt;")
            lineas.append(f"{sangria}>{resto}")
        else:
            lineas.append(linea.replace("<", "&lt;").replace(">", "&gt;"))
    return "\n".join(lineas)


def render_answer(text: str, source_count: int | None = None) -> Markup:
    """Formatea una respuesta generada y devuelve HTML saneado.

    ``source_count`` limita los enlaces ``[n]`` a las fuentes realmente
    citadas; una referencia fuera de rango se conserva como texto.

    El HTML que venga en el texto generado se escapa antes de Markdown: se
    muestra como texto, nunca se interpreta. Después se insertan los enlaces de
    cita y las URL sueltas, que sí son marcado propio y controlado.
    """

    preparado = _escapar_html(text)
    preparado = _citas_enteras(preparado)
    preparado = _CITATION_RE.sub(
        lambda match: _cita(int(match.group(1)), source_count),
        preparado,
    )
    preparado = _URL_RE.sub(_enlazar_url, preparado)
    html = markdown.markdown(
        preparado,
        extensions=["extra", "sane_lists"],
    )
    limpio = nh3.clean(
        html,
        tags=_ANSWER_TAGS,
        clean_content_tags={"script", "style"},
        attributes=_ANSWER_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )
    return Markup(limpio)
