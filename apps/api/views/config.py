"""Vista de configuración: carga de documentos y captura web del corpus."""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from apps.api.capture_jobs import CaptureBusy, CaptureJob, CaptureJobs
from apps.api.ingestion import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_BYTES,
    MAX_FILES,
    IngestReport,
    UploadedDocument,
    ingest_uploads,
)
from apps.api.runtime import templates
from apps.api.security import verify_csrf
from apps.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from apps.api.urlguard import UrlRejected, validate_capture_url
from apps.api.web_capture import SCOPES, CaptureRequest
from carvajal.infrastructure.settings import Settings

__all__ = ["router"]

logger = structlog.get_logger(__name__)
router = APIRouter()

CSRF_INVALIDO = "La sesión del formulario expiró. Recarga la página e inténtalo de nuevo."
SIN_ARCHIVOS = "Selecciona al menos un documento o una carpeta con archivos admitidos."
NO_AUTORIZADO = "La carga de documentos no está autorizada."
MAX_CAPTURE_FORM_CHARS = 10_000


@dataclass(frozen=True)
class _UploadBatch:
    """Archivos válidos para la ingesta y errores de lectura previos."""

    documents: list[UploadedDocument] = field(default_factory=list)
    errors: tuple[str, ...] = ()


@router.get("/configuracion", response_class=HTMLResponse)
def configuracion(request: Request) -> Response:
    """Muestra los formularios de carga y de captura web."""

    return _page(request)


@router.post("/configuracion", response_class=HTMLResponse)
async def subir_documentos(
    request: Request,
    csrf_token: str = Form(default=""),
    token_admin: str = Form(default=""),
    archivos: Annotated[list[UploadFile] | None, File()] = None,
    carpeta: Annotated[list[UploadFile] | None, File()] = None,
) -> Response:
    """Procesa documentos sueltos o una carpeta y reconstruye el corpus."""

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _page(
            request,
            error=CSRF_INVALIDO,
            status_code=403,
        )
    if not _autorizado(request, token_admin):
        logger.warning("config.unauthorized", path=request.url.path)
        return _page(request, error=NO_AUTORIZADO, status_code=403)

    batch = await _leer_archivos(*(archivos or []), *(carpeta or []))
    if not batch.documents:
        detalle = [SIN_ARCHIVOS, *batch.errors]
        return _page(request, error=" ".join(detalle), status_code=422)

    ingestor = getattr(request.app.state, "ingest", None)
    try:
        if ingestor is None:
            report = await asyncio.to_thread(ingest_uploads, batch.documents)
        else:
            report = ingestor(batch.documents)
    except Exception as error:  # el pipeline no debe tumbar la página
        logger.warning("config.ingest_failed", error=str(error))
        report = IngestReport(
            files=len(batch.documents),
            errors=(*batch.errors, "No se pudo procesar la carga; revisa los logs del servidor."),
        )
    else:
        if batch.errors:
            report = IngestReport(
                files=report.files,
                bronze=report.bronze,
                silver=report.silver,
                gold=report.gold,
                documents=report.documents,
                chunks=report.chunks,
                indexed=report.indexed,
                errors=(*batch.errors, *report.errors),
            )
    return _page(request, report=report)


@router.post("/configuracion/captura", response_class=HTMLResponse)
def iniciar_captura(
    request: Request,
    csrf_token: str = Form(default=""),
    urls: str = Form(default=""),
    scope: str = Form(default="manual"),
    limit: str = Form(default=""),
    respect_robots: str = Form(default=""),
) -> Response:
    """Valida el formulario y lanza la captura en segundo plano.

    La captura es pública en esta fase: no exige el token de la carga de
    archivos ni autenticación. El límite de URLs y el guard SSRF son la
    defensa.
    """

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _capture_response(request, error=CSRF_INVALIDO, status_code=403)

    settings = Settings()
    capture_request, errors, form = _parse_capture_form(
        urls=urls,
        scope=scope,
        limit=limit,
        respect_robots=respect_robots,
        settings=settings,
    )
    if errors or capture_request is None:
        return _capture_response(
            request,
            error=" ".join(errors),
            form=form,
            status_code=422,
        )
    try:
        job = _captures(request).start(capture_request)
    except CaptureBusy as error:
        return _capture_response(request, error=str(error), form=form, status_code=409)
    return _capture_response(request, job=job, form=form)


