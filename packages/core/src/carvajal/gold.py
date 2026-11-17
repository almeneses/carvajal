"""Entregables del corpus: capas Bronze, Silver y Gold, más el índice JSONL."""

import fcntl
import json
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from carvajal.models import CapturedPage

OFFICIAL_DOMAIN = "carvajal.com"

IndexEntry = dict[str, str | int]
"""Entrada del índice: los campos publicados de un documento del corpus."""

PRESERVED_FIELDS = ("source_key", "raw_hash", "layer", "updated_at")
"""Campos que sobreviven a una fusión aunque la entrada nueva no los declare.

La identidad, la huella del original, la capa y la fecha de actualización las
escribe el registro del corpus (spec 037); un camino de entrada que todavía no
las conoce no debe borrarlas al fusionar su entrada.
"""


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


def index_entry(
    page: CapturedPage,
    title: str,
    *,
    slug: str | None = None,
    source_key: str = "",
    raw_hash: str = "",
    layer: str = "core",
    updated_at: str = "",
) -> dict[str, str | int]:
    """Construye la entrada del índice JSONL del corpus."""

    entry: dict[str, str | int] = {
        "url": page.url,
        "title": title,
        "slug": slug or page.slug,
        "section": page.section,
        "domain": page.domain,
        "source_type": source_type(page.url),
        "captured_at": page.captured_at.isoformat(),
        "hash": page.content_hash,
        "chars": len(page.markdown),
        "tokens_estimate": page.tokens_estimate,
    }
    if source_key:
        entry["source_key"] = source_key
    if raw_hash:
        entry["raw_hash"] = raw_hash
    if layer:
        entry["layer"] = layer
    if updated_at:
        entry["updated_at"] = updated_at
    return entry


def to_jsonl(entries: list[dict[str, str | int]]) -> str:
    """Serializa las entradas del índice Gold como JSONL."""

    return "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)


def write_bronze(page: CapturedPage, raw_dir: Path, *, slug: str | None = None) -> Path:
    """Guarda el HTML crudo de la página en la capa Bronze."""

    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{slug or page.slug}.html"
    target.write_text(page.html, encoding="utf-8")
    return target


def write_silver(page: CapturedPage, processed_dir: Path, *, slug: str | None = None) -> Path:
    """Guarda el Markdown limpio de la página en la capa Silver."""

    processed_dir.mkdir(parents=True, exist_ok=True)
    target = processed_dir / f"{slug or page.slug}.md"
    body = page.markdown.strip()
    target.write_text(f"{body}\n" if body else "", encoding="utf-8")
    return target


def write_gold(page: CapturedPage, title: str, gold_dir: Path, *, slug: str | None = None) -> Path:
    """Escribe el entregable Gold (Markdown + front matter) en ``gold_dir``."""

    gold_dir.mkdir(parents=True, exist_ok=True)
    markdown = to_gold_markdown(page, title)
    target = gold_dir / f"{slug or page.slug}.md"
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
def _lock_file(lock: Path) -> Generator[None]:
    """Toma un bloqueo exclusivo entre procesos sobre ``lock`` y lo libera al salir."""

    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _index_lock(index_file: Path) -> Generator[None]:
    """Exclusión mutua entre procesos sobre el índice del corpus."""

    with _lock_file(index_file.with_name(f".{index_file.name}.lock")):
        yield


@contextmanager
def corpus_lock(root: Path) -> Generator[None]:
    """Exclusión mutua del corpus completo sobre ``<root>/.corpus.lock``.

    Serializa la escritura de capas y la sincronización entre procesos: dos
    sincronizaciones simultáneas se turnan en vez de competir. Cuando se toma
    junto a ``_index_lock``, este va siempre después (primero ``corpus_lock``).
    """

    with _lock_file(Path(root) / ".corpus.lock"):
        yield


def merge_index(
    index_file: Path,
    entries: Sequence[Mapping[str, str | int]],
    *,
    gold_dir: Path | None = None,
) -> Path:
    """Fusiona entradas en el índice bajo bloqueo y lo reemplaza de forma atómica.

    Con ``gold_dir`` descarta las entradas cuyo documento ya no existe en Gold:
    una captura parcial no debe truncar el índice, pero sí limpiar lo obsoleto.

    La identidad y las huellas de una entrada existente se conservan cuando la
    entrada nueva no las declara (ver ``PRESERVED_FIELDS``); si las declara,
    mandan las nuevas.
    """

    with _index_lock(index_file):
        merged: dict[str, IndexEntry] = read_index(index_file)
        for entry in entries:
            slug = str(entry["slug"])
            previous = merged.get(slug, {})
            merged[slug] = {
                **{field: previous[field] for field in PRESERVED_FIELDS if field in previous},
                **entry,
            }
        if gold_dir is not None:
            merged = {
                slug: entry for slug, entry in merged.items() if (gold_dir / f"{slug}.md").is_file()
            }
        return write_index([merged[slug] for slug in sorted(merged)], index_file)


def read_index(index_file: Path) -> dict[str, IndexEntry]:
    """Lee el índice existente; las líneas corruptas se descartan."""

    merged: dict[str, IndexEntry] = {}
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
