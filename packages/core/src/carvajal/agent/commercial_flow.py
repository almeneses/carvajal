"""Secuencia fija del nodo ``commercial`` (spec 036, T019).

buscar → validar material → (cajas → cotizar) → canal de compra. Todas las cifras salen de
las herramientas deterministas de ``agent/commercial.py``; el LLM solo redacta con el
resultado estructurado y el prompt le prohíbe añadir referencias, cifras o disponibilidad
(FR-004, FR-006, FR-007).

El precio solo se muestra cuando se conoce el país: mostrarlo sin saberlo sería cotizar a
quien quizá compra en otro país (FR-002). Desde la spec 039 cada opción lista sus ofertas,
una por fuente (Carvajal o un distribuidor), en la moneda del país; sin oferta publicada se
declara la ausencia y no se estima ni se convierte desde otro país.
"""

import json
from collections import Counter
from dataclasses import asdict
from datetime import date
from typing import Any
from urllib.parse import SplitResult, urlsplit

from carvajal.agent.commercial import (
    CAPACITY_TOLERANCE_OZ,
    RENEWABLE,
    CommercialRequest,
    boxes_for,
    check_material,
    find_distributors,
    quote_offer,
    search_packaging,
)
from carvajal.agent.commercial_intent import COMMERCIAL_KEYS
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import Reference
from carvajal.commercial import load_commercial_book
from carvajal.commercial.book import CommercialBook, plain
from carvajal.prompts import COMMERCIAL_INSTRUCTIONS, CUSTOM_INSTRUCTIONS

__all__ = [
    "COMMERCIAL_INSTRUCTIONS",
    "CUSTOM_INSTRUCTIONS",
    "MAX_OPTIONS",
    "commercial_prompt_block",
    "commercial_sources",
    "contract_result",
    "custom_need_result",
    "custom_sources",
    "mixed_notice",
    "run_commercial",
]

MAX_OPTIONS = 3
MAX_DISCARDED = 5
MAX_DISTRIBUTORS = 5
MAX_OFFERS = 3
NO_PRICE_NOTE = (
    "{name}: ninguna fuente publica precio para esta referencia en {country}; no se estima ni "
    "se convierte desde otro país. Consulta un distribuidor."
)
_COUNTRY_NAMES = {"MX": "México", "CO": "Colombia"}


def _request(extra: dict[str, Any]) -> CommercialRequest:
    values = {key: extra.get(key) for key in COMMERCIAL_KEYS}
    values["prefers_renewable"] = bool(values["prefers_renewable"])
    return CommercialRequest(**values)


def _option(
    reference: Reference, req: CommercialRequest, today: date | None, catalog: Catalog
) -> dict[str, Any]:
    option: dict[str, Any] = {
        "sku": reference.sku,
        "name": reference.name,
        "material": reference.material,
        "capacity_oz": reference.capacity_oz,
        "units_per_box": reference.units_per_box,
        "country": reference.country,
        "source_url": reference.source_url,
        "captured_at": reference.captured_at.isoformat(),
    }
    boxes = 1
    if req.quantity:
        count = boxes_for(reference, req.quantity)
        option.update(boxes=count.boxes, surplus_units=count.surplus)
        boxes = count.boxes
    if req.country is None:
        return option
    offers = catalog.offers(reference)[:MAX_OFFERS]
    if not offers:
        option["price_note"] = NO_PRICE_NOTE.format(
            name=reference.name, country=_COUNTRY_NAMES.get(req.country, req.country)
        )
        return option
    label = "total" if req.quantity else "price_per_box"
    quotes = [quote_offer(offer, boxes, today=today) for offer in offers]
    option["offers"] = [
        {
            label: f"{item.total} {item.currency}",
            "tax_status": item.tax_status,
            "source_type": item.source_type,
            "source_name": item.source_name,
            "source_city": item.source_city,
            "source_url": item.source_url,
            "captured_at": item.captured_at,
            "notice": item.notice,
        }
        for item in quotes
    ]
    # Compatibilidad con la 036: la primera oferta también va en los campos planos.
    option[label] = f"{quotes[0].total} {quotes[0].currency}"
    option["price_notice"] = quotes[0].notice
    return option


