"""Recibo del pedido: ``GET /pedidos/{folio}.pdf`` para el dueño de la cotización (spec 056).

Es el PDF del pedido de venta que Odoo generó al confirmarlo (el mismo que adjunta al correo
de confirmación). Las mismas dos pruebas que el PDF de la cotización, sin revelar nunca si el
folio existe:

- **Enlace firmado** ``?t=<vence>.<firma>`` con su propio dominio de firma
  (:data:`apps.api.quote_links.ORDER_DOMAIN`): lo entrega WhatsApp.
- **Cookie del visitante** del chat web, sin ``t``.

El dueño es el de la cotización del folio. Cualquier fallo responde 404; si Odoo no está
configurado o no responde, 503.

``app.state.quote_lookup`` y ``app.state.order_pdf`` (``folio -> bytes | None``) permiten
inyectar dobles en las pruebas.
"""

import hmac
from collections.abc import Callable

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from apps.api.quote_links import ORDER_DOMAIN, split_token, verify
from apps.api.views.cotizacion_pdf import QuoteLookup, _default_lookup, _secret, _visitor

__all__ = ["owner_order_link", "router"]

logger = structlog.get_logger(__name__)
router = APIRouter()

NOT_FOUND = "El pedido no existe o el enlace venció."

OrderPdf = Callable[[str], bytes | None]


def _not_found() -> Response:
    return JSONResponse({"detail": NOT_FOUND}, 404, headers={"Cache-Control": "no-store"})


def _order_pdf(request: Request) -> OrderPdf | None:
    """El lector del PDF en Odoo (inyectado o construido una vez); ``None`` sin Odoo."""

    state = request.app.state
    if getattr(state, "order_pdf_built", False) or getattr(state, "order_pdf", None) is not None:
        return getattr(state, "order_pdf", None)
    from carvajal.adapters.crm import build_orders
    from carvajal.infrastructure.settings import Settings

    try:
        settings = getattr(state, "settings", None) or Settings()
        orders = build_orders(settings)
    except Exception as error:  # configuración rota: 503 y se reintenta en la próxima descarga
        logger.warning("order_pdf.build_failed", error_type=type(error).__name__)
        return None
    # Una vez por proceso, también sin Odoo: no se arma un cliente por descarga.
    state.order_pdf = orders.order_pdf if orders is not None else None
    state.order_pdf_built = True
    return state.order_pdf


def owner_order_link(request: Request) -> Callable[[str], str | None]:
    """``folio -> URL`` del recibo para el visitante web dueño (con su cookie, sin token)."""

    settings = getattr(request.app.state, "settings", None)
    base = str(getattr(settings, "public_base_url", "") or "").rstrip("/")

    def link(folio: str) -> str | None:
        try:
            if base:
                return f"{base}{request.app.url_path_for('pedido_pdf', folio=folio)}"
            return str(request.url_for("pedido_pdf", folio=folio))
        except Exception as error:
            logger.warning("order_pdf.link_failed", error_type=type(error).__name__)
            return None

    return link


@router.get("/pedidos/{folio}.pdf", include_in_schema=False)
def pedido_pdf(request: Request, folio: str, t: str = "") -> Response:
    """Recibo del pedido para el dueño de la cotización (enlace firmado o cookie); 404 si no."""

    secret = _secret(request)
    expires, signature = split_token(t)
    visitor = None if t else _visitor(request)
    if len(folio) > 64 or not ((secret and signature) or visitor):
        return _not_found()
    if visitor is None and not secret:  # sin SESSION_SECRET ninguna firma vale
        return _not_found()
    lookup: QuoteLookup = getattr(request.app.state, "quote_lookup", None) or _default_lookup
    try:
        found = lookup(folio)
    except SQLAlchemyError as error:
        logger.warning("order_pdf.lookup_failed", error_type=type(error).__name__)
        return JSONResponse({"detail": "Servicio no disponible."}, 503)
    if found is None:
        return _not_found()
    owner_key, _payload = found
    if visitor is not None:
        allowed = bool(owner_key) and hmac.compare_digest(visitor.encode(), owner_key.encode())
    else:
        allowed = bool(owner_key) and verify(
            folio, owner_key, expires, signature, secret, domain=ORDER_DOMAIN
        )
    if not allowed:
        logger.info("order_pdf.rejected", folio=folio, proof="cookie" if visitor else "firma")
        return _not_found()
    reader = _order_pdf(request)
    if reader is None:
        return JSONResponse({"detail": "Servicio no disponible."}, 503)
    try:
        document = reader(folio)
    except Exception as error:  # Odoo caído o lento: el enlace se puede reintentar
        logger.warning("order_pdf.fetch_failed", folio=folio, error_type=type(error).__name__)
        return JSONResponse({"detail": "Servicio no disponible."}, 503)
    if not document:
        return _not_found()
    logger.info("order_pdf.served", folio=folio, bytes=len(document))
    return Response(
        document,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="pedido-{folio}.pdf"',
            "Cache-Control": "private, no-store",
            "X-Robots-Tag": "noindex",
        },
    )
