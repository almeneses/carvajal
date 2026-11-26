"""Regeneración de los precios por fuente del catálogo (spec 039, T018-T019).

Las capturas de precios viven en ``fuentes/precios_<país>.csv`` tal como las publica cada
fuente: por unidad, por paquete o por caja. ``carvajal catalog-prices`` las normaliza a
precio por caja con las unidades de la referencia y escribe ``precios.csv`` junto con la
sección del manifiesto, en un solo paso atómico.

Reglas (FR-004, FR-006, FR-009):

- Un precio por paquete o por unidad solo se normaliza si la caja contiene un número entero
  de esas presentaciones; si no, se descarta con el motivo.
- Un precio «por caja» cuya caja no coincide con la de la referencia se descarta.
- Nunca se convierte de moneda ni se promedia: cada fuente conserva su propia oferta.
- La orden es idempotente: con las mismas fuentes produce el mismo archivo.
"""

import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from carvajal.catalog.build import _replace_all, _stage_manifest
from carvajal.catalog.loader import PRICES, CatalogError, load_catalog
from carvajal.catalog.models import (
    Country,
    Currency,
    PriceSource,
    Reference,
    ReferencePrice,
    TaxStatus,
)

__all__ = ["PriceBuild", "RawPrice", "build_prices", "normalize", "write_prices"]

SOURCES_DIR = "fuentes"
PRICE_FIELDS = (
    "sku",
    "country",
    "amount",
    "currency",
    "tax_status",
    "source_type",
    "source_name",
    "source_city",
    "valid_until",
    "source_url",
    "captured_at",
)


