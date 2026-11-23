"""Motor de cotización del Cotizador (spec 042).

Determinista: reutiliza la extracción de intención de la spec 036 y la validación de
materiales del catálogo, y calcula presentaciones, dinero, existencias, sedes, mapa y
ventana de entrega. No llama al LLM ni a la red.

La respuesta es un diccionario serializable: el texto del turno y, cuando hay cotización,
el payload que la plantilla pinta dentro del hilo.
"""

import re
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from carvajal.agent.commercial import CAPACITY_TOLERANCE_OZ, CommercialRequest, check_material
from carvajal.agent.commercial_intent import empty_request, extract_commercial
from carvajal.agent.state import Turn
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import Reference
from carvajal.prompts import ABSENCE_POLICY
from carvajal.quotation.calendar import window
from carvajal.quotation.data import COUNTRY, Dataset, Offer, Product, load_dataset

__all__ = ["MAX_ALTERNATIVES", "OUT_OF_SCOPE", "quote"]

MAX_ALTERNATIVES = 2
MAX_DISCARDED = 3
CUTOFF_HOUR = 15
BOGOTA = ZoneInfo("America/Bogota")
COP = "COP"
OUT_OF_SCOPE = f"{ABSENCE_POLICY['out_of_scope']['say']} {ABSENCE_POLICY['out_of_scope']['offer']}"
NO_PRODUCTS = (
    "No encontré en el catálogo referencias que cumplan lo que pedís. "
    "Puedo ampliar la búsqueda o pasarte el contacto de un asesor."
)
_DELIVERY = re.compile(
    r"\b(domicilio|a mi casa|env[ií]o|env[ií]os|entrega a domicilio|entregar)\b", re.I
)
_NO_DELIVERY = re.compile(r"\b(sin domicilio|retiro|lo recojo|paso por)\b", re.I)
_NEGATIVE = re.compile(r"-\s*\d")
_BARE_NUMBER = re.compile(r"\d{1,3}(?:[.,]\d{3})*|\d+")
_GREETING = re.compile(
    r"^\s*(hola|buenas|buen d[ií]a|buenas tardes|buenas noches|hey|qu[eé] tal)\b", re.I
)
GREETING_REPLY = (
    "Hola. Contame qué producto, cuántas unidades y en qué ciudad lo buscás; con eso te "
    "armo la cotización."
)
ASK_PRODUCT = (
    "Decime qué querés cotizar: producto, cantidad y ciudad. Por ejemplo, «20 vasos para "
    "bebida caliente en Bogotá»."
)
_QUANTITY = re.compile(
    r"\b(?:mejor|ahora|sean|ser[ií]an|son|para)\s+(\d{1,3}(?:[.,]\d{3})+|\d+)\b", re.I
)
_FAMILIES_LABEL = {
    "vaso": "vasos",
    "tapa": "tapas",
    "contenedor": "contenedores",
    "charola": "charolas",
    "cubierto": "cubiertos",
    "envase": "envases",
    "plato": "platos",
}
_USE_FAMILIES = {
    "bebida_caliente": ("vaso",),
    "bebida_fria": ("vaso",),
    "comida_caliente": ("contenedor", "plato", "charola", "envase"),
    "comida_fria": ("contenedor", "plato", "charola", "envase"),
    "sopas": ("contenedor", "envase"),
    "frituras": ("contenedor", "charola", "plato"),
    "ensaladas": ("contenedor", "plato"),
    "panaderia": ("charola", "plato", "contenedor"),
    "salsas": ("envase", "contenedor"),
    "sandwiches": ("contenedor", "charola"),
    "frutas": ("contenedor", "plato"),
}
_MONTHS = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)


