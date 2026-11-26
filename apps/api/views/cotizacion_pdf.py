"""PDF de una cotización por enlace firmado: ``GET /cotizaciones/{folio}.pdf?t=<vence>.<firma>``.

También acepta el formato anterior ``?e=<vence>&t=<firma>`` de los enlaces ya enviados.

No usa cookie ni sesión: el enlace lo entrega el canal (WhatsApp) al dueño de la
cotización. La ruta lee la cotización por folio, verifica la firma contra el ``owner_key``
guardado y el vencimiento (:mod:`apps.api.quote_links`) y genera el PDF al vuelo. Cualquier
fallo de verificación responde 404, sin revelar si el folio existe.

``app.state.quote_lookup`` (``folio -> (owner_key, payload) | None``) y
``app.state.quote_link_secret`` permiten inyectar dobles en las pruebas.
"""

from collections.abc import Callable, Mapping
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from apps.api.quote_links import split_token, verify
from apps.api.quote_pdf import build_quote_pdf
from apps.api.quote_view import quote_view
from carvajal.catalog.images import catalog_image

__all__ = ["router"]

logger = structlog.get_logger(__name__)
router = APIRouter()

NOT_FOUND = "La cotización no existe o el enlace venció."

QuoteLookup = Callable[[str], tuple[str, Mapping[str, Any]] | None]


def _default_lookup(folio: str) -> tuple[str, Mapping[str, Any]] | None:
    from apps.api import runtime
    from carvajal.adapters.storage.quotes import SqlQuoteRepository

    with runtime.session_factory()() as session:
        record = SqlQuoteRepository(session).by_folio(folio)
        if record is None:
            return None
        return str(record.get("owner_key") or ""), dict(record.payload)


def _secret(request: Request) -> bytes:
    injected = getattr(request.app.state, "quote_link_secret", None)
    if injected is not None:
        return injected
    settings = getattr(request.app.state, "settings", None)
    secret = getattr(settings, "session_secret", "") if settings is not None else ""
    return secret.encode("utf-8")


def _not_found() -> Response:
    return JSONResponse({"detail": NOT_FOUND}, 404, headers={"Cache-Control": "no-store"})


@router.get("/cotizaciones/{folio}.pdf", include_in_schema=False)
def cotizacion_pdf(request: Request, folio: str, e: int = 0, t: str = "") -> Response:
    """PDF de la cotización si la firma y el vencimiento son válidos; 404 si no."""

    secret = _secret(request)
    expires, signature = split_token(t, e)
    if not secret or not signature or len(folio) > 64:
        return _not_found()
    lookup: QuoteLookup = getattr(request.app.state, "quote_lookup", None) or _default_lookup
    try:
        found = lookup(folio)
    except SQLAlchemyError as error:
        logger.warning("quote_pdf.lookup_failed", error=str(error))
        return JSONResponse({"detail": "Servicio no disponible."}, 503)
    if found is None:
        return _not_found()
    owner_key, payload = found
    if not owner_key or not verify(folio, owner_key, expires, signature, secret):
        logger.info("quote_pdf.rejected", folio=folio)
        return _not_found()
    view = quote_view(payload)
    if view is None:
        return _not_found()
    image = catalog_image(view.sku) if view.sku else None
    # Varios productos (spec 054): una miniatura por fila con la foto de cada SKU.
    line_photos = {
        line.sku: found.path
        for line in view.lines
        if line.sku and (found := catalog_image(line.sku)) is not None
    }
    try:
        document = build_quote_pdf(
            view, photo=None if image is None else image.path, line_photos=line_photos
        )
    except Exception:
        # fpdf2 puede fallar con una fuente o una imagen concreta; no se filtra
        # la traza y el enlace responde como inválido, igual que una firma mala.
        logger.exception("quote_pdf.build_failed", folio=folio)
        return _not_found()
    logger.info("quote_pdf.served", folio=folio, bytes=len(document))
    return Response(
        document,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="cotizacion-{folio}.pdf"',
            "Cache-Control": "private, no-store",
            "X-Robots-Tag": "noindex",
        },
    )
