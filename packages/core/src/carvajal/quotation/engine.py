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

Unidad de compra (spec 044, simulación): ``QuoteRequest.unit`` dice en qué viene la
cantidad (``unidad`` por defecto, ``caja``, ``paquete`` o ``paca``). El motor la convierte a
unidades por referencia, de forma determinista, con la presentación declarada: la del
catálogo (``units_per_box``, ``units_per_pack`` o la «paca x N» del nombre) o la del contrato
comercial (la base de la oferta publicada). Si las fuentes no declaran esa presentación o se
contradicen, la referencia no se cotiza; si ninguna se puede convertir, el motor pide la
cantidad (``missing: ["cantidad"]``) en vez de suponer cuántas unidades trae. Cada opción
publica la conversión hecha (``conversion``) para que nadie la recalcule. Si las opciones
convierten a distinto número de unidades, no son el mismo pedido: se ordenan por precio por
unidad y la respuesta lo dice, con la conversión de cada una. Si el nombre de la ficha
declara otra presentación («paca x 200») y el catálogo no declara la pedida, la oferta del
contrato rotulada con la pedida no basta para convertir.

Temperatura (corrección «tinto»): si la solicitud no dice la temperatura y ni el uso ni el
microondas la implican, el motor la pregunta (``missing: ["temperatura"]``) siempre que la
respuesta cambie qué referencias son aptas. La decisión se toma con ``check_material`` sobre
las referencias de la familia y la capacidad pedidas: si el conjunto apto para contenido
caliente o para frío difiere del que se cotizaría sin temperatura, cotizar sería adivinar el
material. Si no difiere (vasos térmicos que sirven para ambos, platos de EPS, salseras), la
temperatura no cambia nada y no se pregunta. Así la regla cubre vasos, tapas, contenedores
y platos sin una lista fija de familias y sigue al catálogo cuando cambie.

Referencia nombrada (spec 044, ronda 4): ``QuoteRequest.sku`` o ``QuoteRequest.name`` fijan
la referencia que nombró la persona («P1», «J2», «CO-CTT-16», «Vaso Gold de 14 oz») con la
misma búsqueda por nombre de ``buscar_producto`` (``carvajal.agent.reference_names``). Si
identifica una sola, se cotiza exactamente esa (presentación, unidad, conversión, IVA, envío
y total) sin alternativas y sin preguntar la temperatura; si la persona dijo un uso, una
temperatura o el microondas, ``check_material`` sigue mandando y una referencia no apta no
se cotiza (``reason: "referencia_no_apta"``). Un nombre que coincide con varias pide cuál
(``missing: ["referencia"]`` con ``candidates``); uno que no coincide con ninguna, o una
referencia sin precio publicado en la ciudad, devuelve ``sin_resultados`` con su ``reason``
y nunca se sustituye por otra referencia.

``sku`` solo cuenta como SKU exacto: si no lo es, manda ``name`` (y el texto del SKU se
busca como nombre solo cuando no hay ``name``); si los dos fijan referencias distintas, se
pregunta cuál (``conflict``). Si el nombre o la familia pedida dicen otra familia que la de
la referencia («vaso P1» es un contenedor), se cotiza la nombrada y el resultado lo avisa
(``family_mismatch``). Un nombre que solo describe un tipo de producto («vaso de icopor»,
``is_generic_name``) no nombra una referencia: se cotiza como la búsqueda por familia
(temperatura, la más económica y alternativas) entre las referencias que coinciden.

Precios convertidos (2026-11-25): las referencias copiadas de México llevan un precio en COP
convertido con una tasa fechada, sin vendedor ni sede, válido como referencia en cualquier
ciudad. Un precio publicado en la ciudad siempre va antes: la convertida solo es la opción
principal si no hay ninguna publicada o si la persona nombró esa referencia. La respuesta, la
advertencia y la fuente lo dicen.
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
from carvajal.agent.reference_names import (
    exact_sku,
    is_generic_name,
    name_family,
    resolve_reference,
)
from carvajal.agent.state import Turn
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.prompts import ABSENCE_POLICY
from carvajal.quotation.data import (
    COUNTRY,
    REFERENCE_SITE_ID,
    Dataset,
    Offer,
    Product,
    load_dataset,
)