def _channel(req: CommercialRequest, catalog: Catalog | None) -> dict[str, Any]:
    if req.country == "MX":
        return {"type": "tienda_en_linea", "note": "Cada opción enlaza a su ficha en la tienda."}
    if req.country != "CO":
        return {}
    if req.city is None:
        _, cities = find_distributors("", catalog=catalog)
        return {"type": "distribuidores", "cities_with_distributors": cities}
    found, cities = find_distributors(req.city, catalog=catalog)
    channel: dict[str, Any] = {"type": "distribuidores", "city": req.city}
    if not found:
        channel["cities_with_distributors"] = cities
    channel["distributors"] = [
        item.model_dump(mode="json", exclude={"captured_at"}) for item in found[:MAX_DISTRIBUTORS]
    ]
    return channel


def _contract_reference(
    req: CommercialRequest,
    book: CommercialBook,
    catalog: Catalog,
    as_of: date,
    options: list[Reference] | None = None,
) -> Reference | None:
    """Referencia de la ficha del contrato: una opción apta y con oferta publicada.

    La ficha es la tarjeta principal y la vista la pinta en lugar de las opciones, así que
    sale de las mismas opciones que muestra ``run_commercial`` (``options``, ya filtradas
    por ``check_material``) y respeta su orden. Sin ``options`` se recalculan con
    ``check_material``. Manda lo renovable si la persona lo pidió, luego la ficha que
    declara el uso, luego la oferta más barata en la ciudad y, al final, el orden de las
    opciones. Si la mejor no tiene oferta no hay ficha: no aportaría precio y ocultaría las
    demás opciones válidas (evaluación spec 044).
    """

    if options is None:
        matching = [
            reference
            for reference in catalog.references
            if req.capacity_oz is None
            or (
                reference.capacity_oz is not None
                and abs(reference.capacity_oz - req.capacity_oz) <= CAPACITY_TOLERANCE_OZ
            )
        ]
        options, _ = check_material(matching, req, catalog=catalog)
    candidates = [
        (position, reference)
        for position, reference in enumerate(options)
        if reference.country == "CO" and (req.family is None or reference.family == req.family)
    ]

    def priority(item: tuple[int, Reference]) -> tuple[bool, bool, float, int]:
        position, reference = item
        prices = [
            record.offer.amount
            for record in book.offers_for_sku(reference.sku, "CO", as_of=as_of)
            if record.offer is not None
            and (req.city is None or plain(record.offer.city or "") == plain(req.city))
        ]
        not_renewable = req.prefers_renewable and reference.material not in RENEWABLE
        other_use = req.use is not None and req.use not in reference.uses
        cheapest = float(min(prices)) if prices else float("inf")
        return (not_renewable, other_use, cheapest, position)

    ranked = sorted(candidates, key=priority)
    if not ranked or priority(ranked[0])[2] == float("inf"):
        return None
    return ranked[0][1]


def _contract_ficha(reference: Reference) -> dict[str, Any]:
    return {
        "sku": reference.sku,
        "name": reference.name,
        "family": reference.family,
        "material": reference.material,
        "capacity_oz": reference.capacity_oz,
        "uses": list(reference.uses),
        "units_per_pack": reference.units_per_pack,
        "packs_per_box": reference.packs_per_box,
        "units_per_box": reference.units_per_box,
        "source_url": reference.source_url,
        "captured_at": reference.captured_at.isoformat(),
    }


def _contract_offers(
    book: CommercialBook, sku: str, *, city: str | None, as_of: date
) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    for record in book.offers_for_sku(sku, "CO", as_of=as_of):
        offer = record.offer
        if offer is None:
            continue
        site = record.site
        offer_city = (site.city if site else None) or offer.city
        if city is not None and plain(offer_city or "") != plain(city):
            continue
        offers.append(
            {
                "offer_id": offer.offer_id,
                "seller": record.seller.name,
                "seller_kind": record.seller.kind,
                "city": offer_city,
                "presentation": offer.presentation.label,
                "units_in_basis": offer.presentation.units_in_basis,
                "amount": str(offer.amount),
                "currency": offer.currency,
                "tax_status": offer.tax_status,
                "tax_rate": str(offer.tax_rate) if offer.tax_rate is not None else None,
                "source_url": offer.source.url,
                "captured_at": offer.source.captured_at.isoformat(),
                "valid_until": (
                    offer.source.valid_until.isoformat() if offer.source.valid_until else None
                ),
                "availability": record.availability.status
                if record.availability
                else "desconocido",
                "site_id": site.site_id if site else None,
            }
        )
    return offers


