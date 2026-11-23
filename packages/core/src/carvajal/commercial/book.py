"""Libro comercial validado y consultas compartidas por chat y Cotizador.

``build_commercial_book`` es la única puerta de entrada: recibe productos, vendedores, sedes,
ofertas y evidencia, y falla si una unión no cierra. Las consultas devuelven
``CommercialRecord`` con el mismo contrato para los dos consumidores:

- ``city_directory`` lista sedes por ciudad sin exigir precio ni existencias.
- ``offers_for_sku`` reúne todas las ofertas de un SKU, publicadas y de demo.
- ``sites_for_sku`` solo devuelve sedes vinculadas al producto, nunca contactos generales.
"""

import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from carvajal.commercial.models import (
    Availability,
    CityDirectory,
    CommercialRecord,
    MapView,
    Offer,
    Product,
    Seller,
    Site,
    SiteLink,
    SourceRef,
    StockEvidence,
)

__all__ = [
    "CommercialBook",
    "ContractError",
    "build_commercial_book",
    "plain",
]


class ContractError(ValueError):
    """El contrato no cierra: el mensaje nombra el registro y la unión que falla."""


def plain(value: str) -> str:
    """Minúsculas sin tildes ni espacios repetidos, para comparar ciudades y nombres."""

    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_marks.split())


def _sin_mapa(note: str) -> MapView:
    return MapView(available=False, mode="sin_mapa", approximate=True, note=note)


def _duplicate_offer_key(offer: Offer) -> tuple[str, ...]:
    """Contenido que identifica una oferta repetida (no dos ofertas distintas del mismo SKU)."""

    return (
        offer.sku,
        offer.country,
        offer.seller_id,
        offer.site_id or "",
        offer.source.url or "",
        offer.presentation.basis,
        str(offer.presentation.units_in_basis),
        str(offer.amount),
        offer.currency,
        offer.source.captured_at.isoformat(),
        offer.origin,
    )