__all__ = [
    "MAX_ALTERNATIVES",
    "OUT_OF_SCOPE",
    "QUOTE_VERSION",
    "UNITS",
    "Conversion",
    "QuoteRequest",
    "conversion",
    "declared_sizes",
    "published_presentations",
    "quote",
    "quote_request",
]

MAX_ALTERNATIVES = 2
MAX_DISCARDED = 3
MAX_CANDIDATES = 5  # candidatas de un nombre ambiguo que se listan
QUOTE_VERSION = 2
BOGOTA = ZoneInfo("America/Bogota")
COP = "COP"
OUT_OF_SCOPE = f"{ABSENCE_POLICY['out_of_scope']['say']} {ABSENCE_POLICY['out_of_scope']['offer']}"
NO_PRODUCTS = (
    "No encontré en el catálogo referencias que cumplan lo que pides. "
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
    "Hola. Cuéntame qué producto, cuántas unidades y en qué ciudad lo buscas; con eso te "
    "armo la cotización."
)
ASK_PRODUCT = (
    "Dime qué quieres cotizar: producto, cantidad y ciudad. Por ejemplo, «20 vasos para "
    "bebida caliente en Bogotá»."
)
ASK_QUANTITY = (
    "¿Cuántas unidades necesitas? Con eso calculo la presentación, los sobrantes y el total."
)
ASK_DRINK_TEMPERATURE = (
    "¿Es para bebida caliente o fría? El material cambia según la temperatura y no quiero "
    "cotizarte un vaso que no sirva."
)
ASK_TEMPERATURE = (
    "¿El contenido va caliente o frío? El material cambia según la temperatura y no quiero "
    "cotizarte una referencia que no sirva."
)
CONVERTED_WARNING = (
    "Precio de referencia convertido desde el catálogo de México: no lo publica ningún vendedor "
    "en Colombia. Confirma precio, IVA y existencia con un distribuidor."
)
CONVERTED_SHIPPING = "Es un precio de referencia sin vendedor: el envío no se puede calcular."
PENDING_DELIVERY = "Falta saber si va con domicilio o con retiro."
_QUANTITY = re.compile(
    r"\b(?:mejor|ahora|sean|ser[ií]an|son|para)\s+(\d{1,3}(?:[.,]\d{3})+|\d+)\b", re.I
)
# Unidades de compra que entiende ``QuoteRequest.unit`` y su plural para los textos.
UNITS = ("unidad", "caja", "paquete", "paca")
_UNIT_PLURAL = {"unidad": "unidades", "caja": "cajas", "paquete": "paquetes", "paca": "pacas"}
_TIMES = "\u00d7"  # signo de multiplicar de la conversión («10 cajas por 200»)
_UNIT_ARTICLE = {"unidad": "una", "caja": "una", "paquete": "un", "paca": "una"}
# Presentación declarada en el nombre de la ficha: «(paca x 200)», «(paquete x 20)».
_NAME_PRESENTATION = re.compile(r"\b(paca|paquete)\s*(?:x|por)\s*(\d{1,6})\b", re.I)
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
    queda pendiente. ``sku`` o ``name`` fijan la referencia nombrada (ver el módulo).
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
    # Unidad en que viene ``quantity`` (``UNITS``); ``None`` equivale a ``unidad``.
    unit: str | None = None
    # Referencia que nombró la persona: SKU exacto o nombre/código («P1», «Vaso Gold 14 oz»).
    sku: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class _Conversion:
    """Cantidad pedida en una presentación, convertida a unidades de una referencia."""

    quantity: int
    unit: str
    units_per: int
    source: str

    @property
    def units(self) -> int:
        return self.quantity * self.units_per

    def as_dict(self) -> dict[str, Any]:
        label = self.unit if self.quantity == 1 else _UNIT_PLURAL[self.unit]
        per = _units(Decimal(self.units_per))
        return {
            "quantity": self.quantity,
            "unit": self.unit,
            "units_per": self.units_per,
            "units": self.units,
            "source": self.source,
            "text": (
                f"{_units(Decimal(self.quantity))} {label} {_TIMES} {per} "
                f"= {_units(Decimal(self.units))} unidades"
            ),
        }


