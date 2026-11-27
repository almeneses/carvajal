"""Pedido a partir de una cotización registrada (spec 056): comprador y líneas.

Después del folio, el asistente ofrece hacer el pedido. Si la persona acepta, pide nombre y
correo; con los dos, el servicio del chat crea el pedido de venta en el CRM (Odoo) y el CRM
envía la confirmación por correo.

El módulo es puro: interpreta el texto de la persona y traduce las líneas del borrador a
líneas de pedido con los importes que el motor ya calculó (precio por presentación y número
de presentaciones). No llama al CRM ni estima precios.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from carvajal.agent.commercial_intent import product_family
from carvajal.agent.followup import parse_quantities, plain
from carvajal.quotation.draft import line_label, totals

__all__ = [
    "CO_CITIES",
    "EMAIL_MASK",
    "SHIPPING_CODE",
    "Buyer",
    "OrderLine",
    "OrderRequest",
    "asks_order_receipt",
    "asks_order_status",
    "correction_name",
    "declines_order",
    "edits_buyer",
    "find_email",
    "has_buyer_data",
    "is_affirmation",
    "is_bare_no",
    "is_goodbye",
    "mask_email",
    "merge_buyer",
    "name_in",
    "name_marked",
    "order_lines",
    "plausible_name",
    "spoken_address",
    "spoken_email",
    "wants_order_cancel",
    "wants_purchase",
]

SHIPPING_CODE: Final = "ENVIO-CARVAJAL"
EMAIL_MASK: Final = "…"
MAX_NAME: Final = 80

_EMAIL: Final = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Correo dictado por voz: «ana punto perez arroba gmail punto com».
_SPOKEN_AT: Final = re.compile(r"\s*\b(?:arroba|at)\b\s*", re.IGNORECASE)
_SPOKEN_DOT: Final = re.compile(r"\s*\bpunto\b\s*", re.IGNORECASE)
_SPOKEN_UNDERSCORE: Final = re.compile(r"\s*\bgui[oó]n\s+bajo\b\s*", re.IGNORECASE)
_DIGITS: Final = {
    word: index
    for index, word in enumerate(
        ("cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve")
    )
}
_DIGIT_WORDS: Final = "cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve"
_SPOKEN_DASH: Final = re.compile(r"\s*\bgui[oó]n\b\s*", re.IGNORECASE)
# Lo que acompaña al nombre y no es parte de él: «me llamo», «mi correo es», «a nombre de»…
_NAME_NOISE: Final = re.compile(
    r"\b(?:mi\s+nombre\s+es|me\s+llamo|a\s+nombre\s+de|y\s+mi\s+correo\s+es|"
    r"mi\s+correo\s+es|mi\s+correo|mi\s+email|el\s+correo\s+es|correo\s+electr[oó]nico|"
    r"correo|email|e-mail|mail|nombre|soy|es|y)\b[:,]?",
    re.IGNORECASE,
)
# Quien da el nombre lo dice así: con estas marcas un nombre nuevo reemplaza al anterior.
_NAME_MARK: Final = re.compile(r"\b(?:mi nombre es|me llamo|a nombre de|soy)\b")
# El nombre dicho con su marca, en el texto original: «sí, pero a nombre de Marta Gómez».
_MARKED_NAME: Final = re.compile(
    r"\b(?:mi\s+nombre\s+es|el\s+nombre\s+es|me\s+llamo|a\s+nombre\s+de|soy|"
    r"(?:el\s+)?nombre\s+(?:a|por))\s+"
    r"([^,;\n]+)",
    re.IGNORECASE,
)
# «No, es Marta Gómez», «no es Ana, es Ana María»: la corrección tras un «no».
_NO_PREFIX: Final = re.compile(
    r"^\s*(?:no|nop)\b[\s,.:;!-]*(?:es\b|se\s+llama\b|el\s+nombre\s+es\b)?[\s,.:;-]*",
    re.IGNORECASE,
)
_AFFIRM_START: Final = re.compile(
    r"^(?:si|sip|claro|dale|listo|de una|hagale|adelante|ok|okay|okey|vale|perfecto|"
    r"bueno|va|venga|correcto|exacto|asi es|esta bien|confirmo|confirmalo|confirmado)\b"
)
_BARE_NO: Final = frozenset(
    {"no", "nop", "no es", "esta mal", "incorrecto", "no esta bien", "hay un error", "error"}
)
# Palabras que nunca forman parte de un nombre: muletillas, respuestas y temas del pedido.
_NOT_NAME: Final = frozenset(
    """
    hola buenas buenos dias tardes noches gracias muchas mil ok okay okey listo lista
    perfecto perfecta bueno buena vale claro dale una si no nop ya bien super genial
    excelente mejor ahora todavia aun despues luego tarde pienso pensar voy quiero queria
    quisiera puedo puede necesito por favor porfa pedido pedidos compra comprar comprarla
    comprarlo orden cotizacion cotizaciones folio envio entrega pago factura precio confirmo
    confirma confirmar confirmado confirmalo cancela cancelar cancelado cambiar cambio
    corrige corregir otro otra eso esa ese esto todo nada algo mas menos el un unos unas al
    lo le que como cuando donde cual mi tu su con sin para en a o u e adelante senor senora
    hagale venga va sale cuentame cuente digame dime espera momento gusto encantado
    encantada correcto correcta exacto asi esta mal incorrecto datos dato pero entonces pues
    olvidalo olvida olvidar cancelalo cancelala dejalo dejala deja dejar chao chau adios bye
    retomar retoma seguir sigue sigamos continuar cambia cambiale modifica quita agrega
    caro cara barato ladron ladrones robo estafa se manana hoy tranquilo tranquila importa
    hay problema seguro segura me te nos interesa interesan sirve gusta mirando viendo
    eh este mmm aja oiga oye mira bien
    """.split()  # noqa: SIM905
)
# Muletillas que acompañan el nombre al principio o al final («Ana, gracias», «hola, Ana»).
_FILLER: Final = frozenset(
    """
    hola buenas buenos dias tardes noches gracias muchas mil ok okay okey listo perfecto
    bueno vale claro dale si ya bien super genial excelente porfa favor por pero entonces pues
    eh este mmm aja oiga oye mira
    """.split()  # noqa: SIM905
)
_PARTICLES: Final = frozenset({"de", "del", "la", "las", "los"})
# Ciudades de Colombia (y los dos países) que una persona escribe sola a mitad del pedido:
# «Bogotá» o «Cali» no son un nombre. Lista fija, sin leer datos en cada turno; una prueba
# comprueba que cubre las ciudades del directorio de distribuidores
# (``data/commercial/proveedores.csv``).
CO_CITIES: Final = frozenset(
    " ".join(plain(city).split())
    for city in """
    Bogotá|Medellín|Cali|Barranquilla|Cartagena|Cúcuta|Soledad|Ibagué|Bucaramanga|Soacha|
    Santa Marta|Villavicencio|Bello|Valledupar|Pereira|Montería|Pasto|Manizales|Buenaventura|
    Neiva|Palmira|Armenia|Popayán|Sincelejo|Itagüí|Floridablanca|Riohacha|Envigado|Tuluá|
    Dosquebradas|Tunja|Girón|Apartadó|Florencia|Uribia|Maicao|Piedecuesta|Yopal|Facatativá|
    Zipaquirá|Chía|Cartago|Rionegro|Quibdó|Duitama|Sogamoso|Girardot|Barrancabermeja|
    Fusagasugá|Mosquera|Funza|Leticia|Arauca|Mocoa|San Andrés|Ipiales|Tumaco|Magangué|
    Malambo|Sabanalarga|Ciénaga|Caucasia|Turbo|Jamundí|Yumbo|Buga|Ocaña|Pamplona|
    San José del Guaviare|Puerto Carreño|Inírida|Mitú|Colombia|México
    """.split("|")  # noqa: SIM905
    if city.strip()
)
# Siglas que nunca son un nombre aunque vayan con mayúscula: «PDF», «SAS», «NIT»…
_ACRONYM: Final = re.compile(r"^[A-ZÁÉÍÓÚÑ]{2,5}$")
_LETTERS: Final = re.compile(r"[^\W\d_]", re.UNICODE)
_NAME_WORD: Final = re.compile(r"^[^\W\d_]+(?:['-][^\W\d_]+)*$", re.UNICODE)
_NEGATION: Final = re.compile(r"\b(?:no|ni|nunca|tampoco|jamas)\b")

# «Quiero comprarla», «hagamos el pedido», «la compro»: aceptar el pedido con otras palabras.
_PURCHASE: Final = re.compile(
    r"\b(?:compr(?:o|ar|arla|arlo|arlas|arlos|amos|emos)|la\s+compro|lo\s+compro|"
    r"(?:hac|ha[gz])\w*\s+(?:el\s+|un\s+)?pedido|pedirl[ao]s?|ordenar|ordenarl[ao]s?|"
    r"orden\s+de\s+compra|quiero\s+(?:el\s+)?pedido|confirm\w*\s+(?:la\s+)?compra)\b"
)
_DECLINE: Final = frozenset(
    {
        "no",
        "nop",
        "no gracias",
        "ahora no",
        "por ahora no",
        "no por ahora",
        "todavia no",
        "aun no",
        "ya no",
        "mejor no",
        "despues",
        "luego",
        "mas tarde",
        "lo pienso",
        "lo voy a pensar",
        "cancelar",
        "cancela",
        "cancelalo",
        "olvidalo",
    }
)
# «No, mejor no», «no quiero el pedido», «todavía no, gracias»: empieza o se apoya en un no.
_DECLINE_START: Final = re.compile(
    r"^(?:no|nop|mejor no|ya no|todavia no|aun no|ahora no|por ahora no)\b"
    r"|\bno\s+(?:quiero|gracias|por\s+ahora|todavia|aun|hoy)\b"
)


@dataclass(frozen=True)
class Buyer:
    """Datos de quien compra: los dos son obligatorios para crear el pedido."""

    name: str | None = None
    email: str | None = None

    @property
    def complete(self) -> bool:
        return bool(self.name and self.email)

    def to_json(self) -> dict[str, str | None]:
        return {"name": self.name, "email": self.email}

    @classmethod
    def from_json(cls, data: Any) -> "Buyer":
        if not isinstance(data, Mapping):
            return cls()
        name = data.get("name")
        email = data.get("email")
        return cls(
            name=str(name) if name else None,
            email=str(email) if email else None,
        )


@dataclass(frozen=True)
class OrderLine:
    """Línea del pedido: ``quantity`` presentaciones a ``price_unit`` cada una."""

    code: str  # SKU (referencia interna del producto en el CRM)
    name: str  # nombre del producto, para crearlo si el CRM no lo tiene
    description: str  # texto de la línea en el pedido
    quantity: Decimal
    price_unit: Decimal
    service: bool = False  # el envío es un servicio, no un producto


@dataclass(frozen=True)
class OrderRequest:
    """Todo lo que el CRM necesita para el pedido; ``folio`` lo hace idempotente."""

    folio: str
    buyer: Buyer
    channel: str  # «chat web» o «WhatsApp»
    city: str | None
    lines: tuple[OrderLine, ...]
    total: Decimal | None
    notes: tuple[str, ...] = ()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def wants_purchase(text: str) -> bool:
    """«Quiero comprarla», «hagamos el pedido»: aceptar el pedido sin decir «sí».

    «Quiero comprar 500 vasos» es otra cotización, no el pedido de la registrada, y «no
    quiero hacer el pedido» es un rechazo.
    """

    if "?" in text or product_family(text) or parse_quantities(text):
        return False
    folded = plain(text)
    if _NEGATION.search(folded):
        return False
    return _PURCHASE.search(folded) is not None


def declines_order(text: str) -> bool:
    """«No», «ahora no», «no, mejor no», «no quiero el pedido»: sin pedido."""

    folded = plain(text).strip(" .!")
    return folded in _DECLINE or _DECLINE_START.search(folded) is not None


def spoken_email(text: str) -> str:
    """El correo dictado («ana punto perez arroba gmail punto com») escrito como correo.

    Solo si el texto dice «arroba» y no trae ya una «@»: así «punto» o «guion» de una frase
    cualquiera no se tocan.
    """

    if "@" in text or not re.search(r"\barroba\b", text, re.IGNORECASE):
        return text
    parts = _SPOKEN_AT.split(text, maxsplit=1)
    if len(parts) != 2:
        return text
    before, after = parts
    # El usuario del correo es lo último dicho antes de «arroba», con sus «punto» y «guion».
    tail = re.search(
        r"((?:[\w-]+\s*\b(?:punto|gui[oó]n(?:\s+bajo)?)\b\s*)*[\w-]+"
        rf"(?:\s+(?:\d+|{_DIGIT_WORDS}))*)\s*$",
        before,
        re.IGNORECASE,
    )
    if tail is None:
        return text
    local = tail.group(1)
    before = before[: tail.start(1)]
    domain = re.match(r"\s*((?:[\w-]+\s*\bpunto\b\s*)+[A-Za-z]{2,})", after, re.IGNORECASE)
    if domain is None:
        return text
    rest = after[domain.end(1) :]

    def _join(value: str) -> str:
        value = re.sub(
            rf"\b({_DIGIT_WORDS})\b",
            lambda found: str(_DIGITS[plain(found.group(1))]),
            value,
            flags=re.IGNORECASE,
        )
        value = _SPOKEN_UNDERSCORE.sub("_", value)
        value = _SPOKEN_DASH.sub("-", value)
        value = _SPOKEN_DOT.sub(".", value)
        return re.sub(r"\s+", "", value)

    return f"{before}{_join(local)}@{_join(domain.group(1))}{rest}"


def _name_from(text: str) -> str | None:
    """Un nombre de persona en ``text``, o ``None`` si lo que queda no parece un nombre."""

    rest = _EMAIL.sub(" ", text)
    rest = _NAME_NOISE.sub(" ", rest)
    rest = re.sub(r"[\s,;:|/()<>\"'.!¡¿-]+", " ", rest).strip()
    words = rest.split()
    while words and plain(words[0]) in _FILLER:
        words.pop(0)
    while words and plain(words[-1]) in _FILLER:
        words.pop()
    rest = " ".join(words)
    if not words or len(words) > 6:  # una frase larga no es un nombre
        return None
    if any(not _NAME_WORD.match(word) for word in words):
        return None  # cifras, símbolos o restos de un correo
    if any(plain(word) in _NOT_NAME for word in words):
        return None  # «ok gracias», «mejor no», «quiero el pedido»…
    if len(_LETTERS.findall(rest)) < 2:
        return None
    shaped = [
        word.lower()
        if index and word.lower() in _PARTICLES
        else (word if word.isupper() else word.capitalize())
        for index, word in enumerate(words)
    ]
    return " ".join(shaped)[:MAX_NAME]


def merge_buyer(previous: Buyer, text: str) -> Buyer:
    """Los datos de ``previous`` completados con lo que trae ``text`` (correo y nombre).

    El nombre ya dado solo se reemplaza si el mensaje trae un correo nuevo o lo dice con
    una marca («me llamo…», «a nombre de…»): una palabra suelta no pisa el nombre real.
    """

    text = spoken_email(text)
    found = _EMAIL.search(text)
    email = found.group(0).lower() if found else previous.email
    candidate = _name_from(text)
    name = previous.name
    if candidate and (
        not previous.name or found is not None or _NAME_MARK.search(plain(text)) is not None
    ):
        name = candidate
    return Buyer(name=name, email=email)


def find_email(text: str) -> str | None:
    """El correo del mensaje (escrito o dictado), en minúsculas; ``None`` si no hay."""

    found = _EMAIL.search(spoken_email(text))
    return found.group(0).lower() if found else None


def name_in(text: str, *, strict: bool = False) -> str | None:
    """El nombre que trae ``text`` (sin correo ni marcas), o ``None``.

    ``strict`` (la oferta, donde nadie pidió todavía el nombre): solo cuenta un nombre
    escrito como nombre propio, con mayúscula inicial en cada palabra («Ana Pérez»); así una
    frase cualquiera («cuéntame de la empresa») no se toma como nombre.
    """

    spelled = spoken_email(text)
    name = _name_from(spelled)
    if name is None or not strict:
        return name
    original = re.sub(r"[\s,;:|/()<>\"'.!¡¿-]+", " ", _EMAIL.sub(" ", spelled)).split()
    words = [word for word in original if plain(word) not in _FILLER]
    capitalized = all(
        word[:1].isupper() or word.lower() in _PARTICLES for word in words if _NAME_WORD.match(word)
    )
    if not capitalized or not 1 <= len(name.split()) <= 4:
        return None
    return name if plausible_name(name) else None


def plausible_name(name: str) -> bool:
    """``False`` si ``name`` es una ciudad («Bogotá», «Santa Marta») o lleva una sigla.

    Una sigla es una palabra de 2 a 5 letras en mayúsculas («PDF», «SAS», «NIT»). Quien
    escribe todo en mayúsculas («ANA PÉREZ») da un nombre: solo se descarta si alguna palabra
    del nombre va en minúsculas, o si es una sola palabra.
    """

    words = name.split()
    if not words or " ".join(plain(name).split()) in CO_CITIES:
        return False
    acronyms = [word for word in words if _ACRONYM.match(word)]
    if not acronyms:
        return True
    all_caps = all(word.isupper() or word.lower() in _PARTICLES for word in words)
    return all_caps and len(words) >= 2


def name_marked(text: str) -> str | None:
    """El nombre dicho con su marca («me llamo…», «a nombre de…»), o ``None``."""

    found = _MARKED_NAME.search(_EMAIL.sub(" ", spoken_email(text)))
    return _name_from(found.group(1)) if found else None


def correction_name(text: str) -> str | None:
    """El nombre de una corrección: con marca, tras un «no» («no, es Marta») o suelto.

    Sin marca, una ciudad o una sigla suelta («Bogotá», «PDF») no corrige el nombre.
    """

    marked = name_marked(text)
    if marked:
        return marked
    found = _unmarked_correction(text)
    return found if found and plausible_name(found) else None


def _unmarked_correction(text: str) -> str | None:
    text = _EMAIL.sub(" ", spoken_email(text))
    # «No es Ana, es Carla», «corrige el nombre: Carla»: vale lo último tras «es» o «:».
    last = re.search(r".*(?:\bes|:)\s+([^,;:]+)$", text, re.IGNORECASE)
    after_no = _NO_PREFIX.match(text) is not None
    if last:
        candidate = name_in(last.group(1), strict=not after_no)
        if candidate:
            return candidate
    rest = _NO_PREFIX.sub("", text, count=1) if after_no else text
    if not rest.strip():
        return None
    strict = name_in(rest, strict=True)
    if strict or not after_no:
        return strict
    # Tras un «no», un nombre en minúsculas cuenta con nombre y apellido («no, carla ruiz»):
    # una palabra suelta («no sé», «no, mañana») no es una corrección.
    loose = name_in(rest)
    return loose if loose and len(loose.split()) >= 2 else None


def is_affirmation(text: str) -> bool:
    """«Sí», «sí, adelante», «dale», «claro que sí»: acepta, sin negación ni pregunta."""

    if "?" in text:
        return False
    folded = plain(text)
    if _NEGATION.search(folded):
        return False
    return _AFFIRM_START.match(folded) is not None


# Tras el pedido creado (spec 056): anularlo o preguntar cómo va lo gestiona un asesor. Solo
# cuenta el pedido dicho con un determinante («mi pedido», «el pedido», «la compra»): «quiero
# ver vasos para otro pedido» es otra cotización, no el estado del pedido.
_ORDER_DETERMINER: Final = (
    r"(?:mi|mis|el|la|los|las|del|al|ese|esa|este|esta|nuestro|nuestra|tu|su)"
)
_ORDER_NOUN: Final = _ORDER_DETERMINER + r"\s+(?:pedido|orden|compra)s?"
_ORDER_CANCEL: Final = re.compile(
    r"\b(?:cancel\w*|anul\w*|revers\w*|devolver|devuelv\w*|desist\w*)\b(?:\s+\w+){0,3}?\s+"
    + _ORDER_NOUN
    + r"\b|\b(?:ya\s+no|no)\s+quiero\s+"
    + _ORDER_NOUN
    + r"\b|\b"
    + _ORDER_NOUN
    + r"\b(?:\s+\w+){0,3}?\s+(?:cancel\w*|anul\w*)\b"
)
_CANCEL_BARE: Final = frozenset(
    {"cancelalo", "cancelala", "anulalo", "anulala", "cancelar", "anular", "cancela", "anula"}
)
# Las preguntas del estado admiten hasta tres palabras antes del pedido («¿cómo va entonces mi
# pedido?»); los verbos genéricos («ver», «revisar»), solo seguidos del pedido («ver mi pedido»).
_ORDER_STATUS: Final = re.compile(
    r"\b(?:(?:como\s+va|como\s+vamos\s+con|estado|seguimiento|donde\s+(?:esta|va)|"
    r"cuando\s+(?:llega|me\s+llega|despachan|entregan|sale)|que\s+paso\s+con|"
    r"ya\s+(?:salio|despacharon|enviaron|llego))\b(?:\s+\w+){0,3}?|"
    r"(?:muestrame|ver|revisar|consultar))\s+" + _ORDER_NOUN + r"\b"
)
_POSSESSIVE_ORDER: Final = re.compile(
    r"\b(?:mi|mis|nuestro|nuestra|nuestros|nuestras)\s+(?:pedido|orden|compra)s?\b"
)
# El número del pedido («S00005») no es una cantidad de producto.
_ORDER_NUMBER: Final = re.compile(r"\b[sS]\d{3,}\b")
_GOODBYE: Final = re.compile(
    r"^(?:(?:bueno|gracias|ok|listo)\s+)*(?:chao|chau|adios|bye|nos vemos)\b"
)
# «Quiero cambiar el correo», «corrige el nombre»: un cambio de los datos, no del pedido.
_DATA_EDIT: Final = re.compile(
    r"\b(?:cambi\w*|corrig\w*|correg\w*|modific\w*|equivoqu\w*|mal)\b.*\b(?:correo|"
    r"email|mail|nombre|dato|datos)\b|\b(?:correo|nombre|datos?)\b.*\b(?:mal|equivocad\w*)\b"
)


def is_goodbye(text: str) -> bool:
    """«Chao», «gracias, adiós»: se va sin hacer el pedido."""

    return _GOODBYE.match(plain(text).strip(" .!")) is not None


def edits_buyer(text: str) -> bool:
    """«Quiero cambiar el correo», «el nombre está mal»: corrige los datos del pedido."""

    return _DATA_EDIT.search(plain(text)) is not None


def is_bare_no(text: str) -> bool:
    """«No», «está mal»: ante «¿Lo confirmo?», hay algo que corregir (no es cancelar)."""

    return plain(text).strip(" .!") in _BARE_NO


def has_buyer_data(text: str) -> bool:
    """El mensaje trae un correo (escrito o dictado) o algo que parece un nombre."""

    spelled = spoken_email(text)
    return _EMAIL.search(spelled) is not None or _name_from(spelled) is not None


def spoken_address(email: str) -> str:
    """El correo para leerlo en voz: «ana.perez@gmail.com» → «ana punto perez arroba gmail
    punto com», así quien dicta lo puede comprobar."""

    spoken = email.replace("@", " arroba ").replace(".", " punto ")
    return " ".join(spoken.replace("_", " guion bajo ").replace("-", " guion ").split())


def mask_email(email: str) -> str:
    """«ana.perez@correo.com» → «a…@correo.com»: para lo que se guarda o se registra.

    La máscara no lleva caracteres de Markdown: las plantillas ponen el correo en negrita
    (``**…**``) y unos asteriscos dentro la rompían en el historial y en WhatsApp.
    """

    user, _, domain = email.partition("@")
    return f"{user[:1]}{EMAIL_MASK}@{domain}" if domain else EMAIL_MASK


def _names_product(text: str) -> bool:
    """El texto trae un producto o una cantidad sin hablar de «mi pedido»: otra cotización.

    «¿Cómo va mi pedido de vasos?» sigue siendo el pedido creado (posesivo); «quiero ver
    vasos para otro pedido» o «cancela el pedido de 500 vasos» con un carrito nuevo no.
    """

    if _POSSESSIVE_ORDER.search(plain(text)):
        return False
    rest = _ORDER_NUMBER.sub(" ", text)
    return bool(product_family(rest) or parse_quantities(rest))


def wants_order_cancel(text: str, *, bare: bool = True) -> bool:
    """«Cancela el pedido», «quiero anular la compra», «anúlalo»: tras el pedido creado.

    ``bare`` acepta también el «anúlalo» suelto: vale justo después del pedido, no en un
    turno cualquiera. Nunca con un producto o una cantidad («cancela el pedido de vasos»).
    """

    folded = plain(text).strip(" .!")
    if _names_product(text):
        return False
    return (bare and folded in _CANCEL_BARE) or _ORDER_CANCEL.search(folded) is not None


def asks_order_status(text: str) -> bool:
    """«¿Cómo va mi pedido?», «estado del pedido», «¿cuándo llega el pedido?».

    Nunca con un producto o una cantidad: «quiero ver vasos para otro pedido» se cotiza.
    """

    if _names_product(text):
        return False
    return _ORDER_STATUS.search(plain(text)) is not None


# Spec 056: palabras que acompañan el pedido del recibo sin cambiarlo (ya sin tildes). Las
# mismas que acepta el puente de WhatsApp (``is_receipt_request``), más el recibo y el pedido.
_RECEIPT_FILLER: Final = frozenset(
    {
        "si", "el", "la", "en", "un", "una", "de", "del", "y", "a", "mi", "por", "favor",
        "porfa", "porfi", "porfavor", "quiero", "dame", "envia", "enviame", "enviamelo",
        "envialo", "manda", "mandame", "mandamelo", "mandalo", "pasa", "pasame", "pasamelo",
        "me", "lo", "archivo", "gracias", "ok", "okay", "vale", "bueno", "claro", "dale",
        "listo", "pdf", "recibo", "pedido", "compra", "otra", "vez", "nuevo", "puedes",
        "podrias", "reenvia", "reenviame", "descargar", "descarga", "ver", "mandas", "envias",
        "pasas",
    }
)  # fmt: skip
_PDF_TYPED: Final = re.compile(r"(?<![a-z0-9])p ?d ?f(?![a-z0-9])")


def asks_order_receipt(text: str) -> bool:
    """«Mándame el recibo», «el PDF del pedido», «recibo de mi compra»: el recibo del pedido.

    Solo un mensaje corto en el que todo es relleno y que nombra el recibo (o el PDF del
    pedido): «¿cuándo recibo el pedido?» es una pregunta y «el PDF de la cotización» pide la
    cotización. Es la regla del puente de WhatsApp, sin las formas habladas de «PDF».
    """

    folded = " ".join(_PDF_TYPED.sub(" pdf ", plain(text)).split())
    words = re.findall(r"[a-z0-9]+", folded)
    named = "recibo" in words or ("pdf" in words and ("pedido" in words or "compra" in words))
    return named and len(words) <= 8 and all(word in _RECEIPT_FILLER for word in words)


def _plural(basis: str, count: Decimal) -> str:
    if count == 1 or not basis:
        return basis or "unidad"
    return basis + ("es" if basis.endswith(("n", "r", "l", "d")) else "s")


def _line(line: Mapping[str, Any]) -> OrderLine:
    primary = _map(line.get("primary"))
    commercial = _map(line.get("commercial"))
    presentation = _map(primary.get("presentation")) or _map(commercial.get("presentation"))
    sku = str(primary.get("sku") or "SIN-SKU")
    name = line_label(line)
    count = _decimal(presentation.get("count")) or Decimal(1)
    price = _decimal(presentation.get("price_per_presentation"))
    units = presentation.get("units")
    bought = presentation.get("units_bought")
    basis = str(presentation.get("basis") or "caja")
    parts = [f"[{sku}] {name}"]
    if units and bought:
        parts.append(f"{count:f} {_plural(basis, count)} de {units} u. ({bought} unidades)")
    seller = primary.get("seller")
    if seller:
        parts.append(f"Vendedor: {seller}")
    if line.get("city"):
        parts.append(f"Ciudad: {line['city']}")
    if price is None:
        parts.append("Precio por confirmar con el vendedor")
    tax = _map(primary.get("tax"))
    if tax.get("status") == "incluido":
        parts.append("IVA incluido")
    elif tax.get("status") == "no_incluido":
        parts.append("IVA no incluido")
    return OrderLine(
        code=sku,
        name=name,
        description=" · ".join(parts),
        quantity=count,
        price_unit=price if price is not None else Decimal(0),
    )


def order_lines(lines: Sequence[Mapping[str, Any]]) -> tuple[tuple[OrderLine, ...], list[str]]:
    """Líneas del pedido (productos y, si está confirmado, el envío) y notas pendientes."""

    result = [_line(line) for line in lines]
    notes: list[str] = []
    summary = totals(lines)
    if summary.unpriced:
        notes.append("Sin precio publicado: " + ", ".join(summary.unpriced) + ".")
    if summary.shipping_requested:
        if summary.shipping is None:
            notes.append("Envío por confirmar con el vendedor.")
        elif summary.shipping > 0:
            result.append(
                OrderLine(
                    code=SHIPPING_CODE,
                    name="Envío",
                    description="Envío a domicilio (costo declarado por el vendedor)",
                    quantity=Decimal(1),
                    price_unit=summary.shipping,
                    service=True,
                )
            )
    notes.append(f"Impuestos: {summary.tax}.")
    return tuple(result), notes
