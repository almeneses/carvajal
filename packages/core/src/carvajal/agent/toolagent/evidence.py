"""Compuerta de evidencia del modo ``tools`` (spec 044, FR-004; research R2, R3).

El modelo decide qué consultar, pero no decide si su redacción se muestra: lo decide esta
compuerta, determinista, sobre los ``ToolOutcome`` del turno.

- :func:`gate` elige uno de tres caminos. **Evidencia del turno** (``data-model.md``) es al
  menos un ``ToolOutcome`` con ``status == "ok"`` y ``source_ids`` no vacío.

  - ``evidence``: hay evidencia; el texto se transmite con la retención de 48 caracteres.
  - ``abstain``: no hay evidencia, el turno buscó en el corpus (``buscar_corpus``) y
    ninguna otra herramienta aportó datos; la respuesta es ``NO_CONTEXT_ANSWER`` y no se
    transmite nada del modelo.
  - ``no_query``: no hay evidencia ni búsqueda documental (solo ``sin_consulta`` o
    herramientas sin evidencia), alguna herramienta pidió un dato (``needs_input`` o
    ``sin_consulta`` válido con ``pedir_dato``) o devolvió datos citados sin ``ok``
    (``cotizar`` sin precio, con las sedes del directorio), aunque la búsqueda documental
    del turno saliera vacía: la petición del dato (FR-005) y las sedes no se pierden tras
    la abstención. El texto completo se retiene y pasa :func:`filter_unsupported` antes de
    emitirse de una vez, salvo en el caso de :func:`neutral_only`.

- :func:`neutral_only`: el turno buscó en el corpus sin evidencia y solo salió de la
  abstención por una declaración del propio modelo (``sin_consulta`` con ``pedir_dato``) o
  por sedes sin precio. Ninguna herramienta pidió el dato (``needs_input``), así que la
  redacción del modelo no se emite, ni filtrada: el filtro deja pasar afirmaciones
  cualitativas y lo que las sedes respaldan por casualidad (la «terraza 3» de una
  dirección respaldaría «entrega en 3 días»). En su lugar sale el reemplazo neutro.

- :func:`filter_unsupported` es el filtro de la ruta directa aplicado a la salida. La ruta
  directa del modo ``routes`` no tiene un filtro de salida reutilizable (se apoya en el
  prompt), así que aquí se implementa uno conservador que reutiliza el criterio de
  entidades de :mod:`carvajal.application.evidence` (palabras capitalizadas fuera del
  inicio de la frase y entidades conocidas del corpus) y añade cifras (también en letras
  y en números romanos), fechas, montos, empresas del grupo y ciudades de Colombia en
  cualquier posición y en minúscula. Las cifras pequeñas en letras («dos», «tres»…) no
  cuentan: son habituales en una aclaración («te pido dos datos»).
  Lo que ya aportaron la pregunta o los resultados de las herramientas no cuenta como
  afirmación nueva (``allowed``), aunque el resultado sea ``empty`` o ``needs_input``: sus
  sedes, ciudades, campos faltantes y referencias respaldan lo que el modelo diga de ellos.
  Ante la duda bloquea: una abstención de más es preferible a una cifra inventada.

- :func:`ask_for_missing` y :func:`sites_without_price` son los reemplazos neutros del
  camino ``no_query`` cuando el filtro rechaza el texto de un turno que pidió un dato o que
  trajo sedes: se construyen solo con los datos de las herramientas (los campos de
  ``missing``, las ciudades y las sedes), nunca con texto del modelo. Una petición de dato
  no se convierte en «no está en el corpus».
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Literal

from carvajal.agent.toolagent.state import TOOL_BUSCAR_CORPUS, TOOL_SIN_CONSULTA
from carvajal.application.evidence import _KNOWN_ENTITIES, _entities

__all__ = [
    "GATE_ABSTAIN",
    "GATE_EVIDENCE",
    "GATE_NO_QUERY",
    "GatePath",
    "ask_for_missing",
    "filter_unsupported",
    "gate",
    "has_evidence",
    "missing_fields",
    "neutral_only",
    "sites_without_price",
    "unsupported_terms",
]

GatePath = Literal["evidence", "abstain", "no_query"]

GATE_EVIDENCE: Final = "evidence"
GATE_ABSTAIN: Final = "abstain"
GATE_NO_QUERY: Final = "no_query"

# Vocabulario propio del asistente (``AGENT_SYSTEM`` y las aclaraciones de cotización): su
# nombre, el de la empresa, los países donde cotiza y siglas comerciales. No son un dato
# sobre Carvajal aunque vayan en mayúscula a mitad de frase. Plegados (sin tildes).
_ASSISTANT_TERMS: Final = frozenset({"carva", "carvajal", "colombia", "mexico", "iva", "sku"})
# Empresas y marcas del grupo que aparecen en el corpus («Carvajal Empaques» se detecta por
# la mayúscula de «Empaques»). «Publicar» queda fuera: es también un verbo común.
_GROUP_COMPANIES: Final = frozenset(
    {
        "propal",
        "pulpak",
        "mepal",
        "norma",
        "bioform",
        "assenda",
        "cargraphics",
        "liquidezya",
        "zonamerica",
        "recicloplas",
    }
)
# Ciudades de Colombia (las del directorio comercial y las principales), plegadas: una sede
# o una planta en una ciudad es un dato aunque se escriba en minúscula.
_CITIES: Final = frozenset(
    {
        "armenia",
        "barranquilla",
        "bogota",
        "bucaramanga",
        "cali",
        "candelaria",
        "cartagena",
        "cucuta",
        "ibague",
        "manizales",
        "medellin",
        "monteria",
        "neiva",
        "palmira",
        "pasto",
        "pereira",
        "popayan",
        "tunja",
        "valledupar",
        "villavicencio",
        "yumbo",
    }
)
# Entidades que delatan un dato aunque se escriban en minúscula o al inicio de la frase.
# «Carvajal» y «Colombia» quedan fuera: aparecen en cualquier presentación del asistente.
_CORPUS_ENTITIES: Final = (
    frozenset(_KNOWN_ENTITIES) | _GROUP_COMPANIES | _CITIES
) - _ASSISTANT_TERMS
_MONTHS: Final = frozenset(
    {
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "setiembre",
        "octubre",
        "noviembre",
        "diciembre",
    }
)
# Palabras de monto: un «millones» sin cifra también afirma una magnitud.
_AMOUNT_WORDS: Final = frozenset(
    {"millón", "millon", "millones", "billón", "billon", "billones", "pesos", "dólares"}
)
# Cifras, porcentajes y fechas en letras (plegadas). De «dos» a «diez» no cuentan solas:
# son habituales en una aclaración; «dos mil» se detecta por «mil».
_NUMBER_WORDS: Final = frozenset(
    {
        "once",
        "doce",
        "trece",
        "catorce",
        "quince",
        "dieciseis",
        "diecisiete",
        "dieciocho",
        "diecinueve",
        "veinte",
        "veintiun",
        "veintiuno",
        "veintiuna",
        "veintidos",
        "veintitres",
        "veinticuatro",
        "veinticinco",
        "veintiseis",
        "veintisiete",
        "veintiocho",
        "veintinueve",
        "treinta",
        "cuarenta",
        "cincuenta",
        "sesenta",
        "setenta",
        "ochenta",
        "noventa",
        "cien",
        "ciento",
        "cientos",
        "doscientos",
        "doscientas",
        "trescientos",
        "trescientas",
        "cuatrocientos",
        "cuatrocientas",
        "quinientos",
        "quinientas",
        "seiscientos",
        "seiscientas",
        "setecientos",
        "setecientas",
        "ochocientos",
        "ochocientas",
        "novecientos",
        "novecientas",
        "mil",
        "miles",
        "millar",
        "millares",
        "decenas",
        "centenares",
        "porciento",
        # Ordinales de un hecho («la primera empresa»). «Primero» y «segundo» no: son
        # marcadores del discurso («primero, dime la ciudad»); «tercero» es «de terceros».
        "primer",
        "primera",
        "primeras",
        "primeros",
        "tercer",
        "tercera",
        "quinto",
        "quinta",
        "sexto",
        "sexta",
        "septimo",
        "septima",
        "octavo",
        "octava",
        "noveno",
        "novena",
        "decimo",
        "decima",
        # Fechas sin cifra.
        "siglo",
        "siglos",
        "decada",
        "decadas",
        "centenario",
        "aniversario",
    }
)

# Cifra con separadores de miles o decimales: «2.000», «3,2», «1 500».
_NUMBER: Final = re.compile(r"\d+(?:[.,\s]\d+)*")
# Parte decimal: uno o dos dígitos tras el último punto o coma («14.0», «3,5», «2.50»). Tres
# dígitos son separador de miles («2.000»).
_DECIMAL: Final = re.compile(r"(.*\d)[.,](\d{1,2})")
_CURRENCY: Final = re.compile(r"[$€£%]")
# Números romanos de dos o más letras en mayúscula («siglo XX», «XIX»).
_ROMAN: Final = re.compile(r"\b[IVXLCDM]{2,}\b")
_WORD: Final = re.compile(r"[^\W\d_]+")
# Marcadores de lista al inicio de línea («- », «* », «• », «1. », «2) »).
_LIST_MARKER: Final = re.compile(r"^\s*(?:[-*+•]|\d{1,2}[.)])\s+", re.MULTILINE)
# Marcado Markdown que no forma parte de las palabras.
_MARKUP: Final = re.compile(r"[*_`#>|]")
# Fin de frase o de segmento: lo que sigue empieza con mayúscula por gramática.
_SEGMENT_BREAK: Final = re.compile(r"[.!?¡¿:;\n]+")
# Signos que ``application.evidence._entities`` quita a cada palabra, más los del JSON.
_TOKEN_STRIP: Final = "¿?¡!,.;:()[]{}«»\"'"


def has_evidence(results: Iterable[Mapping[str, Any]]) -> bool:
    """Indica si alguna herramienta del turno devolvió ``ok`` con citas ``[n]``."""

    return any(r.get("status") == "ok" and r.get("source_ids") for r in results)


def _declares_ask(outcome: Mapping[str, Any]) -> bool:
    """El modelo declaró que pide un dato: ``sin_consulta`` válido con ``pedir_dato``.

    Un ``sin_consulta`` con argumentos inválidos (``status == "error"``) no declara nada:
    no saca el turno de la abstención ni cuenta como petición en el reemplazo neutro.
    """

    args = outcome.get("args")
    return (
        outcome.get("tool") == TOOL_SIN_CONSULTA
        and outcome.get("status") == "ok"
        and isinstance(args, Mapping)
        and args.get("motivo") == "pedir_dato"
    )


def _asks_for_data(outcome: Mapping[str, Any]) -> bool:
    """Se pidió un dato: la herramienta (``needs_input``) o el modelo (:func:`_declares_ask`)."""

    return outcome.get("status") == "needs_input" or _declares_ask(outcome)


def gate(results: Iterable[Mapping[str, Any]]) -> GatePath:
    """Camino de la respuesta según los ``ToolOutcome`` del turno (research R3).

    Ver el docstring del módulo. Un turno sin herramientas (por ejemplo, un proveedor que
    ignoró ``tool_choice="required"``) cae en ``no_query``: su texto se filtra entero.
    """

    outcomes = list(results)
    if has_evidence(outcomes):
        return GATE_EVIDENCE
    if any(_asks_for_data(r) for r in outcomes):
        # Falta un dato: la pregunta al usuario se filtra en vez de perderse (FR-005).
        return GATE_NO_QUERY
    if any(r.get("tool") != TOOL_BUSCAR_CORPUS and r.get("source_ids") for r in outcomes):
        # Otra herramienta trajo datos citados sin ``ok`` (``cotizar`` sin precio, con las
        # sedes del directorio): se filtra con esos datos en vez de abstenerse del corpus.
        return GATE_NO_QUERY
    if any(r.get("tool") == TOOL_BUSCAR_CORPUS for r in outcomes):
        return GATE_ABSTAIN
    return GATE_NO_QUERY


def neutral_only(results: Iterable[Mapping[str, Any]]) -> bool:
    """El texto ``no_query`` del modelo no se emite: solo vale el reemplazo neutro.

    Es el caso de un turno que buscó en el corpus sin evidencia y que :func:`gate` saca de
    la abstención sin que ninguna herramienta pidiera un dato: lo sacan una declaración del
    modelo (``sin_consulta`` con ``pedir_dato``) o las sedes de un ``cotizar`` sin precio.
    Ninguna de las dos respalda la redacción: la abstención sigue garantizada por el
    servidor (FR-004) y la petición o las sedes salen igual (FR-005), con texto neutro.
    """

    outcomes = list(results)
    return (
        gate(outcomes) == GATE_NO_QUERY
        and any(r.get("tool") == TOOL_BUSCAR_CORPUS for r in outcomes)
        and not any(r.get("status") == "needs_input" for r in outcomes)
    )


def _fold(text: str) -> str:
    """Minúsculas sin tildes, para comparar palabras con lo permitido."""

    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _digits(number: str) -> str:
    """Solo los dígitos: «2.000», «2 000» y «2000» son la misma cifra."""

    return "".join(c for c in number if c.isdigit())


def _canonical(number: str) -> str:
    """Forma comparable de una cifra: dígitos y, si la hay, la parte decimal sin ceros.

    «14.0», «14,00» y «14» son la misma cifra (los datos JSON escriben ``14.0``); «3,5» y
    «3.50» también. «3,5» no es «35» y «2.000» (miles) no es «2».

    Riesgo conocido: el filtro compara cifras sin su contexto, así que la capacidad
    ``capacity_oz: 14.0`` respalda también un «14» con otra unidad («entrega en 14 días»).
    Es el mismo caso que un ``14`` entero en los datos; antes ``14.0`` lo bloqueaba solo por
    accidente (lo leía como «140», que tampoco era la cifra) y bloqueaba también el legítimo
    «14 oz». Atar la cifra a su unidad exigiría saber de qué campo viene cada número.
    """

    decimal = _DECIMAL.fullmatch(number.strip())
    if decimal is None:
        return _digits(number)
    integer, fraction = _digits(decimal.group(1)), decimal.group(2).rstrip("0")
    return f"{integer}.{fraction}" if fraction else integer


def _tokens(text: str) -> set[str]:
    """Palabras del texto tal como las corta el criterio de entidades, plegadas.

    Sirve para respaldar nombres compuestos que vienen enteros en los datos: el SKU
    «CO-VGOLD-14» de una herramienta respalda el «CO-VGOLD-14» del texto.
    """

    return {_fold(raw.strip(_TOKEN_STRIP)) for raw in text.split()} - {""}


def _clean(text: str) -> str:
    """Texto sin marcadores de lista ni marcado Markdown."""

    return _MARKUP.sub(" ", _LIST_MARKER.sub("", text))


def unsupported_terms(text: str, *, allowed: Iterable[str] = ()) -> list[str]:
    """Cifras, fechas, montos y nombres propios del texto que nada de ``allowed`` respalda.

    ``allowed`` son textos que el turno ya trae (la pregunta, el contenido de las
    herramientas, el contexto comercial): una cifra o un nombre que aparezca ahí no es una
    afirmación nueva del modelo. Devuelve los términos en el orden en que aparecen, sin
    repetir; sirve para registrar por qué se bloqueó una respuesta.
    """

    allowed_text = " ".join(allowed)
    allowed_words = set(_WORD.findall(_fold(allowed_text)))
    allowed_tokens = _tokens(allowed_text)
    allowed_numbers = {_canonical(n) for n in _NUMBER.findall(allowed_text)}
    cleaned = _clean(text)
    terms: list[str] = []

    def flag(term: str) -> None:
        if term not in terms:
            terms.append(term)

    for match in _NUMBER.finditer(cleaned):
        raw = match.group().strip()
        if _canonical(raw) not in allowed_numbers:
            flag(raw)
    for symbol in _CURRENCY.findall(cleaned):
        if symbol not in allowed_text:
            flag(symbol)
    for roman in _ROMAN.findall(cleaned):
        if roman not in allowed_text:
            flag(roman)
    for word in _WORD.findall(cleaned):
        folded = _fold(word)
        if folded in allowed_words:
            continue
        if (
            folded in _MONTHS
            or folded in _AMOUNT_WORDS
            or folded in _NUMBER_WORDS
            or folded in _CORPUS_ENTITIES
        ):
            flag(word)
    # Criterio de la ruta directa: palabras capitalizadas fuera del inicio del segmento y
    # entidades conocidas tras preposición (``application.evidence``), por segmento para
    # que la mayúscula de cada oración no cuente como nombre propio.
    for segment in _SEGMENT_BREAK.split(cleaned):
        for entity in _entities(segment):
            folded = _fold(entity)
            if folded in _ASSISTANT_TERMS or folded in allowed_words or folded in allowed_tokens:
                continue
            flag(entity)
    return terms


def filter_unsupported(text: str, *, allowed: Iterable[str] = ()) -> bool:
    """Indica si el texto del camino ``no_query`` debe sustituirse por la abstención.

    Es ``True`` cuando el texto trae alguna cifra, fecha, monto o nombre propio de
    Carvajal que no respaldan la pregunta ni los resultados del turno (``allowed``).
    """

    return bool(unsupported_terms(text, allowed=allowed))


# --- Reemplazos neutros del camino ``no_query`` --------------------------------------------

# Campos que piden las herramientas (``missing``) y cómo se piden. Las frases no traen
# cifras ni nombres propios: pasan el filtro sin respaldo.
_MISSING_LABELS: Final = {
    "producto": "qué producto buscas (vasos, contenedores, tapas…)",
    "temperatura": "si el contenido va caliente, frío o a temperatura ambiente",
    "cantidad": "cuántas unidades necesitas",
    "ciudad": "la ciudad donde lo recibirías",
    "periodo": "el período (año o año y mes)",
}
# Palabras del ``dato`` de ``sin_consulta`` (lo escribe el modelo) que señalan un campo. El
# ``dato`` nunca se copia: solo elige una de las frases de ``_MISSING_LABELS``.
_FIELD_HINTS: Final = {
    "producto": ("producto", "referencia"),
    "temperatura": ("temperatura", "caliente", "fria", "frio"),
    "cantidad": ("cantidad", "unidades", "cuantos", "cuantas"),
    "ciudad": ("ciudad",),
    "periodo": ("periodo",),
}
_GENERIC_ASK: Final = "Para seguir necesito un dato más. ¿Me cuentas con más detalle qué necesitas?"


def _join(items: Sequence[str]) -> str:
    """«a», «a y b», «a, b y c»."""

    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} y {items[-1]}"


def missing_fields(dato: str) -> list[str]:
    """Campos conocidos que nombra el ``dato`` de ``sin_consulta`` con ``pedir_dato``.

    El ``dato`` lo escribe el modelo: no respalda nada ni se repite; solo indica qué frase
    de petición usar. Sin campo conocido devuelve una lista vacía.
    """

    words = set(_WORD.findall(_fold(dato)))
    return [field for field, hints in _FIELD_HINTS.items() if words & set(hints)]


def ask_for_missing(fields: Iterable[str], *, cities: Sequence[str] = ()) -> str:
    """Petición neutra de los datos que faltan, para reemplazar un texto rechazado.

    ``fields`` son los campos de ``missing`` de las herramientas (``ciudad``, ``cantidad``,
    ``temperatura``…); los desconocidos se ignoran. ``cities`` son las ciudades que devolvió
    la herramienta (``ciudades_disponibles``) y solo se listan si falta la ciudad. Sin campo
    conocido la petición es genérica. Nunca es la abstención del corpus.
    """

    known = list(dict.fromkeys(f for f in fields if f in _MISSING_LABELS))
    if not known:
        return _GENERIC_ASK
    labels = [_MISSING_LABELS[f] for f in known]
    if len(labels) == 1:
        text = f"Para seguir me falta un dato: {labels[0]}."
    else:
        text = f"Para seguir me faltan estos datos: {_join(labels)}."
    if "ciudad" in known and cities:
        text += f" Ciudades disponibles: {_join([str(c) for c in cities])}."
    question = "¿Me lo indicas?" if len(labels) == 1 else "¿Me los indicas?"
    return f"{text} {question}"


def sites_without_price(sites: Iterable[Mapping[str, Any]]) -> str:
    """Aviso neutro de una cotización sin precio con las sedes del directorio que trajo.

    Cada sede sale con su nombre, dirección y teléfono (los que haya) y su cita ``[n]``;
    todo viene de los datos de ``cotizar`` y respalda sus propias cifras. Sin sedes con
    nombre devuelve ``""``.
    """

    lines: list[str] = []
    for site in sites:
        name = site.get("nombre")
        if not name:
            continue
        parts = [str(name), *(str(site[k]) for k in ("direccion", "telefono") if site.get(k))]
        cite = f" [{site['n']}]" if isinstance(site.get("n"), int) else ""
        lines.append(f"- {' — '.join(parts)}{cite}")
    if not lines:
        return ""
    return (
        "No encontré un precio publicado para esta búsqueda. Estas sedes del directorio "
        "tienen contacto para confirmar precio y disponibilidad:\n\n" + "\n".join(lines)
    )
