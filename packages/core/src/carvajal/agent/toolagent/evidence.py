"""Compuerta de evidencia del modo ``tools`` (spec 044, FR-004; research R2, R3).

El modelo decide qué consultar, pero no decide si su redacción se muestra: lo decide esta
compuerta, determinista, sobre los ``ToolOutcome`` del turno.

Desde la spec 045 los papeles salen del registro de herramientas (``evidence_role``):
donde este texto dice ``buscar_corpus`` vale para toda herramienta con papel ``corpus`` y
donde dice ``sin_consulta``, para la de papel ``declaration``.

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

- :func:`filtered_evidence`: hay evidencia, pero solo de herramientas no documentales, y
  la búsqueda documental del turno salió vacía. Una herramienta que siempre da ``ok`` con
  fuente (``fecha_actual``, ``indicadores_disponibles``, ``empresas_del_grupo`` o
  ``ciudades_con_cobertura`` sin argumentos, o un ``buscar_producto``) no respalda lo que
  el modelo diga del corpus: el texto se retiene y pasa :func:`filter_unsupported` con lo
  que aportaron las herramientas, como en ``no_query``; si no pasa, el turno se abstiene.
  Así la abstención la sigue garantizando el servidor aunque el modelo, por error o por
  inyección, añada una de esas llamadas a una búsqueda vacía (spec 045, revisión).

- :func:`neutral_only`: el turno buscó en el corpus sin evidencia y salió de la
  abstención por una petición de dato (``needs_input`` de una herramienta o ``sin_consulta``
  con ``pedir_dato``) o por sedes sin precio. Nada de eso respalda lo que el modelo diga
  del corpus, así que su redacción no se emite, ni filtrada: el filtro deja pasar
  afirmaciones cualitativas y lo que las sedes respaldan por casualidad (la «terraza 3» de
  una dirección respaldaría «entrega en 3 días»). En su lugar sale el reemplazo neutro,
  con ``NO_CONTEXT_ANSWER`` para la parte documental. Un ``needs_input`` sin búsqueda
  documental sigue el camino filtrado.

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

- :func:`abstention` es el texto que sustituye una redacción sin respaldo cuando no hay
  petición ni sedes, y se ajusta a lo que el turno consultó (simulación de la spec 044):

  - cada búsqueda vacía declara su ausencia con los datos de la herramienta
    (:func:`tool_misses`: «no lo encontré en el catálogo de Colombia», las ciudades con
    sedes del directorio, lo que sí coincide con el nombre buscado);
  - ``NO_CONTEXT_ANSWER`` («no está en el corpus») solo si el turno buscó en el corpus;
  - sin búsquedas, una respuesta de conversación (:data:`FAREWELL_ANSWER`,
    :data:`GREETING_ANSWER`, :data:`PREFERENCE_ANSWER` o :data:`NO_DATA_ANSWER`): nunca la
    frase del corpus, que afirmaría una búsqueda que no se hizo.

  Ninguno de esos textos lleva cifras ni nombres del modelo: solo datos de las herramientas
  y frases fijas, así que ninguna afirmación sin respaldo pasa.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Literal

from carvajal.agent.toolagent.state import (
    TOOL_BUSCAR_PRODUCTO,
    TOOL_BUSCAR_SEDES,
    TOOL_COTIZAR,
    TOOL_HISTORIA_CARVAJAL,
    TOOL_INDICADOR_FINANCIERO,
    TOOL_PREGUNTAS_FRECUENTES,
)
from carvajal.agent.toolagent.tools.registry import CORPUS_TOOLS, DECLARATION_TOOLS, MEMORY_TOOLS
from carvajal.application.evidence import _KNOWN_ENTITIES, _entities
from carvajal.prompts import NO_CONTEXT_ANSWER

__all__ = [
    "FAREWELL_ANSWER",
    "FORGET_ANSWER",
    "GATE_ABSTAIN",
    "GATE_EVIDENCE",
    "GATE_NO_QUERY",
    "GREETING_ANSWER",
    "MEMORY_DATA_KEYS",
    "MEMORY_UNAVAILABLE_ANSWER",
    "NO_DATA_ANSWER",
    "OUT_OF_SCOPE_ANSWER",
    "PREFERENCE_ANSWER",
    "GatePath",
    "abstention",
    "ask_for_missing",
    "backing_data",
    "filter_unsupported",
    "filtered_evidence",
    "gate",
    "has_evidence",
    "missing_fields",
    "names_city",
    "neutral_only",
    "out_of_scope",
    "sites_without_price",
    "tool_misses",
    "unsupported_terms",
]

GatePath = Literal["evidence", "abstain", "no_query"]

# Claves de los datos de una herramienta que vienen de la memoria del dueño, no de la
# herramienta: no respaldan nada ante la compuerta (FR-017). La pregunta de confirmación
# la construye :func:`ask_for_missing` con ``suggestion``.
MEMORY_DATA_KEYS: Final = frozenset({"sugerencia_ciudad"})

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
# Capacidad en los datos JSON (``"capacity_oz": 14.0``): solo respalda su cifra en onzas.
_CAPACITY_KEY: Final = re.compile(r'"(?:capacity_oz|capacidad_oz)"\s*:\s*(\d+(?:[.,]\d+)?)')
# Unidad de capacidad justo después de una cifra del texto («14 oz», «14 onzas»).
_OZ_AFTER: Final = re.compile(r"\s*(?:oz|onzas?)\b", re.IGNORECASE)
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


def names_city(text: str) -> bool:
    """El texto nombra una ciudad de Colombia (las del directorio y las principales)."""

    return bool(set(_WORD.findall(_fold(text))) & _CITIES)


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
        outcome.get("tool") in DECLARATION_TOOLS
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
    if any(r.get("tool") not in CORPUS_TOOLS and r.get("source_ids") for r in outcomes):
        # Otra herramienta trajo datos citados sin ``ok`` (``cotizar`` sin precio, con las
        # sedes del directorio): se filtra con esos datos en vez de abstenerse del corpus.
        return GATE_NO_QUERY
    if any(r.get("tool") in CORPUS_TOOLS for r in outcomes):
        return GATE_ABSTAIN
    return GATE_NO_QUERY


def neutral_only(results: Iterable[Mapping[str, Any]]) -> bool:
    """El texto ``no_query`` del modelo no se emite: solo vale el reemplazo neutro.

    Es el caso de un turno que buscó en el corpus sin evidencia y que :func:`gate` saca de
    la abstención: lo sacan una petición de dato (``needs_input`` de la herramienta o
    ``sin_consulta`` con ``pedir_dato``) o las sedes de un ``cotizar`` sin precio. Ninguna
    respalda lo que el modelo diga del corpus: un ``needs_input`` solo respalda la petición
    del dato. La abstención sigue garantizada por el servidor (FR-004) y la petición o las
    sedes salen igual (FR-005), con texto neutro.
    """

    outcomes = list(results)
    return gate(outcomes) == GATE_NO_QUERY and any(r.get("tool") in CORPUS_TOOLS for r in outcomes)


def filtered_evidence(results: Iterable[Mapping[str, Any]]) -> bool:
    """El camino es ``evidence``, pero la evidencia no viene de la búsqueda documental.

    Verdadero si el turno consultó el corpus de verdad (alguna herramienta de papel
    ``corpus`` sin ``error``), ninguna de esas consultas trajo evidencia y otra herramienta
    sí dio ``ok`` con fuentes. Entonces el texto no se transmite: se retiene y se filtra con
    los datos de las herramientas, y sin respaldo el turno se abstiene (FR-004).
    """

    outcomes = list(results)
    searched = [r for r in outcomes if r.get("tool") in CORPUS_TOOLS and r.get("status") != "error"]
    return bool(searched) and not has_evidence(searched) and gate(outcomes) == GATE_EVIDENCE


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

    La capacidad de los datos (``capacity_oz: 14.0``) no pasa por aquí sin más: en
    :func:`unsupported_terms` solo respalda su cifra seguida de «oz» u «onzas», para que no
    respalde «entrega en 14 días».
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
    afirmación nueva del modelo. Una capacidad de los datos (``capacity_oz``) solo respalda
    su cifra seguida de «oz» u «onzas»; si la misma cifra viene en otro dato (SKU, nombre,
    pregunta), respalda cualquier unidad. Devuelve los términos en el orden en que
    aparecen, sin repetir; sirve para registrar por qué se bloqueó una respuesta.
    """

    allowed_text = " ".join(allowed)
    allowed_words = set(_WORD.findall(_fold(allowed_text)))
    allowed_tokens = _tokens(allowed_text)
    capacity_numbers = {_canonical(n) for n in _CAPACITY_KEY.findall(allowed_text)}
    allowed_numbers = {_canonical(n) for n in _NUMBER.findall(_CAPACITY_KEY.sub("", allowed_text))}
    cleaned = _clean(text)
    terms: list[str] = []

    def flag(term: str) -> None:
        if term not in terms:
            terms.append(term)

    for match in _NUMBER.finditer(cleaned):
        raw = match.group().strip()
        number = _canonical(raw)
        if number in allowed_numbers:
            continue
        if number in capacity_numbers and _OZ_AFTER.match(cleaned, match.end()):
            continue
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


