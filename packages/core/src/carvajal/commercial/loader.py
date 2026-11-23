"""Carga del contrato comercial desde los datos publicados y versionados.

No crea un catálogo paralelo: reutiliza ``data/catalog/empaques`` para productos, precios y
directorio, y ``data/commercial/proveedores.csv`` para la geocodificación trazable. La
evidencia de existencias vive en ``data/commercial/evidencia_disponibilidad.csv`` y es la
única vía para que una sede pase de «por confirmar» a «verificado».

Identificadores estables:

- ``CO-VEND-…`` / ``MX-VEND-…``: hash de país y nombre normalizado del vendedor.
- ``CO-SEDE-…`` / ``MX-SEDE-…``: hash de país, vendedor, nombre, ciudad y dirección (o
  teléfono si la fuente no publica dirección).
- ``CO-OFERTA-…`` / ``MX-OFERTA-…``: hash del contenido de la oferta, incluidos importe y
  fecha.

Los identificadores de sede y vendedor no dependen del orden del archivo ni de la fecha de
captura: recapturar el directorio no los cambia; corregir la dirección o el nombre de la sede
sí, y por eso la sede conserva ``legacy_provider_id`` mientras dure la migración. El de la
oferta identifica una observación fechada: cambia si cambia el importe o la fecha, que es
justamente lo que un consumidor necesita distinguir.
"""

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import cast

from carvajal.catalog.loader import load_catalog
from carvajal.catalog.models import Reference
from carvajal.commercial.book import CommercialBook, ContractError, build_commercial_book, plain
from carvajal.commercial.models import (
    Availability,
    AvailabilityClaim,
    AvailabilityEvidence,
    Basis,
    Country,
    EvidenceKind,
    GeocodePrecision,
    GeoPoint,
    NormalizedPrice,
    Offer,
    Presentation,
    PriceBasis,
    Product,
    Seller,
    Site,
    SourceRef,
    StockEvidence,
)

__all__ = [
    "CATALOG_DIR",
    "COMMERCIAL_DIR",
    "EVIDENCE_PATH",
    "PROVIDERS_PATH",
    "book_from_paths",
    "commercial_version",
    "load_commercial_book",
    "offer_id_for",
    "seller_id_for",
    "site_id_for",
]

CATALOG_DIR = Path("data") / "catalog" / "empaques"
COMMERCIAL_DIR = Path("data") / "commercial"
PROVIDERS_PATH = COMMERCIAL_DIR / "proveedores.csv"
EVIDENCE_PATH = COMMERCIAL_DIR / "evidencia_disponibilidad.csv"
DEFAULT_GEOCODED_AT = date(2026, 11, 21)
GEOCODER = "OpenStreetMap Nominatim"
OFFICIAL_SELLER = {"MX": "Carvajal Empaques México", "CO": "Carvajal Empaques Colombia"}


def commercial_version() -> str:
    """Versión del aprovisionamiento publicado: la captura más reciente.

    Es la misma fecha que compara el bootstrap contra ``EXPECTED_COMMERCIAL_VERSION``;
    exponerla en ``/health`` permite verificar la versión desplegada sin entrar al
    contenedor. Si no hay fechas legibles devuelve ``desconocida``.
    """

    dates: list[str] = []
    manifest_path = CATALOG_DIR / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        dates.extend(
            str(item.get("captured_at"))
            for item in (manifest.get("sources") or {}).values()
            if isinstance(item, dict) and item.get("captured_at")
        )
    if PROVIDERS_PATH.is_file():
        with PROVIDERS_PATH.open(encoding="utf-8", newline="") as handle:
            dates.extend(
                row["captured_at"] for row in csv.DictReader(handle) if row.get("captured_at")
            )
    return max(dates) if dates else "desconocida"


_EVIDENCE_KINDS = {
    "inventario_vendedor",
    "confirmacion_vendedor",
    "integracion_pos",
    "pedido_confirmado",
}


@dataclass(frozen=True)
class _Provider:
    provider_id: str
    name: str
    city: str
    address: str
    phone: str
    latitude: float | None
    longitude: float | None
    precision: str
    source_url: str
    captured_at: date
    line: int


