"""Captura web desde la pestaña Configuración.

Reutiliza el pipeline de la CLI (``capture``) y el mismo cierre que la carga de
archivos: Gold → PostgreSQL → Qdrant, limitado a los slugs capturados y sin
podar el resto del corpus. La captura es pública en esta fase; el límite de
URLs, el timeout y el guard SSRF de ``urlguard`` son su defensa.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog

from apps.api.ingestion import importar_gold, indexar_gold
from apps.api.urlguard import SafeWebClient, UrlRejected, validate_capture_url
from carvajal.application.ports import Fetcher
from carvajal.domain.frontmatter import parse_front_matter
from carvajal.domain.rules import slug_from_url
from carvajal.infrastructure.settings import Settings
from carvajal.pipeline import CaptureEvent, CaptureReport, capture

__all__ = [
    "SCOPES",
    "CaptureOutcome",
    "CaptureRequest",
    "ingest_urls",
    "resolve_seeds",
]

logger = structlog.get_logger(__name__)

SCOPES = {
    "manual": "Solo las URLs que escribo",
    "oficial": "Solo sitio oficial",
    "terceros": "Solo fuentes de terceros",
    "todo": "Todo el corpus",
}

Importar = Callable[[Path, set[str]], tuple[int, int]]
Indexar = Callable[[Path, set[str]], int]
EventHook = Callable[[CaptureEvent], None]


@dataclass(frozen=True)
class CaptureRequest:
    """Parámetros del formulario de captura ya normalizados por la vista."""

    urls: tuple[str, ...] = ()
    scope: str = "manual"
    respect_robots: bool = True
    limit: int = 5


@dataclass(frozen=True)
class CaptureOutcome:
    """Resumen estable de una captura para la UI y el endpoint de estado."""

    requested: int = 0
    discovered: int = 0
    fetched: int = 0
    written: int = 0
    discarded: int = 0
    chars: int = 0
    documents: int = 0
    chunks: int = 0
    indexed: int = 0
    duration_seconds: float = 0.0
    errors: tuple[str, ...] = ()
    robots_denied: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()
    captured: tuple[str, ...] = ()


def _dedupe(items: Iterable[str]) -> list[str]:
    """Elimina repetidos conservando el orden de entrada."""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _scope_seeds(scope: str, settings: Settings) -> list[str]:
    """Semillas configuradas para el alcance elegido."""

    if scope == "oficial":
        return list(settings.seeds)
    if scope == "terceros":
        return list(settings.external_seeds)
    if scope == "todo":
        return [*settings.seeds, *settings.external_seeds]
    return []


def resolve_seeds(
    request: CaptureRequest,
    settings: Settings,
) -> tuple[list[str], list[str]]:
    """Combina las URLs escritas con las semillas del alcance y aplica el límite.

    Las URLs explícitas van primero para que el límite no las desplace. Si el
    usuario escribió más de las que permite el límite, se devuelve un error en
    lugar de truncarlas en silencio; las semillas del alcance sí se recortan,
    como hacía el control de la primera interfaz.
    """

    errors: list[str] = []
    if request.scope not in SCOPES:
        return [], [f"Alcance desconocido: {request.scope!r}."]

    explicit = _dedupe(url.strip() for url in request.urls if url and url.strip())
    limit = max(1, min(request.limit, settings.capture_max_urls))
    if len(explicit) > limit:
        errors.append(
            f"Escribiste {len(explicit)} URLs y el máximo de esta ejecución es {limit}. "
            "Sube el máximo o quita URLs."
        )
        return [], errors

    pool = _dedupe([*explicit, *_scope_seeds(request.scope, settings)])[:limit]
    if not pool:
        errors.append("Escribe al menos una URL o elige un alcance con semillas.")
    return pool, errors


def _guard_slugs(
    seeds: Sequence[str],
    settings: Settings,
) -> tuple[list[str], list[str]]:
    """Evita que dos URLs compartan el mismo archivo Gold.

    ``slug_from_url`` usa el último segmento de la ruta. Dos URLs distintas con
    el mismo segmento sobrescribirían el mismo documento; se rechaza la segunda
    en vez de perder contenido en silencio.
    """

    errors: list[str] = []
    kept: list[str] = []
    seen: dict[str, str] = {}
    for url in seeds:
        slug = slug_from_url(url)
        previous = seen.get(slug)
        if previous is not None and previous != url:
            errors.append(f"{url}: comparte el slug {slug!r} con {previous}.")
            continue
        path = settings.gold_dir / f"{slug}.md"
        if path.is_file():
            metadata = parse_front_matter(path.read_text(encoding="utf-8"))
            existing = metadata.get("url", "").strip()
            if existing and existing != url:
                errors.append(f"{url}: el slug {slug!r} ya lo usa {existing}.")
                continue
        seen[slug] = url
        kept.append(url)
    return kept, errors


def _validate_seeds(
    seeds: Sequence[str],
    settings: Settings,
) -> tuple[list[str], list[str]]:
    """Valida cada URL antes de que el cliente toque la red."""

    valid: list[str] = []
    errors: list[str] = []
    for url in seeds:
        try:
            validate_capture_url(
                url,
                allowed_domains=settings.capture_allowed_domains,
                allow_private=settings.capture_allow_private_hosts,
            )
        except UrlRejected as error:
            errors.append(f"{url}: {error}")
            continue
        valid.append(url)
    return valid, errors


def _outcome(
    report: CaptureReport | None,
    *,
    requested: int,
    errors: Sequence[str],
    documents: int = 0,
    chunks: int = 0,
    indexed: int = 0,
) -> CaptureOutcome:
    """Construye el resumen a partir del reporte del pipeline."""

    if report is None:
        return CaptureOutcome(requested=requested, errors=tuple(errors))
    return CaptureOutcome(
        requested=requested,
        discovered=report.discovered,
        fetched=report.fetched,
        written=report.written,
        discarded=report.discarded,
        chars=report.chars,
        documents=documents,
        chunks=chunks,
        indexed=indexed,
        duration_seconds=report.duration_seconds,
        errors=tuple(errors),
        robots_denied=tuple(report.robots_denied),
        duplicates=tuple(report.duplicates),
        captured=tuple(page.slug for page in report.pages),
    )


def ingest_urls(
    request: CaptureRequest,
    *,
    settings: Settings | None = None,
    client: Fetcher | None = None,
    importar: Importar | None = None,
    indexar: Indexar | None = None,
    on_event: EventHook | None = None,
) -> CaptureOutcome:
    """Captura las URLs, importa los slugs nuevos y los indexa.

    ``client``, ``importar`` e ``indexar`` se inyectan en las pruebas para no
    tocar la red, PostgreSQL ni Qdrant. En la app real se usan
    ``SafeWebClient``, ``importar_gold`` e ``indexar_gold``.
    """

    settings = settings or Settings()
    seeds, errors = resolve_seeds(request, settings)
    if client is None:
        seeds, invalid = _validate_seeds(seeds, settings)
        errors.extend(invalid)
    seeds, collisions = _guard_slugs(seeds, settings)
    errors.extend(collisions)
    if not seeds:
        return _outcome(None, requested=0, errors=errors)

    if client is None:
        client = SafeWebClient(
            allowed_domains=settings.capture_allowed_domains,
            allow_private=settings.capture_allow_private_hosts,
            max_bytes=settings.capture_max_bytes,
            max_redirects=settings.capture_max_redirects,
            timeout=settings.capture_timeout_seconds,
            respect_robots=request.respect_robots,
        )

    report = capture(
        seeds,
        client=client,
        gold_dir=settings.gold_dir,
        raw_dir=settings.raw_dir,
        processed_dir=settings.processed_dir,
        index_file=settings.index_file,
        on_event=on_event,
    )
    errors.extend(report.errors)
    slugs = {page.slug for page in report.pages}
    documents = chunks = indexed = 0
    if slugs:
        try:
            documents, chunks = (importar or importar_gold)(settings.gold_dir, slugs)
        except Exception as error:  # la UI informa el fallo de la etapa
            logger.warning("capture.import_failed", error=str(error))
            errors.append("No se pudo importar a PostgreSQL; revisa la conexión y los logs.")
        else:
            try:
                indexed = (indexar or indexar_gold)(settings.gold_dir, slugs)
            except Exception as error:  # Ollama o Qdrant pueden no estar disponibles
                logger.warning("capture.index_failed", error=str(error))
                errors.append("No se pudo indexar en Qdrant; revisa Qdrant y Ollama.")
    return _outcome(
        report,
        requested=len(seeds),
        errors=errors,
        documents=documents,
        chunks=chunks,
        indexed=indexed,
    )
