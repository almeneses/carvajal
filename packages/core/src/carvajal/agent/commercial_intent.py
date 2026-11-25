"""Detección y extracción de solicitudes comerciales de empaques (spec 036, T017-T018).

Reglas deterministas, como el resto del router:

- Una pregunta es comercial si nombra un producto del catálogo (vaso, tapa, contenedor…)
  **y** trae una señal de compra («necesito», «precio», «cotiza») o un dato de la necesidad
  (capacidad, cantidad, temperatura, uso, microondas, país o ciudad). «¿Qué empresas
  conforman el sector de Papel y Empaques?» no nombra un producto y sigue en ``rag``.
- Una respuesta corta a una aclaración («caliente», «en Cali», «5.000 unidades») hereda la
  solicitud de los turnos anteriores que sí nombraban el producto.
- La temperatura es el dato que decide el material (FR-002): se toma de la pregunta o del
  uso («café» → caliente; «calentar en microondas» → caliente). Las bebidas que se sirven
  calientes por costumbre también la implican: «tinto», «aromática», «chocolate»,
  «capuchino», «té de hierbas» (spec 044, corrección «tinto»); «vino tinto» no. Si falta,
  el router pide aclaración. El uso se deduce de la familia y la temperatura cuando la
  pregunta no lo dice.
- La capacidad en ml o litros se convierte a onzas líquidas.

Casos límite (T031-T033):

- Una pregunta sobre lo que hace Carvajal («¿Qué vasos fabrica Carvajal Empaques?») sin
  señal de compra ni datos concretos (capacidad, cantidad, lugar, microondas) es
  institucional y va a ``rag`` (FR-009).
- Una necesidad a la medida (industrial, agro, exportación, impresión con logo) no se
  resuelve con el catálogo estándar: se deriva a contacto comercial (FR-010).
- En una pregunta mixta, ``non_commercial_part`` devuelve la parte que pregunta por otro
  tema (la empresa, su historia o sus cifras) para avisarla sin responderla con datos
  inventados; las dudas de la misma compra (entrega, empaque, pago) se atienden con ella.

Las claves del diccionario que devuelve son las de ``CommercialRequest``.
"""

import re
import unicodedata
from typing import Any

from carvajal.agent.state import Turn

__all__ = [
    "COMMERCIAL_KEYS",
    "USES",
    "USE_TEMPERATURE",
    "commercial_signal",
    "empty_request",
    "extract_commercial",
    "is_custom_need",
    "mentions_microwave",
    "microwave_for",
    "non_commercial_part",
    "product_family",
]

COMMERCIAL_KEYS = (
    "use",
    "temperature",
    "microwave",
    "capacity_oz",
    "family",
    "quantity",
    "country",
    "city",
    "prefers_renewable",
)
ML_PER_OZ = 29.5735
FOLLOW_UP_MAX_WORDS = 8
FOLLOW_UP_DEPTH = 3

