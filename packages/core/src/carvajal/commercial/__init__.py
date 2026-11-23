"""Contrato comercial compartido por el chat general y el Cotizador.

Uso típico::

    from carvajal.commercial import load_commercial_book

    book = load_commercial_book()
    bogota = book.city_directory("Bogotá")
    ofertas = book.offers_for_sku("CO-CTT-08", "CO")

``city_directory`` funciona sin precios ni existencias; ``offers_for_sku`` incluye todas las
ofertas del SKU; ``sites_for_sku`` solo devuelve sedes vinculadas por oferta o stock.
"""

from carvajal.commercial.book import CommercialBook, ContractError, build_commercial_book
from carvajal.commercial.loader import (
    book_from_paths,
    load_commercial_book,
    offer_id_for,
    seller_id_for,
    site_id_for,
)
from carvajal.commercial.models import (
    Availability,
    AvailabilityClaim,
    AvailabilityEvidence,
    AvailabilityStatus,
    Basis,
    CityDirectory,
    CommercialRecord,
    ContractDocument,
    Country,
    Currency,
    DeliveryTerms,
    EvidenceKind,
    GeocodePrecision,
    GeoPoint,
    MapView,
    Offer,
    Origin,
    ParityReport,
    Presentation,
    PriceBasis,
    Product,
    RecordType,
    Seller,
    SellerKind,
    Site,
    SiteLink,
    SourceRef,
    SourceType,
    StockEvidence,
    TaxStatus,
    evidence_age_limit,
)
from carvajal.commercial.parity import mexico_parity

__all__ = [
    "Availability",
    "AvailabilityClaim",
    "AvailabilityEvidence",
    "AvailabilityStatus",
    "Basis",
    "CityDirectory",
    "CommercialBook",
    "CommercialRecord",
    "ContractDocument",
    "ContractError",
    "Country",
    "Currency",
    "DeliveryTerms",
    "EvidenceKind",
    "GeoPoint",
    "GeocodePrecision",
    "MapView",
    "Offer",
    "Origin",
    "ParityReport",
    "Presentation",
    "PriceBasis",
    "Product",
    "RecordType",
    "Seller",
    "SellerKind",
    "Site",
    "SiteLink",
    "SourceRef",
    "SourceType",
    "StockEvidence",
    "TaxStatus",
    "book_from_paths",
    "build_commercial_book",
    "evidence_age_limit",
    "load_commercial_book",
    "mexico_parity",
    "offer_id_for",
    "seller_id_for",
    "site_id_for",
]
