"""Construcción de las referencias de México desde el corpus capturado (T007).

Cada ficha de la tienda de Carvajal Empaques México trae dos fuentes de datos: el
bloque ``application/ld+json`` (SKU, precio, moneda, IVA) y la descripción corta
(marca, material, temperatura de uso y unidades por caja). Solo se usa la
biblioteca estándar. Las filas de otros países se curan a mano y se conservan al
regenerar el archivo.
"""

import csv
import html
import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from carvajal.catalog.loader import REFERENCES
from carvajal.catalog.models import Reference

__all__ = ["build_mx_references", "parse_mx_product", "update_manifest", "write_references"]

MX_PRODUCT = re.compile(r"https://mexico\.carvajalempaques\.com/producto/[^\"'\s]+")
_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_SHORT = re.compile(
    r"woocommerce-product-details__short-description\">(.*?)(?:quantity|</form>)", re.S
)
_FIELDS = {
    "brand": r"Marca:\s*(.+?)\s+Materiales:",
    "materials": r"Materiales:\s*(.+?)\s+(?:Temperatura|Color|Cantidad)",
    "temp": r"Temperatura de uso recomendada:\s*Desde\s*(-?\d+)\s*°?\s*C\s*/\s*(-?\d+)\s*°?\s*C",
    "units": r"Cantidad por (?:caja|paquete):\s*(\d+)",
}
_FAMILIES = (
    ("contenedor", ("contenedor", "delibisagra", "hamburguesero")),
    ("cubierto", ("cuchara", "cuchillo", "tenedor")),
    ("vaso", ("vaso",)),
    ("tapa", ("tapa",)),
    ("charola", ("charola",)),
    ("envase", ("envase",)),
    ("plato", ("plato",)),
)
_MATERIALS = (
    ("EPS", ("expandido", "espumado", "eps", "foam", "unicel")),
    ("PULPA", ("pulpa", "fibra", "bagazo")),
    ("PAPEL", ("papel", "cañatek", "canatek")),
    ("ALUMINIO", ("aluminio",)),
    ("PET", ("pet",)),
    ("PP", ("polipropileno", "pp")),
    ("PS", ("poliestireno", "ps", "gpps")),
)


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _plain(value)).strip("_")


def _first(rules: tuple[tuple[str, tuple[str, ...]], ...], text: str) -> str | None:
    """Primer código cuya clave inicia alguna palabra (admite plurales)."""

    words = re.findall(r"[a-z0-9]+", _plain(text))
    for code, keys in rules:
        if any(word.startswith(key) for key in keys for word in words):
            return code
    return None


def _crumb(item: dict[str, Any]) -> str:
    inner = item.get("item")
    name = item.get("name") or (inner.get("name") if isinstance(inner, dict) else "")
    return html.unescape(str(name or ""))


def _product_ld(page: str) -> tuple[dict[str, Any], list[str]]:
    """Bloque ``Product`` y nombres del breadcrumb."""

    product: dict[str, Any] = {}
    crumbs: list[str] = []
    for block in _LD.findall(page):
        graph = json.loads(block)
        for node in graph.get("@graph", [graph]):
            if node.get("@type") == "Product":
                product = node
            elif node.get("@type") == "BreadcrumbList":
                crumbs = [_crumb(item) for item in node.get("itemListElement", [])]
    return product, crumbs