def _contract_sites(
    book: CommercialBook, *, sku: str | None, city: str | None, as_of: date
) -> list[dict[str, Any]]:
    if city is None:
        return []
    links = (
        {item.site_id: item for item in book.sites_for_sku(sku, "CO", as_of=as_of)} if sku else {}
    )
    directory = book.city_directory(city, as_of=as_of)
    sites: list[dict[str, Any]] = []
    for record in directory.of_type("contacto_directorio"):
        site = record.site
        if site is None:
            continue
        location = site.location
        link = links.get(site.site_id)
        sites.append(
            {
                "site_id": site.site_id,
                "name": site.name,
                "city": site.city,
                "address": site.address,
                "phone": site.phone,
                "latitude": location.latitude if location else None,
                "longitude": location.longitude if location else None,
                "precision": location.precision if location else "desconocida",
                "source_url": site.source.url,
                "captured_at": site.source.captured_at.isoformat(),
                "linked": link is not None,
                "availability": link.availability.status if link else "desconocido",
            }
        )
    return sites


def contract_result(
    question: str,
    extra: dict[str, Any],
    *,
    book: CommercialBook | None = None,
    catalog: Catalog | None = None,
    today: date | None = None,
    questions: list[str] | None = None,
    options: list[Reference] | None = None,
) -> dict[str, Any]:
    """Ficha, ofertas, sedes y cotización del contrato publicado para Colombia.

    El resultado es JSON-safe y conserva los campos pendientes como ausencia. La
    cotización la calcula el motor determinista con el texto del turno; si falta un
    dato, la aclaración viaja aparte y no se inventa el importe. ``options`` son las
    opciones aptas que muestra el turno, en su orden: la ficha sale de ellas.
    """

    req = _request(extra)
    if req.country != "CO":
        return {}
    data = catalog or load_catalog()
    commercial = book or load_commercial_book()
    as_of = today or date.today()
    reference = _contract_reference(req, commercial, data, as_of, options)
    result: dict[str, Any] = {
        "cities": list(commercial.cities()),
        "ficha": _contract_ficha(reference) if reference is not None else None,
        "offers": (
            _contract_offers(commercial, reference.sku, city=req.city, as_of=as_of)
            if reference is not None
            else []
        ),
        "sites": _contract_sites(
            commercial,
            sku=reference.sku if reference is not None else None,
            city=req.city,
            as_of=as_of,
        ),
    }
    # Import diferido: ``quotation`` importa ``agent.commercial``, que carga el
    # paquete ``agent``; hacerlo en el módulo crearía un ciclo graph→flow→quotation.
    from carvajal.quotation import quote as quote_engine

    profile = {"city": req.city} if req.city else None
    try:
        quoted = quote_engine(
            question,
            questions=list(questions or []),
            today=as_of,
            profile=profile,
        )
    except Exception as error:  # la cotización no debe tumbar el turno comercial
        result["quote_error"] = str(error)
        return result
    result["quote"] = quoted
    if quoted.get("kind") == "cotizacion":
        result["clarifications"] = list(quoted.get("pending_lines") or [])
    else:
        result["clarifications"] = [quoted.get("reply", "")]
        if req.city is not None and quoted.get("providers"):
            known = {item["site_id"] for item in result["sites"]}
            for provider in quoted["providers"]:
                if provider.get("id") in known:
                    continue
                result["sites"].append(
                    {
                        "site_id": provider.get("id"),
                        "name": provider.get("name"),
                        "city": provider.get("city"),
                        "address": provider.get("address"),
                        "phone": provider.get("phone"),
                        "latitude": provider.get("lat"),
                        "longitude": provider.get("lon"),
                        "precision": provider.get("precision"),
                        "source_url": None,
                        "captured_at": None,
                        "linked": False,
                        "availability": "desconocido",
                    }
                )
    return result


