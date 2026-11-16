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
from collections.abc import Callable, Iterable
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
# Orden del contrato publicado en specs/036-asistente-empaques/plan.md y del
# archivo versionado: no depende del orden de declaración de ``Reference``.
_COLUMNS = (
    "sku",
    "name",
    "family",
    "use",
    "capacity_oz",
    "diameter_mm",
    "material",
    "brand",
    "units_per_pack",
    "packs_per_box",
    "units_per_box",
    "temp_min_c",
    "temp_max_c",
    "price",
    "currency",
    "tax_included",
    "price_valid_until",
    "country",
    "source_url",
    "captured_at",
)


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _plain(value)).strip("_")


def _first(rules: tuple[tuple[str, tuple[str, ...]], ...], text: str) -> str | None:
    """Primer código cuya clave inicia alguna palabra (admite plurales).

    Las claves de tres letras o menos (``ps``, ``pp``, ``pet``) exigen coincidencia
    exacta: ``startswith`` clasificaría como PS cualquier palabra que empiece por «ps».
    """

    words = re.findall(r"[a-z0-9]+", _plain(text))
    for code, keys in rules:
        if any(
            word == key or (len(key) > 3 and word.startswith(key)) for key in keys for word in words
        ):
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
        try:
            graph = json.loads(block)
        except json.JSONDecodeError as error:
            match = MX_PRODUCT.search(page)
            location = match.group(0) if match else "ficha sin URL"
            raise ValueError(f"{location}: bloque ld+json malformado ({error.msg})") from error
        if not isinstance(graph, dict):
            continue
        for node in graph.get("@graph", [graph]):
            if node.get("@type") == "Product":
                product = node
            elif node.get("@type") == "BreadcrumbList":
                crumbs = [_crumb(item) for item in node.get("itemListElement", [])]
    return product, crumbs


def _price_spec(product: dict[str, Any], captured_at: date) -> dict[str, Any]:
    """Primera especificación de precio vigente; ``ValueError`` con la URL si falta.

    Una oferta con ``validThrough`` anterior a la captura es una promoción vencida y no
    se toma como precio de referencia.
    """

    offers = product.get("offers")
    offers = offers if isinstance(offers, list) else [offers]
    candidates: list[dict[str, Any]] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        specs = offer.get("priceSpecification")
        specs = specs if isinstance(specs, list) else [specs]
        for spec in specs:
            if (
                isinstance(spec, dict)
                and spec.get("price") not in (None, "")
                and spec.get("priceCurrency")
            ):
                candidates.append(spec)
    vigentes = [spec for spec in candidates if _vigente(spec.get("validThrough"), captured_at)]
    if not vigentes:
        raise ValueError(f"{product.get('url')}: la ficha no publica precio ni moneda vigentes")
    return {**vigentes[0], "price": str(vigentes[0]["price"])}


