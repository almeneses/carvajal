"""Herramienta ``ciudades_con_cobertura`` (spec 045, T068; contrato agent-tools-045).

Lista las ciudades de Colombia con sedes en el directorio de distribuidores y cuántas sedes
tiene cada una (``CommercialBook.cities`` y las sedes del libro). Con una referencia, separa
las ciudades donde tiene precio publicado cotizable (``engine._priced_cities``, la misma
regla del Cotizador) de las ciudades con una sede vinculada a ella (``sites_for_sku``). Una
sede del directorio nunca confirma existencias: el aviso va siempre.

No pide ciudad (eso es ``buscar_sedes``) ni calcula totales (eso es ``cotizar``). Cada
ciudad y cada cifra que devuelve está en el ``excerpt`` de la fuente ``directorio:CO``; los
precios, en su ``precio:{sku}:{sede}``.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field, field_validator

from carvajal.agent.reference_names import exact_sku, looks_like_sku, resolve_reference
from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _book,
    _Draft,
    _is_official,
    _now_bogota,
    _Sources,
)
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import Reference
from carvajal.commercial.book import CommercialBook, plain
from carvajal.commercial.models import Site, seller_source_type
from carvajal.quotation import Dataset, load_dataset
from carvajal.quotation.data import CommercialDataError, Offer, dataset_from_book
from carvajal.quotation.engine import _priced_cities

__all__ = [
    "DESCRIPTION",
    "EVIDENCE_ROLE",
    "MAX_CANDIDATES",
    "CiudadesConCoberturaArgs",
    "run",
    "status",
]

COUNTRY = "CO"  # el directorio de distribuidores es de Colombia
DIRECTORY_CHUNK = f"directorio:{COUNTRY}"
MAX_CANDIDATES = 8  # candidatas que ve el modelo ante un nombre ambiguo
MAX_TEXT_CHARS = 120
EVIDENCE_ROLE = "data"
STOCK_NOTICE = "una sede no confirma existencias"

DESCRIPTION = (
    "Lista las ciudades de Colombia con distribuidores y cuántas sedes tiene cada una; con "
    "una referencia, separa dónde tiene precio publicado y dónde sede vinculada. Úsala "
    "para: preguntas de cobertura. No la uses para: direcciones o teléfonos de una ciudad "
    "(usa buscar_sedes) ni totales (usa cotizar)."
)


def _optional_text(value: object) -> object:
    """Texto opcional: vacío o solo espacios cuenta como ausente."""

    if isinstance(value, str):
        stripped = " ".join(value.split())
        return stripped or None
    return value


class CiudadesConCoberturaArgs(BaseModel):
    """Cobertura del directorio de distribuidores, opcionalmente para una referencia."""

    sku: str | None = Field(
        default=None,
        max_length=64,
        description="SKU de una referencia concreta, si la persona la nombró.",
    )
    nombre: str | None = Field(
        default=None,
        max_length=MAX_TEXT_CHARS,
        description="Código o nombre de la referencia (P1, J2, portacomidas), si no hay SKU.",
    )

    _sku = field_validator("sku", mode="before")(_optional_text)
    _nombre = field_validator("nombre", mode="before")(_optional_text)


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado fija: nunca interpola el SKU ni el nombre que escribió el modelo."""

    return "Estoy revisando en qué ciudades hay distribuidores…"


# --- Directorio ----------------------------------------------------------------------------


def _directory_sites(book: CommercialBook) -> list[Site]:
    return [site for site in book.sites.values() if site.country == COUNTRY]


def _directory_url(sites: Sequence[Site]) -> str:
    """URL del directorio: la oficial más citada por las sedes (hoy todas citan la misma)."""

    urls = Counter(site.source.url for site in sites if site.source.url)
    official = [url for url, _ in urls.most_common() if _is_official(url)]
    if official:
        return official[0]
    return urls.most_common(1)[0][0] if urls else ""


def _directory_source(sites: Sequence[Site], counts: Sequence[tuple[str, int]]) -> dict[str, Any]:
    url = _directory_url(sites)
    names = Counter(site.source.name for site in sites if site.source.name)
    name = names.most_common(1)[0][0] if names else "Directorio de distribuidores"
    captured = max(site.source.captured_at for site in sites).isoformat()
    by_city = " · ".join(
        f"{city}: {count} {'sede' if count == 1 else 'sedes'}" for city, count in counts
    )
    return {
        "chunk_id": DIRECTORY_CHUNK,
        "slug": "directorio-distribuidores-co",
        "section": "directorio_distribuidores",
        "layer": CORE_LAYER,
        "score": 1.0,
        "excerpt": (
            f"{name}: {len(sites)} sedes en {len(counts)} ciudades de Colombia. {by_city}. "
            f"Capturado el {captured}."
        ),
        "url": url,
        "title": name,
        "source_type": "oficial" if _is_official(url) else "tercero",
    }


