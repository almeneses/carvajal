"""Vista de configuración: carga de documentos y reconstrucción del corpus."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

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

__all__ = ["router"]

logger = structlog.get_logger(__name__)
router = APIRouter()

CSRF_INVALIDO = "La sesión del formulario expiró. Recarga la página e inténtalo de nuevo."
SIN_ARCHIVOS = "Selecciona al menos un documento o una carpeta con archivos admitidos."


@dataclass(frozen=True)
class _UploadBatch:
    """Archivos válidos para la ingesta y errores de lectura previos."""

    documents: list[UploadedDocument] = field(default_factory=list)
    errors: tuple[str, ...] = ()


@router.get("/configuracion", response_class=HTMLResponse)
def configuracion(request: Request) -> Response:
    """Muestra el formulario de carga y el resultado de la última operación."""

    return _page(request)


@router.post("/configuracion", response_class=HTMLResponse)
async def subir_documentos(
    request: Request,
    csrf_token: str = Form(default=""),
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


def _page(
    request: Request,
    *,
    report: IngestReport | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    """Renderiza la configuración con formatos admitidos y resultado."""

    headers = {"X-Carvajal-Error": "1"} if error is not None else {}
    return templates.TemplateResponse(
        request,
        "configuracion.html",
        {
            "report": report,
            "error": error,
            "MAX_FILES": MAX_FILES,
            "MAX_FILE_MB": MAX_FILE_BYTES // 1024 // 1024,
            "formatos": sorted(ALLOWED_EXTENSIONS),
        },
        status_code=status_code,
        headers=headers,
    )
