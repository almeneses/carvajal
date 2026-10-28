"""Extracción de Markdown desde HTML crudo."""

from bs4 import BeautifulSoup

SKIPPED_TAGS = {"script", "style", "nav", "footer", "header"}


def html_to_markdown(html: str) -> str:
    """Convierte HTML crudo a Markdown conservando el contenido editorial.

    Args:
        html: HTML crudo ya descargado (sin depender de la red).

    Returns:
        Representación Markdown legible.
    """

    soup = BeautifulSoup(html, "lxml")
    markdown: list[str] = []

    container = soup.body if soup.body is not None else soup
    for element in container.find_all(True):
        if element.name in SKIPPED_TAGS:
            continue
        part = _element_to_markdown(element)
        if part:
            markdown.append(part)

    return "\n\n".join(markdown)


def _element_to_markdown(element) -> str:
    """Convierte un elemento HTML a fragmento Markdown."""

    tag = element.name
    text = element.get_text(separator=" ", strip=True).strip()
    if not text:
        return ""

    if tag == "h1":
        return f"# {text}"
    if tag == "h2":
        return f"## {text}"
    if tag == "h3":
        return f"### {text}"
    if tag in {"ul", "ol"}:
        items: list[str] = []
        for child in element.find_all(recursive=False):
            child_text = child.get_text(separator=" ", strip=True).strip()
            if child_text:
                items.append(f"- {child_text}")
        return "\n".join(items)

    return text
