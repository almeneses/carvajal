"""Catálogo y libro comercial reales **sin** los precios proyectados (2026-11-26).

Desde la proyección ninguna referencia de Colombia queda sin precio. Las pruebas que cubren
el camino «sin precio» (declarar la ausencia, dar las sedes, no sustituir) lo hacen con los
datos reales menos esas proyecciones: el mismo catálogo que había antes de la decisión.
"""

from datetime import date
from functools import cache

from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.commercial.book import CommercialBook, build_commercial_book
from carvajal.commercial.loader import load_commercial_book
from carvajal.quotation.data import Dataset, dataset_from_book

__all__ = ["book_sin_proyecciones", "catalog_sin_proyecciones", "dataset_sin_proyecciones"]


def _projected(catalog: Catalog) -> set[str]:
    """SKU propios de Colombia cuyo único precio es el proyectado (``sistema``)."""

    return {
        reference.sku
        for reference in catalog.references
        if reference.country == "CO"
        and not reference.is_adapted
        and all(offer.source_type == "sistema" for offer in catalog.offers(reference))
    }


@cache
def catalog_sin_proyecciones() -> Catalog:
    catalog = load_catalog()
    projected = _projected(catalog)
    prices = {
        key: tuple(offer for offer in offers if offer.sku not in projected)
        for key, offers in catalog.prices.items()
    }
    return Catalog(
        references=catalog.references,
        materials=catalog.materials,
        distributors=catalog.distributors,
        prices={key: offers for key, offers in prices.items() if offers},
    )


@cache
def book_sin_proyecciones() -> CommercialBook:
    book = load_commercial_book()
    projected = _projected(load_catalog())
    return build_commercial_book(
        products=book.products.values(),
        sellers=book.sellers.values(),
        sites=book.sites.values(),
        offers=[offer for offer in book.offers if offer.sku not in projected],
        evidence=book.evidence,
    )


def dataset_sin_proyecciones(as_of: date) -> Dataset:
    return dataset_from_book(book_sin_proyecciones(), as_of=as_of)
