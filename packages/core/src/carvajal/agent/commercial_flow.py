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
from carvajal.prompts import COMMERCIAL_INSTRUCTIONS, CUSTOM_INSTRUCTIONS

__all__ = [
    "COMMERCIAL_INSTRUCTIONS",
    "CUSTOM_INSTRUCTIONS",
    "MAX_OPTIONS",
    "commercial_prompt_block",
    "commercial_sources",
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


def run_commercial(
    extra: dict[str, Any], *, catalog: Catalog | None = None, today: date | None = None
) -> dict[str, Any]:
    """Ejecuta la secuencia fija y devuelve el resultado estructurado del turno."""

    req = _request(extra)
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
    """Aviso para la parte no comercial de una pregunta mixta (T033)."""

    return (
        f"La pregunta incluye también una parte que no es comercial: «{other}». No la "
        "respondas aquí ni con datos de este resultado; al final dile al usuario que la haga "
        "en un mensaje aparte para buscarla con sus fuentes."
    )