def declared_sizes(product: Product, unit: str, dataset: Dataset) -> dict[int, list[str]]:
    """Tamaños que el catálogo y el contrato declaran para la presentación ``unit``.

    - Catálogo: ``units_per_pack`` es el paquete; ``units_per_box`` es la caja solo si la
      ficha declara además el paquete (si no, la ficha conoce una sola presentación y no
      dice si es caja o paquete); la «paca x N» o el «paquete x N» del nombre.
    - Contrato: la base de las ofertas publicadas de la referencia (``caja`` o ``paquete``).

    Si el nombre declara otra presentación y el catálogo no declara ``unit``, el contrato no
    basta: devuelve vacío (no se sabe con certeza). Devuelve tamaño → fuentes; más de un
    tamaño es una contradicción.
    """

    sizes: dict[int, list[str]] = {}

    def add(size: int | None, source: str) -> None:
        if size and source not in sizes.setdefault(size, []):
            sizes[size].append(source)

    if unit == "paquete":
        add(product.units_per_pack, "catálogo")
    if unit == "caja" and product.units_per_pack:
        add(product.units_per_box, "catálogo")
    for match in _NAME_PRESENTATION.finditer(product.name):
        if _plain(match.group(1)) == unit:
            add(int(match.group(2)), "catálogo")
    # El nombre de la ficha declara otra presentación («paca x 200») y el catálogo no dice
    # nada de ``unit``: una oferta del contrato rotulada ``unit`` contradice al catálogo (la
    # «caja de 200» del contrato es la paca de la ficha), así que no se cuenta.
    other_in_name = any(
        _plain(match.group(1)) != unit for match in _NAME_PRESENTATION.finditer(product.name)
    )
    if other_in_name and not sizes:
        return sizes
    for offer in dataset.offers_for(product.sku):
        if offer.basis == unit:
            add(offer.units_in_basis, "contrato")
    return sizes


def conversion(
    product: Product, quantity: int, unit: str | None, dataset: Dataset
) -> _Conversion | None:
    """Conversión a unidades de la referencia; ``None`` si no se sabe con certeza.

    Pública desde la spec 045 (``convertir_presentacion`` la reutiliza sin duplicarla).
    """

    if unit in (None, "unidad"):
        return _Conversion(quantity=quantity, unit="unidad", units_per=1, source="")
    sizes = declared_sizes(product, unit, dataset)
    if len(sizes) != 1:
        return None
    [(size, sources)] = sizes.items()
    return _Conversion(quantity=quantity, unit=unit, units_per=size, source=" y ".join(sources))


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


def _possible(
    request: dict[str, Any], dataset: Dataset, skus: frozenset[str] | None = None
) -> list[Product]:
    """Productos de la familia y la capacidad pedidas, antes de validar el material.

    ``skus`` limita la búsqueda a las referencias de un nombre genérico («vaso de icopor»).
    """

    families = _families(request)
    capacity = request.get("capacity_oz")
    return [
        product
        for product in dataset.products
        if product.country == COUNTRY
        and (skus is None or product.sku in skus)
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


def _temperature_missing(
    request: dict[str, Any],
    dataset: Dataset,
    catalog: Catalog,
    skus: frozenset[str] | None = None,
) -> bool:
    """Falta la temperatura y de ella depende qué referencias son aptas.

    Se compara lo que ``check_material`` aceptaría sin temperatura con lo que aceptaría
    para contenido caliente y para frío. El microondas implica calentar.
    """

    if request.get("temperature") or request.get("microwave"):
        return False
    possible = _possible(request, dataset, skus)
    if not possible:
        return False

    def valid(temperature: str | None) -> set[str]:
        commercial = _commercial({**request, "temperature": temperature})
        accepted, _ = check_material(list(possible), commercial, catalog=catalog)
        return {reference.sku for reference in accepted}

    unknown = valid(None)
    return any(valid(temperature) != unknown for temperature in ("caliente", "fria"))


def _candidates(
    request: dict[str, Any],
    dataset: Dataset,
    catalog: Catalog,
    skus: frozenset[str] | None = None,
) -> tuple[list[Product], list[str]]:
    """Productos compatibles con la solicitud y motivos de descarte."""

    possible = _possible(request, dataset, skus)
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
        "price_kind": "convertido" if offer.converted else "publicado",
        "warning": _stock_warning(offer, presentation["units_bought"]),
    }


