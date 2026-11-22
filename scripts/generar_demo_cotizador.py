"""Genera el conjunto de demostración del Cotizador (spec 042).

Combina lo que está publicado con lo que la demo necesita:

- Productos reales: las 102 referencias de Colombia y las 98 de México del catálogo
  curado, sin tocar precios ni fichas.
- Productos simulados: 48 referencias equivalentes a las de México que Colombia no
  publica, para llegar a 150 productos colombianos de demostración. Cada una queda
  marcada con ``origin=simulada``.
- Ofertas: solo de Colombia, con el precio real cuando existe (EDEXA) y uno simulado
  cuando no. Existencias, plazo y envío son siempre simulados. Las 98 referencias de
  México no se cotizan: solo prestan ficha y capacidad a las 48 simuladas.
- Sedes: el directorio real, con coordenadas aproximadas de OpenStreetMap.

Los archivos se escriben en ``data/demo/cotizador/``. No son fuente de RAG ni se
mezclan con el catálogo curado.
"""

import csv
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

CATALOG = Path("data") / "catalog" / "empaques"
DEMO = Path("data") / "demo" / "cotizador"
GEOCODE = DEMO / "geocode_sedes.json"

# Precio base simulado por unidad, en COP. Solo se usa cuando ninguna fuente publica
# precio; el resultado se redondea a decenas y se marca como simulado.
BASE_COP = {
    "vaso": 75,
    "tapa": 50,
    "contenedor": 190,
    "plato": 95,
    "charola": 130,
    "cubierto": 60,
    "envase": 160,
}
PRODUCT_COLUMNS = (
    "sku",
    "country",
    "name",
    "family",
    "brand",
    "material",
    "capacity_oz",
    "units_per_pack",
    "packs_per_box",
    "units_per_box",
    "temp_min_c",
    "temp_max_c",
    "use",
    "origin",
    "source_url",
    "captured_at",
    "note",
)
OFFER_COLUMNS = (
    "sku",
    "country",
    "provider_id",
    "provider",
    "city",
    "basis",
    "units_in_basis",
    "amount",
    "currency",
    "tax_status",
    "valid_until",
    "stock_units",
    "stock_status",
    "lead_days_min",
    "lead_days_max",
    "ship_cost",
    "simulated",
    "source_url",
    "captured_at",
)
PROVIDER_COLUMNS = (
    "provider_id",
    "name",
    "city",
    "address",
    "phone",
    "latitude",
    "longitude",
    "precision",
    "source_url",
    "captured_at",
    "simulated",
)
SIMULATED_TARGET = 48
SHIP_BY_CITY = {"Bogotá": 12000, "Medellín": 18000, "Cali": 18000}
SHIP_OTHER = 25000
LEAD_BY_CITY = {"Bogotá": (1, 2), "Medellín": (1, 3), "Cali": (2, 3)}
LEAD_OTHER = (2, 4)