@dataclass(frozen=True)
class CommercialBook:
    """Productos, vendedores, sedes y ofertas ya validados."""

    products: dict[tuple[str, str], Product]
    sellers: dict[str, Seller]
    sites: dict[str, Site]
    offers: tuple[Offer, ...]
    evidence: tuple[StockEvidence, ...] = ()

    # ------------------------------------------------------------------ consultas
    def product(self, sku: str, country: str | None = None) -> Product | None:
        """Producto por SKU y, opcionalmente, país."""

        if country is not None:
            return self.products.get((sku, country))
        matches = [item for (item_sku, _), item in self.products.items() if item_sku == sku]
        return matches[0] if len(matches) == 1 else None

    def seller(self, seller_id: str) -> Seller | None:
        """Vendedor por identificador."""

        return self.sellers.get(seller_id)

    def site(self, site_id: str) -> Site | None:
        """Sede por identificador estable."""

        return self.sites.get(site_id)

    def offer(self, offer_id: str) -> Offer | None:
        """Oferta por identificador."""

        return next((item for item in self.offers if item.offer_id == offer_id), None)

    def cities(self) -> tuple[str, ...]:
        """Ciudades con al menos una sede, ordenadas sin tildes."""

        return tuple(sorted({site.city for site in self.sites.values()}, key=plain))

    def city_directory(self, city: str, *, as_of: date | None = None) -> CityDirectory:
        """Directorio de una ciudad, con o sin ofertas y existencias.

        Incluye siempre un ``contacto_directorio`` por sede. Si no hay precio ni stock, la
        consulta sigue siendo útil: devuelve nombre, dirección, teléfono, fuente y mapa.
        """

        effective_as_of = as_of or date.today()
        wanted = plain(city)
        sites = sorted(
            (site for site in self.sites.values() if plain(site.city) == wanted),
            key=lambda item: (plain(item.city), plain(item.name), item.site_id),
        )
        records: list[CommercialRecord] = []
        for site in sites:
            records.append(self.directory_record(site))
            for offer in self._offers_for_site(site.site_id):
                records.append(self.offer_record(offer, as_of=effective_as_of))
            for stock in self._stock_records(site.site_id, as_of=effective_as_of):
                records.append(stock)
        counts: dict[str, int] = dict(Counter(item.record_type for item in records))
        return CityDirectory(
            city=sites[0].city if sites else city,
            cities=self.cities(),
            records=tuple(records),
            counts=counts,
        )

    def offers_for_sku(
        self, sku: str, country: str | None = None, *, as_of: date | None = None
    ) -> tuple[CommercialRecord, ...]:
        """Todas las ofertas de un SKU, publicadas y de demo, con su disponibilidad."""

        effective_as_of = as_of or date.today()
        found = [
            self.offer_record(offer, as_of=effective_as_of)
            for offer in self.offers
            if offer.sku == sku and (country is None or offer.country == country)
        ]
        return tuple(
            sorted(
                found,
                key=lambda item: (
                    item.country,
                    plain(item.site.city) if item.site else "",
                    item.offer.offer_id if item.offer else "",
                ),
            )
        )

    def sites_for_sku(
        self, sku: str, country: str | None = None, *, as_of: date | None = None
    ) -> tuple[SiteLink, ...]:
        """Sedes vinculadas al SKU: por oferta o por stock, nunca por el directorio solo."""

        effective_as_of = as_of or date.today()
        links: dict[str, SiteLink] = {}
        for offer in self.offers:
            if offer.sku != sku or (country is not None and offer.country != country):
                continue
            if offer.site_id is None:
                continue
            current = links.get(offer.site_id)
            offer_ids = (*current.offer_ids, offer.offer_id) if current else (offer.offer_id,)
            availability = self._availability_for_offer(offer, as_of=effective_as_of)
            relationship = "oferta_demo" if offer.origin == "demo" else "oferta_publicada"
            if current and current.relationship == "oferta_publicada":
                relationship = "oferta_publicada"
            links[offer.site_id] = SiteLink(
                site_id=offer.site_id,
                sku=sku,
                relationship=relationship,
                offer_ids=offer_ids,
                availability=availability,
            )
        for stock in self.evidence:
            if stock.sku != sku or (country is not None and stock.country != country):
                continue
            availability = self._stock_availability(stock.site_id, stock.sku, as_of=effective_as_of)
            if availability is None or availability.status != "verificado":
                continue
            current = links.get(stock.site_id)
            if current is None:
                links[stock.site_id] = SiteLink(
                    site_id=stock.site_id,
                    sku=sku,
                    relationship="stock_verificado",
                    availability=availability,
                )
            else:
                links[stock.site_id] = current.model_copy(
                    update={"relationship": "stock_verificado", "availability": availability}
                )
        return tuple(
            sorted(
                links.values(),
                key=lambda item: (
                    plain(self.sites[item.site_id].city),
                    plain(self.sites[item.site_id].name),
                    item.site_id,
                ),
            )
        )

    def summary(self) -> dict[str, int]:
        """Conteos que un consumidor o una prueba pueden verificar de un vistazo."""

        countries = Counter(item.country for item in self.offers)
        return {
            "productos": len(self.products),
            "vendedores": len(self.sellers),
            "sedes": len(self.sites),
            "ciudades": len(self.cities()),
            "ofertas": len(self.offers),
            "ofertas_publicadas": sum(1 for item in self.offers if item.origin == "publicada"),
            "ofertas_demo": sum(1 for item in self.offers if item.origin == "demo"),
            "ofertas_co": countries["CO"],
            "ofertas_mx": countries["MX"],
            "evidencias": len(self.evidence),
        }

    # ------------------------------------------------------------ construcciones
    def directory_record(self, site: Site) -> CommercialRecord:
        """Contacto general del directorio; no es una oferta ni promete existencias."""

        return CommercialRecord(
            record_type="contacto_directorio",
            record_id=f"directorio:{site.site_id}",
            country=site.country,
            seller=self._seller_or_error(site.seller_id),
            site=site,
            source=site.source,
            captured_at=site.source.captured_at,
            map=site.map_view,
        )

    def offer_record(self, offer: Offer, *, as_of: date | None = None) -> CommercialRecord:
        """Oferta publicada o de demo con la misma forma para todos los consumidores."""

        site = self.sites.get(offer.site_id) if offer.site_id else None
        availability = self._availability_for_offer(offer, as_of=as_of)
        return CommercialRecord(
            record_type="oferta_demo" if offer.origin == "demo" else "oferta_publicada",
            record_id=f"oferta:{offer.offer_id}",
            country=offer.country,
            sku=offer.sku,
            seller=self._seller_or_error(offer.seller_id),
            site=site,
            offer=offer,
            availability=availability,
            source=offer.source,
            captured_at=offer.source.captured_at,
            map=site.map_view if site else _sin_mapa("Oferta sin sede física atribuible."),
        )

    def _availability_for_offer(self, offer: Offer, *, as_of: date | None = None) -> Availability:
        """La evidencia por sede manda sobre la afirmación que traía la oferta."""

        if offer.site_id is not None:
            stock = self._stock_availability(offer.site_id, offer.sku, as_of=as_of)
            if stock is not None:
                return stock
        return offer.availability.effective(as_of) if as_of else offer.availability

    def _offers_for_site(self, site_id: str) -> tuple[Offer, ...]:
        return tuple(
            sorted(
                (item for item in self.offers if item.site_id == site_id),
                key=lambda item: (item.sku, item.offer_id),
            )
        )

    def _stock_availability(
        self, site_id: str, sku: str, *, as_of: date | None = None
    ) -> Availability | None:
        items = tuple(
            item.evidence for item in self.evidence if item.site_id == site_id and item.sku == sku
        )
        if not items:
            return None
        declared = next(
            item.declared_status
            for item in self.evidence
            if item.site_id == site_id and item.sku == sku
        )
        proves = any(item.proves_stock for item in items)
        availability = Availability.from_claim(
            declared,
            units=items[0].units,
            checked_at=max(item.recorded_at for item in items),
            evidence=items,
            note=(
                "Existencia respaldada por evidencia registrada."
                if proves
                else "Afirmación sin evidencia admisible; queda por confirmar."
            ),
        )
        return availability.effective(as_of) if as_of else availability

    def _stock_records(
        self, site_id: str, *, as_of: date | None = None
    ) -> tuple[CommercialRecord, ...]:
        skus = sorted({item.sku for item in self.evidence if item.site_id == site_id})
        records: list[CommercialRecord] = []
        for sku in skus:
            availability = self._stock_availability(site_id, sku, as_of=as_of)
            if availability is None or availability.status != "verificado":
                continue
            site = self.sites[site_id]
            latest = max(availability.evidence, key=lambda item: item.recorded_at)
            linked = next(
                (offer for offer in self.offers if offer.site_id == site_id and offer.sku == sku),
                None,
            )
            records.append(
                CommercialRecord(
                    record_type="stock_verificado",
                    record_id=f"stock:{site_id}:{sku}",
                    country=self.products[(sku, site.country)].country,
                    sku=sku,
                    seller=self._seller_or_error(site.seller_id),
                    site=site,
                    offer=linked,
                    availability=availability,
                    source=SourceRef(
                        type="vendedor",
                        name=latest.recorded_by,
                        url=latest.source_url,
                        captured_at=latest.recorded_at,
                        note=latest.reference,
                    ),
                    captured_at=latest.recorded_at,
                    map=site.map_view,
                )
            )
        return tuple(records)

    def _seller_or_error(self, seller_id: str) -> Seller:
        seller = self.sellers.get(seller_id)
        if seller is None:
            raise ContractError(f"vendedor desconocido {seller_id}")
        return seller