def _digest(parts: tuple[str, ...], size: int) -> str:
    text = "\x1f".join(parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size].upper()


def seller_id_for(country: str, name: str) -> str:
    """Identificador estable del vendedor, independiente de sus sedes."""

    return f"{country}-VEND-{_digest((country, plain(name)), 8)}"


def site_id_for(
    country: str, seller_id: str, name: str, city: str, address: str, phone: str
) -> str:
    """Identificador estable de una sede a partir de su identidad publicada.

    La fuente, la fecha y el teléfono no entran en el hash: recapturar el directorio o
    cambiar el número de contacto no debe renombrar la sede. La dirección es el ancla; si
    la fuente no la publica, se usa el teléfono como último recurso. El vendedor entra en
    la identidad porque dos marcas pueden compartir una misma dirección física.
    """

    normalized_address = plain(address)
    anchor = normalized_address if normalized_address else f"tel:{plain(phone)}"
    identity = (country, seller_id, plain(name), plain(city), anchor)
    return f"{country}-SEDE-{_digest(identity, 10)}"


def offer_id_for(
    *,
    country: str,
    sku: str,
    seller_id: str,
    site_id: str | None,
    source_url: str | None,
    basis: str,
    units: int,
    amount: Decimal,
    captured_at: date,
) -> str:
    """Identificador estable de una observación publicada.

    El sufijo ``publicada`` se conserva en el hash para que los identificadores emitidos
    antes de retirar la demo sigan siendo válidos; dos ofertas del mismo SKU no colisionan y
    una recaptura con otro importe o fecha es una observación nueva.
    """

    return f"{country}-OFERTA-{
        _digest(
            (
                country,
                sku,
                seller_id,
                site_id or '',
                source_url or '',
                basis,
                str(units),
                str(amount),
                captured_at.isoformat(),
                'publicada',
            ),
            12,
        )
    }"


def _read_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    if not path.is_file():
        raise ContractError(f"{path}: falta el archivo del contrato comercial.")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [(reader.line_num, dict(row)) for row in reader]


def _value(raw: str | None) -> str | None:
    text = (raw or "").strip()
    return text or None


def _decimal(raw: str | None) -> Decimal | None:
    text = _value(raw)
    return None if text is None else Decimal(text)


def _int(raw: str | None) -> int | None:
    text = _value(raw)
    return None if text is None else int(text)


def _float(raw: str | None) -> float | None:
    text = _value(raw)
    return None if text is None else float(text)


def _date(raw: str | None, where: str) -> date:
    text = _value(raw)
    if text is None:
        raise ContractError(f"{where}: falta la fecha.")
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ContractError(f"{where}: fecha inválida {text!r}.") from error


def _providers(providers_path: Path) -> list[_Provider]:
    providers: list[_Provider] = []
    for line, row in _read_rows(providers_path):
        where = f"proveedores.csv:{line}"
        providers.append(
            _Provider(
                provider_id=row["provider_id"],
                name=row["name"],
                city=row["city"],
                address=row.get("address", ""),
                phone=row.get("phone", ""),
                latitude=_float(row.get("latitude")),
                longitude=_float(row.get("longitude")),
                precision=(row.get("precision") or "desconocida").strip(),
                source_url=row["source_url"],
                captured_at=_date(row["captured_at"], where),
                line=line,
            )
        )
    return providers


def _match_provider(
    index: dict[tuple[str, ...], _Provider], row: dict[str, str]
) -> _Provider | None:
    name, city = plain(row["name"]), plain(row["city"])
    address = plain(row.get("address", ""))
    # Sin fallback por «nombre + ciudad»: Sideral tiene dos sucursales en Bogotá y elegir
    # la primera atribuiría la geocodificación a la sede equivocada.
    keys = (
        (name, city, address, row.get("phone", "")),
        (name, city, address),
    )
    for key in keys:
        found = index.get(key)
        if found is not None:
            return found
    return None