def _read(name: str) -> list[dict[str, str]]:
    with (CATALOG / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _seed(value: str) -> int:
    """Entero estable por SKU: la simulación se repite igual en cada corrida."""

    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def _mix(family: str) -> int:
    """Mezcla la familia para variar el precio del tema."""

    return sum(family.encode("utf-8")) % 7 * 5


def _simulated_price(family: str, units_in_basis: int, sku: str) -> int:
    """Precio simulado por presentación, en COP, a partir de un costo unitario base."""

    unit = BASE_COP.get(family, 100) + _mix(family) + _seed(sku) % 11
    return int(Decimal(unit * units_in_basis).quantize(Decimal("10")))


def _co_products(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    products = []
    for row in rows:
        if row["country"] != "CO":
            continue
        products.append(
            {
                "sku": row["sku"],
                "country": "CO",
                "name": row["name"],
                "family": row["family"],
                "brand": row["brand"],
                "material": row["material"],
                "capacity_oz": row["capacity_oz"],
                "units_per_pack": row["units_per_pack"],
                "packs_per_box": row["packs_per_box"],
                "units_per_box": row["units_per_box"],
                "temp_min_c": row["temp_min_c"],
                "temp_max_c": row["temp_max_c"],
                "use": row["use"],
                "origin": "real",
                "source_url": row["source_url"],
                "captured_at": row["captured_at"],
                "note": "",
            }
        )
    return products


def _simulated_co(rows: list[dict[str, str]], real: list[dict[str, str]]) -> list[dict[str, str]]:
    """48 referencias equivalentes de México que Colombia no publica.

    La familia que Colombia no tiene (cubierto) entra primero; el resto conserva
    capacidad y material de la ficha mexicana. El SKU es propio de la demo.
    """

    existing = {item["name"].strip().lower() for item in real}
    candidates = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if row["country"] != "MX" or row["name"].strip().lower() in existing:
            continue
        # El catálogo mexicano repite fichas casi idénticas; la demo toma una por variante.
        key = (row["name"].strip().lower(), row["capacity_oz"], row["family"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(row)
    candidates.sort(
        key=lambda row: (
            row["family"] != "cubierto",
            row["material"] not in {"PAPEL", "PULPA"},
            row["family"],
            row["sku"],
        )
    )
    simulated = []
    for index, row in enumerate(candidates[:SIMULATED_TARGET], start=1):
        simulated.append(
            {
                "sku": f"DEMO-CO-{index:03d}",
                "country": "CO",
                "name": row["name"],
                "family": row["family"],
                "brand": row["brand"],
                "material": row["material"],
                "capacity_oz": row["capacity_oz"],
                "units_per_pack": row["units_per_pack"],
                "packs_per_box": row["packs_per_box"],
                "units_per_box": row["units_per_box"],
                "temp_min_c": row["temp_min_c"],
                "temp_max_c": row["temp_max_c"],
                "use": row["use"],
                "origin": "simulada",
                "source_url": "",
                "captured_at": datetime.now(UTC).date().isoformat(),
                "note": (
                    "Producto de demostración: equivalente de una referencia publicada en "
                    f"México ({row['sku']}), no publicada en Colombia. No es una oferta real."
                ),
            }
        )
    if len(simulated) < SIMULATED_TARGET:
        raise SystemExit(
            f"Solo hay {len(simulated)} candidatas de México; se necesitan {SIMULATED_TARGET}."
        )
    return simulated


def _providers() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    geocoded = json.loads(GEOCODE.read_text(encoding="utf-8"))
    rows = []
    for position, item in enumerate(geocoded, start=1):
        rows.append(
            {
                "provider_id": f"p{position:02d}",
                "name": item["name"],
                "city": item["city"],
                "address": item["address"],
                "phone": item["phone"],
                "latitude": item["lat"] or "",
                "longitude": item["lon"] or "",
                "precision": item["precision"],
                "source_url": item["source_url"],
                "captured_at": item["captured_at"],
                "simulated": "false",
            }
        )
    return rows, geocoded


def _real_co_prices() -> dict[str, dict[str, str]]:
    """Precio publicado por EDEXA, con la presentación tal como la publica la tienda."""

    with (CATALOG / "fuentes" / "precios_co.csv").open(encoding="utf-8", newline="") as handle:
        return {row["sku"]: row for row in csv.DictReader(handle)}


def _offer(
    product: dict[str, str],
    provider: dict[str, str],
    *,
    index: int,
    price: dict[str, str] | None,
) -> dict[str, str]:
    """Una oferta por producto y sede, con precio real o simulado y stock simulado."""

    units = int(product["units_per_box"] or 0)
    basis, units_in_basis = "caja", units
    amount = _simulated_price(product["family"], units_in_basis, product["sku"])
    taxed, simulated = "incluido", ["price", "stock", "lead_days", "ship_cost"]
    if price is not None:
        basis = price["basis"]
        units_in_basis = int(price["units_in_basis"])
        amount = int(Decimal(price["amount"]))
        taxed = price["tax_status"]
        simulated = ["stock", "lead_days", "ship_cost"]

    bucket = index % 11
    if index % 17 == 0:
        stock_units, stock_status = 0, "agotado"
    elif bucket == 0:
        stock_units, stock_status = max(1, units_in_basis // 2), "insuficiente"
    elif bucket == 5:
        stock_units, stock_status = None, "desconocido"
    else:
        stock_units, stock_status = units_in_basis * (1 + _seed(product["sku"]) % 12), "disponible"

    low, high = LEAD_BY_CITY.get(provider["city"], LEAD_OTHER)
    return {
        "sku": product["sku"],
        "country": "CO",
        "provider_id": provider["provider_id"],
        "provider": provider["name"],
        "city": provider["city"],
        "basis": basis,
        "units_in_basis": str(units_in_basis),
        "amount": str(amount),
        "currency": "COP",
        "tax_status": taxed,
        "valid_until": "",
        "stock_units": "" if stock_units is None else str(stock_units),
        "stock_status": stock_status,
        "lead_days_min": str(low),
        "lead_days_max": str(high),
        "ship_cost": str(SHIP_BY_CITY.get(provider["city"], SHIP_OTHER)),
        "simulated": "|".join(simulated),
        "source_url": price["source_url"] if price else "",
        "captured_at": price["captured_at"] if price else datetime.now(UTC).date().isoformat(),
    }


def _ficha(offer: dict[str, str], product: dict[str, str], provider: dict[str, str]) -> str:
    """Documento citable de una oferta: procedencia, presentación y condiciones.

    El motor no lee este Markdown; es la ficha que la persona puede abrir para auditar
    de dónde salió el precio, la sede y qué campos son simulados.
    """

    simulated = offer["simulated"].split("|") if offer["simulated"] else []
    precio = (
        f"{offer['amount']} {offer['currency']} por {offer['basis']} "
        f"de {offer['units_in_basis']} unidades"
    )
    front = {
        "sku": product["sku"],
        "name": product["name"],
        "provider": provider["name"],
        "provider_id": provider["provider_id"],
        "city": provider["city"],
        "source_url": offer["source_url"],
        "captured_at": offer["captured_at"],
        "origin": product["origin"],
        "simulated_fields": ", ".join(simulated) or "ninguno",
    }
    lines = ["---"]
    lines += [f"{key}: {value}" for key, value in front.items()]
    lines += [
        "---",
        "",
        f"# {product['name']}",
        "",
        f"- **Marca**: {product['brand'] or 'sin marca declarada'}",
        f"- **Material**: {product['material']}",
        f"- **Capacidad**: {product['capacity_oz'] or 'no declarada'} oz",
        f"- **Caja**: {product['units_per_box']} unidades",
        f"- **Presentación ofertada**: {precio}",
        f"- **Existencia**: {offer['stock_status']}"
        + (f" ({offer['stock_units']} unidades)" if offer["stock_units"] else ""),
        f"- **Entrega estimada**: {offer['lead_days_min']} a {offer['lead_days_max']} días hábiles",
        f"- **Sede**: {provider['name']} — {provider['address'] or 'sin dirección'} "
        f"({provider['phone'] or 'sin teléfono'})",
        f"- **Procedencia**: {offer['source_url'] or 'sin publicación colombiana'}, "
        f"capturada el {offer['captured_at']}",
        "",
        "## Campos simulados",
        "",
        "La demo marca campo por campo: " + (", ".join(simulated) or "ninguno") + ".",
        "Los precios publicados no se estiman, convierten ni promedian.",
        "",
    ]
    if product.get("note"):
        lines.append(f"> {product['note']}")
        lines.append("")
    return "\n".join(lines)


def _write_fichas(
    offers: list[dict[str, str]],
    products: dict[str, dict[str, str]],
    providers: dict[str, dict[str, str]],
) -> int:
    """Una ficha Markdown por oferta colombiana; reemplaza las anteriores."""

    folder = DEMO / "fichas"
    folder.mkdir(parents=True, exist_ok=True)
    for previous in folder.glob("*.md"):
        previous.unlink()
    written = 0
    for offer in offers:
        if offer["country"] != "CO":
            continue
        product = products[offer["sku"]]
        provider = providers[offer["provider_id"]]
        slug = f"{offer['sku']}-{offer['provider_id']}"
        (folder / f"{slug}.md").write_text(_ficha(offer, product, provider), encoding="utf-8")
        written += 1
    return written


def _write(name: str, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with (DEMO / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    DEMO.mkdir(parents=True, exist_ok=True)
    references = _read("referencias.csv")
    co_real = _co_products(references)
    mx_rows = [row for row in references if row["country"] == "MX"]
    co_simulated = _simulated_co(references, co_real)
    co_products = co_real + co_simulated

    providers, _ = _providers()
    priced = _real_co_prices()
    edexa = next(item for item in providers if item["name"] == "EDEXA")
    others = [item for item in providers if item["name"] != "EDEXA"]
    offers: list[dict[str, str]] = []
    for index, product in enumerate(co_products):
        price = priced.get(product["sku"])
        if price is not None:
            offers.append(_offer(product, edexa, index=index, price=price))
            continue
        provider = others[index % len(others)]
        offers.append(_offer(product, provider, index=index, price=None))

    # Comparación de sedes: una segunda oferta, en otra ciudad, para diez referencias.
    first_city = {offer["sku"]: offer["city"] for offer in offers}
    comparison = [item for item in co_products if item["origin"] == "real"][:10]
    for offset, product in enumerate(comparison):
        elsewhere = [item for item in others if item["city"] != first_city[product["sku"]]]
        provider = elsewhere[offset % len(elsewhere)]
        offer = _offer(product, provider, index=offset + 7, price=priced.get(product["sku"]))
        # Otra sede no siempre cobra lo mismo: se varía entre -8 % y +6 % la presentación.
        factor = Decimal("0.92") + Decimal(offset % 8) / Decimal("100")
        offer["amount"] = str(int((Decimal(offer["amount"]) * factor).quantize(Decimal("10"))))
        if "price" not in offer["simulated"].split("|"):
            offer["simulated"] = "price|" + offer["simulated"]
        offers.append(offer)

    # Solo la extensión simulada vive en la demo: los 102 productos reales se leen del
    # catálogo curado y no se duplican aquí. Las ofertas son solo de Colombia: el motor
    # no cotiza otros países y una fila en MXN nunca sería elegible.
    _write("productos.csv", PRODUCT_COLUMNS, co_simulated)
    _write("ofertas.csv", OFFER_COLUMNS, offers)
    _write("proveedores.csv", PROVIDER_COLUMNS, providers)
    fichas = _write_fichas(
        offers,
        {product["sku"]: product for product in co_products},
        {provider["provider_id"]: provider for provider in providers},
    )

    manifest: dict[str, Any] = {
        "spec": "042-cotizador",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_by": "scripts/generar_demo_cotizador.py",
        "banner": (
            "Modo demo: existencias, plazos y entregas son simulados. Los productos "
            "marcados como simulados tampoco están publicados en Colombia."
        ),
        "counts": {
            "productos_co": len(co_products),
            "productos_co_reales": len(co_real),
            "productos_co_simulados": len(co_simulated),
            "productos_mx": len(mx_rows),
            "ofertas_co": len(offers),
            "sedes": len(providers),
            "fichas": fichas,
        },
        "sources": {
            "productos_reales": {
                "origin": "data/catalog/empaques/referencias.csv (specs 036 y 039)",
                "captured_at": "2026-11-18",
                "rule": (
                    "No se copian: el Cotizador los lee del catálogo curado. Este archivo "
                    "solo contiene las 48 referencias simuladas que Colombia no publica."
                ),
            },
            "precios_co": {
                "origin": "data/catalog/empaques/fuentes/precios_co.csv",
                "captured_at": "2026-11-18",
                "source": "EDEXA (distribuidor del directorio oficial)",
                "rule": "Precio por paquete o caja tal como lo publica la tienda; no se convierte.",
            },
            "precios_mx": {
                "origin": "data/catalog/empaques/referencias.csv",
                "captured_at": "2026-10-23 / 2026-11-18",
                "source": "Tienda en línea de Carvajal Empaques México",
                "rule": (
                    "Precio oficial por caja en MXN: solo documenta el origen de las 48 "
                    "referencias simuladas. No se escribe en el dataset ni se convierte."
                ),
            },
            "sedes": {
                "origin": "data/catalog/empaques/distribuidores.csv",
                "captured_at": "2026-10-23",
                "geocoding": "OpenStreetMap Nominatim, 2026-11-21",
                "rule": (
                    "Dirección y teléfono son del directorio oficial; latitud y longitud son "
                    "aproximadas y conservan su precisión (direccion, via, ciudad)."
                ),
            },
        },
        "simulation_rules": {
            "productos": (
                "48 referencias equivalentes a fichas de México se presentan como oferta "
                "colombiana de demostración; no están publicadas en Colombia."
            ),
            "precio": (
                "Costo unitario base por familia (COP) por las unidades de la presentación, "
                "redondeado a decenas. El precio real de EDEXA no se toca."
            ),
            "stock": (
                "Agotado cada 17 ofertas, insuficiente cada 11, desconocido cada 11, "
                "disponible el resto; las unidades salen de un hash estable del SKU."
            ),
            "entrega": (
                "Días hábiles con festivos de Colombia, corte a las 15:00, sin contar el día "
                "del pedido. Envío 12.000 COP en Bogotá, 18.000 en Medellín y Cali, 25.000 en "
                "el resto; gratis desde 500.000 COP (regla del motor, no de la fila)."
            ),
        },
        "limitations": [
            "Las existencias y los plazos no provienen de ningún proveedor real.",
            "Los productos simulados no existen en el portafolio publicado de Colombia.",
            "Las coordenadas son aproximadas; 'ciudad' significa centroide y no una sede.",
            "Los precios de EDEXA son de referencia y no confirman disponibilidad.",
            "Este conjunto no se indexa en Qdrant ni se cita como fuente del corpus.",
        ],
    }
    (DEMO / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
