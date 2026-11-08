"""Importa el corpus HTML de un proyecto donante a la capa extendida.

El donante se lee en modo solo lectura. El texto se vuelve a extraer del HTML
Bronze con el extractor propio; si queda vacío, se usa el Silver del donante como
respaldo. Los PDF quedan fuera. El resultado se escribe en
``data/external/<fuente>/gold/`` con índice y manifiesto.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from carvajal.config import (
    EXTERNAL_CATEGORIES,
    EXTERNAL_CORPUS_DIR,
    EXTERNAL_DIR,
    EXTERNAL_MAX_CHARS,
    EXTERNAL_RELEASE,
    EXTERNAL_SOURCE,
    GOLD_DIR,
    LAYER_EXTENDED,
)
from carvajal.extracting import html_to_markdown
from carvajal.gold import domain_of

CATALOG_FILE = Path("data") / "gold" / "modulo_1" / "scraping_todos.jsonl"
SOURCE_TYPE = "colaborador"
MIN_CHARS = 200


@dataclass(frozen=True)
class ImportReport:
    """Métricas de la importación."""

    imported: int
    chars: int
    duplicates: int
    skipped: int


def import_external(
    corpus_root: Path = EXTERNAL_CORPUS_DIR,
    output_dir: Path | None = None,
    source: str = EXTERNAL_SOURCE,
) -> ImportReport:
    """Importa el HTML recuperable del corpus donante.

    Descarta los documentos cuya URL ya está en el núcleo y los duplicados por
    URL o hash. Se detiene al agotar ``EXTERNAL_MAX_CHARS``.

    Args:
        corpus_root: Raíz del repositorio donante (solo lectura).
        output_dir: Destino (por defecto ``data/external/<fuente>``).
        source: Nombre de la fuente para ``origin`` y el manifiesto.

    Returns:
        Reporte con documentos importados, caracteres, duplicados y descartes.
    """

    records = _catalog_records(corpus_root)
    core_urls = _core_urls()
    target = output_dir or EXTERNAL_DIR / source
    documents, report = _collect(records, core_urls, corpus_root)

    staging = target.with_name(f"{target.name}.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    _write(staging, documents, source=source)
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    return report


def _catalog_records(corpus_root: Path) -> list[dict]:
    """Lee el catálogo donante y devuelve solo HTML descargado y utilizable."""

    catalog = corpus_root / CATALOG_FILE
    if not catalog.exists():
        raise FileNotFoundError(f"No se encontró el catálogo del corpus externo: {catalog}")

    records = []
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        path = corpus_root / str(record.get("path") or "")
        if (
            record.get("status") == "fetched"
            and record.get("text_present")
            and record.get("category") in EXTERNAL_CATEGORIES
            and path.suffix.lower() == ".html"
            and path.exists()
        ):
            records.append(record)

    return sorted(records, key=lambda record: (record.get("priority", 99), record.get("depth", 99)))


def _collect(
    records: list[dict], core_urls: set[str], corpus_root: Path
) -> tuple[list[tuple[dict, str]], ImportReport]:
    """Extrae, filtra y deduplica los documentos del catálogo."""

    documents: list[tuple[dict, str]] = []
    seen_hashes: set[str] = set()
    seen_urls = set(core_urls)
    chars = duplicates = skipped = 0

    for record in records:
        if chars >= EXTERNAL_MAX_CHARS:
            break
        body = _body(record, corpus_root)
        if len(body) < MIN_CHARS:
            skipped += 1
            continue
        url = normalize_url(str(record.get("url") or ""))
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if url in seen_urls or digest in seen_hashes:
            duplicates += 1
            continue
        if documents and chars + len(body) > EXTERNAL_MAX_CHARS:
            break
        seen_urls.add(url)
        seen_hashes.add(digest)
        documents.append((record, body))
        chars += len(body)

    report = ImportReport(
        imported=len(documents), chars=chars, duplicates=duplicates, skipped=skipped
    )
    return documents, report


def _body(record: dict, corpus_root: Path) -> str:
    """Extrae el HTML y, si queda vacío, usa el texto Silver del donante."""

    path = corpus_root / str(record.get("path") or "")
    body = html_to_markdown(path.read_text(encoding="utf-8", errors="ignore")).strip()
    if len(body) >= MIN_CHARS:
        return body
    return _silver_text(corpus_root / str(record.get("text_path") or "")) or body


def _silver_text(path: Path) -> str:
    """Concatena el texto ya extraído del donante como respaldo."""

    if not path.is_file():
        return ""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return "\n\n".join(str(row.get("text") or "").strip() for row in rows).strip()


def _core_urls(gold_dir: Path = GOLD_DIR) -> set[str]:
    """URLs del núcleo, para no duplicar en la capa extendida."""

    urls: set[str] = set()
    for path in gold_dir.glob("*.md"):
        for line in path.read_text(encoding="utf-8").splitlines()[1:20]:
            if line.startswith("---"):
                break
            key, separator, value = line.partition(":")
            if separator and key.strip() == "url":
                urls.add(normalize_url(value.strip()))
                break
    return urls


def normalize_url(url: str) -> str:
    """Normaliza una URL para comparar duplicados entre corpus."""

    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


def _write(output_dir: Path, documents: list[tuple[dict, str]], source: str) -> None:
    """Escribe Gold, índice y manifiesto de la importación."""

    gold_dir = output_dir / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    by_category: dict[str, int] = {}

    for record, body in documents:
        slug = f"ext_{record['id']}"
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        metadata = {
            "slug": slug,
            "url": str(record.get("url") or ""),
            "title": _title(record, body),
            "section": str(record.get("category") or ""),
            "domain": domain_of(str(record.get("url") or "")),
            "category": str(record.get("category") or ""),
            "entity": str(record.get("entity") or ""),
            "origin": source,
            "release": EXTERNAL_RELEASE,
            "captured_at": str(record.get("fetched_at") or ""),
            "hash": digest,
            "chars": len(body),
        }
        (gold_dir / f"{slug}.md").write_text(_markdown(metadata, body), encoding="utf-8")
        entries.append({**metadata, "source_type": SOURCE_TYPE, "layer": LAYER_EXTENDED})
        by_category[metadata["category"]] = by_category.get(metadata["category"], 0) + 1

    (gold_dir / "index.jsonl").write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
    )
    manifest = {
        "source": source,
        "release": EXTERNAL_RELEASE,
        "generated_at": datetime.now(UTC).isoformat(),
        "categories": list(EXTERNAL_CATEGORIES),
        "documents": len(documents),
        "chars": sum(len(body) for _, body in documents),
        "by_category": dict(sorted(by_category.items())),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _markdown(metadata: dict[str, str | int], body: str) -> str:
    """Serializa un documento con su *front matter* de procedencia."""

    front_matter = "".join(f"{key}: {value}\n" for key, value in metadata.items())
    return (
        f"---\n{front_matter}source_type: {SOURCE_TYPE}\nlayer: {LAYER_EXTENDED}\n---\n\n{body}\n"
    )


def _title(record: dict, body: str) -> str:
    """Elige un título útil del catálogo y cae al primer bloque del cuerpo."""

    title = " ".join(str(record.get("title") or "").split())[:140]
    if len(title) >= 8:
        return title
    for line in body.splitlines():
        if line.strip():
            return line.strip()[:140]
    return str(record.get("id") or "Documento externo")