def run_commercial(
    extra: dict[str, Any],
    *,
    catalog: Catalog | None = None,
    today: date | None = None,
    question: str | None = None,
    questions: list[str] | None = None,
) -> dict[str, Any]:
    """Ejecuta la secuencia fija y devuelve el resultado estructurado del turno."""

    req = _request(extra)
    use_contract = catalog is None and req.country == "CO"
    data = catalog or load_catalog()
    valid, discarded = check_material(search_packaging(req, catalog=data), req, catalog=data)
    chosen = valid[:MAX_OPTIONS]
    pending: list[str] = []
    if req.country is None:
        pending.append("país (México o Colombia), para indicar precio y dónde comprar")
    elif req.country == "CO" and req.city is None:
        pending.append("ciudad, para indicar los distribuidores")
    if req.quantity is None and chosen:
        pending.append("cantidad en unidades, si quiere calcular cajas")
    result: dict[str, Any] = {
        "request": asdict(req),
        "options": [_option(reference, req, today, data) for reference in chosen],
        "discarded": [asdict(item) for item in discarded[:MAX_DISCARDED]],
        "discarded_total": len(discarded),
        "channel": _channel(req, data),
        "pending": pending,
    }
    if req.country and req.country not in data.countries:
        # Sin referencias del país no es que «ninguna cumpla»: el catálogo aún no las tiene.
        result["catalog_note"] = (
            f"El catálogo cargado todavía no tiene referencias de {req.country}; "
            "remite a los distribuidores sin recomendar productos."
        )
    if req.prefers_renewable:
        result["renewable_available"] = any(ref.material in RENEWABLE for ref in chosen)
    if use_contract:
        try:
            contract = contract_result(
                question or "",
                extra,
                catalog=data,
                today=today,
                questions=questions,
                options=chosen,
            )
        except Exception as error:  # el contrato no debe tumbar la ruta comercial
            result["contract_error"] = str(error)
        else:
            if contract:
                # Se conservan las opciones de catálogo para compatibilidad de la API;
                # la vista prioriza ficha, ofertas, sedes y cotización del contrato.
                result.update(contract)
    return result


def commercial_prompt_block(
    result: dict[str, Any], *, instructions: str = COMMERCIAL_INSTRUCTIONS
) -> str:
    """Instrucciones y resultado serializado para el prompt del nodo."""

    data = json.dumps(result, ensure_ascii=False, indent=1)
    return f"{instructions}\n\nResultado estructurado:\n{data}"


