"""Copia el catálogo de México a Colombia con precios convertidos a COP.

Decisión del equipo del 2026-11-25 (uso académico): ver ``carvajal.catalog.conversion``. Toma
las referencias de México ya curadas en ``referencias.csv`` y escribe, en un solo paso:

- las copias ``CO-MX-<sku>`` al final de ``referencias.csv``;
- sus precios convertidos en ``precios.csv``, con procedencia ``sistema``;
- la sección ``referencias_co_desde_mx`` del manifiesto, con la tasa usada.

No toca las referencias ni los precios publicados. Es idempotente: con la misma tasa
(``fuentes/tasa_mxn_cop.json``) y las mismas fichas produce los mismos archivos.

Uso: ``uv run python scripts/adaptar_catalogo_mx.py``. Las fotos se bajan aparte con
``uv run --extra capture python scripts/curar_imagenes_co.py --desde-mx``.
"""

import csv

from carvajal.catalog.build import _replace_all, _stage_manifest
from carvajal.catalog.conversion import (
    ATTRIBUTES_FILE,
    EXTRA_CITIES,
    RATE_FILE,
    adapt_all,
    load_attributes,
    load_rate,
)
from carvajal.catalog.loader import DEFAULT_DIR, PRICES, REFERENCES, load_catalog
from carvajal.catalog.models import Reference, ReferencePrice


def _write(path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            data = row.model_dump(mode="json")
            writer.writerow({key: "" if data[key] is None else _cell(data[key]) for key in fields})


def _cell(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _header(path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def main() -> None:
    load_catalog.cache_clear()
    catalog = load_catalog()
    rate = load_rate(DEFAULT_DIR)
    attributes = load_attributes(DEFAULT_DIR)
    cities = sorted({item.city for item in catalog.distributors})
    copies, converted = adapt_all(catalog.references, rate, cities=cities, attributes=attributes)

    references: list[Reference] = [item for item in catalog.references if not item.is_adapted]
    published: list[ReferencePrice] = [
        offer
        for offers in catalog.prices.values()
        for offer in offers
        if offer.source_type != "sistema"
    ]
    # El mismo orden que ``carvajal catalog-prices``: regenerar uno no reordena el otro.
    prices = sorted(
        [*published, *converted],
        key=lambda item: (item.country, item.sku, item.source_url, item.source_city or ""),
    )
    targets = [
        (DEFAULT_DIR / REFERENCES, [*references, *copies]),
        (DEFAULT_DIR / PRICES, prices),
    ]
    staged = []

    def _valida() -> None:
        load_catalog.cache_clear()
        load_catalog()

    try:
        for target, rows in targets:
            temporary = target.with_name(f".{target.name}.tmp")
            staged.append((temporary, target))
            _write(temporary, _header(target), rows)
        staged.append(
            _stage_manifest(
                DEFAULT_DIR,
                "referencias_co_desde_mx",
                {
                    "origin": "Tienda de Carvajal Empaques México, copiada a Colombia",
                    "generated_by": "scripts/adaptar_catalogo_mx.py",
                    "raw": RATE_FILE.as_posix(),
                    "captured_at": rate.date.isoformat(),
                    "rows": len(copies),
                    "prices": len(converted),
                    "cities": cities,
                    "distribution": (
                        f"Todas en Bogotá y cada una en otras {EXTRA_CITIES} ciudades del "
                        "directorio, rotando dentro de cada familia."
                    ),
                    "attributes": ATTRIBUTES_FILE.as_posix(),
                    "attributes_applied": len(attributes),
                    "cop_per_mxn": str(rate.cop_per_mxn),
                    "authorization": (
                        "Decisión del equipo del 2026-11-25 para uso académico; enmienda la regla "
                        "de la spec 039 de no convertir precios desde México."
                    ),
                    "rule": (
                        "Precio por caja en MXN por la tasa, redondeado al peso. Procedencia "
                        "«sistema»: no es un precio publicado en Colombia y no tiene vendedor, "
                        "sede ni existencias. IVA «no_indicado»: el precio de origen incluye el "
                        "IVA de México."
                    ),
                },
            )
        )
        _replace_all(staged, _valida)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    print(
        f"Colombia: {len(copies)} referencias copiadas y {len(converted)} precios convertidos "
        f"en {len(cities)} ciudades "
        f"(1 MXN = {rate.cop_per_mxn} COP, {rate.date.isoformat()})."
    )


if __name__ == "__main__":
    main()