_FAMILIES = {
    "vaso": r"vasos?|vasitos?",
    "tapa": r"tapas?|domos?",
    # «cazuela» y «bowl» son contenedores del catálogo de Colombia (spec 039).
    "contenedor": r"contenedor(?:es)?|recipientes?|portacomidas?|cazuelas?|bowls?",
    "charola": r"charolas?|bandejas?",
    "cubierto": r"cubiertos?|tenedor(?:es)?|cucharas?|cucharitas?|cuchillos?",
    "envase": r"envases?",
    "plato": r"platos?",
}
_GENERIC_PRODUCT = r"desechables?|empaques? (?:para|de) (?:llevar|comida|domicilio|delivery|mi)"
# El orden importa: lo frío va antes para que «café frío» no se lea como caliente.
_USES = (
    (
        "bebida_fria",
        r"cafe (?:frio|helado)|te (?:frio|helado)|chocolate (?:frio|helado)|jugos?|refrescos?"
        r"|gaseosas?|limonadas?|malteadas?|smoothies?|frappes?|granizados?|bebidas? frias?",
    ),
    # «te» suelto es también pronombre («¿qué te sirve?»): solo cuenta con su apellido.
    # «tinto» es el café negro colombiano, salvo en «vino tinto» (``_WINE`` lo aparta antes).
    (
        "bebida_caliente",
        r"cafe|cafecitos?|tint(?:os?|icos?)|capuchinos?|cappuccinos?|lattes?"
        r"|expresos?|espressos?|te (?:caliente|negro|verde|de hierbas|chai)|infusion(?:es)?"
        r"|chocolates?|chocolatitos?|aromaticas?|aromatiquitas?|bebidas? calientes?",
    ),
    ("sopas", r"sopas?|caldos?|consomes?"),
    ("frituras", r"frituras?|papas fritas|fritos?"),
    ("ensaladas", r"ensaladas?"),
    ("frutas", r"frutas?"),
    ("panaderia", r"pan|panes|panaderia|pasteles?|postres?|galletas?"),
    ("sandwiches", r"sandwich(?:es)?|sanduches?|emparedados?"),
    ("salsas", r"salsas?|aderezos?"),
    ("comida_fria", r"comidas? frias?"),
    ("comida_caliente", r"comidas? calientes?|almuerzos?"),
)
USES = frozenset(name for name, _ in _USES)
# «vino tinto», «vinos tintos» o «vino  tinto» no son café: se quitan antes de buscar el uso.
_WINE = re.compile(r"\bvinos?\s+tint\w*")
# Usos que implican la temperatura; el motor de cotización no la pregunta para ellos.
USE_TEMPERATURE = {
    "bebida_caliente": "caliente",
    "comida_caliente": "caliente",
    "sopas": "caliente",
    "frituras": "caliente",
    "bebida_fria": "fria",
    "comida_fria": "fria",
    "ensaladas": "fria",
    "frutas": "fria",
}
_FOOD_FAMILIES = {"contenedor", "charola", "plato", "envase"}
_INTENT = re.compile(
    r"\b(necesit\w*|busc\w*|quier\w*|compr\w*|cotiz\w*|precios?|cuesta|cuanto vale|cajas?"
    r"|recomiend\w*|recomendacion|sugier\w*|me sirve|conviene|donde (?:compro|consigo)|cuest\w*"
    r"|distribuidor\w*|para mi (?:negocio|cafeteria|restaurante|local|tienda|panaderia))\b"
)
_CUSTOM = re.compile(
    r"\b(a la medida|personalizad\w*|con (?:mi|nuestro|el) logo|industrial\w*|agro\w*"
    r"|agricol\w*|exporta\w*|corrugad\w*|flexibles?|maquila\w*|big ?bags?|estibas?)\b"
)
_PACKAGING = re.compile(r"\b(empaques?|embalajes?|bolsas?|cajas?|sacos?|etiquetas?)\b")
# Señal de compra más estrecha que ``_INTENT`` para :func:`commercial_signal`: «busco» o
# «quiero» acompañan también a preguntas institucionales («quiero saber quién fundó…»).
_BUYING = re.compile(
    r"\b(precios?|cuesta|cuestan|cuanto vale\w*|cotiz\w*"
    r"|compr(?:a|ar|as|o|amos|an|e|en)|distribuidor\w*"
    r"|donde (?:compro|consigo|se consigue\w*|se compra\w*|venden))\b"
)
# Temas ajenos a la compra: una parte solo se aparta cuando pregunta por la empresa, su
# historia o sus cifras. «¿Cuál es el tiempo de entrega?» es de la misma compra.
_OTHER_TOPIC = re.compile(
    r"\b(empresas?|compania|historia|fund\w*|quien\w*|ingresos?|ventas?|accion(?:es)?"
    r"|sostenibilidad|reportes?|noticias?|empleados?|plantas?|sedes?|mision|vision|valores"
    r"|politicas?|presencia)\b"
)
# Señales concretas de compra: con ellas una pregunta que nombra a Carvajal sigue siendo
# comercial («Carvajal, 5000 vasos para microondas»). La capacidad o la ciudad solas no
# bastan: «¿Qué vasos vende Carvajal en Cali?» sigue siendo institucional.
_HARD_DATA = ("quantity", "microwave", "temperature", "use")
_RENEWABLE = re.compile(
    r"\b(renovables?|compostables?|biodegradables?|ecologic\w*|sostenibles?|de papel|de carton"
    r"|fibra|cana|bagazo|pulpa|sin plastico|sin icopor)\b"
)
# Microondas sobre texto ya sin tildes ni mayúsculas (``_plain``). Cuenta la familia de
# la palabra («microondas», «micro-ondas», «microhondas», «microndas», «mircoondas»,
# «microondable(s)», «microondear»), «microwave», «micro» suelto solo tras «para», «al»,
# «en» u «horno» («aptos para micro», «que vayan al micro») y «MW» solo en «aptos para
# MW» (suelto es también megavatio). No cuentan
# «micro empresa», «micro-negocio», «micro y pequeñas empresas» ni el cartón
# «microondulado»; tampoco una negación cercana («no necesito que sean para microondas»).
_MICROWAVE = re.compile(
    r"\bmi(?:cr|rc)o[\s-]?h?o?nd(?!ul)\w*"
    r"|\bmicrowaves?\b"
    r"|\b(?:para(?:\s+el)?|al|en(?:\s+el)?|horno(?:\s+de)?)\s+micro\b"
    r"(?![\s,-]*(?:y\s+)?(?:empres|negoci|emprend|credit|financ|peque|median|ondul|corrug))"
    r"|\bapt[oa]s?\s+(?:para(?:\s+el)?|al)\s+mw\b"
)
# La negación vale dentro de la misma cláusula: «no necesito tapa, pero sí para microondas»
# sigue pidiendo microondas.
_MICROWAVE_NEGATION = re.compile(
    r"\bno\s+(?:necesit\w*|requier\w*|hace\s+falta|es\s+necesario|van?)\b[^.,;?!]{0,40}$"
    r"|\bsin\s+(?:ir\s+al\s+|usar\s+el\s+)?$"
)
_HOT = re.compile(r"\b(calientes?|calentar|hirviendo)\b")
_COLD = re.compile(r"\b(fri[ao]s?|helad[ao]s?|refrigera\w*|congela\w*)\b")
_NUMBER = r"\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?"
# Un número de cuatro cifras entre 1900 y 2099 en un seguimiento es un año, no una cantidad.
_YEAR = re.compile(r"(?:19|20)\d{2}")
_CO_CITIES = (
    "Bogotá Medellín Cali Barranquilla Cartagena Bucaramanga Pereira Manizales Armenia Ibagué "
    "Neiva Pasto Villavicencio Cúcuta Santa_Marta Popayán Tunja Montería Valledupar Sincelejo"
)
_MX_CITIES = "Ciudad_de_México CDMX Monterrey Guadalajara Puebla Querétaro Tijuana León Mérida"
_COUNTRIES = {"mexico": "MX", "colombia": "CO"}


