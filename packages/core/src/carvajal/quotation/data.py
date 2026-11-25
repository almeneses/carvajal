"""Carga del read model comercial del Cotizador sobre el contrato publicado (spec 043).

El catálogo curado (``data/catalog/empaques``) aporta la ficha del producto;
``carvajal.commercial`` aporta la oferta publicada, la sede y la disponibilidad. No se leen
ni se generan datos simulados: una fila sin fuente publicada no entra al flujo público.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cache

from carvajal.catalog.loader import load_catalog
from carvajal.catalog.models import Reference
from carvajal.commercial import AvailabilityEvidence, DeliveryTerms
from carvajal.commercial.book import CommercialBook
from carvajal.commercial.loader import load_commercial_book
from carvajal.commercial.models import Offer as ContractOffer
from carvajal.commercial.models import Site

__all__ = [
    "REFERENCE_SITE_ID",
    "CommercialDataError",
    "Dataset",
    "Offer",
    "Product",
    "Provider",
    "dataset_from_book",
    "load_dataset",
]

COUNTRY = "CO"
# Precio convertido desde México (``catalog.conversion``): no lo vende ninguna sede. Se ofrece
# como referencia en todas las ciudades con este identificador, que no está en el directorio.
REFERENCE_SITE_ID = "CO-REFERENCIA"

# La ficha del producto es la referencia curada: no se duplica el catálogo.
Product = Reference


class CommercialDataError(ValueError):
    """El contrato comercial falta, no cierra o no cubre el país del Cotizador."""


@dataclass(frozen=True)
class Offer:
    """Precio publicado y disponibilidad de una sede para una referencia."""

    sku: str
    country: str
    site_id: str
    provider: str
    city: str
    basis: str
    units_in_basis: int
    amount: Decimal
    currency: str
    tax_status: str
    tax_rate: Decimal | None
    stock_status: str
    stock_units: int | None
    source_name: str
    source_url: str
    source_valid_until: date | None
    captured_at: date
    evidence: tuple[AvailabilityEvidence, ...] = ()
    delivery: DeliveryTerms | None = None

    @property
    def converted(self) -> bool:
        """Precio convertido desde México: referencia, no publicado por un vendedor."""

        return self.site_id == REFERENCE_SITE_ID

    @property
    def confirmed(self) -> bool:
        """Solo una existencia con evidencia vigente se presenta como confirmada."""

        return self.stock_status in {"verificado", "no_disponible"}


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
    _by_sku: dict[str, Product] = field(init=False, repr=False, compare=False)
    _by_provider: dict[str, Provider] = field(init=False, repr=False, compare=False)

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

    def providers_in(self, city: str) -> tuple[Provider, ...]:
        """Sedes del directorio en una ciudad, tengan o no precio publicado."""

        wanted = city.strip().casefold()
        return tuple(
            sorted(
                (provider for provider in self.providers if provider.city.casefold() == wanted),
                key=lambda item: (item.name.casefold(), item.provider_id),
            )
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_sku", {product.sku: product for product in self.products})
        object.__setattr__(
            self, "_by_provider", {provider.provider_id: provider for provider in self.providers}
        )


def _provider_from_site(site: Site) -> Provider:
    location = site.location
    return Provider(
        provider_id=site.site_id,
        name=site.name,
        city=site.city,
        address=site.address,
        phone=site.phone or None,
        latitude=location.latitude if location else None,
        longitude=location.longitude if location else None,
        precision=location.precision if location else "desconocida",
        source_url=site.source.url or "",
        captured_at=site.source.captured_at,
    )


def _offer_from_contract(
    offer: ContractOffer,
    *,
    site: Site,
    availability_status: str,
    stock_units: int | None,
    evidence: tuple[AvailabilityEvidence, ...],
) -> Offer:
    return Offer(
        sku=offer.sku,
        country=offer.country,
        site_id=site.site_id,
        provider=site.name,
        city=site.city,
        basis=offer.presentation.basis,
        units_in_basis=offer.presentation.units_in_basis,
        amount=offer.amount,
        currency=offer.currency,
        tax_status=offer.tax_status,
        tax_rate=offer.tax_rate,
        stock_status=availability_status,
        stock_units=stock_units,
        source_name=offer.source.name,
        source_url=offer.source.url or "",
        source_valid_until=offer.source.valid_until,
        captured_at=offer.source.captured_at,
        evidence=evidence,
        delivery=offer.delivery,
    )


def dataset_from_book(book: CommercialBook, *, as_of: date) -> Dataset:
    """Adapta un libro comercial ya validado al read model del motor.

    La fecha fija con la que se degrada la evidencia de stock vencida o sin vigencia.
    """
    catalog = load_catalog()
    known = set(book.products)
    products = tuple(
        reference
        for reference in catalog.references
        if reference.country == COUNTRY and (reference.sku, reference.country) in known
    )
    providers = tuple(
        _provider_from_site(site)
        for site in sorted(book.sites.values(), key=lambda item: item.site_id)
        if site.country == COUNTRY
    )
    offers: list[Offer] = []
    for offer in book.offers:
        if offer.country != COUNTRY or offer.site_id is None:
            continue
        site = book.site(offer.site_id)
        if site is None:
            raise CommercialDataError(f"la oferta {offer.offer_id} cita una sede inexistente")
        record = book.offer_record(offer, as_of=as_of)
        availability = record.availability
        if availability is None:
            raise CommercialDataError(f"la oferta {offer.offer_id} no declara disponibilidad")
        offers.append(
            _offer_from_contract(
                offer,
                site=site,
                availability_status=availability.status,
                stock_units=availability.units,
                evidence=availability.evidence,
            )
        )
    offers.extend(_converted_offers(book, {provider.city for provider in providers}))
    if not products:
        raise CommercialDataError("el catálogo curado no tiene referencias de Colombia")
    return Dataset(products=products, offers=tuple(offers), providers=providers)


def _converted_offers(book: CommercialBook, cities: set[str]) -> list[Offer]:
    """Precios convertidos desde México, uno por ciudad: la referencia no depende de una sede."""

    return [
        Offer(
            sku=offer.sku,
            country=offer.country,
            site_id=REFERENCE_SITE_ID,
            provider=offer.source.name,
            city=city,
            basis=offer.presentation.basis,
            units_in_basis=offer.presentation.units_in_basis,
            amount=offer.amount,
            currency=offer.currency,
            tax_status=offer.tax_status,
            tax_rate=offer.tax_rate,
            stock_status=offer.availability.status,
            stock_units=None,
            source_name=offer.source.name,
            source_url=offer.source.url or "",
            source_valid_until=offer.source.valid_until,
            captured_at=offer.source.captured_at,
        )
        for offer in book.offers
        if offer.country == COUNTRY and offer.source.type == "sistema"
        for city in sorted(cities)
    ]


@cache
def load_dataset(as_of: str | None = None) -> Dataset:
    """Carga el catálogo y las ofertas publicadas, y verifica sus uniones.

    ``as_of`` fija la fecha con la que se evalúa la vigencia de la evidencia de stock.
    """

    day = date.fromisoformat(as_of) if as_of else date.today()
    return dataset_from_book(load_commercial_book(), as_of=day)
