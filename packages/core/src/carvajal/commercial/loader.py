"""Carga del contrato comercial desde los datos ya versionados.

No crea un catálogo paralelo: reutiliza ``data/catalog/empaques`` para productos, precios y
directorio, y ``data/demo/cotizador`` solo para la extensión simulada y la geocodificación.
La evidencia de existencias vive en ``data/commercial/evidencia_disponibilidad.csv`` y es la
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
import re
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
    Currency,
    DeliveryTerms,
    EvidenceKind,
    GeocodePrecision,
    GeoPoint,
    Offer,
    Presentation,
    PriceBasis,
    Product,
    Seller,
    Site,
    SourceRef,
    StockEvidence,
    TaxStatus,
)

__all__ = [
    "CATALOG_DIR",
    "DEMO_DIR",
    "EVIDENCE_PATH",
    "book_from_paths",
    "load_commercial_book",
    "offer_id_for",
    "seller_id_for",
    "site_id_for",
]

CATALOG_DIR = Path("data") / "catalog" / "empaques"
DEMO_DIR = Path("data") / "demo" / "cotizador"
EVIDENCE_PATH = Path("data") / "commercial" / "evidencia_disponibilidad.csv"
DEFAULT_GEOCODED_AT = date(2026, 11, 21)
GEOCODER = "OpenStreetMap Nominatim"
OFFICIAL_SELLER = {"MX": "Carvajal Empaques México", "CO": "Carvajal Empaques Colombia"}
DEMO_SELLER_SOURCE = "Cotizador demo"
_STOCK_STATUSES = {"disponible", "insuficiente", "agotado", "desconocido"}
_EVIDENCE_KINDS = {
    "inventario_vendedor",
    "confirmacion_vendedor",
    "integracion_pos",
    "pedido_confirmado",
    "simulacion",
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
    origin: str,
) -> str:
    """Identificador estable de una observación de precio; dos ofertas del mismo SKU no
    colisionan, y una recaptura con otro importe o fecha es una observación nueva."""

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
                origin,
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


def _geocoded_at(demo_dir: Path) -> date:
    """Fecha de geocodificación declarada en el manifiesto de la demo."""

    path = demo_dir / "manifest.json"
    if not path.is_file():
        return DEFAULT_GEOCODED_AT
    try:
        text = str(
            json.loads(path.read_text(encoding="utf-8"))
            .get("sources", {})
            .get("sedes", {})
            .get("geocoding", "")
        )
    except (json.JSONDecodeError, AttributeError):
        return DEFAULT_GEOCODED_AT
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return date.fromisoformat(match.group(0)) if match else DEFAULT_GEOCODED_AT


def _generated_at(demo_dir: Path) -> date:
    """Fecha en que se generó la simulación (no la fecha de captura del precio)."""

    path = demo_dir / "manifest.json"
    if not path.is_file():
        return DEFAULT_GEOCODED_AT
    try:
        text = str(json.loads(path.read_text(encoding="utf-8")).get("generated_at", ""))
    except (json.JSONDecodeError, AttributeError):
        return DEFAULT_GEOCODED_AT
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return date.fromisoformat(match.group(0)) if match else DEFAULT_GEOCODED_AT


def _providers(demo_dir: Path) -> tuple[list[_Provider], date]:
    geocoded_at = _geocoded_at(demo_dir)
    providers: list[_Provider] = []
    for line, row in _read_rows(demo_dir / "proveedores.csv"):
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
    return providers, geocoded_at


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
    catalog_dir: Path, demo_dir: Path
) -> tuple[list[Site], dict[str, str], list[Seller]]:
    """Sedes del directorio, mapa provider_id→site_id y vendedores del directorio."""

    providers, geocoded_at = _providers(demo_dir)
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
                geocoded_at=geocoded_at,
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


def _natural_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    return _read_rows(path)


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
        origin="real",
        source=SourceRef(
            type="oficial",
            name=OFFICIAL_SELLER[reference.country],
            url=reference.source_url,
            captured_at=reference.captured_at,
            valid_until=reference.price_valid_until,
            note="Ficha del catálogo curado.",
        ),
    )


def _demo_product(row: dict[str, str], where: str, demo_dir: Path) -> Product:
    return Product(
        sku=row["sku"],
        country=cast(Country, row["country"]),
        name=row["name"],
        family=row["family"],
        units_per_box=int(row["units_per_box"]),
        origin="demo",
        source=SourceRef(
            type="demo",
            name=DEMO_SELLER_SOURCE,
            captured_at=_date(row["captured_at"], where),
            note="Referencia simulada: no está publicada en Colombia.",
        ),
    )


def _delivery_from_demo(row: dict[str, str], *, generated_at: date) -> DeliveryTerms:
    """Plazo y envío de la demo, siempre marcados como simulados."""

    simulated_fields = {part for part in row.get("simulated", "").split("|") if part}
    return DeliveryTerms(
        lead_days_min=_int(row.get("lead_days_min")) or 0,
        lead_days_max=_int(row.get("lead_days_max")) or 0,
        ship_cost=_decimal(row.get("ship_cost")),
        simulated=bool({"lead_days", "ship_cost"} & simulated_fields),
        checked_at=generated_at,
        note="Plazo y envío simulados por la demo." if simulated_fields else "",
    )


def _claim_from_demo(
    row: dict[str, str], *, where: str, checked_at: date | None = None
) -> Availability:
    """Convierte el stock de la demo en una afirmación que nunca es verificada."""

    declared = (row.get("stock_status") or "desconocido").strip()
    if declared not in _STOCK_STATUSES:
        raise ContractError(f"{where}: stock_status desconocido {declared!r}.")
    simulated = "stock" in {part for part in row.get("simulated", "").split("|") if part}
    note = (
        "Existencia simulada por la demo; no es inventario del vendedor."
        if simulated
        else "Existencia declarada por la fuente, sin evidencia adjunta."
    )
    return Availability.from_claim(
        cast(AvailabilityClaim, declared),
        units=_int(row.get("stock_units")),
        simulated=simulated,
        checked_at=checked_at or _date(row.get("captured_at"), where),
        note=note,
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
                    origin="publicada",
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
                origin="publicada",
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
    demo_dir: Path,
    sites: list[Site],
    seller_map: dict[str, Seller],
) -> tuple[list[Offer], list[StockEvidence]]:
    """Ofertas de ``precios.csv`` con su presentación publicada y el stock de la demo."""

    raw_by_url = _raw_presentations(catalog_dir / "fuentes" / "precios_co.csv")
    generated_at = _generated_at(demo_dir)
    demo_rows = _natural_rows(demo_dir / "ofertas.csv")
    demo_real = {
        (row["sku"], row["provider_id"]): (line, row)
        for line, row in demo_rows
        if "price" not in {part for part in row.get("simulated", "").split("|") if part}
    }
    offers: list[Offer] = []
    evidence: list[StockEvidence] = []
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
            PriceBasis(
                basis="caja",
                units_in_basis=reference.units_per_box,
                amount=reference_price.amount,
            )
            if published is not None
            else None
        )
        availability = Availability.unknown(
            note="El precio se publica sin existencias verificadas."
        )
        delivery = None
        if site is not None and site.legacy_provider_id is not None:
            matched = demo_real.get((reference_price.sku, site.legacy_provider_id))
            if matched is not None:
                line, row = matched
                availability = _claim_from_demo(
                    row, where=f"ofertas.csv:{line}", checked_at=generated_at
                )
                delivery = _delivery_from_demo(row, generated_at=generated_at)
                if "stock" in {part for part in row["simulated"].split("|") if part}:
                    evidence.append(
                        StockEvidence(
                            sku=reference_price.sku,
                            country=reference_price.country,
                            site_id=site.site_id,
                            declared_status=cast(AvailabilityClaim, row["stock_status"]),
                            evidence=AvailabilityEvidence(
                                kind="simulacion",
                                recorded_by="Generador de la demo",
                                recorded_at=generated_at,
                                reference="simulación estable del Cotizador",
                                units=_int(row.get("stock_units")),
                                note="No es evidencia real; no habilita stock verificado.",
                            ),
                        )
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
                    origin="publicada",
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
                origin="publicada",
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
                delivery=delivery,
                note=(
                    "La presentación y el importe son los publicados; "
                    "`normalized` es la vista por caja."
                ),
            )
        )
    return offers, evidence


def _demo_offers(
    *,
    demo_dir: Path,
    products: dict[tuple[str, str], Product],
    providers: dict[str, str],
    seller_map: dict[str, Seller],
) -> tuple[list[Offer], list[StockEvidence]]:
    rows = _natural_rows(demo_dir / "ofertas.csv")
    generated_at = _generated_at(demo_dir)
    site_by_provider = dict(providers.items())
    offers: list[Offer] = []
    evidence: list[StockEvidence] = []
    for line, row in rows:
        where = f"ofertas.csv:{line}"
        simulated = {part for part in row.get("simulated", "").split("|") if part}
        if "price" not in simulated:
            continue  # la oferta canónica viene de precios.csv
        key = (row["sku"], row["country"])
        if key not in products:
            raise ContractError(f"{where}: oferta huérfana de {row['sku']}")
        site_id = site_by_provider.get(row["provider_id"])
        if site_id is None:
            raise ContractError(f"{where}: sede desconocida {row['provider_id']}")
        seller_id = seller_id_for(row["country"], row["provider"])
        if seller_id not in seller_map:
            seller_map[seller_id] = Seller(
                seller_id=seller_id,
                name=row["provider"],
                country=cast(Country, row["country"]),
                kind="distribuidor",
                source=SourceRef(
                    type="demo",
                    name=DEMO_SELLER_SOURCE,
                    captured_at=_date(row["captured_at"], where),
                ),
            )
        presentation = Presentation(
            basis=cast(Basis, row["basis"]),
            units_in_basis=int(row["units_in_basis"]),
        )
        amount = Decimal(row["amount"])
        source = SourceRef(
            type="demo",
            name=DEMO_SELLER_SOURCE,
            captured_at=_date(row["captured_at"], where),
            note=(
                "Precio simulado; la URL que traía el CSV no se cita porque no respalda el importe."
            ),
        )
        availability = _claim_from_demo(row, where=where, checked_at=generated_at)
        offers.append(
            Offer(
                offer_id=offer_id_for(
                    country=row["country"],
                    sku=row["sku"],
                    seller_id=seller_id,
                    site_id=site_id,
                    source_url=None,
                    basis=presentation.basis,
                    units=presentation.units_in_basis,
                    amount=amount,
                    captured_at=source.captured_at,
                    origin="demo",
                ),
                sku=row["sku"],
                country=cast(Country, row["country"]),
                seller_id=seller_id,
                site_id=site_id,
                city=row["city"],
                presentation=presentation,
                amount=amount,
                currency=cast(Currency, row["currency"]),
                tax_status=cast(TaxStatus, row["tax_status"]),
                tax_rate=None,
                tax_simulated=True,
                origin="demo",
                source=source,
                availability=availability,
                delivery=_delivery_from_demo(row, generated_at=generated_at),
                note="Oferta de demostración: precio, IVA y existencia simulados.",
            )
        )
        if "stock" in simulated:
            evidence.append(
                StockEvidence(
                    sku=row["sku"],
                    country=cast(Country, row["country"]),
                    site_id=site_id,
                    declared_status=cast(AvailabilityClaim, row["stock_status"]),
                    evidence=AvailabilityEvidence(
                        kind="simulacion",
                        recorded_by="Generador de la demo",
                        recorded_at=generated_at,
                        reference="simulación estable del Cotizador",
                        units=_int(row.get("stock_units")),
                        note="No es evidencia real; no habilita stock verificado.",
                    ),
                )
            )
    return offers, evidence


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
                    AvailabilityClaim, (row.get("declared_status") or "disponible").strip()
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
    demo_dir: Path,
    evidence_path: Path | None = None,
) -> CommercialBook:
    """Construye y valida el libro con los archivos indicados."""

    catalog = load_catalog(str(catalog_dir))
    products: dict[tuple[str, str], Product] = {}
    for reference in catalog.references:
        products[(reference.sku, reference.country)] = _product_from_reference(reference)
    for line, row in _read_rows(demo_dir / "productos.csv"):
        product = _demo_product(row, f"productos.csv:{line}", demo_dir)
        key = (product.sku, product.country)
        if key in products:
            raise ContractError(f"productos.csv:{line}: SKU repetido {product.sku}")
        products[key] = product

    sites, provider_to_site, directory_sellers = _site_candidates(catalog_dir, demo_dir)
    seller_map = {seller.seller_id: seller for seller in directory_sellers}

    mx_offers, mx_seller = _inline_mx_offers(catalog)
    seller_map[mx_seller.seller_id] = mx_seller
    co_offers, co_stock = _published_co_offers(
        catalog,
        catalog_dir=catalog_dir,
        demo_dir=demo_dir,
        sites=sites,
        seller_map=seller_map,
    )
    demo_offers, demo_stock = _demo_offers(
        demo_dir=demo_dir,
        products=products,
        providers=provider_to_site,
        seller_map=seller_map,
    )
    evidence = _contract_evidence(evidence_path or EVIDENCE_PATH, products=products, sites=sites)
    return build_commercial_book(
        products=products.values(),
        sellers=seller_map.values(),
        sites=sites,
        offers=(*mx_offers, *co_offers, *demo_offers),
        evidence=(*co_stock, *demo_stock, *evidence),
    )


@cache
def load_commercial_book(
    catalog_dir: str = str(CATALOG_DIR),
    demo_dir: str = str(DEMO_DIR),
    evidence_path: str = str(EVIDENCE_PATH),
) -> CommercialBook:
    """Libro comercial cacheado por rutas; las pruebas limpian la caché."""

    return book_from_paths(Path(catalog_dir), Path(demo_dir), Path(evidence_path))
