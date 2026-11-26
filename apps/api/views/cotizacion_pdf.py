"""PDF de una cotización: ``GET /cotizaciones/{folio}.pdf`` para su dueño.

Dos pruebas de que quien pide es el dueño, sin revelar nunca si el folio existe:

- **Enlace firmado** ``?t=<vence>.<firma>`` (también el formato anterior ``?e=<vence>&t=<firma>``
  de los enlaces ya enviados). Lo entrega un canal sin cookie (WhatsApp): la ruta verifica
  la firma contra el ``owner_key`` guardado y el vencimiento (:mod:`apps.api.quote_links`).
- **Cookie del visitante** del chat web, sin ``t``: la identidad del navegador
  (:mod:`apps.api.chat.identity`) debe ser el ``owner_key`` guardado. Así la web enlaza
  ``/cotizaciones/{folio}.pdf`` (:func:`owner_pdf_link`) sin token que guardar ni que venza.

La ruta lee la cotización por folio y genera el PDF al vuelo. Cualquier fallo de verificación
responde 404.

``app.state.quote_lookup`` (``folio -> (owner_key, payload) | None``) y
``app.state.quote_link_secret`` permiten inyectar dobles en las pruebas.
"""

import hmac
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

__all__ = ["owner_pdf_link", "router"]

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


def _visitor(request: Request) -> str | None:
    """Dueño que la cookie del chat web identifica; ``None`` si no hay o no es válida."""

    # Import diferido: la vista del chat enlaza el PDF con :func:`owner_pdf_link`.
    from apps.api.views.chat import request_identity

    try:
        return request_identity(request).read(request)
    except Exception as error:  # una cookie rara nunca tumba la ruta: responde como sin dueño
        logger.warning("quote_pdf.identity_failed", error_type=type(error).__name__)
        return None


def owner_pdf_link(request: Request) -> Callable[[str], str | None]:
    """``folio -> URL`` del PDF para el visitante web dueño: la abre con su cookie, sin token.

    ``None`` si la URL no se puede armar: quien la usa no ofrece el PDF.
    """

    def link(folio: str) -> str | None:
        try:
            return str(request.url_for("cotizacion_pdf", folio=folio))
        except Exception as error:
            logger.warning("quote_pdf.link_failed", error_type=type(error).__name__)
            return None

    return link


@router.get("/cotizaciones/{folio}.pdf", include_in_schema=False)
def cotizacion_pdf(request: Request, folio: str, e: int = 0, t: str = "") -> Response:
    """PDF de la cotización para su dueño (enlace firmado o cookie del chat); 404 si no."""

    secret = _secret(request)
    expires, signature = split_token(t, e)
    visitor = None if signature else _visitor(request)
    if len(folio) > 64 or not ((secret and signature) or visitor):
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
    if visitor is not None:
        allowed = bool(owner_key) and hmac.compare_digest(visitor.encode(), owner_key.encode())
    else:
        allowed = bool(owner_key) and verify(folio, owner_key, expires, signature, secret)
    if not allowed:
        logger.info("quote_pdf.rejected", folio=folio, proof="cookie" if visitor else "firma")
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
