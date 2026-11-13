"""Ruta de salud: estado del proceso y conteos del corpus."""

from logging import getLogger
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from apps.api.deps import DB_UNAVAILABLE, Handlers, get_handlers

__all__ = ["router"]

logger = getLogger(__name__)

router = APIRouter(tags=["salud"])


@router.get("/live")
def live() -> dict[str, str]:
    """Vitalidad del proceso: no toca la base, para el healthcheck del contenedor."""

    return {"status": "ok"}


@router.get("/health")
def health(handlers: Annotated[Handlers, Depends(get_handlers)]) -> JSONResponse:
    """Publica los conteos del corpus o el estado degradado sin filtrar detalles."""

    try:
        status = handlers.health()
    except (SQLAlchemyError, OSError) as error:  # la base no responde
        logger.warning("Health check falló: %s", error)
        return JSONResponse({"status": "degraded", "detail": DB_UNAVAILABLE}, status_code=503)
    return JSONResponse({"status": "ok", "documents": status.documents, "chunks": status.chunks})
