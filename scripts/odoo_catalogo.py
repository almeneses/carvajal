"""Sincroniza el catálogo de Colombia con Odoo (spec 056).

Crea o actualiza un producto por referencia (``default_code`` = SKU) con el precio por caja
de la ficha o, si la ficha no lo publica, el de la primera oferta en COP de
``precios.csv``; también crea el producto de servicio del envío. Es idempotente: una segunda
pasada solo escribe lo que cambió.

Uso (lee ``ODOO_URL``, ``ODOO_DB`` y ``ODOO_API_KEY`` del entorno)::

    uv run python scripts/odoo_catalogo.py            # sincroniza
    uv run python scripts/odoo_catalogo.py --dry-run  # solo cuenta
    uv run python scripts/odoo_catalogo.py --imagenes # además, las fotos que falten

Desde el equipo del operador, con un túnel al Odoo de la VPS::

    ssh -N -L 8169:127.0.0.1:8169 deploy@<vps> -p 2422 &
    ODOO_URL=http://127.0.0.1:8169 ODOO_DB=carvajal ODOO_API_KEY=... \\
        uv run python scripts/odoo_catalogo.py
"""

import argparse
import sys

from carvajal.adapters.crm import build_orders
from carvajal.catalog.images import catalog_image
from carvajal.catalog.loader import load_catalog
from carvajal.catalog.models import Reference
from carvajal.infrastructure.settings import Settings


def colombian_references() -> list[Reference]:
    """Referencias de Colombia con el precio por caja en COP, si hay alguno publicado."""

    catalog = load_catalog()
    result = []
    for reference in catalog.references:
        if reference.country != "CO":
            continue
        if reference.price is None or reference.currency != "COP":
            offer = next((o for o in catalog.offers(reference) if o.currency == "COP"), None)
            if offer is not None:
                reference = reference.model_copy(
                    update={
                        "price": offer.amount,
                        "currency": "COP",
                        "tax_included": offer.tax_status == "incluido",
                    }
                )
        result.append(reference)
    return result


def catalog_photos(references: list[Reference]) -> dict[str, bytes]:
    """Fotos del catálogo (``imagenes/``) de las referencias que tienen una."""

    photos = {}
    for reference in references:
        image = catalog_image(reference.sku)
        if image is not None:
            photos[reference.sku] = image.path.read_bytes()
    return photos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="solo cuenta las referencias")
    parser.add_argument("--imagenes", action="store_true", help="sube las fotos que falten")
    args = parser.parse_args(argv)
    references = colombian_references()
    priced = sum(1 for reference in references if reference.price is not None)
    print(f"Referencias de Colombia: {len(references)} ({priced} con precio en COP)")
    photos = catalog_photos(references) if args.imagenes else {}
    if args.imagenes:
        print(f"Fotos en el catálogo: {len(photos)}")
    if args.dry_run:
        return 0
    orders = build_orders(Settings())
    if orders is None:
        print("Faltan ODOO_URL, ODOO_DB u ODOO_API_KEY.", file=sys.stderr)
        return 2
    report = orders.sync_catalog(references)
    if args.imagenes:
        report |= orders.sync_images(photos)
    print("Odoo:", ", ".join(f"{key} {value}" for key, value in report.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