def _site_candidates(
    catalog_dir: Path,
    providers_path: Path,
) -> tuple[list[Site], dict[str, str], list[Seller]]:
    """Sedes del directorio, mapa provider_id→site_id y vendedores del directorio."""

    providers = _providers(providers_path)
    index: dict[tuple[str, ...], _Provider] = {}
    for provider in providers:
        name, city = plain(provider.name), plain(provider.city)
        address = plain(provider.address)
        index[(name, city, address, provider.phone)] = provider
        index.setdefault((name, city, address), provider)

    directory = _read_rows(catalog_dir / "distribuidores.csv")
    candidates: list[tuple[str, Site, _Provider | None]] = []
    for line, row in directory:
        where = f"distribuidores.csv:{line}"
        provider = _match_provider(index, row)
        country = "CO"
        seller_id = seller_id_for(country, row["name"])
        base = site_id_for(
            country,
            seller_id,
            row["name"],
            row["city"],
            row.get("address", ""),
            row.get("phone", ""),
        )
        location = None
        if (
            provider is not None
            and provider.latitude is not None
            and provider.longitude is not None
        ):
            location = GeoPoint(
                latitude=provider.latitude,
                longitude=provider.longitude,
                precision=cast(GeocodePrecision, provider.precision),
                geocoder=GEOCODER,
                geocoded_at=DEFAULT_GEOCODED_AT,
            )
        site = Site(
            site_id=base,
            legacy_provider_id=provider.provider_id if provider else None,
            seller_id=seller_id,
            country=country,
            name=row["name"],
            city=row["city"],
            address=_value(row.get("address")),
            phone=row.get("phone", ""),
            source=SourceRef(
                type="directorio",
                name="Directorio de Distribuidores Carvajal Empaques Colombia",
                url=row["source_url"],
                captured_at=_date(row["captured_at"], where),
            ),
            location=location,
        )
        candidates.append((base, site, provider))

    # Colisión de hash: se resuelve de forma determinista, no por orden del archivo.
    groups: dict[str, list[tuple[str, Site, _Provider | None]]] = {}
    for item in candidates:
        groups.setdefault(item[0], []).append(item)
    unique: dict[str, Site] = {}
    provider_to_site: dict[str, str] = {}
    for base, group in groups.items():
        group.sort(key=lambda item: (plain(item[1].name), plain(item[1].city), item[1].phone))
        for position, (_, site, provider) in enumerate(group, start=1):
            site_id = base if position == 1 else f"{base}-{position}"
            final = site.model_copy(update={"site_id": site_id})
            unique[site_id] = final
            if provider is not None:
                provider_to_site[provider.provider_id] = site_id

    sellers: dict[str, Seller] = {}
    for site in unique.values():
        sellers.setdefault(
            site.seller_id,
            Seller(
                seller_id=site.seller_id,
                name=site.name,
                country=site.country,
                kind="distribuidor",
                source=site.source,
            ),
        )
    return (
        sorted(unique.values(), key=lambda item: item.site_id),
        provider_to_site,
        list(sellers.values()),
    )


def _raw_presentations(path: Path) -> dict[tuple[str, str], PriceBasis]:
    """Precio tal como lo publica la fuente, para no perder paquete/unidad al normalizar."""

    if not path.is_file():
        return {}
    result: dict[tuple[str, str], PriceBasis] = {}
    for _, row in _read_rows(path):
        result[(row["sku"], row["source_url"])] = PriceBasis(
            basis=cast(Basis, row["basis"]),
            units_in_basis=int(row["units_in_basis"]),
            amount=_decimal(row["amount"]) or Decimal(0),
        )
    return result


def _product_from_reference(reference: Reference) -> Product:
    return Product(
        sku=reference.sku,
        country=reference.country,
        name=reference.name,
        family=reference.family,
        units_per_box=reference.units_per_box,
        source=SourceRef(
            type="oficial",
            name=OFFICIAL_SELLER[reference.country],
            url=reference.source_url,
            captured_at=reference.captured_at,
            valid_until=reference.price_valid_until,
            note="Ficha del catálogo curado.",
        ),
    )