def ask_for_missing(
    fields: Iterable[str],
    *,
    cities: Sequence[str] = (),
    suggestion: str | None = None,
    candidates: Sequence[Mapping[str, Any]] = (),
    partial: bool = False,
    outside_directory: bool = False,
) -> str:
    """Petición neutra de los datos que faltan, para reemplazar un texto rechazado.

    ``fields`` son los campos de ``missing`` de las herramientas (``ciudad``, ``cantidad``,
    ``temperatura``…); los desconocidos se ignoran. ``cities`` son las ciudades que devolvió
    la herramienta (``ciudades_disponibles``) y solo se listan si falta la ciudad. Sin campo
    conocido la petición es genérica. Nunca es la abstención del corpus. ``suggestion`` es
    la ciudad habitual de la memoria (``sugerencia_ciudad``): si falta la ciudad, se ofrece
    como pregunta de confirmación, nunca como dato.
    """

    fields = list(fields)
    which = _which_reference(candidates, partial=partial) if "referencia" in fields else ""
    known = list(dict.fromkeys(f for f in fields if f in _MISSING_LABELS))
    if outside_directory and "ciudad" in known:
        # La persona sí dijo una ciudad, pero no está en el directorio: no se le pide «la
        # ciudad» como si faltara (decía «me falta la ciudad» tras «… en Leticia»).
        known.remove("ciudad")
        place = "No tengo precios ni sedes del directorio en esa ciudad."
        if cities:
            place += f" Ciudades disponibles: {_join([str(c) for c in cities])}."
            place += " ¿En cuál de ellas lo cotizo?"
        else:
            place += " ¿En qué otra ciudad lo cotizo?"
        rest = ask_for_missing(known) if known else ""
        return "\n\n".join(part for part in (which, place, rest) if part)
    if which:
        rest = ask_for_missing(known, cities=cities, suggestion=suggestion) if known else ""
        return "\n\n".join(part for part in (which, rest) if part)
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
    if "ciudad" in known and suggestion:
        question += f" ¿O lo hago en {suggestion}, tu ciudad habitual?"
    return f"{text} {question}"