def _stock_warning(offer: Offer, needed: int) -> str:
    if offer.converted:
        return CONVERTED_WARNING
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
        note = CONVERTED_SHIPPING if offer.converted else "La sede no publica condiciones de envío."
        return {**empty, "note": note}
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


def _reference_provider(offer: Offer) -> dict[str, Any]:
    """Lugar del vendedor en un precio convertido: sin sede, dirección ni teléfono."""

    return {
        "id": REFERENCE_SITE_ID,
        "name": offer.source_name,
        "city": offer.city,
        "address": None,
        "phone": None,
        "lat": None,
        "lon": None,
        "precision": "desconocida",
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
    if option.get("conversion"):
        # Su propia conversión: con cajas o pacas cada referencia es un pedido distinto.
        parts.append(option["conversion"]["text"])
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


def _candidate(reference: Product) -> dict[str, Any]:
    return {
        "sku": reference.sku,
        "name": reference.name,
        "family": reference.family,
        "material": reference.material,
        "capacity_oz": reference.capacity_oz,
    }


@dataclass(frozen=True, slots=True)
class _Named:
    """Referencia nombrada que se cotiza; ``asked_family``, la familia que dijo la persona
    cuando la referencia es de otra («vaso P1»: el P1 es un contenedor)."""

    reference: Product
    asked_family: str | None = None


@dataclass(frozen=True, slots=True)
class _GenericName:
    """Nombre que solo describe un tipo de producto: se cotiza como búsqueda por familia
    entre las referencias que coinciden con él."""

    skus: frozenset[str]


def _not_found(data: dict[str, Any]) -> dict[str, Any]:
    return _empty(
        "sin_resultados",
        "No encontré en el catálogo de Colombia una referencia con ese nombre o código, y "
        "no cotizo otra en su lugar. Puedo buscarla por uso, familia o capacidad.",
        request=data,
        reason="referencia_no_encontrada",
    )


def _ask_which(
    named: list[Product],
    data: dict[str, Any],
    *,
    partial: bool = False,
    conflict: bool = False,
) -> dict[str, Any]:
    """Aclaración con las candidatas: nombre ambiguo, parcial o en conflicto con el SKU."""

    listed = "; ".join(f"{item.name} ({item.sku})" for item in named[:MAX_CANDIDATES])
    if conflict:
        lead = "El SKU y el nombre que diste corresponden a referencias distintas"
    elif partial:
        lead = "No encontré una referencia con exactamente ese nombre; las más parecidas son"
    else:
        lead = "Ese nombre coincide con varias referencias"
    extra: dict[str, Any] = {"conflict": True} if conflict else {}
    return _needs(
        "referencia",
        f"{lead}: {listed}. ¿Cuál cotizo?",
        request=data,
        candidates=[_candidate(item) for item in named[:MAX_CANDIDATES]],
        candidates_total=len(named),
        partial=partial,
        **extra,
    )


def _generic_name(
    found: list[Product], name: str, request: QuoteRequest, data: dict[str, Any], catalog: Catalog
) -> _GenericName:
    """Limita la búsqueda por familia a las referencias del nombre genérico.

    La familia es la pedida si está entre las del nombre; si no, la que dice el sustantivo
    del nombre («vaso» también coincide con las tapas «para vaso») o la única que hay. Si
    el nombre abarca varias familias y no dice cuál («icopor»), queda sin familia.
    """

    families = {reference.family for reference in found}
    said = name_family(name, catalog)
    if request.family in families:
        family: str | None = request.family
    elif said in families:
        family = said
    elif len(families) == 1:
        family = next(iter(families))
    else:
        family = None
    if family is not None:
        found = [reference for reference in found if reference.family == family]
    data["family"] = family
    return _GenericName(skus=frozenset(reference.sku for reference in found))


def _asked_family(
    name: str, reference: Product, request: QuoteRequest, catalog: Catalog
) -> str | None:
    """Familia que dijo la persona si no es la de la referencia, o ``None``.

    Manda el sustantivo de tipo del nombre («vaso P1» → vaso); si el nombre no trae uno
    («P1», un SKU), la familia que pasó la llamada. «Portacomidas J2» con ``family="vaso"``
    no avisa nada: el nombre dice portacomidas y el J2 lo es.
    """

    said = name_family(name, catalog) if name else None
    asked = said if said is not None else request.family
    return asked if asked and asked != reference.family else None


def _named_reference(
    request: QuoteRequest, data: dict[str, Any], dataset: Dataset, catalog: Catalog
) -> _Named | _GenericName | dict[str, Any]:
    """Referencia que fijan ``sku`` o ``name``, o la respuesta si no se puede cotizar.

    Devuelve la ficha si identifica exactamente una referencia apta para lo que dijo la
    persona; el conjunto de referencias si el nombre es genérico; si no, la aclaración
    (varias candidatas, coincidencia parcial o SKU y nombre en conflicto) o el
    ``sin_resultados`` (no existe, no es apta o no tiene ningún precio publicado).
    """

    pool = [reference for reference in catalog.references if reference.country == COUNTRY]
    sku_text = (request.sku or "").strip()
    name = (request.name or "").strip()
    by_sku = exact_sku(sku_text, pool) if sku_text else None
    if by_sku is None and not name:
        # Sin nombre, el texto del SKU se busca como nombre («P1» pasado en ``sku``).
        name = sku_text
    narrow = {"family": request.family, "capacity_oz": request.capacity_oz}
    if by_sku is None and is_generic_name(name, catalog):
        generic = resolve_reference(name, pool, catalog, family=request.family)
        if generic.named and not generic.partial:
            return _generic_name(generic.named, name, request, data, catalog)
    if by_sku is not None:
        named, partial = [by_sku], False
        if name and name != sku_text:
            by_name = resolve_reference(name, pool, catalog, **narrow)
            skus_by_name = {item.sku for item in by_name.named}
            if by_name.named and not by_name.partial and by_sku.sku not in skus_by_name:
                # El SKU y el nombre fijan referencias distintas: no gana ninguno en silencio.
                return _ask_which([by_sku, *by_name.named], data, conflict=True)
    elif name:
        match = resolve_reference(name, pool, catalog, **narrow)
        named, partial = match.named, match.partial
    else:
        named, partial = [], False
    if not named:
        return _not_found(data)
    if len(named) > 1 or partial:
        return _ask_which(named, data, partial=partial)
    [reference] = named
    data["family"] = reference.family
    if data.get("use") or data.get("temperature") or data.get("microwave"):
        # La persona dijo para qué es: la guía de materiales sigue mandando.
        _, discarded = check_material([reference], _commercial(data), catalog=catalog)
        if discarded:
            reason = discarded[0].reason
            # Sin ``request.sku``: una referencia no apta no queda fijada para el seguimiento.
            return _empty(
                "sin_resultados",
                f"{reason} No la cotizo para ese uso; puedo buscar una referencia que sí sirva.",
                request=data,
                reason="referencia_no_apta",
                sku=reference.sku,
                discarded=[reason],
            )
    data["sku"] = reference.sku
    if not _priced_cities(reference, dataset):
        return _without_price(reference, data, dataset)
    return _Named(reference, _asked_family(name, reference, request, catalog))


def published_presentations(
    product: Product, dataset: Dataset, city: str | None = None
) -> list[str]:
    """Presentación publicada de la referencia: la del nombre («paca x 200») o, si el nombre
    no declara ninguna, las de sus ofertas en la ciudad («caja de 1000»).

    Con ``city=None`` (spec 045, ``convertir_presentacion``, que no recibe ciudad) cuentan
    las ofertas publicadas de todas las ciudades.
    """

    found = [
        f"{_plain(match.group(1))} de {match.group(2)}"
        for match in _NAME_PRESENTATION.finditer(product.name)
    ]
    if found:
        return found
    return sorted(
        {
            f"{offer.basis} de {offer.units_in_basis}"
            for offer in dataset.offers_for(product.sku)
            if city is None or offer.city == city
        }
    )


# Alias privados de la 044: el motor y quien los importe siguen funcionando igual.
_declared_sizes = declared_sizes
_conversion = conversion
_published_presentations = published_presentations
# Conversión con certeza (cantidad, presentación, unidades por presentación y fuente).
Conversion = _Conversion


def _priced_cities(reference: Product, dataset: Dataset) -> list[str]:
    """Ciudades donde la referencia tiene un precio publicado que se puede cotizar."""

    return sorted(
        {
            offer.city
            for offer in dataset.offers_for(reference.sku)
            if offer.stock_status != "no_disponible"
        }
    )


def _without_price(reference: Product, data: dict[str, Any], dataset: Dataset) -> dict[str, Any]:
    """La referencia nombrada no tiene precio en la ciudad: sedes, sin sustituirla."""

    city = data.get("city")
    priced = _priced_cities(reference, dataset)
    sedes = dataset.providers_in(city) if city else ()
    reply = f"Hoy no hay precio publicado de {reference.name}" + (f" en {city}." if city else ".")
    if priced:
        reply += f" Tiene precio publicado en {', '.join(priced)}."
    if sedes:
        reply += f" Tengo {len(sedes)} sede(s) del directorio con contacto en {city}."
    reply += " No la sustituyo por otra referencia."
    return _empty(
        "sin_resultados",
        reply,
        request=data,
        reason="sin_precio",
        sku=reference.sku,
        name=reference.name,
        reference=_candidate(reference),
        cities=list(dataset.cities()),
        providers=[_provider(item) for item in sedes],
        priced_cities=priced,
    )


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

    named: Product | None = None
    asked_family: str | None = None
    skus: frozenset[str] | None = None
    if request.sku or request.name:
        resolved = _named_reference(request, data, dataset, catalog)
        if isinstance(resolved, dict):
            return resolved
        if isinstance(resolved, _GenericName):
            skus = resolved.skus
        else:
            named, asked_family = resolved.reference, resolved.asked_family
    if named is None and not data["family"] and not data["use"]:
        return _needs("producto", ASK_PRODUCT, request=data)
    # Con la referencia fijada la temperatura no cambia qué se cotiza: no se pregunta.
    if named is None and _temperature_missing(data, dataset, catalog, skus):
        reply = ASK_DRINK_TEMPERATURE if data["family"] == "vaso" else ASK_TEMPERATURE
        return _needs("temperatura", reply, request=data)
    quantity = request.quantity
    if quantity is None or quantity < 1:
        return _needs("cantidad", ASK_QUANTITY, request=data)
    unit = request.unit or "unidad"
    if unit not in UNITS:
        return _needs("cantidad", ASK_QUANTITY, request=data)
    if unit != "unidad":
        data["unit"] = unit
    if city is None:
        return _needs(
            "ciudad",
            "¿En qué ciudad lo buscas? Tengo sedes en "
            f"{', '.join(dataset.cities())}; con la ciudad te muestro precios y sedes.",
            request=data,
            cities=list(dataset.cities()),
        )

    if named is not None:
        here = [
            offer
            for offer in dataset.offers_for(named.sku)
            if offer.city == city and offer.stock_status != "no_disponible"
        ]
        if not here:
            return _without_price(named, data, dataset)
        products, discarded = [named], []
    else:
        products, discarded = _candidates(data, dataset, catalog, skus)
    if not products:
        return _empty(
            "sin_resultados",
            NO_PRODUCTS + (f" Descarté: {' '.join(discarded)}" if discarded else ""),
            request=data,
            discarded=discarded,
        )

    options = []
    # Referencias con precio en la ciudad cuya presentación pedida no se sabe convertir.
    unconvertible: list[Product] = []
    for product in products:
        offers = [
            offer
            for offer in dataset.offers_for(product.sku)
            if offer.city == city and offer.stock_status != "no_disponible"
        ]
        if not offers:
            continue
        conversion = _conversion(product, quantity, unit, dataset)
        if conversion is None:
            unconvertible.append(product)
            continue
        units = conversion.units
        offer = min(offers, key=lambda item: _offer_for(item, units)["subtotal"])
        if offer.converted:
            place = _reference_provider(offer)
        else:
            provider = dataset.provider(offer.site_id)
            if provider is None:
                continue
            place = _provider(provider)
        option = _option(product, offer, units, place)
        if unit != "unidad":
            option["conversion"] = conversion.as_dict()
        options.append((product, offer, option))
    # Con cajas, paquetes o pacas cada referencia puede convertir a otro número de unidades:
    # el subtotal compara pedidos distintos, así que se ordena por precio por unidad.
    mixed = len({option["units_requested"] for _, _, option in options}) > 1
    if mixed:
        options.sort(
            key=lambda item: (
                Decimal(item[2]["presentation"]["unit_price"]),
                Decimal(item[2]["presentation"]["subtotal"]),
            )
        )
    else:
        options.sort(key=lambda item: Decimal(item[2]["presentation"]["subtotal"]))
    # Un precio publicado siempre va antes que uno convertido (orden estable).
    options.sort(key=lambda item: item[1].converted)
    if unconvertible:
        # Primero los de conversión: explican por qué falta una referencia pedida. La lista
        # completa respeta el tope.
        discarded = [
            *(
                f"{item.name}: no se sabe con certeza cuántas unidades trae {_UNIT_ARTICLE[unit]} "
                f"{unit} (el catálogo y el contrato no lo declaran)."
                for item in unconvertible
            ),
            *discarded,
        ][:MAX_DISCARDED]
    if not options and unconvertible and named is not None:
        # La referencia nombrada: su nombre y su presentación publicada, no «esas cajas».
        published = _published_presentations(named, dataset, city)
        shown = f" Su presentación publicada es {', '.join(published)}." if published else ""
        bases = [_UNIT_PLURAL.get(item.split()[0]) for item in published]
        other = next((item for item in bases if item and item != _UNIT_PLURAL[unit]), None)
        ask = f"en unidades o en {other}" if other else "en unidades"
        return _needs(
            "cantidad",
            f"No sé con certeza cuántas unidades trae {_UNIT_ARTICLE[unit]} {unit} de "
            f"{named.name}: el catálogo y el contrato no declaran esa presentación para esta "
            f"referencia.{shown} ¿Me das la cantidad {ask}?",
            request=data,
            unit=unit,
            unconvertible=[named.sku],
            presentations=published,
        )
    if not options and unconvertible:
        plural = _UNIT_PLURAL[unit]
        return _needs(
            "cantidad",
            f"No sé con certeza cuántas unidades trae cada una de esas {plural}: el catálogo "
            "y el contrato no declaran esa presentación para las referencias que aplican. "
            "¿Me das la cantidad en unidades?",
            request=data,
            unit=unit,
            unconvertible=[item.sku for item in unconvertible],
        )
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

    if offer.converted:
        # Nadie la vende a ese precio: se dan los contactos del directorio de la ciudad.
        providers = [_provider(item) for item in dataset.providers_in(city)]

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
    asked = (
        f"{_units(Decimal(quantity))} {label}"
        if "conversion" not in primary
        else f"{label} ({primary['conversion']['text']})"
    )
    if named is not None and asked_family:
        # Ni el nombre ni la familia pedida son la de la referencia: se dice, no se calla.
        cheapest = (
            f"La referencia que nombraste es de {_FAMILIES_LABEL.get(named.family, named.family)}"
            f" y no de {_FAMILIES_LABEL.get(asked_family, asked_family)}; cotizo esa,"
        )
    elif named is not None:
        cheapest = "Cotizo la referencia que pediste,"
    else:
        cheapest = "La de menor precio por unidad" if mixed else "La más económica"
        if not offer.converted and any(item.converted for _, item, _ in options):
            cheapest += " con precio publicado"
        cheapest += " es"
    reply = (
        f"Cotización para {asked} en {city}. "
        f"{cheapest} {product.name}: "
        f"{primary['presentation']['count']} "
        f"{primary['presentation']['basis']} de "
        f"{_units(Decimal(primary['presentation']['units']))} unidades por "
        f"{_money(Decimal(primary['presentation']['price_per_presentation']))} COP."
    )
    if mixed:
        # Cada opción con su propia conversión: no se comparan como si fueran el mismo pedido.
        others = "".join(
            f"; {item['name']}: {item['conversion']['text']}"
            for _, _, item in options[1 : 1 + MAX_ALTERNATIVES]
        )
        reply += (
            f" Cada referencia trae una cantidad distinta por {unit} "
            f"({primary['conversion']['text']}{others}): confirma qué {unit} buscas."
        )
    if offer.converted:
        reply += f" {CONVERTED_WARNING}"
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
        **(
            {"family_mismatch": {"asked": asked_family, "family": product.family}}
            if asked_family
            else {}
        ),
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