def _vigente(raw: Any, captured_at: date) -> bool:
    """Una oferta sin fecha de vigencia o con fecha futura sigue siendo válida."""

    if not raw:
        return True
    try:
        return date.fromisoformat(str(raw)[:10]) >= captured_at
    except ValueError:
        return True  # fecha ilegible: se conserva lo publicado por la ficha


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
    location = str(product.get("url") or "ficha sin URL")
    if not product.get("name"):
        raise ValueError(f"{location}: la ficha no publica nombre")
    if not product.get("url"):
        raise ValueError(f"{location}: la ficha no publica URL")
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
    if temp_range is None and "temperatura" in _plain(short):
        notes.append(f"{product['url']}: temperatura en formato no reconocido; se omite")
    if temp_range and "bebida_caliente" in uses and temp_range[1] < 60:
        notes.append(
            f"{product['url']}: temperatura {temp_range} contradice «bebida caliente»; se omite"
        )
        temp_range = None
    if family == "tapa":
        capacity = None  # «12oz a 24oz» es compatibilidad, no capacidad

    spec = _price_spec(product, captured_at)
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
        # Se lee la página completa: la URL puede aparecer tras cabeceras o scripts
        # largos y descartarla por un límite fijo perdería la ficha en silencio.
        page = path.read_text(encoding="utf-8", errors="ignore")
        url = MX_PRODUCT.search(page)
        if url is None:
            continue
        try:
            reference = parse_mx_product(page, captured_at, notes)
        except ValueError as error:
            notes.append(str(error))
            continue
        if reference is None:
            notes.append(f"{url.group(0)}: no es una referencia por caja (combo o kit)")
            continue
        previous = by_sku.get(reference.sku)
        if previous is not None:
            notes.append(
                f"{reference.source_url}: SKU {reference.sku} repetido; "
                f"ya estaba en {previous.source_url}"
            )
            continue
        by_sku[reference.sku] = reference
    return [by_sku[sku] for sku in sorted(by_sku)], sorted(set(notes))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_references(
    directory: Path,
    mx: list[Reference],
    *,
    allow_missing: bool = False,
    notes: list[str] | None = None,
    manifest: tuple[str, dict[str, Any]] | None = None,
    check: Callable[[], None] | None = None,
) -> int:
    """Reescribe ``referencias.csv`` con las filas de México y conserva las demás.

    Las filas conservadas se validan antes de escribir y el archivo se reemplaza de una
    sola vez: un fallo deja intacto el original con las filas curadas a mano. Si el corpus
    deja fuera un SKU que ya estaba en el archivo, la orden aborta antes de escribir salvo
    que ``allow_missing`` lo autorice; en ese caso la baja se anota en ``notes``.

    ``manifest`` permite actualizar el manifiesto en el mismo paso: CSV y manifiesto se
    confirman juntos, y si un reemplazo falla se restaura el otro, de modo que nunca quedan
    desincronizados. ``notes`` recoge además los SKU retirados con ``allow_missing``.
    ``check`` se ejecuta tras los reemplazos: si lanza, ambos archivos se restauran.
    """

    target = directory / REFERENCES
    fields = list(_COLUMNS)
    kept: list[Reference] = []
    existing_mx: dict[str, Reference] = {}
    existing_lines: dict[str, int] = {}
    if target.is_file():
        with target.open(encoding="utf-8", newline="") as handle:
            for line, row in enumerate(csv.DictReader(handle), start=2):
                if row.get("country") == "MX":
                    try:  # una fila MX inválida también detiene todo antes de escribir
                        reference = Reference.model_validate(row)
                    except ValueError as error:
                        raise ValueError(f"{REFERENCES}:{line}: {error}") from error
                    if reference.sku in existing_mx:
                        raise ValueError(
                            f"{REFERENCES}:{line}: SKU repetido {reference.sku} "
                            f"(ya en la línea {existing_lines[reference.sku]})"
                        )
                    existing_mx[reference.sku] = reference
                    existing_lines[reference.sku] = line
                    continue
                try:  # una fila curada inválida detiene todo antes de tocar el archivo
                    kept.append(Reference.model_validate(row))
                except ValueError as error:
                    raise ValueError(f"{REFERENCES}:{line}: {error}") from error
    missing = sorted(set(existing_mx) - {reference.sku for reference in mx})
    if missing:
        listed = ", ".join(missing)  # listado completo: la baja debe ser auditable
        if not allow_missing:
            raise ValueError(
                f"{REFERENCES}: el corpus no incluye {len(missing)} referencia(s) ya presentes "
                f"({listed}); usa --allow-missing para aceptar la baja"
            )
        if notes is not None:
            notes.append(f"{REFERENCES}: baja autorizada de {len(missing)} referencia(s): {listed}")
    temporary = target.with_name(f".{target.name}.tmp")
    manifest_staged: tuple[Path, Path] | None = None
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for reference in [*mx, *kept]:
                row = reference.model_dump(mode="json")
                writer.writerow({key: _cell(row[key]) for key in fields})
        staged = [(temporary, target)]
        if manifest is not None:
            manifest_staged = _stage_manifest(directory, *manifest)
            staged.append(manifest_staged)
        # Un único punto de confirmación: si el segundo reemplazo falla, se restaura
        # el primero, así CSV y manifiesto nunca quedan desincronizados.
        _replace_all(staged, check)
    finally:
        temporary.unlink(missing_ok=True)  # un fallo a mitad no deja temporales huérfanos
        if manifest_staged is not None:
            manifest_staged[0].unlink(missing_ok=True)
    return len(mx) + len(kept)


def _replace_all(staged: list[tuple[Path, Path]], check: Callable[[], None] | None = None) -> None:
    """Reemplaza varios temporales, valida y restaura los destinos si algo falla."""

    backups: list[tuple[Path, Path | None]] = []
    replaced: list[Path] = []
    preservar: set[Path] = set()
    try:
        # Los respaldos se crean dentro del ``try``: si su escritura falla, el
        # ``finally`` borra los temporales y cualquier ``.bak`` parcial.
        for _, target in staged:
            backup = None
            if target.is_file():
                backup = target.with_name(f".{target.name}.bak")
                backups.append((target, backup))
                backup.write_bytes(target.read_bytes())
            else:
                backups.append((target, None))
        for temporary, target in staged:
            temporary.replace(target)
            replaced.append(target)
        if check is not None:
            check()  # validación posterior al reemplazo: un fallo deshace la transacción
    except Exception as error:
        for target, backup in backups:
            if target not in replaced:
                continue
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    backup.replace(target)
            except OSError as restore_error:
                # Se conserva el error original y se deja constancia del fallo de
                # restauración en su nota; nunca se silencia la causa inicial.
                error.add_note(f"no se pudo restaurar {target.name}: {restore_error}")
                if backup is not None:
                    preservar.add(backup)  # el respaldo es la única copia del original
        raise
    finally:
        for _, backup in backups:
            if backup is not None and backup not in preservar:
                backup.unlink(missing_ok=True)


def _stage_manifest(directory: Path, section: str, data: dict[str, Any]) -> tuple[Path, Path]:
    """Escribe ``manifest.json`` en un temporal y devuelve ``(temporal, destino)``."""

    target = directory / "manifest.json"
    manifest: dict[str, Any] = {}
    if target.is_file():
        manifest = json.loads(target.read_text(encoding="utf-8"))
    manifest.setdefault("spec", "036-asistente-empaques")
    manifest.setdefault("sources", {})[section] = data
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        temporary.unlink(missing_ok=True)  # una escritura fallida no deja temporales
        raise
    return temporary, target


def update_manifest(directory: Path, section: str, data: dict[str, Any]) -> Path:
    """Actualiza una sección de ``manifest.json`` y conserva las curadas a mano.

    El reemplazo es atómico: un fallo deja intacto el manifiesto anterior.
    """

    temporary, target = _stage_manifest(directory, section, data)
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
