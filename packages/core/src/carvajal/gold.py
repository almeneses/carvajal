"""Entregables del corpus: capas Bronze, Silver y Gold, más el índice JSONL."""

import fcntl
import json
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from carvajal.models import CapturedPage

OFFICIAL_DOMAIN = "carvajal.com"


def domain_of(url: str) -> str:
    """Devuelve el dominio de la URL sin ``www.``."""

    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def source_type(url: str) -> str:
    """Clasifica la fuente como ``oficial`` (sitio de Carvajal) o ``tercero``."""

    host = domain_of(url)
    return (
        "oficial" if host == OFFICIAL_DOMAIN or host.endswith(f".{OFFICIAL_DOMAIN}") else "tercero"
    )


def to_gold_markdown(page: CapturedPage, title: str) -> str:
    """Serializa la página al formato Gold: front matter + cuerpo.

    El *front matter* registra la procedencia (``domain`` y ``source_type``), la sección
    editorial, la fecha de captura y la huella ``hash`` del contenido.

    Args:
        page: Página capturada con HTML y Markdown.
        title: Título editorial de la página.

    Returns:
        Markdown listo para versionarse en ``data/gold/``.
    """

    front_matter = (
        f"---\n"
        f"url: {page.url}\n"
        f"title: {title}\n"
        f"section: {page.section}\n"
        f"domain: {page.domain}\n"
        f"source_type: {source_type(page.url)}\n"
        f"captured_at: {page.captured_at.isoformat()}\n"
        f"hash: {page.content_hash}\n"
        f"chars: {len(page.markdown)}\n"
        f"---\n"
    )
    body = page.markdown.strip()
    return f"{front_matter}\n{body}\n" if body else front_matter


def index_entry(page: CapturedPage, title: str) -> dict[str, str | int]:
    """Construye la entrada del índice JSONL del corpus."""

    return {
        "url": page.url,
        "title": title,
        "slug": page.slug,
        "section": page.section,
        "domain": page.domain,
        "source_type": source_type(page.url),
        "captured_at": page.captured_at.isoformat(),
        "hash": page.content_hash,
        "chars": len(page.markdown),
        "tokens_estimate": page.tokens_estimate,
    }


def to_jsonl(entries: list[dict[str, str | int]]) -> str:
    """Serializa las entradas del índice Gold como JSONL."""

    return "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)


def write_bronze(page: CapturedPage, raw_dir: Path) -> Path:
    """Guarda el HTML crudo de la página en la capa Bronze."""

    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{page.slug}.html"
    target.write_text(page.html, encoding="utf-8")
    return target


def write_silver(page: CapturedPage, processed_dir: Path) -> Path:
    """Guarda el Markdown limpio de la página en la capa Silver."""

    processed_dir.mkdir(parents=True, exist_ok=True)
    target = processed_dir / f"{page.slug}.md"
    body = page.markdown.strip()
    target.write_text(f"{body}\n" if body else "", encoding="utf-8")
    return target


def write_gold(page: CapturedPage, title: str, gold_dir: Path) -> Path:
    """Escribe el entregable Gold (Markdown + front matter) en ``gold_dir``."""

    gold_dir.mkdir(parents=True, exist_ok=True)
    markdown = to_gold_markdown(page, title)
    target = gold_dir / f"{page.slug}.md"
    target.write_text(markdown, encoding="utf-8")
    return target


def write_index(entries: list[dict[str, str | int]], index_file: Path) -> Path:
    """Escribe el índice JSONL de forma atómica (temporal + reemplazo)."""

    index_file.parent.mkdir(parents=True, exist_ok=True)
    temporal = index_file.with_name(f".{index_file.name}.tmp")
    temporal.write_text(to_jsonl(entries), encoding="utf-8")
    temporal.replace(index_file)
    return index_file


@contextmanager
def _index_lock(index_file: Path) -> Generator[None]:
    """Exclusión mutua entre procesos sobre el índice del corpus."""

    lock = index_file.with_name(f".{index_file.name}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def merge_index(
    index_file: Path,
    entries: Sequence[Mapping[str, str | int]],
    *,
    gold_dir: Path | None = None,
) -> Path:
    """Fusiona entradas en el índice bajo bloqueo y lo reemplaza de forma atómica.

    Con ``gold_dir`` descarta las entradas cuyo documento ya no existe en Gold:
    una captura parcial no debe truncar el índice, pero sí limpiar lo obsoleto.
    """

    with _index_lock(index_file):
        merged: dict[str, Mapping[str, str | int]] = _read_index(index_file)
        for entry in entries:
            merged[str(entry["slug"])] = entry
        if gold_dir is not None:
            merged = {
                slug: entry for slug, entry in merged.items() if (gold_dir / f"{slug}.md").is_file()
            }
        return write_index([dict(merged[slug]) for slug in sorted(merged)], index_file)


def _read_index(index_file: Path) -> dict[str, Mapping[str, str | int]]:
    """Lee el índice existente; las líneas corruptas se descartan."""

    merged: dict[str, Mapping[str, str | int]] = {}
    if not index_file.is_file():
        return merged
    for line in index_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            previous = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(previous, dict) and previous.get("slug"):
            merged[str(previous["slug"])] = previous
    return merged