# --- Referencia ----------------------------------------------------------------------------


def _candidate(reference: Reference) -> dict[str, Any]:
    return {
        "sku": reference.sku,
        "nombre": reference.name,
        "familia": reference.family,
        "material": reference.material,
        "capacidad_oz": reference.capacity_oz,
    }


def _ask_which(named: Sequence[Reference], *, partial: bool, conflict: bool) -> _Draft:
    """Varias referencias (o ninguna exacta): se pregunta cuál, sin dar cobertura."""

    unique = list({item.sku: item for item in named}.values())
    if conflict:
        message = "El sku y el nombre corresponden a referencias distintas del catálogo."
    elif partial:
        message = (
            "Ninguna referencia coincide con todos los términos del nombre; estas coinciden "
            "en parte."
        )
    else:
        message = "El nombre coincide con varias referencias del catálogo."
    message += " Pregunta a la persona cuál quiere; no elijas tú."
    data: dict[str, Any] = {
        "candidatas": [_candidate(item) for item in unique[:MAX_CANDIDATES]],
        "total_candidatas": len(unique),
    }
    if partial:
        data["parcial"] = True
    if conflict:
        data["conflicto_sku_nombre"] = True
    return _Draft("needs_input", message, data=data, missing=["referencia"])


def _not_found() -> _Draft:
    return _Draft(
        "empty",
        "No encontré esa referencia en el catálogo de Colombia; no hay cobertura que dar.",
        data={"referencia_encontrada": False},
    )


def _resolve(args: CiudadesConCoberturaArgs, catalog: Catalog) -> Reference | _Draft:
    """Referencia de Colombia que fijan ``sku`` o ``nombre``, o el borrador si no se puede.

    Misma regla que el Cotizador (``engine._named_reference``): el SKU exacto manda; sin
    nombre, un ``sku`` que no es SKU exacto ni tiene forma de SKU («J2») se busca como
    nombre; uno con forma de SKU que no existe («CO-NOEXISTE-1») es ``empty``; un SKU y un
    nombre de referencias distintas no se resuelven en silencio.
    """

    pool = [item for item in catalog.references if item.country == COUNTRY]
    sku_text = args.sku or ""
    name = args.nombre or ""
    by_sku = exact_sku(sku_text, pool) if sku_text else None
    if by_sku is None and sku_text:
        foreign = exact_sku(sku_text, catalog.references)
        if foreign is not None:
            return _Draft(
                "empty",
                "La referencia no es del catálogo de Colombia; el directorio de sedes solo "
                "cubre Colombia.",
                data={"pais_referencia": foreign.country},
            )
        if looks_like_sku(sku_text):
            # Con forma de SKU, el SKU es exacto o no es (como ``ficha_producto``): no se
            # reinterpreta como nombre, que coincidiría en parte con fichas ajenas.
            return _not_found()
        if not name:
            name = sku_text
    if by_sku is not None:
        if name and name != sku_text:
            by_name = resolve_reference(name, pool, catalog)
            named_skus = {item.sku for item in by_name.named}
            if by_name.named and not by_name.partial and by_sku.sku not in named_skus:
                return _ask_which([by_sku, *by_name.named], partial=False, conflict=True)
        return by_sku
    match = resolve_reference(name, pool, catalog)
    if not match.named:
        return _not_found()
    if len(match.named) > 1 or match.partial:
        return _ask_which(match.named, partial=match.partial, conflict=False)
    return match.named[0]


def _dataset(deps: ToolDeps) -> Dataset:
    """Read model del Cotizador coherente con el libro de las dependencias."""

    if deps.dataset is not None:
        return deps.dataset
    day = deps.today or _now_bogota(deps).date()
    if deps.book is not None:
        return dataset_from_book(deps.book, as_of=day)
    return load_dataset(day.isoformat())


