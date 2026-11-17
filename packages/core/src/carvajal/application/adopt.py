"""Adopción de identidad en un registro anterior a la spec 037 (US1).

Los corpus creados antes de la identidad estable no tienen ``source_key`` ni
``raw_hash`` en sus ``index.jsonl``. La adopción los completa **sin reindexar**:
los slugs no se renombran y el contenido no se toca. Cuando varias entradas
legadas resuelven la misma fuente (por ejemplo, dos versiones de una subida
homónima), la más reciente conserva la fuente y las demás pasan a
``legacy:<slug>`` como versiones archivadas.

La escritura es la del registro: :func:`carvajal.gold.merge_index`, atómica y
bajo el bloqueo del índice.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from carvajal.gold import merge_index
from carvajal.identity import source_key_for_upload, source_key_for_url
from carvajal.registry import INDEX_NAME, IndexedDocument, load_registry

__all__ = ["AdoptedEntry", "AdoptionReport", "adopt_registry"]

LEGACY_PREFIX = "legacy:"
_HASH_SUFFIX = re.compile(r"-[0-9a-f]{6,}$")
_ONLY_HASH = re.compile(r"[0-9a-f]{6,}")


@dataclass(frozen=True)
class AdoptedEntry:
    """Entrada del registro que la adopción tocó."""

    slug: str
    area: Path
    source_key: str
    raw_hash: str
    conflicts_with: str = ""

    @property
    def conflicted(self) -> bool:
        """La entrada perdió la fuente frente a otra versión más reciente."""

        return bool(self.conflicts_with)


@dataclass(frozen=True)
class AdoptionReport:
    """Resultado de una adopción, con lo adoptado y lo archivado."""

    adopted: tuple[AdoptedEntry, ...] = ()
    conflicts: tuple[AdoptedEntry, ...] = ()
    unchanged: int = 0

    @property
    def changed(self) -> int:
        """Entradas cuyo índice cambió en esta corrida."""

        return len(self.adopted) + len(self.conflicts)


def adopt_registry(
    areas: Sequence[str | Path],
    *,
    raw_dir: Path | None = None,
) -> AdoptionReport:
    """Completa ``source_key`` y ``raw_hash`` en las áreas del registro.

    Args:
        areas: Directorios con ``index.jsonl``, en orden de prioridad.
        raw_dir: Raíz de Bronze; si se indica, ``raw_hash`` se calcula desde el
            archivo cuyo nombre coincide con el slug.

    Returns:
        Reporte con las entradas adoptadas, las archivadas como ``legacy:`` y
        cuántas no necesitaban cambios.
    """

    registry = load_registry(areas)
    if not registry.documents:
        return AdoptionReport()

    updates: dict[Path, list[dict[str, str | int]]] = defaultdict(list)
    adopted: list[AdoptedEntry] = []
    conflicts: list[AdoptedEntry] = []
    unchanged = 0

    proposals: list[tuple[IndexedDocument, str]] = []
    seen_hashes: dict[str, str] = {}
    for document in registry.documents:
        if document.source_key:
            seen_hashes[document.source_key] = document.slug
            continue
        proposals.append((document, _proposed_key(document)))

    # La fuente canónica solo puede tener un dueño: si otra entrada ya la
    # declara, la legada se archiva en lugar de robarle la identidad.
    taken: dict[str, str] = dict(seen_hashes)
    grouped: dict[str, list[IndexedDocument]] = defaultdict(list)
    for document, key in proposals:
        owner = taken.get(key)
        if owner is not None and owner != document.slug:
            updates[document.area].append(
                {"slug": document.slug, "source_key": f"{LEGACY_PREFIX}{document.slug}"}
            )
            conflicts.append(
                AdoptedEntry(
                    slug=document.slug,
                    area=document.area,
                    source_key=f"{LEGACY_PREFIX}{document.slug}",
                    raw_hash="",
                    conflicts_with=owner,
                )
            )
            continue
        grouped[key].append(document)

    for key, documents in grouped.items():
        winner = max(documents, key=_recency)
        for document in documents:
            if document is winner:
                updates[document.area].append(
                    {
                        "slug": document.slug,
                        "source_key": key,
                        "raw_hash": _raw_hash(document, raw_dir),
                    }
                )
                adopted.append(
                    AdoptedEntry(
                        slug=document.slug,
                        area=document.area,
                        source_key=key,
                        raw_hash=_raw_hash(document, raw_dir),
                    )
                )
                continue
            legacy = f"{LEGACY_PREFIX}{document.slug}"
            updates[document.area].append({"slug": document.slug, "source_key": legacy})
            conflicts.append(
                AdoptedEntry(
                    slug=document.slug,
                    area=document.area,
                    source_key=legacy,
                    raw_hash="",
                    conflicts_with=winner.slug,
                )
            )

    for document in registry.documents:
        if not document.source_key:
            continue
        raw_hash = _raw_hash(document, raw_dir)
        if raw_hash and raw_hash != document.raw_hash:
            updates[document.area].append({"slug": document.slug, "raw_hash": raw_hash})
            adopted.append(
                AdoptedEntry(
                    slug=document.slug,
                    area=document.area,
                    source_key=document.source_key,
                    raw_hash=raw_hash,
                )
            )
        else:
            unchanged += 1

    for area, entries in updates.items():
        merge_index(area / INDEX_NAME, entries)

    return AdoptionReport(
        adopted=tuple(adopted),
        conflicts=tuple(conflicts),
        unchanged=unchanged,
    )


def _proposed_key(document: IndexedDocument) -> str:
    """Fuente canónica propuesta para una entrada sin ``source_key``."""

    url = str(document.entry.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        return source_key_for_url(url)
    base = _upload_name(document.slug)
    if base:
        return source_key_for_upload(base)
    return f"{LEGACY_PREFIX}{document.slug}"


def _upload_name(slug: str) -> str:
    """Nombre recuperable de una subida: el slug sin el sufijo de hash.

    Un slug compuesto solo por un hash no permite recuperar el nombre original.
    """

    if _ONLY_HASH.fullmatch(slug):
        return ""
    return _HASH_SUFFIX.sub("", slug)


def _recency(document: IndexedDocument) -> tuple[datetime, str]:
    """Fecha de la entrada para elegir la versión vigente; empata por slug."""

    for field in ("updated_at", "captured_at"):
        raw = str(document.entry.get(field) or "")
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            continue
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC), document.slug)
    return (datetime.min.replace(tzinfo=UTC), document.slug)


def _raw_hash(document: IndexedDocument, raw_dir: Path | None) -> str:
    """SHA-256 de los bytes de Bronze, o vacío si no se puede localizar."""

    if raw_dir is None or not raw_dir.is_dir():
        return ""
    for path in sorted(raw_dir.rglob(f"{document.slug}.*")):
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    return ""
