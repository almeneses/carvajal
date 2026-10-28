"""Generación del entregable Gold: Markdown con front matter + índice JSONL."""

import json
from pathlib import Path

from carvajal.models import CapturedPage


def to_gold_markdown(page: CapturedPage, title: str) -> str:
    """Serializa la página al formato Gold: front matter + cuerpo.

    Args:
        page: Página capturada con HTML y Markdown.
        title: Título editorial de la página.

    Returns:
        Markdown listo para versionarse en ``data/gold/``.
    """

    front_matter = (
        f"---\nurl: {page.url}\ntitle: {title}\ncaptured_at: {page.captured_at.isoformat()}\n---\n"
    )
    return f"{front_matter}\n{page.markdown.strip()}\n"


def index_entry(page: CapturedPage, title: str) -> dict[str, str]:
    """Construye la entrada del índice JSONL de corpus."""

    return {"url": page.url, "title": title, "slug": page.slug}


def to_jsonl(entries: list[dict[str, str]]) -> str:
    """Serializa las entradas del índice Gold como JSONL."""

    return "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)


def write_gold(page: CapturedPage, title: str, gold_dir: Path) -> Path:
    """Escribe el entregable Gold (Markdown + entrada de índice) en ``gold_dir``."""

    gold_dir.mkdir(parents=True, exist_ok=True)
    markdown = to_gold_markdown(page, title)
    target = gold_dir / f"{page.slug}.md"
    target.write_text(markdown, encoding="utf-8")
    return target