def _plain(value: str) -> str:
    """Minúsculas sin tildes, para comparar sin depender de la ortografía."""

    decomposed = unicodedata.normalize("NFKD", value.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _cities(names: str, country: str) -> dict[str, tuple[str, str]]:
    return {
        _plain(name.replace("_", " ")): (name.replace("_", " "), country) for name in names.split()
    }


_KNOWN_CITIES = {**_cities(_CO_CITIES, "CO"), **_cities(_MX_CITIES, "MX")}


def _number(raw: str) -> float:
    """«5.000» y «5,000» son miles; «5,5» y «5.5» son decimales."""

    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        return float(re.sub(r"[.,]", "", raw))
    return float(raw.replace(",", "."))


def mentions_microwave(text: str) -> bool:
    """La frase pide un empaque que vaya al microondas (detector único del producto).

    Acepta texto crudo o ya normalizado. Lo usan la extracción comercial y la red de
    seguridad del modo ``tools``; la simulación de conversaciones debe usarlo también para
    medir lo mismo que detecta el producto.
    """

    plain = _plain(text)
    return any(
        not _MICROWAVE_NEGATION.search(plain[: match.start()])
        for match in _MICROWAVE.finditer(plain)
    )


def microwave_for(question: str, family: str | None) -> bool | None:
    """``True`` si la pregunta pide microondas para ``family``; si no, ``None``.

    Si la pregunta nombra otra familia («vasos para café y contenedores para microondas»)
    no se sabe a cuál se refiere y no se deduce nada.
    """

    if not mentions_microwave(question):
        return None
    plain = _plain(question)
    named = {name for name, pattern in _FAMILIES.items() if _search(pattern, plain)}
    return True if named <= {family} or (family is None and len(named) <= 1) else None


def _search(pattern: str, text: str) -> re.Match[str] | None:
    return re.search(rf"\b(?:{pattern})\b", text)


def _family(text: str) -> str | None:
    """La familia que se nombra primero: «tapas para vasos» pide tapas."""

    found = [
        (match.start(), name)
        for name, pattern in _FAMILIES.items()
        if (match := _search(pattern, text))
    ]
    return min(found)[1] if found else None


def product_family(text: str) -> str | None:
    """Familia del producto que nombra el texto («mejor vasos» → ``vaso``), o ``None``."""

    return _family(_plain(text))


def _capacity(text: str) -> float | None:
    match = re.search(rf"({_NUMBER})\s*(oz|onzas?|ml|mililitros?|l|litros?)\b", text)
    if match is None:
        return None
    value, unit = _number(match.group(1)), match.group(2)
    if unit.startswith(("oz", "onza")):
        return value
    milliliters = value if unit.startswith(("ml", "mili")) else value * 1000
    return round(milliliters / ML_PER_OZ, 2)


def _quantity(text: str) -> int | None:
    nouns = "|".join(_FAMILIES.values())
    thousand = re.search(rf"({_NUMBER})\s*mil\b", text)
    if thousand:
        return int(_number(thousand.group(1)) * 1000)
    counted = re.search(rf"({_NUMBER})\s*(?:unidades|piezas|pzas?|{nouns})\b", text)
    approx = re.search(
        rf"\b(?:unas?|unos|aproximadamente|cerca de)\s+({_NUMBER})\b(?!\s*(?:oz|ml))", text
    )
    match = counted or approx
    return int(_number(match.group(1))) if match else None


def _place(question: str, text: str) -> tuple[str | None, str | None]:
    """País y ciudad. Una ciudad conocida implica su país; otra se conserva tal cual."""

    country = next((code for name, code in _COUNTRIES.items() if _search(name, text)), None)
    for plain, (display, city_country) in _KNOWN_CITIES.items():
        if _search(re.escape(plain), text):
            return country or city_country, display
    other = re.search(r"\ben\s+([A-ZÁÉÍÓÚÑ]\w+(?:\s+(?:de\s+)?[A-ZÁÉÍÓÚÑ]\w+)?)", question)
    if (
        other
        and _plain(other.group(1)) not in _COUNTRIES
        and "carvajal" not in _plain(other.group(1))
    ):
        return country, other.group(1)
    return country, None


def _extract(question: str) -> tuple[bool, dict[str, Any]]:
    """Datos que trae una sola pregunta y si nombra un producto."""

    text = _plain(question)
    family = _family(text)
    without_wine = _WINE.sub(" ", text)
    use = next((name for name, pattern in _USES if _search(pattern, without_wine)), None)
    microwave = True if mentions_microwave(text) else None
    temperature: str | None = None
    if _COLD.search(text):
        temperature = "fria"
    elif _HOT.search(text) or microwave:
        temperature = "caliente"
    elif "ambiente" in text:
        temperature = "ambiente"
    country, city = _place(question, text)
    data: dict[str, Any] = {
        "use": use,
        "temperature": temperature,
        "microwave": microwave,
        "capacity_oz": _capacity(text),
        "family": family,
        "quantity": _quantity(text),
        "country": country,
        "city": city,
        "prefers_renewable": True if _RENEWABLE.search(text) else None,
    }
    product = family is not None or _search(_GENERIC_PRODUCT, text) is not None
    if _INTENT.search(text):
        data["_intent"] = True
    return product, data


def _merge(base: dict[str, Any], newer: dict[str, Any]) -> dict[str, Any]:
    """Lo que dice el turno más reciente reemplaza lo anterior; lo que calla se conserva."""

    return {**base, **{key: value for key, value in newer.items() if value is not None}}


def _complete(data: dict[str, Any]) -> dict[str, Any]:
    """Deduce temperatura y uso a partir de lo dicho, sin inventar datos nuevos."""

    result = {key: data.get(key) for key in COMMERCIAL_KEYS}
    if result["temperature"] is None and result["use"] in USE_TEMPERATURE:
        result["temperature"] = USE_TEMPERATURE[result["use"]]
    if result["use"] is None and result["temperature"] in ("caliente", "fria"):
        if result["family"] == "vaso":
            result["use"] = f"bebida_{result['temperature']}"
        elif result["family"] in _FOOD_FAMILIES:
            result["use"] = f"comida_{result['temperature']}"
    result["prefers_renewable"] = bool(result["prefers_renewable"])
    return result


def _is_question(text: str) -> bool:
    """Una pregunta corta no es la respuesta a una aclaración comercial."""

    return "?" in text or "¿" in text


def _has_signal(data: dict[str, Any]) -> bool:
    """Señal de compra o dato de la necesidad; nombrar el producto no basta."""

    # ``family`` solo nombra el producto y ``prefers_renewable`` es una preferencia
    # de material: ninguno de los dos convierte una pregunta institucional en compra.
    keys = [key for key in COMMERCIAL_KEYS if key not in ("family", "prefers_renewable")]
    return any(data.get(key) is not None for key in (*keys, "_intent"))


def _is_institutional(question: str, data: dict[str, Any]) -> bool:
    """Nombrar a Carvajal y su producto sin señal de compra ni datos duros (FR-009)."""

    return (
        "carvajal" in _plain(question)
        and not data.get("_intent")
        and all(data.get(key) is None for key in _HARD_DATA)
    )


def _previous_request(turns: list[Turn]) -> list[dict[str, Any]] | None:
    """Datos de los turnos previos de la misma solicitud, del más antiguo al más reciente.

    Se recorre hacia atrás hasta el turno que nombró el producto. Si antes aparece un turno
    sin relación con la compra, no hay solicitud que heredar.
    """

    chain: list[dict[str, Any]] = []
    for turn in reversed(turns[-FOLLOW_UP_DEPTH:]):
        product, data = _extract(turn["question"])
        if data["quantity"] is None:
            # «¿y si son 4.500?» no nombra el producto: la cantidad del seguimiento cuenta
            # para el turno siguiente, aunque el turno sea una pregunta.
            data["quantity"] = _follow_up_quantity(_plain(turn["question"]))
        if not (product or _has_signal(data)):
            # Un saludo u otro turno sin datos no invalida la respuesta a la aclaración.
            continue
        if not product and data["quantity"] is None and _is_question(turn["question"]):
            # Una pregunta intermedia cambia de tema: no se hereda a través de ella.
            return None
        chain.append(data)
        if product:
            return list(reversed(chain))
    return None


def _follow_up_quantity(text: str) -> int | None:
    """Cantidad suelta de un seguimiento («¿y si son 4.500?»), nunca una capacidad ni un año."""

    match = re.search(
        rf"\b(?:son|serian|seran|para)\s+({_NUMBER})\b(?!\s*(?:oz|onzas?|ml|mililitros?|l|litros?)\b)",
        text,
    )
    if match is None:
        return None
    raw = match.group(1)
    if _YEAR.fullmatch(raw):
        # «¿y para 2025?» pregunta por un período, no por una cantidad de cajas.
        return None
    return int(_number(raw))


def _inherit(previous: list[dict[str, Any]], data: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in [*previous, data]:
        merged = _merge(merged, item)
    return _complete(merged)


def extract_commercial(question: str, turns: list[Turn] | None = None) -> dict[str, Any] | None:
    """Solicitud comercial con las claves de ``CommercialRequest``, o ``None`` si no lo es."""

    turns = turns or []
    product, data = _extract(question)
    short = len(question.split()) <= FOLLOW_UP_MAX_WORDS and "carvajal" not in _plain(question)
    if product:
        if _is_institutional(question, data):
            return None
        if not _has_signal(data):
            return None
        request = _complete(data)
        # «¿y si son 4.500 vasos?»: nombra el producto pero no dice para qué; si sigue una
        # solicitud del mismo producto, hereda lo que ya se dijo en lugar de pedirlo otra vez.
        if short and request["temperature"] is None:
            previous = _previous_request(turns)
            if previous and previous[0]["family"] in (None, data["family"]):
                return _inherit(previous, data)
        return request
    # Respuesta corta a una aclaración: hereda la solicitud de los turnos anteriores.
    if not short:
        return None
    if _is_question(question) and _OTHER_TOPIC.search(_plain(question)):
        return None
    if data["quantity"] is None:
        data["quantity"] = _follow_up_quantity(_plain(question))
    if not _has_signal({key: value for key, value in data.items() if key != "_intent"}):
        return None
    previous = _previous_request(turns)
    return _inherit(previous, data) if previous else None


def commercial_signal(question: str) -> bool:
    """La pregunta nombra un producto del catálogo, un precio, una cantidad o dónde comprar.

    Más amplia que :func:`extract_commercial`: no exige señal de compra junto al producto,
    así que «¿qué tiene que ver el bagazo con los portacomidas Bioform?» la tiene aunque sea
    institucional. La usa el gatekeeper para no quitar las consultas estructuradas cuando
    Jev elige ``rag`` (spec 046, R1 de la evidencia). Sin red ni servicios.
    """

    product, data = _extract(question)
    text = _plain(question)
    return (
        product
        or _BUYING.search(text) is not None
        or data["quantity"] is not None
        or data["capacity_oz"] is not None
    )


def empty_request() -> dict[str, Any]:
    """Solicitud sin datos, con las claves del contrato."""

    return _complete({})


def is_custom_need(question: str) -> bool:
    """Necesidad a la medida de empaque o embalaje, fuera del catálogo estándar (FR-010).

    La regla institucional (FR-009) se evalúa primero: «¿Qué empaques flexibles fabrica
    Carvajal Empaques?» se informa desde el corpus, aunque «flexibles» sea una señal de
    necesidad a la medida, porque no hay compra ni datos concretos.
    """

    text = _plain(question)
    product = _family(text) is not None or _PACKAGING.search(text) is not None
    if not (product and _CUSTOM.search(text)):
        return False
    _, data = _extract(question)
    return not _is_institutional(question, data)


def non_commercial_part(question: str) -> str | None:
    """Parte de una pregunta mixta que no es comercial, o ``None`` si no la hay.

    Se parte por signos de interrogación, puntos, comas y la conjunción «y». Una parte
    cuenta si tiene al menos tres palabras, pregunta por otro tema (empresa, historia,
    cifras) y no trae producto, señal de compra ni dato de la necesidad: así «¿Cuánto
    cuestan?», «¿Cuál es el tiempo de entrega?» o «Es para mi cafetería en Cali» siguen
    siendo parte de la compra.
    """

    parts = [part.strip(" ,") for part in re.split(r"[¿?]+|\.\s+|;\s*|,\s*|\s+y\s+", question)]
    for part in parts:
        if len(part.split()) < 3:
            continue
        product, data = _extract(part)
        if (
            not product
            and not _has_signal(data)
            and _OTHER_TOPIC.search(_plain(part))
            and _follow_up_quantity(_plain(part)) is None
        ):
            return part
    return None
