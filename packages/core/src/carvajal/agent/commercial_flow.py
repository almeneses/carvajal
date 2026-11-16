"""Secuencia fija del nodo ``commercial`` (spec 036, T019).

buscar → validar material → (cajas → cotizar) → canal de compra. Todas las cifras salen de
las herramientas deterministas de ``agent/commercial.py``; el LLM solo redacta con el
resultado estructurado y el prompt le prohíbe añadir referencias, cifras o disponibilidad
(FR-004, FR-006, FR-007).

El precio solo se muestra cuando el país es México: es el único catálogo con precio y
mostrarlo sin saber el país sería cotizar a quien quizá compra en Colombia (FR-002).
"""

import json
from dataclasses import asdict
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from carvajal.agent.commercial import (
    RENEWABLE,
    CommercialRequest,
    NoPriceError,
    boxes_for,
    check_material,
    find_distributors,
    quote,
    search_packaging,
)
from carvajal.agent.commercial_intent import COMMERCIAL_KEYS
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import Reference

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

COMMERCIAL_INSTRUCTIONS = (
    "Eres el asesor comercial de Carvajal Empaques. Redacta la respuesta usando solo el "
    "resultado estructurado de abajo. No añadas referencias, materiales, cifras, precios, "
    "existencias ni plazos que no estén en él, y no calcules nada: las cajas y los totales ya "
    "vienen calculados. Para cada opción menciona el SKU, el material, las unidades por caja "
    "y la ficha con su fecha de captura. Si hay precio, copia la moneda y el aviso tal cual. "
    "Si una opción no tiene precio, dilo sin estimarlo. Si no hay opciones, dilo, resume los "
    "descartes y no ofrezcas alternativas que incumplan la regla de material. Si hay datos "
    "pendientes, termina pidiéndolos. No tomes pedidos ni pidas datos de pago."
)

CUSTOM_INSTRUCTIONS = (
    "La necesidad es a la medida (uso industrial, agro, exportación, impresión personalizada "
    "o similar) y no está en el catálogo estándar de Carvajal Empaques. No recomiendes "
    "referencias, materiales ni cifras. Explica que la atiende un asesor comercial y entrega "
    "solo los canales del resultado de abajo, sin inventar teléfonos, correos ni plazos."
)


def _request(extra: dict[str, Any]) -> CommercialRequest:
    values = {key: extra.get(key) for key in COMMERCIAL_KEYS}
    values["prefers_renewable"] = bool(values["prefers_renewable"])
    return CommercialRequest(**values)


def _option(reference: Reference, req: CommercialRequest, today: date | None) -> dict[str, Any]:
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
    if req.country != "MX":
        return option
    try:
        result = quote(reference, boxes, today=today)
    except NoPriceError as error:
        option["price_note"] = str(error)
        return option
    label = "total" if req.quantity else "price_per_box"
    option[label] = f"{result.total} {result.currency}"
    option["price_notice"] = result.notice
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
        "options": [_option(reference, req, today) for reference in chosen],
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
    """Una cita por opción: la ficha de origen con su fecha de captura (FR-007)."""

    return [
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
        for option in result["options"]
    ]


def custom_need_result(*, catalog: Catalog | None = None) -> dict[str, Any]:
    """Canales oficiales para derivar una necesidad a la medida (FR-010).

    Salen del catálogo capturado: la tienda de México (raíz de sus fichas) y el directorio
    de distribuidores de Colombia. No hay contacto comercial publicado fuera de ellos.
    """

    data = catalog or load_catalog()
    contacts: list[dict[str, Any]] = []
    mx = next((ref for ref in data.references if ref.country == "MX"), None)
    if mx is not None:
        parts = urlsplit(mx.source_url)
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
