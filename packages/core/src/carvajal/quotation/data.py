"""Carga del read model comercial del Cotizador (spec 042).

El catálogo curado (``data/catalog/empaques``) es la única fuente de productos; los CSV de
``data/demo/cotizador/`` solo añaden la extensión simulada y las condiciones comerciales
(ofertas, existencias, envío y sedes). Una fila inválida detiene la carga: cotizar con
datos a medias es peor que no cotizar.
"""

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

from carvajal.catalog.loader import load_catalog
from carvajal.catalog.models import Reference

__all__ = [
    "DEFAULT_DIR",
    "Dataset",
    "DemoDataError",
    "Offer",
    "Product",
    "Provider",
    "load_dataset",
]

DEFAULT_DIR = Path("data") / "demo" / "cotizador"
EXTRAS = "productos.csv"
OFFERS = "ofertas.csv"
PROVIDERS = "proveedores.csv"
MANIFEST = "manifest.json"
COUNTRY = "CO"


class DemoDataError(ValueError):
    """El conjunto de demostración falta o está incompleto."""


@dataclass(frozen=True)
class Product:
    """Referencia cotizable, real o de demostración."""

    sku: str
    country: str
    name: str
    family: str
    brand: str | None
    material: str
    capacity_oz: float | None
    units_per_pack: int | None
    packs_per_box: int | None
    units_per_box: int
    temp_min_c: float | None
    temp_max_c: float | None
    use: str
    origin: str
    source_url: str
    captured_at: date
    note: str

    @property
    def uses(self) -> tuple[str, ...]:
        """Usos declarados por la ficha, separados por «|»."""

        return tuple(part.strip() for part in self.use.split("|") if part.strip())

    @property
    def simulated(self) -> bool:
        """El producto no existe en el portafolio publicado de Colombia."""

        return self.origin == "simulada"


@dataclass(frozen=True)
class Offer:
    """Precio y existencias de una sede para una referencia."""

    sku: str
    country: str
    provider_id: str
    provider: str
    city: str
    basis: str
    units_in_basis: int
    amount: Decimal
    currency: str
    tax_status: str
    stock_units: int | None
    stock_status: str
    lead_days_min: int
    lead_days_max: int
    ship_cost: Decimal | None
    simulated_fields: frozenset[str]
    source_url: str
    captured_at: date

    @property
    def price_simulated(self) -> bool:
        """El precio no lo publica ninguna fuente: lo pone la demo."""

        return "price" in self.simulated_fields

    @property
    def stock_simulated(self) -> bool:
        """La existencia es simulada (siempre lo es en Colombia)."""

        return "stock" in self.simulated_fields


@dataclass(frozen=True)
class Provider:
    """Sede del directorio con su precisión de geocodificación."""

    provider_id: str
    name: str
    city: str
    address: str | None
    phone: str | None
    latitude: float | None
    longitude: float | None
    precision: str
    source_url: str
    captured_at: date