def _inline_mx_offers(catalog) -> tuple[list[Offer], Seller]:
    seller_id = seller_id_for("MX", OFFICIAL_SELLER["MX"])
    seller_source: SourceRef | None = None
    offers: list[Offer] = []
    for reference in catalog.references:
        if reference.country != "MX" or reference.price is None:
            continue
        if reference.currency is None or reference.tax_included is None:
            raise ContractError(f"referencia MX {reference.sku} con precio incompleto")
        source = SourceRef(
            type="oficial",
            name=OFFICIAL_SELLER["MX"],
            url=reference.source_url,
            captured_at=reference.captured_at,
            valid_until=reference.price_valid_until,
            note="Precio por caja publicado en la ficha.",
        )
        seller_source = seller_source or source
        presentation = Presentation(basis="caja", units_in_basis=reference.units_per_box)
        amount = reference.price
        offers.append(
            Offer(
                offer_id=offer_id_for(
                    country="MX",
                    sku=reference.sku,
                    seller_id=seller_id,
                    site_id=None,
                    source_url=reference.source_url,
                    basis=presentation.basis,
                    units=presentation.units_in_basis,
                    amount=amount,
                    captured_at=reference.captured_at,
                ),
                sku=reference.sku,
                country="MX",
                seller_id=seller_id,
                city=None,
                presentation=presentation,
                amount=amount,
                currency="MXN",
                tax_status="incluido" if reference.tax_included else "no_incluido",
                tax_rate=None,
                source=source,
                availability=Availability.unknown(
                    note="La ficha publica precio; no declara existencias."
                ),
                note="Lectura inline de México conservada como oferta oficial.",
            )
        )
    if seller_source is None:
        seller_source = SourceRef(
            type="oficial",
            name=OFFICIAL_SELLER["MX"],
            url="https://mexico.carvajalempaques.com/",
            captured_at=DEFAULT_GEOCODED_AT,
        )
    seller = Seller(
        seller_id=seller_id,
        name=OFFICIAL_SELLER["MX"],
        country="MX",
        kind="fabricante",
        source=seller_source,
    )
    return offers, seller


def _published_co_offers(
    catalog,
    *,
    catalog_dir: Path,
    sites: list[Site],
    seller_map: dict[str, Seller],
) -> list[Offer]:
    """Ofertas de ``precios_co.csv`` con la presentación que publicó el vendedor."""

    raw_by_url = _raw_presentations(catalog_dir / "fuentes" / "precios_co.csv")
    offers: list[Offer] = []
    for reference_price in sorted(
        (item for values in catalog.prices.values() for item in values),
        key=lambda item: (item.country, item.sku, item.source_name),
    ):
        reference = next(
            (
                item
                for item in catalog.references
                if item.sku == reference_price.sku and item.country == reference_price.country
            ),
            None,
        )
        if reference is None:
            raise ContractError(f"precio huérfano de {reference_price.sku}")
        seller_id = seller_id_for(reference_price.country, reference_price.source_name)
        seller = seller_map.get(seller_id)
        if seller is None:
            seller = Seller(
                seller_id=seller_id,
                name=reference_price.source_name,
                country=reference_price.country,
                kind="distribuidor",
                source=SourceRef(
                    type="vendedor",
                    name=reference_price.source_name,
                    url=reference_price.source_url,
                    captured_at=reference_price.captured_at,
                ),
            )
            seller_map[seller_id] = seller
        seller_sites = [
            site
            for site in sites
            if site.seller_id == seller_id
            and (
                reference_price.source_city is None
                or plain(site.city) == plain(reference_price.source_city)
            )
        ]
        site = seller_sites[0] if len(seller_sites) == 1 else None
        published = raw_by_url.get((reference_price.sku, reference_price.source_url))
        if published is not None:
            presentation = Presentation(
                basis=published.basis,
                units_in_basis=published.units_in_basis,
            )
            amount = published.amount
        else:
            presentation = Presentation(basis="caja", units_in_basis=reference.units_per_box)
            amount = reference_price.amount
        normalized = (
            NormalizedPrice(
                units_in_basis=reference.units_per_box,
                amount=reference_price.amount,
            )
            if published is not None
            else None
        )
        availability = Availability.unknown(
            note="El precio se publica sin existencias verificadas."
        )
        offers.append(
            Offer(
                offer_id=offer_id_for(
                    country=reference_price.country,
                    sku=reference_price.sku,
                    seller_id=seller_id,
                    site_id=site.site_id if site else None,
                    source_url=reference_price.source_url,
                    basis=presentation.basis,
                    units=presentation.units_in_basis,
                    amount=amount,
                    captured_at=reference_price.captured_at,
                ),
                sku=reference_price.sku,
                country=reference_price.country,
                seller_id=seller_id,
                site_id=site.site_id if site else None,
                city=reference_price.source_city or (site.city if site else None),
                presentation=presentation,
                amount=amount,
                currency=reference_price.currency,
                tax_status=reference_price.tax_status,
                tax_rate=None,
                source=SourceRef(
                    type="vendedor",
                    name=reference_price.source_name,
                    url=reference_price.source_url,
                    captured_at=reference_price.captured_at,
                    valid_until=reference_price.valid_until,
                    note="Precio publicado por un tercero; no es precio de Carvajal.",
                ),
                normalized=normalized,
                availability=availability,
                note=(
                    "La presentación y el importe son los publicados; "
                    "`normalized` es la vista por caja."
                ),
            )
        )
    return offers


