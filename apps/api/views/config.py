"""Vista de configuración: carga de documentos y captura web del corpus."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from apps.api.capture_jobs import CaptureBusy, CaptureJob, CaptureJobs, CaptureRateLimited
from apps.api.ingestion import ALLOWED_EXTENSIONS, MAX_FILE_BYTES, MAX_FILES, UploadedDocument
from apps.api.runtime import templates
from apps.api.security import verify_csrf
from apps.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from apps.api.upload_jobs import UploadBusy, UploadJob, UploadJobs
from apps.api.urlguard import UrlRejected, validate_capture_url
from apps.api.web_capture import SCOPES, CaptureRequest
from carvajal.infrastructure.settings import Settings

__all__ = ["router"]

logger = structlog.get_logger(__name__)
router = APIRouter()

CSRF_INVALIDO = "La sesión del formulario expiró. Recarga la página e inténtalo de nuevo."
SIN_ARCHIVOS = "Selecciona al menos un documento o una carpeta con archivos admitidos."
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
    archivos: Annotated[list[UploadFile] | None, File()] = None,
    carpeta: Annotated[list[UploadFile] | None, File()] = None,
) -> Response:
    """Lee los archivos y lanza la ingesta en segundo plano."""

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _carga_response(
            request,
            carga_error=CSRF_INVALIDO,
            status_code=403,
            oob=False,
        )

    batch = await _leer_archivos(*(archivos or []), *(carpeta or []))
    if not batch.documents:
        detalle = [SIN_ARCHIVOS, *batch.errors]
        return _carga_response(
            request,
            carga_error=" ".join(detalle),
            status_code=422,
            oob=False,
        )

    try:
        job = _uploads(request).start(batch.documents, notes=batch.errors)
    except UploadBusy as error:
        return _carga_response(
            request,
            carga_error=str(error),
            status_code=409,
            oob=False,
        )
    return _carga_response(request, job=job, nuevo_id=job.id)


@router.get("/configuracion/carga/{job_id}", response_class=HTMLResponse)
def estado_carga(request: Request, job_id: str) -> Response:
    """Devuelve el estado de una carga para el polling de la interfaz."""

    job = _uploads(request).get(job_id)
    if job is None:
        return JSONResponse({"error": "La carga no existe o expiró."}, status_code=404)
    return _carga_response(request, job=job)


@router.get("/configuracion/carga/{job_id}/estado")
def estado_carga_json(request: Request, job_id: str) -> Response:
    """Expone el mismo estado en JSON para pruebas y operación."""

    job = _uploads(request).get(job_id)
    if job is None:
        return JSONResponse({"error": "La carga no existe o expiró."}, status_code=404)
    data = job.snapshot()
    data["report"] = asdict(job.report) if job.report is not None else None
    return JSONResponse(data)


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
    owner_key = request.client.host if request.client is not None else "desconocido"
    try:
        job = _captures(request).start(capture_request, owner_key=owner_key)
    except CaptureBusy as error:
        return _capture_response(request, error=str(error), form=form, status_code=409)
    except CaptureRateLimited as error:
        return _capture_response(
            request,
            error=str(error),
            form=form,
            status_code=429,
            headers={"Retry-After": str(error.retry_after)},
        )
    return _capture_response(request, job=job, form=form)


@router.get("/configuracion/captura/{job_id}/eventos")
async def eventos_captura(request: Request, job_id: str) -> Response:
    """Emite el progreso de una captura por SSE y corta si el cliente se va."""

    jobs = _captures(request)
    if jobs.get(job_id) is None:
        return JSONResponse({"error": "La captura no existe o expiró."}, status_code=404)
    return StreamingResponse(
        jobs.stream_async(job_id, request.is_disconnected),
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


def _captures(request: Request) -> CaptureJobs:
    """Registro de capturas de la app; se crea si una prueba no lo inyectó."""

    registry = getattr(request.app.state, "captures", None)
    if isinstance(registry, CaptureJobs):
        return registry
    registry = CaptureJobs()
    request.app.state.captures = registry
    return registry


def _uploads(request: Request) -> UploadJobs:
    """Registro de cargas de la app; se crea si una prueba no lo inyectó."""

    registry = getattr(request.app.state, "uploads", None)
    if isinstance(registry, UploadJobs):
        return registry
    registry = UploadJobs()
    request.app.state.uploads = registry
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


def _carga_context(
    job: UploadJob | None,
    *,
    carga_error: str | None = None,
    nuevo_id: str = "",
    oob: bool = False,
) -> dict[str, Any]:
    """Contexto del panel de carga y de la lista lateral de jobs."""

    report = job.report if job is not None else None
    notes = list(job.notes) if job is not None else []
    issues = [*(report.errors if report is not None else []), *notes]
    ok = bool(report is not None and report.ok and not notes)
    if job is None:
        estado = "error" if carga_error else "idle"
        cargando = False
    else:
        estado = "running" if job.status in {"pending", "running"} else job.status
        cargando = job.status in {"pending", "running"}
    return {
        "report": report,
        "carga_job": job.snapshot() if job is not None else None,
        "carga_error": carga_error,
        "carga_estado": estado,
        "cargando": cargando,
        "carga_issues": issues,
        "carga_ok": ok,
        "carga_nuevo_id": nuevo_id,
        "carga_oob": oob,
    }


def _page(
    request: Request,
    *,
    error: str | None = None,
    carga_error: str | None = None,
    status_code: int = 200,
    job: CaptureJob | None = None,
    carga_job: UploadJob | None = None,
    form: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    nuevo_id: str = "",
) -> Response:
    """Renderiza la configuración con formatos admitidos y resultado."""

    merged = {"X-Carvajal-Error": "1"} if error is not None else {}
    merged.update(headers or {})
    return templates.TemplateResponse(
        request,
        "configuracion.html",
        {
            "error": error,
            "partial": False,
            "job": _job_view(job),
            "carga_jobs": _uploads(request).recent(),
            "MAX_FILES": MAX_FILES,
            "MAX_FILE_MB": MAX_FILE_BYTES // 1024 // 1024,
            "formatos": sorted(ALLOWED_EXTENSIONS),
            **_carga_context(
                carga_job,
                carga_error=carga_error,
                nuevo_id=nuevo_id,
                oob=False,
            ),
            **_capture_context(form=form),
        },
        status_code=status_code,
        headers=merged,
    )


def _carga_partial(
    request: Request,
    *,
    job: UploadJob | None = None,
    carga_error: str | None = None,
    nuevo_id: str = "",
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    oob: bool = True,
) -> Response:
    """Parcial del panel de carga para HTMX."""

    merged = {"X-Carvajal-Error": "1"} if carga_error is not None else {}
    merged.update(headers or {})
    return templates.TemplateResponse(
        request,
        "_carga_estado.html",
        {
            "carga_jobs": _uploads(request).recent(),
            **_carga_context(
                job,
                carga_error=carga_error,
                nuevo_id=nuevo_id,
                oob=oob,
            ),
        },
        status_code=status_code,
        headers=merged,
    )


def _carga_response(
    request: Request,
    *,
    job: UploadJob | None = None,
    carga_error: str | None = None,
    nuevo_id: str = "",
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    oob: bool = True,
) -> Response:
    """Responde el parcial para HTMX o la página completa sin JavaScript."""

    if request.headers.get("HX-Request", "").lower() == "true":
        return _carga_partial(
            request,
            job=job,
            carga_error=carga_error,
            nuevo_id=nuevo_id,
            status_code=status_code,
            headers=headers,
            oob=oob,
        )
    return _page(
        request,
        carga_error=carga_error,
        carga_job=job,
        nuevo_id=nuevo_id,
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
    headers: dict[str, str] | None = None,
) -> Response:
    """Parcial de HTMX con el formulario y el progreso de la captura."""

    merged = {"X-Carvajal-Error": "1"} if error is not None else {}
    merged.update(headers or {})
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
        headers=merged,
    )


def _capture_response(
    request: Request,
    *,
    job: CaptureJob | None = None,
    error: str | None = None,
    form: dict[str, Any] | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    """Responde el parcial para HTMX o la página completa sin JavaScript."""

    if request.headers.get("HX-Request", "").lower() == "true":
        return _capture_partial(
            request,
            job=job,
            error=error,
            form=form,
            status_code=status_code,
            headers=headers,
        )
    return _page(
        request,
        error=error,
        status_code=status_code,
        job=job,
        form=form,
        headers=headers,
    )