@dataclass(frozen=True)
class Dataset:
    """Conjunto validado y listo para el motor."""

    products: tuple[Product, ...]
    offers: tuple[Offer, ...]
    providers: tuple[Provider, ...]
    banner: str
    free_from: Decimal
    _by_sku: dict[str, "Product"] = field(init=False, repr=False, compare=False)
    _by_provider: dict[str, "Provider"] = field(init=False, repr=False, compare=False)

    def product(self, sku: str) -> Product | None:
        """Producto por SKU."""

        return self._by_sku.get(sku)

    def provider(self, provider_id: str) -> Provider | None:
        """Sede por identificador."""

        return self._by_provider.get(provider_id)

    def offers_for(self, sku: str) -> tuple[Offer, ...]:
        """Ofertas de una referencia."""

        return tuple(offer for offer in self.offers if offer.sku == sku)

    def cities(self) -> tuple[str, ...]:
        """Ciudades con al menos una sede."""

        return tuple(sorted({provider.city for provider in self.providers}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_sku", {product.sku: product for product in self.products})
        object.__setattr__(
            self, "_by_provider", {provider.provider_id: provider for provider in self.providers}
        )


def _value(raw: str) -> str | None:
    """Celda vacía como ``None``."""

    text = raw.strip()
    return text or None


def _decimal(raw: str) -> Decimal | None:
    text = _value(raw)
    return None if text is None else Decimal(text)


def _int(raw: str) -> int | None:
    text = _value(raw)
    return None if text is None else int(text)


def _float(raw: str) -> float | None:
    text = _value(raw)
    return None if text is None else float(text)


def _date(raw: str, where: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise DemoDataError(f"{where}: fecha inválida {raw!r}.") from error


def _product_from_reference(reference: Reference) -> Product:
    """Referencia del catálogo curado como producto cotizable."""

    return Product(
        sku=reference.sku,
        country=reference.country,
        name=reference.name,
        family=reference.family,
        brand=reference.brand,
        material=reference.material,
        capacity_oz=reference.capacity_oz,
        units_per_pack=reference.units_per_pack,
        packs_per_box=reference.packs_per_box,
        units_per_box=reference.units_per_box,
        temp_min_c=reference.temp_min_c,
        temp_max_c=reference.temp_max_c,
        use=reference.use,
        origin="real",
        source_url=reference.source_url,
        captured_at=reference.captured_at,
        note="",
    )


def _read(directory: Path, name: str) -> list[dict[str, str]]:
    path = directory / name
    if not path.is_file():
        raise DemoDataError(f"{path}: falta el archivo de la demo.")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise DemoDataError(f"{path}: el archivo no tiene filas.")
    return rows


@cache
def load_dataset(directory: str = str(DEFAULT_DIR)) -> Dataset:
    """Carga productos, ofertas y sedes, y verifica sus uniones."""

    base = Path(directory)
    catalog = load_catalog()
    products = [
        _product_from_reference(reference)
        for reference in catalog.references
        if reference.country == COUNTRY
    ]
    for index, row in enumerate(_read(base, EXTRAS), start=2):
        where = f"{EXTRAS}:{index}"
        products.append(
            Product(
                sku=row["sku"],
                country=row["country"],
                name=row["name"],
                family=row["family"],
                brand=_value(row["brand"]),
                material=row["material"],
                capacity_oz=_float(row["capacity_oz"]),
                units_per_pack=_int(row["units_per_pack"]),
                packs_per_box=_int(row["packs_per_box"]),
                units_per_box=int(row["units_per_box"]),
                temp_min_c=_float(row["temp_min_c"]),
                temp_max_c=_float(row["temp_max_c"]),
                use=row["use"],
                origin=row["origin"] or "simulada",
                source_url=row["source_url"],
                captured_at=_date(row["captured_at"], where),
                note=row["note"],
            )
        )
    providers = [
        Provider(
            provider_id=row["provider_id"],
            name=row["name"],
            city=row["city"],
            address=_value(row["address"]),
            phone=_value(row["phone"]),
            latitude=_float(row["latitude"]),
            longitude=_float(row["longitude"]),
            precision=row["precision"],
            source_url=row["source_url"],
            captured_at=_date(row["captured_at"], f"{PROVIDERS}:{index}"),
        )
        for index, row in enumerate(_read(base, PROVIDERS), start=2)
    ]
    offers = []
    for index, row in enumerate(_read(base, OFFERS), start=2):
        where = f"{OFFERS}:{index}"
        offers.append(
            Offer(
                sku=row["sku"],
                country=row["country"],
                provider_id=row["provider_id"],
                provider=row["provider"],
                city=row["city"],
                basis=row["basis"],
                units_in_basis=int(row["units_in_basis"]),
                amount=Decimal(row["amount"]),
                currency=row["currency"],
                tax_status=row["tax_status"],
                stock_units=_int(row["stock_units"]),
                stock_status=row["stock_status"],
                lead_days_min=_int(row["lead_days_min"]) or 0,
                lead_days_max=_int(row["lead_days_max"]) or 0,
                ship_cost=_decimal(row["ship_cost"]),
                simulated_fields=frozenset(field for field in row["simulated"].split("|") if field),
                source_url=row["source_url"],
                captured_at=_date(row["captured_at"], where),
            )
        )
    known_skus = {(product.sku, product.country) for product in products}
    known_skus |= {(reference.sku, reference.country) for reference in catalog.references}
    for offer in offers:
        if (offer.sku, offer.country) not in known_skus:
            raise DemoDataError(f"{OFFERS}: oferta huérfana de {offer.sku}.")
    known_providers = {provider.provider_id for provider in providers}
    for offer in offers:
        if offer.country == COUNTRY and offer.provider_id not in known_providers:
            raise DemoDataError(f"{OFFERS}: sede desconocida {offer.provider_id}.")

    manifest = json.loads((base / MANIFEST).read_text(encoding="utf-8"))
    return Dataset(
        products=tuple(products),
        offers=tuple(offers),
        providers=tuple(providers),
        banner=str(manifest.get("banner", "")),
        free_from=Decimal("500000"),
    )