def _plain(value: str) -> str:
    """Minúsculas sin tildes, para comparar ciudades y textos."""

    decomposed = unicodedata.normalize("NFKD", value.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _money(value: Decimal) -> str:
    """COP sin decimales con separador de miles."""

    return f"{int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)):,}".replace(",", ".")


def _units(value: Decimal) -> str:
    """Unidades con separador de miles."""

    return f"{int(value):,}".replace(",", ".")


def _short(moment: date) -> str:
    return f"{moment.day} {_MONTHS[moment.month - 1]}"


def _span(start: date, end: date) -> str:
    if start == end:
        return _short(start)
    if start.month == end.month:
        return f"{start.day} y {_short(end)}"
    return f"{_short(start)} y {_short(end)}"


def _turns(questions: list[str]) -> list[Turn]:
    return [Turn(question=item, answer="", incomplete=False) for item in questions]


def _last_request(questions: list[str]) -> dict[str, Any] | None:
    """Última solicitud comercial que se pudo interpretar en el hilo."""

    history = _turns(questions)
    last = None
    for index in range(len(history)):
        parsed = extract_commercial(history[index]["question"], history[:index])
        if parsed is not None:
            last = parsed
    return last


def _delivery(questions: list[str]) -> bool | None:
    """La última mención de domicilio o retiro decide; ``None`` si no se mencionó."""

    requested: bool | None = None
    for text in questions:
        if _NO_DELIVERY.search(text):
            requested = False
        elif _DELIVERY.search(text):
            requested = True
    return requested


def _city(text: str, dataset: Dataset) -> str | None:
    plain = _plain(text)
    for city in dataset.cities():
        if re.search(rf"\b{re.escape(_plain(city))}\b", plain):
            return city
    return None


def _quantity(text: str) -> int | None:
    match = _QUANTITY.search(text)
    if match is None:
        return None
    raw = match.group(1)
    return int(raw.replace(".", "").replace(",", "")) if "," in raw or "." in raw else int(raw)


def _request(
    text: str,
    questions: list[str],
    dataset: Dataset,
) -> tuple[dict[str, Any], bool | None, str | None, dict[str, Any] | None]:
    """Solicitud del turno, domicilio, ciudad y lo interpretado de este mensaje.

    ``current`` es ``None`` cuando el mensaje no es comercial por sí mismo; el motor
    decide entonces si es un seguimiento válido («mejor 100», «en Cali», «con domicilio»)
    o si debe preguntar qué cotizar en lugar de repetir el turno anterior.
    """

    history = _turns(questions)
    current = extract_commercial(text, history)
    merged = {**empty_request(), **(_last_request(questions) or {}), **(current or {})}
    city = (current or {}).get("city") or _city(text, dataset) or merged.get("city")
    if city:
        merged["city"] = city
    quantity = _quantity(text)
    if quantity is not None:
        merged["quantity"] = quantity
    return merged, _delivery([*questions, text]), city, current


def _reference(product: Product) -> Reference:
    """Referencia del catálogo para reutilizar la validación de material.

    Las simuladas no tienen ficha colombiana; se valida con el material y el uso, y su
    URL apunta a la raíz oficial para no inventar una ficha.
    """

    return Reference.model_validate(
        {
            "sku": product.sku,
            "name": product.name,
            "family": product.family,
            "use": product.use,
            "capacity_oz": product.capacity_oz,
            "material": product.material,
            "brand": product.brand,
            "units_per_pack": product.units_per_pack,
            "packs_per_box": product.packs_per_box,
            "units_per_box": product.units_per_box,
            "temp_min_c": product.temp_min_c,
            "temp_max_c": product.temp_max_c,
            "country": product.country,
            "source_url": product.source_url or "https://colombia.carvajalempaques.com/",
            "captured_at": product.captured_at,
        }
    )


def _families(request: dict[str, Any]) -> tuple[str, ...]:
    family = request.get("family")
    if family:
        return (family,)
    return _USE_FAMILIES.get(request.get("use") or "", tuple(_FAMILIES_LABEL))


def _candidates(
    request: dict[str, Any], dataset: Dataset, catalog: Catalog
) -> tuple[list[Product], list[str]]:
    """Productos compatibles con la solicitud y motivos de descarte."""

    families = _families(request)
    capacity = request.get("capacity_oz")
    possible = [
        product
        for product in dataset.products
        if product.country == COUNTRY
        and product.use
        and product.family in families
        and (
            capacity is None
            or (
                product.capacity_oz is not None
                and abs(product.capacity_oz - capacity) <= CAPACITY_TOLERANCE_OZ
            )
        )
    ]
    if not possible:
        return [], []
    commercial = CommercialRequest(
        use=request.get("use"),
        temperature=request.get("temperature"),
        microwave=request.get("microwave"),
        capacity_oz=capacity,
        family=request.get("family"),
        quantity=request.get("quantity"),
        country=COUNTRY,
        city=request.get("city"),
        prefers_renewable=bool(request.get("prefers_renewable")),
    )
    valid, discarded = check_material(
        [_reference(product) for product in possible], commercial, catalog=catalog
    )
    valid_skus = {reference.sku for reference in valid}
    reasons = [item.reason for item in discarded[:MAX_DISCARDED]]
    chosen = [product for product in possible if product.sku in valid_skus]
    chosen.sort(key=lambda item: (request.get("use") not in item.uses, item.sku))
    return chosen, reasons


def _offer_for(offer: Offer, quantity: int) -> dict[str, Any]:
    """Presentación necesaria y totales de una oferta para la cantidad pedida."""

    units = offer.units_in_basis
    count = max(1, -(-quantity // units))
    bought = count * units
    subtotal = offer.amount * count
    unit_price = (offer.amount / units).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "basis": offer.basis,
        "units": units,
        "count": count,
        "units_bought": bought,
        "units_surplus": bought - quantity,
        "subtotal": str(int(subtotal)),
        "unit_price": str(unit_price),
    }


def _option(
    product: Product,
    offer: Offer,
    quantity: int,
    provider: dict[str, Any],
    *,
    allow_demo: bool = True,
) -> dict[str, Any]:
    presentation = _offer_for(offer, quantity)
    stock_status = offer.stock_status if allow_demo else "desconocido"
    return {
        "sku": product.sku,
        "name": product.name,
        "family": product.family,
        "brand": product.brand,
        "material": product.material,
        "capacity_oz": product.capacity_oz,
        "origin": product.origin,
        "simulated": product.simulated,
        "presentation": presentation,
        "units_requested": quantity,
        "currency": offer.currency,
        "tax_status": offer.tax_status,
        "provider": provider,
        "stock": {
            "status": stock_status,
            "units": offer.stock_units if allow_demo else None,
            "simulated": offer.stock_simulated and allow_demo,
        },
        "price_simulated": offer.price_simulated,
        "source_url": offer.source_url,
        "captured_at": offer.captured_at.isoformat(),
        "warning": _stock_warning(offer, presentation["units_bought"])
        if allow_demo
        else "La existencia no se consulta sin el modo demo.",
    }


def _stock_warning(offer: Offer, needed: int) -> str:
    if offer.stock_status == "insuficiente":
        available = offer.stock_units or 0
        return (
            f"La sede declara {_units(Decimal(available))} unidades y esta presentación "
            f"necesita {_units(Decimal(needed))}. Conviene confirmar antes de pedir."
        )
    if offer.stock_status == "desconocido":
        return "La sede no confirma existencia; se puede consultar al pedir."
    return ""


def _provider(provider: Any, sku: str) -> dict[str, Any]:
    return {
        "id": provider.provider_id,
        "ficha": f"{sku}-{provider.provider_id}",
        "name": provider.name,
        "city": provider.city,
        "address": provider.address,
        "phone": provider.phone,
        "lat": provider.latitude,
        "lon": provider.longitude,
        "precision": provider.precision,
    }


def _difference(primary: Product, other: Product, option: dict[str, Any]) -> str:
    parts = []
    if other.capacity_oz and primary.capacity_oz and other.capacity_oz != primary.capacity_oz:
        parts.append(f"{other.capacity_oz:g} oz en lugar de {primary.capacity_oz:g} oz")
    if other.material != primary.material:
        parts.append(f"material {other.material}")
    presentation = option["presentation"]
    parts.append(f"{presentation['count']} {presentation['basis']} de {presentation['units']}")
    if other.simulated:
        parts.append("referencia de demostración")
    return "; ".join(parts)


def _delivery_block(
    offer: Offer,
    subtotal: Decimal,
    dataset: Dataset,
    *,
    today: date,
    now: datetime,
) -> dict[str, Any]:
    free = subtotal >= dataset.free_from
    ship = Decimal(0) if free else offer.ship_cost
    start = today if now.hour < CUTOFF_HOUR else today + timedelta(days=1)
    first, last = window(start, offer.lead_days_min or 1, offer.lead_days_max or 1)
    return {
        "requested": True,
        "window": _span(first, last),
        "days_min": offer.lead_days_min,
        "days_max": offer.lead_days_max,
        "ship_cost": None if ship is None else str(int(ship)),
        "free": free,
        "free_from": str(int(dataset.free_from)),
        "covered": True,
        "cutoff": CUTOFF_HOUR,
    }


def _empty(kind: str, reply: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "reply": reply, **extra}


def quote(
    text: str,
    *,
    questions: list[str] | None = None,
    today: date | None = None,
    now: datetime | None = None,
    dataset: Dataset | None = None,
    catalog: Catalog | None = None,
    allow_demo: bool = True,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Cotiza el turno o devuelve el texto que pide el dato que falta."""

    questions = questions or []
    dataset = dataset or load_dataset()
    catalog = catalog or load_catalog()
    moment = now or datetime.now(BOGOTA)
    day = today or moment.date()
    request, mentioned_delivery, city, current = _request(text, questions, dataset)
    remembered = profile or {}
    if city is None and remembered.get("city"):
        known = {_plain(item): item for item in dataset.cities()}
        city = known.get(_plain(str(remembered["city"])))
        if city:
            request["city"] = city
    wants_delivery = (
        bool(remembered.get("delivery")) if mentioned_delivery is None else mentioned_delivery
    )
    preferences = " ".join(str(item) for item in remembered.get("preferences") or [])
    if preferences and re.search(r"renovable|compostable|biodegradable|ecol[oó]gic", preferences):
        request["prefers_renewable"] = True

    follow_up = (
        current is not None
        or _quantity(text) is not None
        or _city(text, dataset) is not None
        or mentioned_delivery is not None
        or _BARE_NUMBER.fullmatch(text.strip()) is not None
    )
    if not follow_up:
        if _GREETING.search(text):
            return _empty("aclaracion", GREETING_REPLY, request=request)
        if _last_request(questions) is None:
            return _empty("fuera_de_alcance", OUT_OF_SCOPE)
        return _empty("aclaracion", ASK_PRODUCT, request=request)
    if not request.get("family") and not request.get("use"):
        return _empty("fuera_de_alcance", OUT_OF_SCOPE)
    quantity = request.get("quantity")
    if _NEGATIVE.search(text) or quantity is None or quantity < 1:
        return _empty(
            "aclaracion",
            "¿Cuántas unidades necesitás? Con eso calculo la presentación, los sobrantes "
            "y el total.",
            request=request,
        )
    if city is None:
        return _empty(
            "aclaracion",
            "¿En qué ciudad lo buscás? Tengo sedes en "
            f"{', '.join(dataset.cities())}; con la ciudad te muestro precios y sedes.",
            request=request,
            cities=list(dataset.cities()),
        )

    products, discarded = _candidates(request, dataset, catalog)
    if not allow_demo:
        products = [product for product in products if not product.simulated]
    if not products:
        return _empty(
            "sin_resultados",
            NO_PRODUCTS + (f" Descarté: {' '.join(discarded)}" if discarded else ""),
            request=request,
            discarded=discarded,
        )

    options = []
    for product in products:
        offers = [
            offer
            for offer in dataset.offers_for(product.sku)
            if offer.city == city
            and offer.stock_status != "agotado"
            and (allow_demo or not offer.price_simulated)
        ]
        if not offers:
            continue
        offer = min(offers, key=lambda item: _offer_for(item, quantity)["subtotal"])
        provider = dataset.provider(offer.provider_id)
        if provider is None:
            continue
        options.append(
            (
                product,
                offer,
                _option(
                    product,
                    offer,
                    quantity,
                    _provider(provider, product.sku),
                    allow_demo=allow_demo,
                ),
            )
        )
    options.sort(key=lambda item: int(item[2]["presentation"]["subtotal"]))
    if not options:
        return _empty(
            "sin_resultados",
            f"Hoy no hay ofertas con existencia en {city}. Las sedes están en "
            f"{', '.join(dataset.cities())}; puedo cotizar en otra ciudad.",
            request=request,
            cities=list(dataset.cities()),
        )

    product, offer, primary = options[0]
    alternatives = []
    for other, _, other_option in options[1 : 1 + MAX_ALTERNATIVES]:
        alternatives.append(
            {
                **other_option,
                "difference": _difference(product, other, other_option),
            }
        )
    providers = []
    for item in dataset.offers_for(product.sku):
        if item.city != city:
            continue
        provider = dataset.provider(item.provider_id)
        if provider is not None and provider.provider_id not in {p["id"] for p in providers}:
            providers.append(_provider(provider, product.sku))

    subtotal = Decimal(primary["presentation"]["subtotal"])
    if wants_delivery and not allow_demo:
        delivery = {"requested": True, "covered": False}
    elif wants_delivery:
        delivery = _delivery_block(offer, subtotal, dataset, today=day, now=moment)
    else:
        delivery = {"requested": False}
    total: Decimal | None = subtotal
    if wants_delivery:
        total = (
            None if delivery.get("ship_cost") is None else subtotal + Decimal(delivery["ship_cost"])
        )

    label = _FAMILIES_LABEL.get(product.family, product.family)
    greeting = f"Listo, {remembered['name']}. " if remembered.get("name") else ""
    reply = (
        f"{greeting}Cotización para {_units(Decimal(quantity))} {label} en {city}. "
        f"La más económica es {product.name}: "
        f"{primary['presentation']['count']} "
        f"{primary['presentation']['basis']} de "
        f"{_units(Decimal(primary['presentation']['units']))} unidades por "
        f"{_money(subtotal)} COP."
    )
    return {
        "kind": "cotizacion",
        "reply": reply,
        "request": request,
        "primary": primary,
        "alternatives": alternatives,
        "providers": providers,
        "delivery": delivery,
        "total": None if total is None else str(int(total)),
        "demo": allow_demo,
        "city": city,
        "discarded": discarded,
    }
