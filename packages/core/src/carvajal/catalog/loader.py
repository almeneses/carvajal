"""Carga y validación del catálogo curado de empaques.

Los CSV de ``data/catalog/empaques/`` se leen una sola vez por proceso, como
``data/indicators.json``. Una fila inválida detiene la carga con el archivo y la
línea exactos: un catálogo a medias recomendaría con datos incompletos.

``precios.csv`` (spec 039) es opcional: guarda las ofertas por fuente. Cada oferta debe
apuntar a una referencia existente del mismo país.
"""

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ValidationError

from carvajal.catalog.models import Distributor, MaterialRule, Reference, ReferencePrice

__all__ = ["DEFAULT_DIR", "Catalog", "CatalogError", "inline_offer", "load_catalog"]

DEFAULT_DIR = Path("data") / "catalog" / "empaques"
REFERENCES = "referencias.csv"
MATERIALS = "materiales.csv"
DISTRIBUTORS = "distribuidores.csv"
PRICES = "precios.csv"
MANIFEST = "manifest.json"
# Nombre de la fuente oficial cuando el precio viene inline en ``referencias.csv``.
OFFICIAL_SOURCE = {"MX": "Carvajal Empaques México", "CO": "Carvajal Empaques Colombia"}


class CatalogError(ValueError):
    """El catálogo no se puede cargar; el mensaje nombra archivo y línea."""


@dataclass(frozen=True)
class Catalog:
    """Catálogo validado y listo para las herramientas comerciales."""

    references: tuple[Reference, ...]
    materials: dict[str, MaterialRule]
    distributors: tuple[Distributor, ...]
    # Ofertas de ``precios.csv`` por (sku, país), en el orden del archivo (spec 039).
    prices: dict[tuple[str, str], tuple[ReferencePrice, ...]] = field(default_factory=dict)
    # Países presentes, calculados una vez al construir el catálogo.
    countries: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "countries", frozenset(ref.country for ref in self.references))

    def offers(self, reference: Reference) -> tuple[ReferencePrice, ...]:
        """Precios publicados para la referencia, uno por fuente (spec 039, FR-005).

        El precio inline de ``referencias.csv`` cuenta como la oferta oficial; si
        ``precios.csv`` ya trae una oferta oficial para la referencia, gana la del archivo
        de precios. Nunca se mezclan países: la clave incluye el país de la referencia.
        """

        explicit = self.prices.get((reference.sku, reference.country), ())
        inline = inline_offer(reference)
        if inline is None or any(offer.source_type == "oficial" for offer in explicit):
            return explicit
        return (inline, *explicit)


def inline_offer(reference: Reference) -> ReferencePrice | None:
    """El precio de la ficha como oferta oficial, o ``None`` si no publica precio."""

    if reference.price is None or reference.currency is None or reference.tax_included is None:
        return None
    return ReferencePrice(
        sku=reference.sku,
        country=reference.country,
        amount=reference.price,
        currency=reference.currency,
        tax_status="incluido" if reference.tax_included else "no_incluido",
        source_type="oficial",
        source_name=OFFICIAL_SOURCE[reference.country],
        valid_until=reference.price_valid_until,
        source_url=reference.source_url,
        captured_at=reference.captured_at,
    )


def _check_header[T: BaseModel](path: Path, model: type[T], header: Sequence[str]) -> None:
    """La cabecera debe listar exactamente los campos del modelo, en cualquier orden."""

    missing = [name for name in model.model_fields if name not in header]
    extra = [name for name in header if name not in model.model_fields]
    if not missing and not extra:
        return
    details: list[str] = []
    if missing:
        details.append(f"faltan {', '.join(missing)}")
    if extra:
        details.append(f"sobran {', '.join(extra)}")
    raise CatalogError(f"{path.name}: cabecera inválida: {'; '.join(details)}.")


def _read[T: BaseModel](path: Path, model: type[T]) -> tuple[tuple[int, T], ...]:
    """Valida la cabecera y cada fila y devuelve su línea física en el archivo.

    Se usa ``reader.line_num`` en vez de contar filas lógicas: una celda entrecomillada
    con saltos de línea ocupa varias líneas del archivo y el mensaje debe apuntar a una
    línea real (la cabecera es la línea 1).
    """

    if not path.is_file():
        raise CatalogError(f"{path}: no existe el archivo del catálogo.")
    rows: list[tuple[int, T]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _check_header(path, model, reader.fieldnames or [])
        for raw in reader:
            line = reader.line_num
            try:
                rows.append((line, model.model_validate(raw)))
            except ValidationError as error:
                first = error.errors()[0]
                field = ".".join(str(part) for part in first["loc"]) or "fila"
                raise CatalogError(f"{path.name}:{line}: {field}: {first['msg']}") from error
    if not rows:
        raise CatalogError(f"{path}: el archivo no tiene filas.")
    return tuple(rows)


def _check_manifest(path: Path) -> None:
    """Si hay manifiesto, debe ser JSON válido con sus secciones de fuentes (spec 039, T008)."""

    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CatalogError(
            f"{path.name}: JSON inválido ({error.msg}, línea {error.lineno})."
        ) from error
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        raise CatalogError(f"{path.name}: falta el objeto «sources» con la procedencia.")


@cache
def load_catalog(directory: str = str(DEFAULT_DIR)) -> Catalog:
    """Carga los tres CSV y verifica claves únicas.

    El resultado se cachea por directorio durante la vida del proceso; las pruebas
    limpian la caché con ``load_catalog.cache_clear()``.
    """

    base = Path(directory)
    _check_manifest(base / MANIFEST)
    references = _read(base / REFERENCES, Reference)
    materials = _read(base / MATERIALS, MaterialRule)
    distributors = _read(base / DISTRIBUTORS, Distributor)

    seen: dict[tuple[str, str], int] = {}
    for line, reference in references:
        key = (reference.sku, reference.country)
        if key in seen:
            raise CatalogError(
                f"{REFERENCES}:{line}: SKU repetido {reference.sku} en {reference.country} "
                f"(ya en la línea {seen[key]})."
            )
        seen[key] = line

    by_material: dict[str, MaterialRule] = {}
    first_line: dict[str, int] = {}
    for line, rule in materials:
        if rule.material in by_material:
            raise CatalogError(
                f"{MATERIALS}:{line}: material repetido {rule.material} "
                f"(ya en la línea {first_line[rule.material]})."
            )
        by_material[rule.material] = rule
        first_line[rule.material] = line

    prices: dict[tuple[str, str], list[ReferencePrice]] = {}
    if (base / PRICES).is_file():
        offers_seen: dict[tuple[str, str, str], int] = {}
        for line, offer in _read(base / PRICES, ReferencePrice):
            key = (offer.sku, offer.country)
            if key not in seen:
                raise CatalogError(
                    f"{PRICES}:{line}: precio huérfano: no hay referencia {offer.sku} "
                    f"en {offer.country}."
                )
            source = (offer.sku, offer.country, offer.source_url)
            if source in offers_seen:
                raise CatalogError(
                    f"{PRICES}:{line}: precio repetido de {offer.sku} para la misma fuente "
                    f"(ya en la línea {offers_seen[source]})."
                )
            offers_seen[source] = line
            prices.setdefault(key, []).append(offer)

    return Catalog(
        references=tuple(reference for _, reference in references),
        materials=by_material,
        distributors=tuple(distributor for _, distributor in distributors),
        prices={key: tuple(values) for key, values in prices.items()},
    )