def commercial_sources(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Una cita por opción (la ficha con su fecha) y una por cada precio de un tercero (FR-007).

    El precio de un distribuidor se cita con su página y su fecha: el usuario debe poder ver
    quién lo publica (spec 039, FR-005).
    """

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in result["options"]:
        sources.append(
            {
                "chunk_id": f"catalogo:{option['sku']}",
                "slug": option["sku"],
                "section": "catalogo_empaques",
                "layer": "core",
                "score": 1.0,
                "excerpt": (
                    f"{option['material']} · {option['units_per_box']} unidades por caja · "
                    f"ficha capturada el {option['captured_at']}"
                ),
                "url": option["source_url"],
                "title": option["name"],
                "source_type": "oficial",
            }
        )
        for offer in option.get("offers", []):
            if offer["source_type"] != "tercero" or offer["source_url"] in seen:
                continue
            seen.add(offer["source_url"])
            sources.append(
                {
                    "chunk_id": f"precio:{option['sku']}:{len(seen)}",
                    "slug": f"precio-{option['sku']}-{len(seen)}",
                    "section": "catalogo_empaques",
                    "layer": "core",
                    "score": 1.0,
                    "excerpt": (
                        f"Precio publicado por {offer['source_name']} · "
                        f"capturado el {offer['captured_at']}"
                    ),
                    "url": offer["source_url"],
                    "title": f"{offer['source_name']}: {option['name']}",
                    "source_type": "tercero",
                }
            )
    ficha = result.get("ficha")
    if ficha and ficha.get("source_url") and ficha["source_url"] not in seen:
        seen.add(ficha["source_url"])
        sources.append(
            {
                "chunk_id": f"catalogo:{ficha['sku']}",
                "slug": ficha["sku"],
                "section": "catalogo_empaques",
                "layer": "core",
                "score": 1.0,
                "excerpt": (
                    f"{ficha['material']} · {ficha['units_per_box']} unidades por caja · "
                    f"ficha capturada el {ficha['captured_at']}"
                ),
                "url": ficha["source_url"],
                "title": ficha["name"],
                "source_type": "oficial",
            }
        )
    for offer in result.get("offers") or []:
        url = offer.get("source_url")
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "chunk_id": f"precio:{offer['offer_id']}",
                "slug": f"precio-{offer['offer_id']}",
                "section": "catalogo_empaques",
                "layer": "core",
                "score": 1.0,
                "excerpt": (
                    f"Precio {offer['amount']} {offer['currency']} por "
                    f"{offer['presentation']} · IVA {offer['tax_status']} · "
                    f"publicado por {offer['seller']} · capturado el {offer['captured_at']}"
                ),
                "url": url,
                "title": f"{offer['seller']}: {ficha['name'] if ficha else offer['offer_id']}",
                "source_type": "tercero" if offer["seller_kind"] == "distribuidor" else "oficial",
            }
        )
    for site in result.get("sites") or []:
        url = site.get("source_url")
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "chunk_id": f"sede:{site['site_id']}",
                "slug": f"sede-{site['site_id']}",
                "section": "directorio_distribuidores",
                "layer": "core",
                "score": 1.0,
                "excerpt": (
                    f"{site['name']} · {site['city']} · {site.get('address') or 'sin dirección'} "
                    f"· precisión {site['precision']}"
                ),
                "url": url,
                "title": f"Sede {site['name']} ({site['city']})",
                "source_type": "oficial",
            }
        )
    return sources


def _mx_store(data: Catalog) -> tuple[SplitResult, Reference] | None:
    """Raíz de la tienda MX: el host que aporta más fichas del país.

    Una ficha aislada de otro dominio (un marketplace o un PDF) no desvía el canal; si el
    catálogo cambia de host, la raíz se sigue deduciendo de las fichas, sin hardcodear el
    dominio.
    """

    pages = [(urlsplit(ref.source_url), ref) for ref in data.references if ref.country == "MX"]
    if not pages:
        return None
    hosts = Counter(parts.netloc for parts, _ in pages)
    store = hosts.most_common(1)[0][0]
    return next((parts, ref) for parts, ref in pages if parts.netloc == store)


def custom_need_result(*, catalog: Catalog | None = None) -> dict[str, Any]:
    """Canales oficiales para derivar una necesidad a la medida (FR-010).

    Salen del catálogo capturado: la tienda de México (raíz de sus fichas) y el directorio
    de distribuidores de Colombia. No hay contacto comercial publicado fuera de ellos.
    """

    data = catalog or load_catalog()
    contacts: list[dict[str, Any]] = []
    store = _mx_store(data)
    if store is not None:
        parts, mx = store
        contacts.append(
            {
                "country": "MX",
                "channel": "tienda en línea de Carvajal Empaques México",
                "url": f"{parts.scheme}://{parts.netloc}/",
                "captured_at": mx.captured_at.isoformat(),
            }
        )
    if data.distributors:
        first = data.distributors[0]
        contacts.append(
            {
                "country": "CO",
                "channel": "directorio de distribuidores de Carvajal Empaques Colombia",
                "url": first.source_url,
                "captured_at": first.captured_at.isoformat(),
            }
        )
    return {"custom_need": True, "options": [], "contacts": contacts}


def custom_sources(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Cita de cada canal de contacto, con su fecha de captura."""

    return [
        {
            "chunk_id": f"canal:{contact['country']}",
            "slug": f"canal-{contact['country'].lower()}",
            "section": "catalogo_empaques",
            "layer": "core",
            "score": 1.0,
            "excerpt": f"{contact['channel']} · capturado el {contact['captured_at']}",
            "url": contact["url"],
            "title": contact["channel"],
            "source_type": "oficial",
        }
        for contact in result["contacts"]
    ]


def mixed_notice(other: str) -> str:
    """Aviso para la parte no comercial de una pregunta mixta (T033, spec 043)."""

    return (
        f"La pregunta incluye también una parte que no es comercial: «{other}». "
        "Responde esa parte solo con el contexto documental adjunto y sus citas; si ese "
        "contexto falta o no alcanza, dilo sin inventar. Mantén separadas las cifras "
        "comerciales de la información del corpus."
    )