def _which_reference(candidates: Sequence[Mapping[str, Any]], *, partial: bool = False) -> str:
    """«¿Cuál quieres?» con las candidatas que devolvió ``cotizar`` (nombre y SKU, sus datos).

    ``partial``: ninguna coincide con todo el nombre; se dice, no se presentan como exactas.
    """

    lines = [
        f"- {item['nombre']} (SKU {item['sku']})"
        for item in candidates
        if isinstance(item, Mapping) and item.get("nombre") and item.get("sku")
    ]
    if not lines:
        return ""
    lead = (
        "Ninguna referencia coincide exactamente; estas se parecen. ¿Cuál quieres?"
        if partial
        else "Hay varias referencias que coinciden. ¿Cuál quieres?"
    )
    return f"{lead}\n\n" + "\n".join(lines)


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


# --- Abstención ajustada a lo que se consultó ----------------------------------------------

# Respuestas de conversación de un turno sin búsquedas: frases fijas, sin cifras ni nombres.
FAREWELL_ANSWER: Final = "Con gusto. Si necesitas algo más, aquí estoy."
GREETING_ANSWER: Final = (
    "Hola, soy Carva. Puedo ayudarte con empaques (qué referencia te sirve, precios y dónde "
    "comprar) y con información pública de Carvajal. ¿Qué necesitas?"
)
PREFERENCE_ANSWER: Final = "Listo, lo tendré en cuenta."
# «Olvida todo lo que sabes de mí» (spec 045): confirmación fija, sin repetir ningún dato.
FORGET_ANSWER: Final = "Listo, ya olvidé lo que sabía de ti."
MEMORY_UNAVAILABLE_ANSWER: Final = (
    "Ahora no puedo gestionar lo que recuerdo de ti; intenta de nuevo en un momento."
)
OUT_OF_SCOPE_ANSWER: Final = (
    "Eso no está en mi corpus ni corresponde a Carvajal: solo respondo sobre Carvajal (su "
    "historia, sus empresas y sus cifras públicas) y sobre los empaques de Carvajal "
    "Empaques (qué referencia te sirve, precios y dónde comprar). ¿Te ayudo con algo de eso?"
)
NO_DATA_ANSWER: Final = (
    "No tengo un dato verificado para responder eso. Si me cuentas qué necesitas (un "
    "producto, un precio, dónde comprar o un dato de Carvajal), lo busco."
)
_CATALOG_COUNTRIES: Final = {"CO": "Colombia", "MX": "México"}
_MISS_OFFER: Final = "Si quieres, busco otra opción o te digo dónde comprar."