@router.get("/configuracion/captura/{job_id}/eventos")
def eventos_captura(request: Request, job_id: str) -> Response:
    """Emite el progreso de una captura por SSE."""

    jobs = _captures(request)
    if jobs.get(job_id) is None:
        return JSONResponse({"error": "La captura no existe o expiró."}, status_code=404)
    return StreamingResponse(
        jobs.stream(job_id),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


@router.get("/configuracion/captura/{job_id}")
def estado_captura(request: Request, job_id: str) -> Response:
    """Expone el estado final por si el stream se corta o el navegador recarga."""

    job = _captures(request).get(job_id)
    if job is None:
        return JSONResponse({"error": "La captura no existe o expiró."}, status_code=404)
    return JSONResponse(
        {
            "id": job.id,
            "status": job.status,
            "error": job.error,
            "outcome": asdict(job.outcome) if job.outcome is not None else None,
        }
    )


async def _leer_archivos(*subidas: UploadFile) -> _UploadBatch:
    """Lee las subidas y descarta las que exceden el límite antes de cargarlas."""

    documentos: list[UploadedDocument] = []
    errores: list[str] = []
    for subida in subidas[:MAX_FILES]:
        nombre = (subida.filename or "").strip()
        if not nombre:
            errores.append("Se recibió un archivo sin nombre.")
            continue
        tamano = getattr(subida, "size", None)
        if tamano is not None and tamano > MAX_FILE_BYTES:
            errores.append(f"{nombre}: supera el límite de {MAX_FILE_BYTES // 1024 // 1024} MB.")
            continue
        documentos.append(
            UploadedDocument(
                filename=nombre,
                data=await subida.read(),
                content_type=subida.content_type or "",
            )
        )
    if len(subidas) > MAX_FILES:
        errores.append(f"Solo se procesan {MAX_FILES} archivos por carga.")
    return _UploadBatch(documents=documentos, errors=tuple(errores))


def _autorizado(request: Request, token: str) -> bool:
    """Exige el token configurado o, sin token, una petición de loopback."""

    esperado = Settings().config_token.strip()
    if esperado:
        return hmac.compare_digest(esperado.encode("utf-8"), token.encode("utf-8"))
    host = request.client.host if request.client is not None else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _captures(request: Request) -> CaptureJobs:
    """Registro de jobs de la app; se crea si una prueba no lo inyectó."""

    registry = getattr(request.app.state, "captures", None)
    if isinstance(registry, CaptureJobs):
        return registry
    registry = CaptureJobs()
    request.app.state.captures = registry
    return registry


def _checked(value: str | None) -> bool:
    """Interpreta el checkbox HTML como booleano."""

    return (value or "").strip().lower() in {"on", "true", "1", "si", "sí"}


def _parse_capture_form(
    *,
    urls: str,
    scope: str,
    limit: str,
    respect_robots: str,
    settings: Settings,
) -> tuple[CaptureRequest | None, list[str], dict[str, Any]]:
    """Normaliza el formulario y valida las URLs antes de tocar la red."""

    errors: list[str] = []
    clean_scope = (scope or "manual").strip().lower()
    if clean_scope not in SCOPES:
        errors.append(f"Alcance desconocido: {clean_scope!r}.")
        clean_scope = "manual"

    raw_limit = (limit or "").strip() or str(settings.capture_default_urls)
    max_urls = max(1, settings.capture_max_urls)
    try:
        clean_limit = int(raw_limit)
    except ValueError:
        errors.append("El máximo de URLs debe ser un número entero.")
        clean_limit = min(settings.capture_default_urls, max_urls)
    if not 1 <= clean_limit <= max_urls:
        errors.append(f"El máximo de URLs debe estar entre 1 y {max_urls}.")
        clean_limit = min(max(clean_limit, 1), max_urls)

    text = urls or ""
    if len(text) > MAX_CAPTURE_FORM_CHARS:
        errors.append(f"El campo de URLs supera {MAX_CAPTURE_FORM_CHARS} caracteres.")

    raw_urls = [line.strip() for line in text.splitlines() if line.strip()]
    deduped = list(dict.fromkeys(raw_urls))
    if len(deduped) > clean_limit:
        errors.append(
            f"Escribiste {len(deduped)} URLs y el máximo de esta ejecución es {clean_limit}. "
            "Sube el máximo o quita URLs."
        )
    for url in deduped:
        try:
            validate_capture_url(
                url,
                allowed_domains=settings.capture_allowed_domains,
                allow_private=settings.capture_allow_private_hosts,
            )
        except UrlRejected as error:
            errors.append(f"{url}: {error}")

    if clean_scope == "manual" and not deduped:
        errors.append("Escribe al menos una URL o elige un alcance con semillas.")

    form = {
        "urls": text,
        "scope": clean_scope,
        "limit": str(clean_limit),
        "respect_robots": _checked(respect_robots),
    }
    capture_request = CaptureRequest(
        urls=tuple(deduped),
        scope=clean_scope,
        respect_robots=form["respect_robots"],
        limit=clean_limit,
    )
    return (capture_request if not errors else None), errors, form


def _scope_options(settings: Settings) -> list[tuple[str, str]]:
    """Etiquetas del alcance con el número de semillas configuradas."""

    return [
        ("manual", SCOPES["manual"]),
        ("oficial", f"{SCOPES['oficial']} ({len(settings.seeds)} URLs)"),
        ("terceros", f"{SCOPES['terceros']} ({len(settings.external_seeds)} URLs)"),
        ("todo", f"{SCOPES['todo']} ({settings.capture_max_urls} URLs)"),
    ]


def _capture_context(
    *,
    form: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Contexto compartido por la página y el parcial de captura."""

    settings = Settings()
    max_urls = max(1, settings.capture_max_urls)
    default_limit = min(settings.capture_default_urls, max_urls)
    values = form or {}
    return {
        "max_urls": max_urls,
        "scopes": _scope_options(settings),
        "capture_allowed_domains": settings.capture_allowed_domains,
        "form": {
            "urls": values.get("urls", ""),
            "scope": values.get("scope", "manual"),
            "limit": values.get("limit", str(default_limit)),
            "respect_robots": values.get("respect_robots", True),
        },
    }


def _job_view(job: CaptureJob | None) -> dict[str, Any] | None:
    """Datos mínimos del job para la plantilla; no expone el objeto interno."""

    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "stream_url": f"/configuracion/captura/{job.id}/eventos",
    }


def _page(
    request: Request,
    *,
    report: IngestReport | None = None,
    error: str | None = None,
    status_code: int = 200,
    job: CaptureJob | None = None,
    form: dict[str, Any] | None = None,
) -> Response:
    """Renderiza la configuración con formatos admitidos y resultado."""

    headers = {"X-Carvajal-Error": "1"} if error is not None else {}
    token_requerido = bool(Settings().config_token.strip())
    return templates.TemplateResponse(
        request,
        "configuracion.html",
        {
            "report": report,
            "error": error,
            "partial": False,
            "job": _job_view(job),
            "MAX_FILES": MAX_FILES,
            "MAX_FILE_MB": MAX_FILE_BYTES // 1024 // 1024,
            "formatos": sorted(ALLOWED_EXTENSIONS),
            "token_requerido": token_requerido,
            **_capture_context(form=form),
        },
        status_code=status_code,
        headers=headers,
    )


def _capture_partial(
    request: Request,
    *,
    job: CaptureJob | None = None,
    error: str | None = None,
    form: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    """Parcial de HTMX con el formulario y el progreso de la captura."""

    headers = {"X-Carvajal-Error": "1"} if error is not None else {}
    return templates.TemplateResponse(
        request,
        "_captura.html",
        {
            "job": _job_view(job),
            "error": error,
            "partial": True,
            **_capture_context(form=form),
        },
        status_code=status_code,
        headers=headers,
    )


def _capture_response(
    request: Request,
    *,
    job: CaptureJob | None = None,
    error: str | None = None,
    form: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    """Responde el parcial para HTMX o la página completa sin JavaScript."""

    if request.headers.get("HX-Request", "").lower() == "true":
        return _capture_partial(
            request,
            job=job,
            error=error,
            form=form,
            status_code=status_code,
        )
    return _page(
        request,
        error=error,
        status_code=status_code,
        job=job,
        form=form,
    )
