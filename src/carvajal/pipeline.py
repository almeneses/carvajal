"""Orquestación del pipeline: captura sobre las semillas del corpus."""

from pathlib import Path

from carvajal.config import GOLD_DIR
from carvajal.extracting import html_to_markdown
from carvajal.fetching import WebClient
from carvajal.gold import write_gold
from carvajal.models import CapturedPage


def capture(
    seeds: tuple[str, ...],
    client: WebClient | None = None,
    gold_dir: Path | None = None,
) -> list[CapturedPage]:
    """Ejecuta el ciclo captura → extracción → Gold sobre las semillas dadas.

    Args:
        seeds: Semillas de URLs a capturar.
        client: Cliente HTTP (se reusa una sola sesión).
        gold_dir: Directorio de entregables Gold (por defecto ``data/gold``).

    Returns:
        Páginas capturadas en orden.
    """

    web_client = client or WebClient()
    target_dir = gold_dir or GOLD_DIR
    pages: list[CapturedPage] = []

    for url in seeds:
        html = web_client.fetch(url)
        markdown = html_to_markdown(html)
        pages.append(CapturedPage(url=url, html=html, markdown=markdown))
        title = _title_from_markdown(markdown) or _title_from_url(url)
        write_gold(pages[-1], title=title, gold_dir=target_dir)

    return pages


def _title_from_markdown(markdown: str) -> str:
    """Toma el primer encabezado del Markdown como título editorial."""

    for level in ("# ", "## "):
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith(level):
                return stripped[len(level) :].strip()
    return ""


def _title_from_url(url: str) -> str:
    """Deriva un título editorial simple a partir de la URL."""

    path = url.rstrip("/").rsplit("/", 1)[-1]
    return path.replace("-", " ").replace("_", " ").title() or "Home"
