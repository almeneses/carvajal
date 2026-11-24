"""Fotos del catálogo: ``GET /catalogo/imagenes/{sku}.jpg``.

Son públicas (no llevan datos de nadie) y solo se sirven los SKU del manifiesto
``imagenes.csv``: el parámetro nunca se usa como ruta. WhatsApp las enlaza en la
cotización y muestra la vista previa.
"""

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

from carvajal.catalog.images import catalog_image

__all__ = ["router"]

router = APIRouter()


@router.get("/catalogo/imagenes/{sku}.jpg", include_in_schema=False)
def catalogo_imagen(sku: str) -> Response:
    """Foto del SKU o 404."""

    image = catalog_image(sku) if len(sku) <= 64 else None
    if image is None:
        return JSONResponse({"detail": "Sin foto para esta referencia."}, 404)
    media = "image/png" if image.path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(image.path, media_type=media)