def _seller_kind_type(book: CommercialBook, offer: Offer) -> str:
    """Tercero si lo publica un distribuidor (como ``commercial_sources``)."""

    for item in book.offers:
        if item.sku == offer.sku and item.site_id == offer.site_id:
            seller = book.seller(item.seller_id)
            if seller is not None:
                return seller_source_type(seller.kind)
    return "oficial" if _is_official(offer.source_url) else "tercero"


def _price_source(book: CommercialBook, reference: Reference, offer: Offer) -> dict[str, Any]:
    tax = f" · IVA {offer.tax_status}" if offer.tax_status else ""
    return {
        "chunk_id": f"precio:{offer.sku}:{offer.site_id}",
        "slug": f"precio-{offer.sku}",
        "section": "cotizador",
        "layer": CORE_LAYER,
        "score": 1.0,
        "excerpt": (
            f"Precio {offer.amount} {offer.currency} por {offer.basis} de "
            f"{offer.units_in_basis}{tax} · publicado por {offer.source_name} · sede "
            f"{offer.provider} en {offer.city} · capturado el {offer.captured_at.isoformat()}"
        ),
        "url": offer.source_url,
        "title": f"{offer.source_name}: {reference.name}",
        "source_type": _seller_kind_type(book, offer),
    }


# --- Herramienta ---------------------------------------------------------------------------


def run(
    args: CiudadesConCoberturaArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    """Ciudades del directorio con su número de sedes y, con referencia, precio frente a sede."""

    reference: Reference | None = None
    if args.sku or args.nombre:
        resolved = _resolve(args, deps.catalog or load_catalog())
        if isinstance(resolved, _Draft):
            return resolved
        reference = resolved

    book = _book(deps)
    sites = _directory_sites(book)
    if not sites:
        return _Draft("empty", "El directorio de distribuidores no tiene sedes en Colombia.")
    dataset: Dataset | None = None
    if reference is not None:
        try:
            dataset = _dataset(deps)
        except CommercialDataError:
            return _Draft(
                "error", "No pude leer los precios publicados; no hay datos de cobertura."
            )

    per_city = Counter(site.city for site in sites)
    counts = [(city, per_city[city]) for city in sorted(per_city, key=plain)]
    [directory_n] = sources.add([_directory_source(sites, counts)])
    data: dict[str, Any] = {
        "n": directory_n,
        "ciudades": [{"ciudad": city, "sedes": count} for city, count in counts],
        "total_ciudades": len(counts),
        "total_sedes": len(sites),
        "aviso": STOCK_NOTICE,
    }
    message = (
        f"El directorio de distribuidores tiene {len(sites)} sedes en {len(counts)} ciudades "
        "de Colombia. Una sede del directorio no confirma existencias: siguen por confirmar "
        "con la sede."
    )
    if reference is None or dataset is None:
        return _Draft("ok", message, data=data)

    priced = _priced_cities(reference, dataset)
    linked_sites = book.sites_for_sku(
        reference.sku, COUNTRY, as_of=deps.today or _now_bogota(deps).date()
    )
    linked_cities = {
        site.city for link in linked_sites if (site := book.site(link.site_id)) is not None
    }
    offers = [
        offer
        for offer in dataset.offers_for(reference.sku)
        if offer.stock_status != "no_disponible" and offer.city in priced and not offer.converted
    ]
    # El precio convertido desde México no prueba cobertura en ninguna ciudad.
    converted = any(offer.converted for offer in dataset.offers_for(reference.sku))
    priced = sorted({offer.city for offer in offers}, key=plain)
    price_numbers = sources.add(_price_source(book, reference, offer) for offer in offers)
    data["referencia"] = {"sku": reference.sku, "nombre": reference.name}
    data["con_precio"] = priced
    data["con_sede"] = sorted(linked_cities, key=plain)
    data["fuentes_precio"] = list(dict.fromkeys(price_numbers))
    if priced:
        message += f" La referencia tiene precio publicado en: {', '.join(priced)}."
    elif converted:
        message += (
            " La referencia viene del catálogo de México: solo tiene un precio de referencia "
            "convertido a COP y ninguna sede la publica en Colombia."
        )
    else:
        message += " Hoy la referencia está sin precio publicado que se pueda cotizar."
    if linked_cities:
        message += (
            f" Tiene sede vinculada en: {', '.join(data['con_sede'])}; en las demás ciudades "
            "las sedes no la tienen vinculada."
        )
    else:
        message += " Ninguna sede del directorio la tiene vinculada."
    return _Draft("ok", message, data=data)
