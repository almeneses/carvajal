"""Motor de cotización del Cotizador (spec 042).

Determinista: reutiliza la extracción de intención de la spec 036 y la validación de
materiales del catálogo, y calcula presentaciones, dinero, existencias, sedes, mapa y
ventana de entrega. No llama al LLM ni a la red.

La respuesta es un diccionario serializable: el texto del turno y, cuando hay cotización,
el payload que la plantilla pinta dentro del hilo.

Dos entradas (spec 044, R4):

- ``quote_request(QuoteRequest)`` recibe los campos ya interpretados y no lee texto. Es la
  que usa la herramienta ``cotizar`` del agente y contiene toda la lógica de candidatos,
  ofertas, presentaciones, IVA, envío y total.
- ``quote(text)`` interpreta el texto del turno con las reglas de la spec 036 (historial,
  perfil, seguimientos, saludos) y delega en ``quote_request()``.

Temperatura (corrección «tinto»): si la solicitud no dice la temperatura y ni el uso ni el
microondas la implican, el motor la pregunta (``missing: ["temperatura"]``) siempre que la
respuesta cambie qué referencias son aptas. La decisión se toma con ``check_material`` sobre
las referencias de la familia y la capacidad pedidas: si el conjunto apto para contenido
caliente o para frío difiere del que se cotizaría sin temperatura, cotizar sería adivinar el
material. Si no difiere (vasos térmicos que sirven para ambos, platos de EPS, salseras), la
temperatura no cambia nada y no se pregunta. Así la regla cubre vasos, tapas, contenedores
y platos sin una lista fija de familias y sigue al catálogo cuando cambie.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from carvajal.agent.commercial import CAPACITY_TOLERANCE_OZ, CommercialRequest, check_material
from carvajal.agent.commercial_intent import USE_TEMPERATURE, empty_request, extract_commercial
from carvajal.agent.state import Turn
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.prompts import ABSENCE_POLICY
from carvajal.quotation.data import COUNTRY, Dataset, Offer, Product, load_dataset

__all__ = [
    "MAX_ALTERNATIVES",
    "OUT_OF_SCOPE",
    "QUOTE_VERSION",
    "QuoteRequest",
    "quote",
    "quote_request",
]

MAX_ALTERNATIVES = 2
MAX_DISCARDED = 3
QUOTE_VERSION = 2
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
ASK_QUANTITY = (
    "¿Cuántas unidades necesitás? Con eso calculo la presentación, los sobrantes y el total."
)
ASK_DRINK_TEMPERATURE = (
    "¿Es para bebida caliente o fría? El material cambia según la temperatura y no quiero "
    "cotizarte un vaso que no sirva."
)
ASK_TEMPERATURE = (
    "¿El contenido va caliente o frío? El material cambia según la temperatura y no quiero "
    "cotizarte una referencia que no sirva."
)
PENDING_DELIVERY = "Falta saber si va con domicilio o con retiro."
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
_LINE_WORDS = {
    "vaso": "vaso",
    "vasos": "vaso",
    "tapa": "tapa",
    "tapas": "tapa",
    "contenedor": "contenedor",
    "contenedores": "contenedor",
    "charola": "charola",
    "charolas": "charola",
    "cubierto": "cubierto",
    "cubiertos": "cubierto",
    "cuchara": "cubierto",
    "cucharas": "cubierto",
    "tenedor": "cubierto",
    "tenedores": "cubierto",
    "cuchillo": "cubierto",
    "cuchillos": "cubierto",
    "envase": "envase",
    "envases": "envase",
    "plato": "plato",
    "platos": "plato",
}
_LINE = re.compile(
    r"\b(" + "|".join(sorted(_LINE_WORDS, key=len, reverse=True)) + r")\b",
    re.I,
)
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


@dataclass(frozen=True, slots=True)
class QuoteRequest:
    """Solicitud de cotización ya interpretada (spec 044, ``data-model.md`` QuoteRequest).

    Ningún campo se deduce de texto libre. ``temperature`` se completa con el uso cuando
    este la implica («bebida_caliente» → caliente); ``city`` se normaliza contra el
    dataset; ``delivery = None`` significa que no se sabe si va con domicilio, y el total
    queda pendiente.
    """

    family: str | None = None
    use: str | None = None
    capacity_oz: float | None = None
    temperature: str | None = None
    microwave: bool | None = None
    prefers_renewable: bool | None = None
    quantity: int | None = None
    city: str | None = None
    delivery: bool | None = None


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
    if (
        quantity is None
        and _last_request(questions) is not None
        and _BARE_NUMBER.fullmatch(text.strip()) is not None
    ):
        digits = text.strip().replace(".", "").replace(",", "")
        quantity = int(digits) if digits.isdigit() else None
    if quantity is not None:
        merged["quantity"] = quantity
    return merged, _delivery([*questions, text]), city, current


def _families(request: dict[str, Any]) -> tuple[str, ...]:
    family = request.get("family")
    if family:
        return (family,)
    return _USE_FAMILIES.get(request.get("use") or "", tuple(_FAMILIES_LABEL))


def _possible(request: dict[str, Any], dataset: Dataset) -> list[Product]:
    """Productos de la familia y la capacidad pedidas, antes de validar el material."""

    families = _families(request)
    capacity = request.get("capacity_oz")
    return [
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


def _commercial(request: dict[str, Any]) -> CommercialRequest:
    return CommercialRequest(
        use=request.get("use"),
        temperature=request.get("temperature"),
        microwave=request.get("microwave"),
        capacity_oz=request.get("capacity_oz"),
        family=request.get("family"),
        quantity=request.get("quantity"),
        country=COUNTRY,
        city=request.get("city"),
        prefers_renewable=bool(request.get("prefers_renewable")),
    )


def _temperature_missing(request: dict[str, Any], dataset: Dataset, catalog: Catalog) -> bool:
    """Falta la temperatura y de ella depende qué referencias son aptas.

    Se compara lo que ``check_material`` aceptaría sin temperatura con lo que aceptaría
    para contenido caliente y para frío. El microondas implica calentar.
    """

    if request.get("temperature") or request.get("microwave"):
        return False
    possible = _possible(request, dataset)
    if not possible:
        return False

    def valid(temperature: str | None) -> set[str]:
        commercial = _commercial({**request, "temperature": temperature})
        accepted, _ = check_material(list(possible), commercial, catalog=catalog)
        return {reference.sku for reference in accepted}

    unknown = valid(None)
    return any(valid(temperature) != unknown for temperature in ("caliente", "fria"))


def _candidates(
    request: dict[str, Any], dataset: Dataset, catalog: Catalog
) -> tuple[list[Product], list[str]]:
    """Productos compatibles con la solicitud y motivos de descarte."""

    possible = _possible(request, dataset)
    if not possible:
        return [], []
    valid, discarded = check_material(list(possible), _commercial(request), catalog=catalog)
    valid_skus = {reference.sku for reference in valid}
    reasons = [item.reason for item in discarded[:MAX_DISCARDED]]
    chosen = [product for product in possible if product.sku in valid_skus]
    chosen.sort(key=lambda item: (request.get("use") not in item.uses, item.sku))
    return chosen, reasons


def _decimal_text(value: Decimal) -> str:
    """Importe como texto exacto; JSON no admite ``Decimal`` y no se redondea."""

    return str(value)


def _mention_families(text: str) -> tuple[str, ...]:
    """Familias nombradas en el mensaje, en el orden en que aparecen."""

    found: list[str] = []
    for match in _LINE.finditer(_plain(text)):
        family = _LINE_WORDS[match.group(1)]
        if family not in found:
            found.append(family)
    return tuple(found)


def _pending_lines(text: str, quoted_family: str) -> list[dict[str, str]]:
    """Líneas de una solicitud múltiple que quedan pendientes de elegir.

    El piloto emite una cotización por solicitud. Las demás líneas se conservan con su
    familia y estado, sin precio ni cantidad inventados.
    """

    families = _mention_families(text)
    if len(families) < 2:
        return []
    return [
        {"family": family, "label": _FAMILIES_LABEL.get(family, family), "status": "pendiente"}
        for family in families
        if family != quoted_family
    ]


def _offer_for(offer: Offer, quantity: int) -> dict[str, Any]:
    """Presentación necesaria y totales de una oferta para la cantidad pedida.

    Todos los importes se calculan con ``Decimal`` y se publican como texto exacto.
    """

    units = offer.units_in_basis
    count = max(1, -(-quantity // units))
    bought = count * units
    subtotal = offer.amount * count
    unit_price = (offer.amount / units).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "basis": offer.basis,
        "units": units,
        "units_requested": quantity,
        "count": count,
        "units_bought": bought,
        "units_surplus": bought - quantity,
        "price_per_presentation": _decimal_text(offer.amount),
        "subtotal": _decimal_text(subtotal),
        "unit_price": _decimal_text(unit_price),
    }


def _option(
    product: Product,
    offer: Offer,
    quantity: int,
    provider: dict[str, Any],
) -> dict[str, Any]:
    presentation = _offer_for(offer, quantity)
    return {
        "sku": product.sku,
        "name": product.name,
        "family": product.family,
        "brand": product.brand,
        "material": product.material,
        "capacity_oz": product.capacity_oz,
        "presentation": presentation,
        "units_requested": quantity,
        "currency": offer.currency,
        "tax_status": offer.tax_status,
        "tax": {
            "status": offer.tax_status,
            "rate": _decimal_text(offer.tax_rate) if offer.tax_rate is not None else None,
        },
        "source": {
            "name": offer.source_name,
            "url": offer.source_url,
            "captured_at": offer.captured_at.isoformat(),
        },
        "commercial_valid_until": (
            offer.source_valid_until.isoformat() if offer.source_valid_until else None
        ),
        "provider": provider,
        "seller": offer.source_name,
        "stock": {
            "status": offer.stock_status,
            "units": offer.stock_units,
            "evidence": [
                {
                    "source_url": item.source_url,
                    "observed_at": item.recorded_at.isoformat(),
                    "valid_until": item.valid_until.isoformat() if item.valid_until else None,
                }
                for item in offer.evidence
            ],
        },
        "source_url": offer.source_url,
        "captured_at": offer.captured_at.isoformat(),
        "warning": _stock_warning(offer, presentation["units_bought"]),
    }


def _stock_warning(offer: Offer, needed: int) -> str:
    if offer.stock_status == "verificado" and offer.stock_units is not None:
        if offer.stock_units < needed:
            return (
                f"La sede confirma {_units(Decimal(offer.stock_units))} unidades y esta "
                f"presentación necesita {_units(Decimal(needed))}. Conviene confirmar antes "
                "de pedir."
            )
        return ""
    return "La sede no confirma existencia vigente; se puede consultar al pedir."


def _shipping(offer: Offer, *, requested: bool | None, as_of: date) -> dict[str, Any]:
    """Envío separado del precio; solo se confirma con evidencia vigente y costo declarado.

    ``requested = None`` (no se sabe si va con domicilio) deja el envío pendiente.
    """

    if requested is None:
        return {
            "requested": None,
            "status": "pending",
            "cost": None,
            "free": False,
            "source_url": None,
            "observed_at": None,
            "valid_until": None,
            "note": PENDING_DELIVERY,
        }
    empty = {
        "requested": requested,
        "status": "not_requested" if not requested else "pending",
        "cost": None,
        "free": False,
        "source_url": None,
        "observed_at": None,
        "valid_until": None,
        "note": "",
    }
    if not requested:
        return empty
    terms = offer.delivery
    if terms is None:
        return {**empty, "note": "La sede no publica condiciones de envío."}
    evidence = (
        terms.source_url is not None
        and terms.checked_at is not None
        and terms.valid_until is not None
        and terms.valid_until >= as_of
    )
    if not evidence:
        return {
            **empty,
            "note": "El envío no tiene evidencia vigente con fuente y fecha.",
        }
    if terms.ship_cost is None:
        return {
            **empty,
            "source_url": terms.source_url,
            "observed_at": terms.checked_at.isoformat() if terms.checked_at else None,
            "valid_until": terms.valid_until.isoformat() if terms.valid_until else None,
            "note": "La evidencia no declara costo de envío.",
        }
    return {
        "requested": True,
        "status": "confirmed",
        "cost": _decimal_text(terms.ship_cost),
        "free": terms.ship_cost == 0,
        "days_min": terms.lead_days_min,
        "days_max": terms.lead_days_max,
        "source_url": terms.source_url,
        "observed_at": terms.checked_at.isoformat() if terms.checked_at else None,
        "valid_until": terms.valid_until.isoformat() if terms.valid_until else None,
        "note": terms.note,
    }


def _provider(provider: Any) -> dict[str, Any]:
    return {
        "id": provider.provider_id,
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
    return "; ".join(parts)


def _empty(kind: str, reply: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "reply": reply, **extra}


def _known_city(city: str | None, dataset: Dataset) -> str | None:
    """Ciudad con la grafía del dataset; una ciudad sin sede se conserva tal cual."""

    if city is None or not city.strip():
        return None
    known = {_plain(item): item for item in dataset.cities()}
    return known.get(_plain(city.strip()), city.strip())


def _needs(missing: str, reply: str, **extra: Any) -> dict[str, Any]:
    """Aclaración con el dato que falta (``needs_input`` de la herramienta ``cotizar``)."""

    return _empty("aclaracion", reply, missing=[missing], **extra)


def quote_request(
    request: QuoteRequest,
    *,
    today: date | None = None,
    now: datetime | None = None,
    dataset: Dataset | None = None,
    catalog: Catalog | None = None,
) -> dict[str, Any]:
    """Cotiza una solicitud ya interpretada o pide el dato que falta, sin leer texto."""

    moment = now or datetime.now(BOGOTA)
    day = today or moment.date()
    dataset = dataset or load_dataset(day.isoformat())
    catalog = catalog or load_catalog()
    city = _known_city(request.city, dataset)
    data: dict[str, Any] = {
        "use": request.use,
        "temperature": request.temperature or USE_TEMPERATURE.get(request.use or ""),
        "microwave": request.microwave,
        "capacity_oz": request.capacity_oz,
        "family": request.family,
        "quantity": request.quantity,
        "country": COUNTRY,
        "city": city,
        "prefers_renewable": bool(request.prefers_renewable),
    }

    if not request.family and not request.use:
        return _needs("producto", ASK_PRODUCT, request=data)
    if _temperature_missing(data, dataset, catalog):
        reply = ASK_DRINK_TEMPERATURE if request.family == "vaso" else ASK_TEMPERATURE
        return _needs("temperatura", reply, request=data)
    quantity = request.quantity
    if quantity is None or quantity < 1:
        return _needs("cantidad", ASK_QUANTITY, request=data)
    if city is None:
        return _needs(
            "ciudad",
            "¿En qué ciudad lo buscás? Tengo sedes en "
            f"{', '.join(dataset.cities())}; con la ciudad te muestro precios y sedes.",
            request=data,
            cities=list(dataset.cities()),
        )

    products, discarded = _candidates(data, dataset, catalog)
    if not products:
        return _empty(
            "sin_resultados",
            NO_PRODUCTS + (f" Descarté: {' '.join(discarded)}" if discarded else ""),
            request=data,
            discarded=discarded,
        )

    options = []
    for product in products:
        offers = [
            offer
            for offer in dataset.offers_for(product.sku)
            if offer.city == city and offer.stock_status != "no_disponible"
        ]
        if not offers:
            continue
        offer = min(offers, key=lambda item: _offer_for(item, quantity)["subtotal"])
        provider = dataset.provider(offer.site_id)
        if provider is None:
            continue
        options.append(
            (
                product,
                offer,
                _option(product, offer, quantity, _provider(provider)),
            )
        )
    options.sort(key=lambda item: Decimal(item[2]["presentation"]["subtotal"]))
    if not options:
        sedes = dataset.providers_in(city)
        return _empty(
            "sin_resultados",
            f"Hoy no hay precios publicados para esta búsqueda en {city}. "
            f"Tengo {len(sedes)} sede(s) del directorio con contacto; "
            "puedo cotizar en otra ciudad con precio publicado.",
            request=data,
            cities=list(dataset.cities()),
            providers=[_provider(item) for item in sedes],
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
        provider = dataset.provider(item.site_id)
        if provider is not None and provider.provider_id not in {p["id"] for p in providers}:
            providers.append(_provider(provider))

    wants_delivery = request.delivery
    subtotal = Decimal(primary["presentation"]["subtotal"])
    shipping = _shipping(offer, requested=wants_delivery, as_of=day)
    if wants_delivery is False:
        total: Decimal | None = subtotal
    elif shipping["status"] == "confirmed" and shipping["cost"] is not None:
        total = subtotal + Decimal(shipping["cost"])
    else:
        total = None

    commercial_valid_until = (
        offer.source_valid_until.isoformat() if offer.source_valid_until else None
    )
    commercial = {
        "version": QUOTE_VERSION,
        "currency": offer.currency,
        "presentation": primary["presentation"],
        "tax": primary["tax"],
        "shipping": shipping,
        "subtotal": _decimal_text(subtotal),
        "total": None if total is None else _decimal_text(total),
        "valid_until": commercial_valid_until,
        "source": primary["source"],
        "pending_lines": [],
    }
    delivery = {
        "requested": wants_delivery,
        "covered": shipping["status"] == "confirmed",
        "ship_cost": shipping["cost"],
        "free": shipping["free"],
        "days_min": shipping.get("days_min"),
        "days_max": shipping.get("days_max"),
        "source_url": shipping["source_url"],
        "valid_until": shipping["valid_until"],
        "note": shipping["note"],
    }

    label = _FAMILIES_LABEL.get(product.family, product.family)
    reply = (
        f"Cotización para {_units(Decimal(quantity))} {label} en {city}. "
        f"La más económica es {product.name}: "
        f"{primary['presentation']['count']} "
        f"{primary['presentation']['basis']} de "
        f"{_units(Decimal(primary['presentation']['units']))} unidades por "
        f"{_money(Decimal(primary['presentation']['price_per_presentation']))} COP."
    )
    if wants_delivery is None:
        reply += f" {PENDING_DELIVERY} El total queda pendiente."
    elif wants_delivery:
        if shipping["status"] == "confirmed":
            reply += (
                " El envío tiene costo cero respaldado."
                if shipping["free"]
                else f" El envío cuesta {_money(Decimal(shipping['cost'] or '0'))} COP."
            )
        else:
            reply += " El envío no tiene precio respaldado; el total final queda pendiente."
    if total is not None:
        reply += f" Total: {_money(total)} COP."
    if commercial_valid_until:
        reply += f" Vigencia publicada por el vendedor hasta {commercial_valid_until}."

    return {
        "kind": "cotizacion",
        "version": QUOTE_VERSION,
        "reply": reply,
        "request": data,
        "primary": primary,
        "alternatives": alternatives,
        "providers": providers,
        "delivery": delivery,
        "commercial": commercial,
        "pending_lines": [],
        "total": None if total is None else _decimal_text(total),
        "city": city,
        "discarded": discarded,
    }


def quote(
    text: str,
    *,
    questions: list[str] | None = None,
    today: date | None = None,
    now: datetime | None = None,
    dataset: Dataset | None = None,
    catalog: Catalog | None = None,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Interpreta el turno, cotiza con ``quote_request()`` o pide el dato que falta."""

    questions = questions or []
    moment = now or datetime.now(BOGOTA)
    day = today or moment.date()
    dataset = dataset or load_dataset(day.isoformat())
    catalog = catalog or load_catalog()
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

    # Una cantidad negativa en el texto se descarta: el motor la pregunta.
    quantity = None if _NEGATIVE.search(text) else request.get("quantity")
    result = quote_request(
        QuoteRequest(
            family=request.get("family"),
            use=request.get("use"),
            capacity_oz=request.get("capacity_oz"),
            temperature=request.get("temperature"),
            microwave=request.get("microwave"),
            prefers_renewable=request.get("prefers_renewable"),
            quantity=quantity,
            city=city,
            delivery=wants_delivery,
        ),
        today=day,
        now=moment,
        dataset=dataset,
        catalog=catalog,
    )
    # La solicitud publicada es la interpretada del texto, como antes de la spec 044.
    result["request"] = request
    if result["kind"] != "cotizacion":
        return result

    if remembered.get("name"):
        result["reply"] = f"Listo, {remembered['name']}. {result['reply']}"
    pending_lines = _pending_lines(text, result["primary"]["family"])
    if pending_lines:
        result["pending_lines"] = pending_lines
        result["commercial"]["pending_lines"] = pending_lines
        result["reply"] += (
            " Quedan líneas pendientes: " + ", ".join(item["label"] for item in pending_lines) + "."
        )
    return result