def parse_mx_product(
    page: str, captured_at: date, warnings: list[str] | None = None
) -> Reference | None:
    """Convierte una ficha en ``Reference``; ``None`` si no es un producto unitario.

    Las contradicciones de la ficha se anotan en ``warnings`` y el dato dudoso no se usa.
    """

    notes = warnings if warnings is not None else []
    product, crumbs = _product_ld(page)
    if not product.get("sku"):
        return None
    name = html.unescape(str(product["name"]))
    family = _first(_FAMILIES, name)
    if family is None or _plain(name).startswith(("combo", "kit")):
        return None  # combos y kits: no son una referencia vendible por caja
    match = _SHORT.search(page)
    short = (
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))) if match else ""
    )
    found = {key: re.search(rx, short) for key, rx in _FIELDS.items()}
    material = _first(
        _MATERIALS, (found["materials"].group(1) if found["materials"] else "") + " " + name
    )
    if material is None:
        raise ValueError(f"{product.get('url')}: material no reconocido")

    capacity = re.search(r"(\d+(?:[.,]\d+)?)\s*_?\s*oz", name + " " + short, re.I)
    diameter = re.search(r"(\d+(?:\.\d+)?)\s*mm", name + " " + short, re.I)
    both = name + " " + short
    packs = re.search(r"(?:caja|paquete)\s*(\d+)\s*x\s*(\d+)\s*-?\s*(\d+)?", both, re.I)
    per_box = (int(packs.group(1)), int(packs.group(2))) if packs else None
    units: int | None = None
    if per_box and packs and packs.group(3) and per_box[0] * per_box[1] == int(packs.group(3)):
        units = int(packs.group(3))  # «caja 20x20 400»: el desglose y el total coinciden
    elif found["units"]:
        units = int(found["units"].group(1))
    else:
        box = re.search(r"caja con\s*(\d+)", both, re.I)
        units = int(box.group(1)) if box else None
    if units is None:
        raise ValueError(f"{product.get('url')}: sin unidades por caja")
    if per_box and per_box[0] * per_box[1] != units:
        per_box = None  # el desglose no cuadra con el total: se conserva solo el total
    titled = re.search(r"caja con\s*(\d+)", name, re.I)
    if titled and int(titled.group(1)) != units:
        notes.append(
            f"{product['url']}: el título dice {titled.group(1)} unidades y la descripción "
            f"{units}; se usa la descripción"
        )

    uses = [_slug(crumbs[2])] if len(crumbs) > 3 else []
    text = _plain(name + " " + short)
    if "bebida caliente" in text:
        uses.append("bebida_caliente")
    elif family == "vaso":
        uses.append("bebida_fria")
    if "termic" in text:
        uses.append("comida_caliente")

    temp_range = (
        (float(found["temp"].group(1)), float(found["temp"].group(2))) if found["temp"] else None
    )
    if temp_range and "bebida_caliente" in uses and temp_range[1] < 60:
        notes.append(
            f"{product['url']}: temperatura {temp_range} contradice «bebida caliente»; se omite"
        )
        temp_range = None
    if family == "tapa":
        capacity = None  # «12oz a 24oz» es compatibilidad, no capacidad

    spec = product["offers"][0]["priceSpecification"][0]
    # El JSON-LD de WooCommerce marca ``valueAddedTaxIncluded: false`` por defecto;
    # manda el texto visible junto al precio.
    tax = bool(spec.get("valueAddedTaxIncluded")) or "iva incluido" in _plain(page)
    return Reference.model_validate(
        {
            "sku": str(product["sku"]),
            "name": name,
            "family": family,
            "use": "|".join(dict.fromkeys(uses)) or "general",
            "capacity_oz": float(capacity.group(1).replace(",", ".")) if capacity else None,
            "diameter_mm": float(diameter.group(1)) if diameter else None,
            "material": material,
            "brand": found["brand"].group(1).title() if found["brand"] else None,
            "units_per_pack": per_box[1] if per_box else None,
            "packs_per_box": per_box[0] if per_box else None,
            "units_per_box": units,
            "temp_min_c": temp_range[0] if temp_range else None,
            "temp_max_c": temp_range[1] if temp_range else None,
            "price": spec["price"],
            "currency": spec["priceCurrency"],
            "tax_included": tax,
            "price_valid_until": spec.get("validThrough"),
            "country": "MX",
            "source_url": str(product["url"]),
            "captured_at": captured_at,
        }
    )


def build_mx_references(
    pages: Iterable[Path], captured_at: date
) -> tuple[list[Reference], list[str]]:
    """Fichas de México: una referencia por SKU y las observaciones (descartes y avisos)."""

    by_sku: dict[str, Reference] = {}
    notes: list[str] = []
    for path in pages:
        with path.open("rb") as handle:  # la cabecera basta para descartar otras páginas
            url = MX_PRODUCT.search(handle.read(8000).decode("utf-8", errors="ignore"))
        if url is None:
            continue
        page = path.read_text(encoding="utf-8", errors="ignore")
        try:
            reference = parse_mx_product(page, captured_at, notes)
        except ValueError as error:
            notes.append(str(error))
            continue
        if reference is None:
            notes.append(f"{url.group(0)}: no es una referencia por caja (combo o kit)")
            continue
        by_sku.setdefault(reference.sku, reference)
    return [by_sku[sku] for sku in sorted(by_sku)], sorted(set(notes))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_references(directory: Path, mx: list[Reference]) -> int:
    """Reescribe ``referencias.csv`` con las filas de México y conserva las demás."""

    target = directory / REFERENCES
    provenance = ("source_url", "captured_at")
    fields = [name for name in Reference.model_fields if name not in provenance]
    fields += list(provenance)
    kept: list[dict[str, str]] = []
    if target.is_file():
        with target.open(encoding="utf-8", newline="") as handle:
            kept = [row for row in csv.DictReader(handle) if row.get("country") != "MX"]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for reference in mx:
            row = reference.model_dump(mode="json")
            writer.writerow({key: _cell(row[key]) for key in fields})
        for row in kept:
            writer.writerow({key: row.get(key, "") for key in fields})
    return len(mx) + len(kept)


def update_manifest(directory: Path, section: str, data: dict[str, Any]) -> Path:
    """Actualiza una sección de ``manifest.json`` y conserva las curadas a mano."""

    target = directory / "manifest.json"
    manifest: dict[str, Any] = {}
    if target.is_file():
        manifest = json.loads(target.read_text(encoding="utf-8"))
    manifest.setdefault("spec", "036-asistente-empaques")
    manifest.setdefault("sources", {})[section] = data
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
