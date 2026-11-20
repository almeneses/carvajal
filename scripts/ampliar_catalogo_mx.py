"""Amplía el catálogo de México con las fichas que no estaban en el corpus (spec 039).

El catálogo de la 036 se generó con las 55 fichas que traía el corpus del 2026-10-23; la
tienda publica 102 productos. Este script toma las fichas faltantes, capturadas el
2026-11-18 respetando ``robots.txt``, y las pasa por el **mismo** ``parse_mx_product`` de
``carvajal catalog-build``: no hay un segundo parser.

``fuentes/mx_ampliacion.json`` guarda, por ficha, solo lo que el parser lee: el bloque
JSON-LD del producto (nombre, URL, SKU y especificación de precio), el breadcrumb, la
descripción corta y si la página dice «IVA incluido». Con eso se rearma una página mínima.

Las referencias que salen del corpus (``catalog-build``) no se tocan; las nuevas entran con su
fecha de captura.

Uso: ``uv run python scripts/ampliar_catalogo_mx.py``.
"""

import html
import json
from datetime import date

from carvajal.catalog.build import parse_mx_product, write_references
from carvajal.catalog.loader import DEFAULT_DIR, load_catalog

SOURCE = DEFAULT_DIR / "fuentes" / "mx_ampliacion.json"
CAPTURED = date(2026, 11, 18)


def minimal_page(item: dict) -> str:
    """Página con las tres piezas que lee ``parse_mx_product``."""

    graph = {"@graph": [node for node in (item["p"], item["c"]) if node]}
    tax = " IVA incluido" if item["iva"] else ""
    return (
        f'<link rel="canonical" href="{item["u"]}">'
        f'<script type="application/ld+json">{json.dumps(graph, ensure_ascii=False)}</script>'
        f'<div class="woocommerce-product-details__short-description">'
        f"{html.escape(item['s'])} quantity</div>{tax}"
    )


def main() -> None:
    items = json.loads(SOURCE.read_text(encoding="utf-8"))
    load_catalog.cache_clear()
    catalog = load_catalog()
    # Las fichas del JSON se reconstruyen en cada corrida: así la orden es idempotente y el
    # manifiesto describe siempre la ampliación completa, no solo la última diferencia.
    urls = {item["u"] for item in items}
    existing = [
        ref for ref in catalog.references if ref.country == "MX" and ref.source_url not in urls
    ]
    known = {ref.sku for ref in existing}
    added = []
    notes: list[str] = []
    for item in items:
        try:
            reference = parse_mx_product(minimal_page(item), CAPTURED, notes)
        except ValueError as error:
            notes.append(str(error))
            continue
        if reference is None:
            notes.append(f"{item['u']}: no es una referencia por caja (combo o kit)")
            continue
        if reference.sku in known:
            notes.append(f"{item['u']}: SKU {reference.sku} ya estaba en el catálogo")
            continue
        known.add(reference.sku)
        added.append(reference)

    def _valida() -> None:
        load_catalog.cache_clear()
        load_catalog()

    total = write_references(
        DEFAULT_DIR,
        sorted([*existing, *added], key=lambda ref: ref.sku),
        notes=notes,
        manifest=(
            "referencias_mx_ampliacion",
            {
                "origin": (
                    "Tienda en línea de Carvajal Empaques México (fichas no incluidas en el corpus)"
                ),
                "generated_by": "scripts/ampliar_catalogo_mx.py con parse_mx_product",
                "raw": "fuentes/mx_ampliacion.json",
                "captured_at": CAPTURED.isoformat(),
                "pages_read": len(items),
                "rows": len(added),
                "notes": sorted(set(notes)),
            },
        ),
        check=_valida,
    )
    print(f"México: {len(added)} referencias nuevas (total en el archivo: {total}).")
    for note in sorted(set(notes)):
        print(f"  - {note}")


if __name__ == "__main__":
    main()