def _named_matches(data: Mapping[str, Any]) -> str:
    """Referencias aptas que coinciden con el nombre buscado fuera de los filtros."""

    found = [
        f"{item['nombre']} ({item['sku']})"
        for item in data.get("fuera_de_filtros") or []
        if isinstance(item, Mapping) and item.get("apto") and item.get("nombre") and item.get("sku")
    ]
    if not found:
        return ""
    return f" Con ese nombre sí aparece fuera de esa búsqueda: {_join(found)}."


def tool_misses(payloads: Iterable[tuple[str, Mapping[str, Any]]]) -> list[str]:
    """Una frase por búsqueda vacía del turno, con lo que sí trajo la herramienta.

    ``payloads`` son ``(herramienta, datos)`` de cada ``ToolMessage``. Solo cuentan los
    ``empty``: ``buscar_producto`` (catálogo del país consultado y las coincidencias por
    nombre fuera de los filtros), ``buscar_sedes`` (ciudades con cobertura), ``cotizar`` sin
    sedes e ``indicador_financiero``. ``buscar_corpus`` no: su ausencia es
    ``NO_CONTEXT_ANSWER``. No se repite ningún argumento del modelo; las frases repetidas se
    dan una vez.
    """

    lines: list[str] = []

    def add(line: str) -> None:
        if line not in lines:
            lines.append(line)

    for tool, data in payloads:
        if data.get("status") != "empty":
            continue
        if tool == TOOL_BUSCAR_PRODUCTO:
            country = _CATALOG_COUNTRIES.get(str(data.get("pais_catalogo") or "CO"), "Colombia")
            add(f"No lo encontré en el catálogo de {country}.{_named_matches(data)}")
        elif tool == TOOL_BUSCAR_SEDES:
            cities = [str(city) for city in data.get("ciudades_con_cobertura") or []]
            line = "No hay sedes del directorio en esa ciudad."
            if cities:
                line += f" Las ciudades con sedes son: {_join(cities)}."
            add(line)
        elif tool == TOOL_COTIZAR and not data.get("sedes"):
            add("No encontré un precio publicado que cumpla esa solicitud.")
        elif tool == TOOL_INDICADOR_FINANCIERO:
            add("No hay un registro publicado de ese indicador para ese período; no lo estimo.")
        elif tool == TOOL_HISTORIA_CARVAJAL:
            # Los años cercanos son datos de la herramienta (los hitos de la historia oficial).
            years = [str(year) for year in data.get("anios_cercanos") or []]
            line = "La historia oficial no registra un hito en ese año; no lo interpolo."
            if years:
                line += f" Los años más cercanos con hitos son: {_join(years)}."
            add(line)
    return lines