def _contract_evidence(path: Path, *, products, sites) -> list[StockEvidence]:
    if not path.is_file():
        return []
    sellers = {site.site_id: site.seller_id for site in sites}
    evidence: list[StockEvidence] = []
    for line, row in _read_rows(path):
        where = f"{path.name}:{line}"
        kind = row["kind"].strip()
        if kind not in _EVIDENCE_KINDS:
            raise ContractError(f"{where}: kind desconocido {kind!r}.")
        site_id = row["site_id"].strip()
        if site_id not in sellers:
            raise ContractError(f"{where}: evidencia huérfana de la sede {site_id}.")
        if (row["sku"].strip(), row["country"].strip()) not in products:
            raise ContractError(f"{where}: evidencia huérfana de {row['sku']}.")
        evidence.append(
            StockEvidence(
                sku=row["sku"].strip(),
                country=cast(Country, row["country"].strip()),
                site_id=site_id,
                declared_status=cast(
                    AvailabilityClaim, (row.get("declared_status") or "desconocido").strip()
                ),
                evidence=AvailabilityEvidence(
                    kind=cast(EvidenceKind, kind),
                    recorded_by=row["recorded_by"].strip(),
                    recorded_at=_date(row["recorded_at"], where),
                    reference=row["reference"].strip(),
                    source_url=_value(row.get("source_url")),
                    units=_int(row.get("units")),
                    valid_until=(
                        _date(row["valid_until"], where) if _value(row.get("valid_until")) else None
                    ),
                    note=row.get("note", ""),
                ),
            )
        )
    return evidence


def book_from_paths(
    catalog_dir: Path,
    evidence_path: Path | None = None,
    *,
    providers_path: Path | None = None,
) -> CommercialBook:
    """Construye y valida el libro con catálogo, precios, sedes y evidencia versionados.

    El flujo público solo lee datos publicados: catálogo curado, ``proveedores.csv`` y la
    evidencia de disponibilidad. No hay generación ni lectura de simulación.
    """

    catalog = load_catalog(str(catalog_dir))
    products = {
        (reference.sku, reference.country): _product_from_reference(reference)
        for reference in catalog.references
    }

    sites, _provider_to_site, directory_sellers = _site_candidates(
        catalog_dir, providers_path or PROVIDERS_PATH
    )
    seller_map = {seller.seller_id: seller for seller in directory_sellers}

    mx_offers, mx_seller = _inline_mx_offers(catalog)
    seller_map[mx_seller.seller_id] = mx_seller
    co_offers = _published_co_offers(
        catalog,
        catalog_dir=catalog_dir,
        sites=sites,
        seller_map=seller_map,
    )
    evidence = _contract_evidence(evidence_path or EVIDENCE_PATH, products=products, sites=sites)
    return build_commercial_book(
        products=products.values(),
        sellers=seller_map.values(),
        sites=sites,
        offers=(*mx_offers, *co_offers),
        evidence=evidence,
    )


@cache
def load_commercial_book(
    catalog_dir: str = str(CATALOG_DIR),
    evidence_path: str = str(EVIDENCE_PATH),
    providers_path: str | None = None,
) -> CommercialBook:
    """Libro comercial cacheado por rutas; las pruebas limpian la caché."""

    return book_from_paths(
        Path(catalog_dir),
        Path(evidence_path),
        providers_path=Path(providers_path) if providers_path else None,
    )