class RawPrice(BaseModel):
    """Un precio tal como lo publica la fuente, antes de normalizarlo a caja."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    sku: str = Field(min_length=1)
    country: Country
    amount: Decimal = Field(ge=0)
    currency: Currency
    basis: Literal["unidad", "paquete", "caja"]
    units_in_basis: int = Field(gt=0)
    tax_status: TaxStatus
    source_type: PriceSource
    source_name: str = Field(min_length=1)
    source_city: str | None = None
    source_url: str
    captured_at: date
    valid_until: date | None = None

    @model_validator(mode="before")
    @classmethod
    def _vacios_a_none(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {key: (None if value == "" else value) for key, value in data.items()}
        return data


@dataclass
class PriceBuild:
    """Resultado de normalizar las capturas de un país."""

    country: str
    offers: list[ReferencePrice] = field(default_factory=list)
    discarded: list[dict[str, str]] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    changed: list[dict[str, str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def normalize(raw: RawPrice, reference: Reference) -> ReferencePrice | str:
    """Precio por caja de la referencia, o el motivo por el que no se puede publicar."""

    if raw.basis == "caja":
        if raw.units_in_basis != reference.units_per_box:
            return (
                f"la caja de la fuente trae {raw.units_in_basis} unidades y la de la "
                f"referencia {reference.units_per_box}"
            )
        amount = raw.amount
    else:
        per = 1 if raw.basis == "unidad" else raw.units_in_basis
        if reference.units_per_box % per:
            return (
                f"la caja ({reference.units_per_box} unidades) no contiene un número entero "
                f"de {raw.basis}s de {per}"
            )
        amount = raw.amount * (reference.units_per_box // per)
    return ReferencePrice(
        sku=raw.sku,
        country=raw.country,
        amount=amount,
        currency=raw.currency,
        tax_status=raw.tax_status,
        source_type=raw.source_type,
        source_name=raw.source_name,
        source_city=raw.source_city,
        valid_until=raw.valid_until,
        source_url=raw.source_url,
        captured_at=raw.captured_at,
    )


def _read_raw(path: Path) -> list[tuple[int, RawPrice]]:
    if not path.is_file():
        raise CatalogError(f"{path}: no existe el archivo de capturas de precios.")
    rows: list[tuple[int, RawPrice]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            try:
                rows.append((reader.line_num, RawPrice.model_validate(raw)))
            except ValidationError as error:
                first = error.errors()[0]
                where = ".".join(str(part) for part in first["loc"]) or "fila"
                raise CatalogError(
                    f"{path.name}:{reader.line_num}: {where}: {first['msg']}"
                ) from error
    return rows


def _key(offer: ReferencePrice) -> tuple[str, str, str, str]:
    return (offer.country, offer.sku, offer.source_url, offer.source_city or "")


def _existing(directory: Path) -> list[ReferencePrice]:
    path = directory / PRICES
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [ReferencePrice.model_validate(row) for row in csv.DictReader(handle)]


def build_prices(directory: Path, country: str) -> PriceBuild:
    """Normaliza ``fuentes/precios_<país>.csv`` y calcula las diferencias con lo publicado."""

    load_catalog.cache_clear()
    catalog = load_catalog(str(directory))
    references = {(ref.sku, ref.country): ref for ref in catalog.references}
    raw_path = directory / SOURCES_DIR / f"precios_{country.lower()}.csv"
    result = PriceBuild(country=country)
    seen: set[tuple[str, str, str, str]] = set()
    for line, raw in _read_raw(raw_path):
        where = f"{raw_path.name}:{line} {raw.sku}"
        if raw.country != country:
            result.discarded.append(
                {"row": where, "reason": f"es de {raw.country}, no de {country}"}
            )
            continue
        reference = references.get((raw.sku, raw.country))
        if reference is None:
            result.discarded.append(
                {"row": where, "reason": f"no hay referencia {raw.sku} en {country}"}
            )
            continue
        offer = normalize(raw, reference)
        if isinstance(offer, str):
            result.discarded.append({"row": where, "reason": offer})
            continue
        if _key(offer) in seen:
            result.discarded.append(
                {"row": where, "reason": "precio repetido para la misma fuente"}
            )
            continue
        seen.add(_key(offer))
        result.offers.append(offer)
    result.offers.sort(key=_key)

    before = {
        _key(offer): offer
        for offer in _existing(directory)
        if offer.country == country and offer.source_type != "sistema"
    }
    after = {_key(offer): offer for offer in result.offers}
    for key in sorted(after.keys() - before.keys()):
        result.added.append(f"{key[1]} · {after[key].source_name}")
    for key in sorted(before.keys() - after.keys()):
        result.removed.append(f"{key[1]} · {before[key].source_name}")
    for key in sorted(after.keys() & before.keys()):
        old, new = before[key], after[key]
        if old.amount != new.amount or old.tax_status != new.tax_status:
            result.changed.append(
                {
                    "sku": key[1],
                    "source": new.source_name,
                    "before": f"{old.amount} {old.currency} ({old.captured_at.isoformat()})",
                    "after": f"{new.amount} {new.currency} ({new.captured_at.isoformat()})",
                }
            )
    return result


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def write_prices(directory: Path, build: PriceBuild) -> int:
    """Escribe ``precios.csv`` y la sección del manifiesto juntos, o ninguno.

    Conserva las ofertas de otros países y las convertidas por el sistema, que regenera
    ``scripts/adaptar_catalogo_mx.py``. Tras el reemplazo se vuelve a cargar el catálogo:
    si no valida, ambos archivos se restauran.
    """

    others = [
        offer
        for offer in _existing(directory)
        if offer.country != build.country or offer.source_type == "sistema"
    ]
    offers = sorted([*others, *build.offers], key=_key)
    target = directory / PRICES
    temporary = target.with_name(f".{target.name}.tmp")
    captured = max((offer.captured_at for offer in build.offers), default=None)
    section = {
        "origin": "Tiendas en línea de distribuidores del directorio oficial (precio publicado)",
        "generated_by": "carvajal catalog-prices",
        "raw": f"{SOURCES_DIR}/precios_{build.country.lower()}.csv",
        "captured_at": captured.isoformat() if captured else None,
        "rows": len(build.offers),
        "sources": sorted({offer.source_name for offer in build.offers}),
        "rule": (
            "Solo precios publicados para el país; no se estima, no se convierte de moneda "
            "ni se promedia."
        ),
        "discarded": build.discarded,
        "changes": {"added": build.added, "changed": build.changed, "removed": build.removed},
    }
    manifest_staged: tuple[Path, Path] | None = None

    def _check() -> None:
        load_catalog.cache_clear()
        load_catalog(str(directory))

    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(PRICE_FIELDS), lineterminator="\n")
            writer.writeheader()
            for offer in offers:
                row = offer.model_dump(mode="json")
                writer.writerow({name: _cell(row[name]) for name in PRICE_FIELDS})
        manifest_staged = _stage_manifest(directory, f"precios_{build.country.lower()}", section)
        _replace_all([(temporary, target), manifest_staged], _check)
    finally:
        temporary.unlink(missing_ok=True)
        if manifest_staged is not None:
            manifest_staged[0].unlink(missing_ok=True)
    return len(offers)