def backing_data(tool: str, data: Mapping[str, Any]) -> Mapping[str, Any]:
    """Parte de los datos de una herramienta que puede respaldar texto en ``no_query``.

    ``preguntas_frecuentes`` devuelve preguntas y respuestas que redactó un LLM: solo su
    ``fragmento`` (el pasaje literal del núcleo que las verificó) respalda algo, nunca la
    ``pregunta`` ni la ``respuesta`` (spec 045, R18). Las demás herramientas devuelven
    datos deterministas y se usan enteros, salvo las claves de :data:`MEMORY_DATA_KEYS`:
    ``sugerencia_ciudad`` es la ciudad habitual de la memoria del dueño y, como los demás
    valores de memoria, nunca respalda un dato de Carvajal (FR-017).
    """

    if tool == TOOL_PREGUNTAS_FRECUENTES:
        entries = data.get("preguntas") or []
        return {
            "fragmentos": [
                str(entry.get("fragmento") or "") for entry in entries if isinstance(entry, Mapping)
            ]
        }
    if MEMORY_DATA_KEYS.isdisjoint(data):
        return data
    return {key: value for key, value in data.items() if key not in MEMORY_DATA_KEYS}


def abstention(
    payloads: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    conversation: str = NO_DATA_ANSWER,
) -> tuple[str, bool]:
    """Texto que sustituye una redacción sin respaldo y si es una abstención.

    Las ausencias de :func:`tool_misses` van primero; ``NO_CONTEXT_ANSWER`` solo si el turno
    buscó de verdad en el corpus (una llamada con ``error`` no buscó). Sin nada de eso sale
    ``conversation`` (la respuesta de conversación que elige quien llama); es una abstención
    salvo que sea una despedida, un saludo o la confirmación de una preferencia.
    """

    items = list(payloads)
    parts = tool_misses(items)
    # Una búsqueda documental con argumentos inválidos (``error``) no consultó el corpus.
    if any(tool in CORPUS_TOOLS and data.get("status") != "error" for tool, data in items):
        parts.append(NO_CONTEXT_ANSWER)
    elif parts:
        parts.append(_MISS_OFFER)
    if parts:
        return " ".join(parts), True
    conversational = conversation in {
        FAREWELL_ANSWER,
        GREETING_ANSWER,
        PREFERENCE_ANSWER,
        FORGET_ANSWER,
        MEMORY_UNAVAILABLE_ANSWER,
    }
    return conversation, not conversational


def out_of_scope(payloads: Iterable[tuple[str, Mapping[str, Any]]]) -> bool:
    """El modelo declaró el tema fuera del alcance y no consultó ningún dato.

    Entonces su redacción no sale: la respuesta es :data:`OUT_OF_SCOPE_ANSWER`. Una opinión
    sobre famosos o un programa en Python pasarían el filtro de ``no_query`` (los nombres
    vienen en la pregunta y el código no trae cifras), así que la negativa es fija. Si el
    turno además consultó algo (por ejemplo, una pregunta mixta), no aplica.
    """

    declared = False
    for tool, data in payloads:
        if tool in DECLARATION_TOOLS:
            if data.get("status") == "ok" and data.get("motivo") == "fuera_de_alcance":
                declared = True
        elif tool not in MEMORY_TOOLS:
            return False
    return declared