def build_commercial_book(
    *,
    products: Iterable[Product],
    sellers: Iterable[Seller],
    sites: Iterable[Site],
    offers: Iterable[Offer],
    evidence: Iterable[StockEvidence] = (),
) -> CommercialBook:
    """Valida las uniones y devuelve el libro; cualquier huérfano detiene la carga."""

    product_map: dict[tuple[str, str], Product] = {}
    for product in products:
        key = (product.sku, product.country)
        if key in product_map:
            raise ContractError(f"producto repetido {product.sku} en {product.country}")
        product_map[key] = product

    seller_map: dict[str, Seller] = {}
    for seller in sellers:
        if seller.seller_id in seller_map:
            raise ContractError(f"vendedor repetido {seller.seller_id}")
        seller_map[seller.seller_id] = seller

    site_map: dict[str, Site] = {}
    for site in sites:
        if site.site_id in site_map:
            raise ContractError(f"sede repetida {site.site_id}")
        seller = seller_map.get(site.seller_id)
        if seller is None:
            raise ContractError(f"sede huérfana {site.site_id}: vendedor {site.seller_id}")
        if seller.country != site.country:
            raise ContractError(
                f"sede {site.site_id} es de {site.country} y su vendedor de {seller.country}"
            )
        site_map[site.site_id] = site

    offer_list = tuple(offers)
    seen_offers: dict[str, Offer] = {}
    seen_content: dict[tuple[str, ...], str] = {}
    for offer in offer_list:
        if offer.offer_id in seen_offers:
            raise ContractError(f"oferta repetida {offer.offer_id}")
        seen_offers[offer.offer_id] = offer
        if (offer.sku, offer.country) not in product_map:
            raise ContractError(
                f"oferta huérfana {offer.offer_id}: no hay producto {offer.sku} en {offer.country}"
            )
        seller = seller_map.get(offer.seller_id)
        if seller is None:
            raise ContractError(f"oferta huérfana {offer.offer_id}: vendedor {offer.seller_id}")
        if seller.country != offer.country:
            raise ContractError(
                f"oferta {offer.offer_id} es de {offer.country} y su vendedor de {seller.country}"
            )
        if offer.site_id is not None:
            site = site_map.get(offer.site_id)
            if site is None:
                raise ContractError(f"oferta huérfana {offer.offer_id}: sede {offer.site_id}")
            if site.seller_id != offer.seller_id:
                raise ContractError(
                    f"oferta {offer.offer_id}: la sede {site.site_id} es de otro vendedor"
                )
            if site.country != offer.country:
                raise ContractError(f"oferta {offer.offer_id}: sede y oferta de países distintos")
            if offer.city is not None and plain(offer.city) != plain(site.city):
                raise ContractError(
                    f"oferta {offer.offer_id}: ciudad {offer.city!r} no coincide con {site.city!r}"
                )
        if offer.published and offer.source.url is None:
            raise ContractError(f"oferta publicada {offer.offer_id} sin URL de fuente")
        if offer.availability.status in {"verificado", "no_disponible"} and offer.site_id is None:
            raise ContractError(f"oferta {offer.offer_id}: existencias por sede sin site_id")
        content = _duplicate_offer_key(offer)
        if content in seen_content:
            raise ContractError(
                f"oferta duplicada {offer.offer_id} (ya existe {seen_content[content]})"
            )
        seen_content[content] = offer.offer_id

    evidence_list = tuple(evidence)
    for stock in evidence_list:
        if (stock.sku, stock.country) not in product_map:
            raise ContractError(
                f"evidencia huérfana: no hay producto {stock.sku} en {stock.country}"
            )
        site = site_map.get(stock.site_id)
        if site is None:
            raise ContractError(f"evidencia huérfana: sede {stock.site_id}")
        if site.country != stock.country:
            raise ContractError("evidencia y sede de países distintos")

    return CommercialBook(
        products=product_map,
        sellers=seller_map,
        sites=site_map,
        offers=offer_list,
        evidence=evidence_list,
    )
