"""Compuerta de evidencia del modo ``tools`` (spec 044, FR-004; research R2, R3).

El modelo decide qué consultar, pero no decide si su redacción se muestra: lo decide esta
compuerta, determinista, sobre los ``ToolOutcome`` del turno.

- :func:`gate` elige uno de tres caminos. **Evidencia del turno** (``data-model.md``) es al
  menos un ``ToolOutcome`` con ``status == "ok"`` y ``source_ids`` no vacío.

  - ``evidence``: hay evidencia; el texto se transmite con la retención de 48 caracteres.
  - ``abstain``: no hay evidencia y el turno buscó en el corpus (``buscar_corpus``); la
    respuesta es ``NO_CONTEXT_ANSWER`` y no se transmite nada del modelo.
  - ``no_query``: no hay evidencia ni búsqueda documental (solo ``sin_consulta`` o
    herramientas sin evidencia), o alguna herramienta pidió un dato (``needs_input``)
    aunque la búsqueda documental del turno saliera vacía: la petición del dato (FR-005)
    no se pierde tras la abstención. El texto completo se retiene y pasa
    :func:`filter_unsupported` antes de emitirse de una vez.

- :func:`filter_unsupported` es el filtro de la ruta directa aplicado a la salida. La ruta
  directa del modo ``routes`` no tiene un filtro de salida reutilizable (se apoya en el
  prompt), así que aquí se implementa uno conservador que reutiliza el criterio de
  entidades de :mod:`carvajal.application.evidence` (palabras capitalizadas fuera del
  inicio de la frase y entidades conocidas del corpus) y añade cifras (también en letras
  y en números romanos), fechas, montos, empresas del grupo y ciudades de Colombia en
  cualquier posición y en minúscula. Las cifras pequeñas en letras («dos», «tres»…) no
  cuentan: son habituales en una aclaración («te pido dos datos»).
  Lo que ya aportaron la pregunta o los resultados de las herramientas no cuenta como
  afirmación nueva (``allowed``). Ante la duda bloquea: una abstención de más es preferible
  a una cifra inventada.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any, Final, Literal

from carvajal.agent.toolagent.state import TOOL_BUSCAR_CORPUS
from carvajal.application.evidence import _KNOWN_ENTITIES, _entities

__all__ = [
    "GATE_ABSTAIN",
    "GATE_EVIDENCE",
    "GATE_NO_QUERY",
    "GatePath",
    "filter_unsupported",
    "gate",
    "has_evidence",
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


def has_evidence(results: Iterable[Mapping[str, Any]]) -> bool:
    """Indica si alguna herramienta del turno devolvió ``ok`` con citas ``[n]``."""

    return any(r.get("status") == "ok" and r.get("source_ids") for r in results)


def gate(results: Iterable[Mapping[str, Any]]) -> GatePath:
    """Camino de la respuesta según los ``ToolOutcome`` del turno (research R3).

    Ver el docstring del módulo. Un turno sin herramientas (por ejemplo, un proveedor que
    ignoró ``tool_choice="required"``) cae en ``no_query``: su texto se filtra entero.
    """

    outcomes = list(results)
    if has_evidence(outcomes):
        return GATE_EVIDENCE
    if any(r.get("status") == "needs_input" for r in outcomes):
        # Falta un dato: la pregunta al usuario se filtra en vez de perderse (FR-005).
        return GATE_NO_QUERY
    if any(r.get("tool") == TOOL_BUSCAR_CORPUS for r in outcomes):
        return GATE_ABSTAIN
    return GATE_NO_QUERY


def _fold(text: str) -> str:
    """Minúsculas sin tildes, para comparar palabras con lo permitido."""

    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _digits(number: str) -> str:
    """Solo los dígitos: «2.000», «2 000» y «2000» son la misma cifra."""

    return "".join(c for c in number if c.isdigit())


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
    allowed_numbers = {_digits(n) for n in _NUMBER.findall(allowed_text)}
    cleaned = _clean(text)
    terms: list[str] = []

    def flag(term: str) -> None:
        if term not in terms:
            terms.append(term)

    for match in _NUMBER.finditer(cleaned):
        raw = match.group().strip()
        if _digits(raw) not in allowed_numbers:
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
            if folded in _ASSISTANT_TERMS or folded in allowed_words:
                continue
            flag(entity)
    return terms


def filter_unsupported(text: str, *, allowed: Iterable[str] = ()) -> bool:
    """Indica si el texto del camino ``no_query`` debe sustituirse por la abstención.

    Es ``True`` cuando el texto trae alguna cifra, fecha, monto o nombre propio de
    Carvajal que no respaldan la pregunta ni los resultados del turno (``allowed``).
    """

    return bool(unsupported_terms(text, allowed=allowed))
