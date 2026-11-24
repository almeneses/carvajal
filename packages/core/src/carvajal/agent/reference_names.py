"""Búsqueda de referencias del catálogo por nombre, código o SKU (spec 044).

La comparten la herramienta ``buscar_producto`` y el motor del Cotizador
(``QuoteRequest.sku`` / ``QuoteRequest.name``), para que «P1», «J2», «CO-CTT-16» o «vaso de
icopor» identifiquen la misma referencia al explorar y al cotizar.

El texto de la persona y la ficha se comparan por términos normalizados (sin tildes ni
mayúsculas): todos los términos de la búsqueda deben estar en el nombre, el SKU, la familia,
el material o sus nombres coloquiales. Los códigos y números se comparan enteros («P1» no es
«P1S») y el tamaño del empaque («paca x 200») no identifica una referencia.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from carvajal.agent.commercial import CAPACITY_TOLERANCE_OZ
from carvajal.catalog.loader import Catalog
from carvajal.catalog.models import Reference
from carvajal.commercial.book import plain

__all__ = [
    "NamedReferences",
    "capacity_fits",
    "exact_sku",
    "informative_terms",
    "is_generic_name",
    "match_named",
    "matched_terms",
    "name_family",
    "query_terms",
    "resolve_reference",
]

_NAME_STOPWORDS = frozenset(
    {
        "a", "al", "con", "de", "del", "el", "en", "la", "las", "lo", "los", "o", "para",
        "por", "sin", "un", "una", "unas", "unos", "y", "x",
        "oz", "onza", "onzas", "cm", "mm", "ml", "centimetro", "centimetros", "pzs", "und",
        "unidad", "unidades", "caja", "cajas", "paca", "pacas", "paquete", "paquetes",
        "codigo", "modelo", "numero", "ref", "referencia", "sku", "tipo",
    }
)  # fmt: skip
# Nombres coloquiales del material: «icopor» (Colombia), «unicel» (México) y «anime»
# (Venezuela) son el EPS. Se agregan a los términos de la ficha, no a la búsqueda.
_MATERIAL_ALIASES = {
    "EPS": "icopor unicel anime foam espuma espumado poliestireno expandido termico",
    "PS": "poliestireno plastico",
    "PP": "polipropileno plastico",
    # «rpet» no es alias del PET: solo coincide con las fichas que dicen RPET (reciclado).
    "PET": "plastico",
    "PULPA": "pulpa bagazo cana fibra",
    "PAPEL": "papel carton",
}
_FAMILY_ALIASES = {"charola": "bandeja", "tapa": "domo", "cubierto": "cubiertos"}
_DIMENSION = re.compile(r"(\d)\s*[x\u00d7]\s*(\d)")  # «14 x 14» → «14x14»
_NUMBER_UNIT = re.compile(r"(\d)(oz|cm|mm|ml|pzs)\b")  # «8oz» → «8 oz»
_NAME_TOKEN = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
# Tamaño del empaque: «(paquete x 50)», «(paca x 200)», «Caja con 1000 pzs», «Caja 20X50
# 1000», «300 unidades». No identifica la referencia y se quita antes de comparar: una
# búsqueda que trae la cantidad no debe coincidir con fichas que no tienen nada que ver.
_PACK_SIZE = re.compile(
    r"\b(?:caja|paca|paquete)s?\s+(?:(?:con|de|x)\s*)?\d+(?:x\d+)?(?:\s+\d+)?"
    r"|\bx\s*\d+"
    r"|\b\d+\s*(?:pzs|piezas|und|unidades)\b"
)
# Capacidad declarada en el nombre de una ficha sin ``capacity_oz``: «3,5 a 7 oz» es un
# rango; «12 y 16 oz», dos medidas.
_OZ_PAIR = re.compile(r"(\d+(?:\.\d+)?)\s*(?:oz)?\s*\b(a|y)\b\s*(\d+(?:\.\d+)?)\s*oz")
_OZ_SINGLE = re.compile(r"(\d+(?:\.\d+)?)\s*oz")
_SKU_TEXT = re.compile(r"[\s_]+")


def name_terms(text: str) -> list[str]:
    """Términos normalizados de un texto: «Tapa 3,5 a 7 oz» → tapa, 3.5, a, 7, oz.

    Sin el tamaño del empaque: «Vaso 3,5 oz (paquete x 50)» → vaso, 3.5, oz.
    """

    folded = _NUMBER_UNIT.sub(r"\1 \2", _DIMENSION.sub(r"\1x\2", plain(text)))
    folded = _PACK_SIZE.sub(" ", folded)
    return [term.replace(",", ".") for term in _NAME_TOKEN.findall(folded)]


def stems(term: str) -> frozenset[str]:
    """Raíces de un término sin plural ni género («vasos», «térmica») para comparar.

    Los códigos y números («P1», «J2», «34», «14x14») se comparan enteros: «P1» no
    coincide con «P1S».
    """

    if not term.isalpha() or len(term) < 4:
        return frozenset({term})
    bases = {term}
    if term.endswith("s"):
        bases.add(term[:-1])
    if term.endswith("es"):
        bases.add(term[:-2])
    return frozenset(base[:-1] if len(base) > 4 and base[-1] in "aeo" else base for base in bases)


def query_terms(name: str | None) -> list[frozenset[str]]:
    """Términos de la búsqueda por nombre (sin palabras vacías ni unidades)."""

    if not name:
        return []
    return [stems(term) for term in name_terms(name) if term not in _NAME_STOPWORDS]


def reference_stems(reference: Reference) -> frozenset[str]:
    """Raíces de la ficha: nombre, SKU, familia, material y sus nombres coloquiales."""

    capacity = f"{reference.capacity_oz:g}" if reference.capacity_oz is not None else ""
    text = " ".join(
        (
            reference.name,
            reference.sku,
            reference.family,
            _FAMILY_ALIASES.get(reference.family, ""),
            reference.material,
            _MATERIAL_ALIASES.get(reference.material, ""),
            capacity,
        )
    )
    return frozenset(stem for term in name_terms(text) for stem in stems(term))


def matched_terms(reference: Reference, query: Sequence[frozenset[str]]) -> int:
    """Cuántos términos de la búsqueda están en la ficha."""

    found = reference_stems(reference)
    return sum(1 for term in query if term & found)


def _name_capacities(name: str) -> list[tuple[float, float]]:
    """Capacidades en onzas que declara el nombre, como rangos ``(mínima, máxima)``."""

    text = plain(name).replace(",", ".")
    found: list[tuple[float, float]] = []
    for low, joiner, high in _OZ_PAIR.findall(text):
        if joiner == "a":
            found.append((float(low), float(high)))
        else:
            found.extend(((float(low), float(low)), (float(high), float(high))))
    found.extend((float(value), float(value)) for value in _OZ_SINGLE.findall(text))
    return found


def capacity_fits(reference: Reference, capacity: float) -> bool:
    """Capacidad de la ficha o, si no la tiene, la que declara su nombre (tapas «3,5 a 7 oz»)."""

    tolerance = CAPACITY_TOLERANCE_OZ
    if reference.capacity_oz is not None:
        return abs(reference.capacity_oz - capacity) <= tolerance
    return any(
        low - tolerance <= capacity <= high + tolerance
        for low, high in _name_capacities(reference.name)
    )


def _family_words(family: str | None, catalog: Catalog) -> frozenset[str]:
    """Raíces de los nombres de familia del catálogo y del alias de la familia pedida."""

    families = {reference.family for reference in catalog.references}
    text = " ".join((*families, _FAMILY_ALIASES.get(family or "", "")))
    return frozenset(stem for term in name_terms(text) for stem in stems(term))


def informative_terms(
    query: Sequence[frozenset[str]],
    found: Sequence[Reference],
    family: str | None,
    catalog: Catalog,
) -> list[frozenset[str]]:
    """Términos que distinguen una referencia de otra dentro de la búsqueda.

    No lo son el nombre de una familia («vaso», «tapa», «plato») ni el término que está en
    todas las candidatas: con ellos, «vaso J2» daba por coincidencia parcial todos los
    vasos y escondía el J2 (contenedor) que la persona nombró.
    """

    words = _family_words(family, catalog)
    found_stems = [reference_stems(reference) for reference in found]
    return [
        term
        for term in query
        if not term & words
        and not (len(found_stems) > 1 and all(term & item for item in found_stems))
    ]


@dataclass(frozen=True)
class NamedReferences:
    """Referencias que coinciden con un nombre; ``partial`` si les falta un término."""

    named: list[Reference]
    partial: bool = False


def match_named(
    query: Sequence[frozenset[str]],
    name: str,
    found: Sequence[Reference],
    family: str | None,
    catalog: Catalog,
) -> NamedReferences:
    """Referencias de ``found`` que coinciden con el nombre.

    Primero las que tienen todos los términos; si no hay y la búsqueda tiene varios, las
    que tienen todos menos uno con al menos un término informativo (``partial``: «vaso de
    icopor 16 oz» trae los vasos de EPS de otra capacidad y los de 16 oz de otro material;
    «vaso J2» no trae todos los vasos). El SKU exacto va primero.
    """

    exact = plain(name)
    informative = informative_terms(query, found, family, catalog)
    counts = [(reference, matched_terms(reference, query)) for reference in found]
    named = [reference for reference, count in counts if count == len(query)]
    partial = False
    if not named and len(query) >= 2 and informative:
        named = [
            reference
            for reference, count in counts
            if count == len(query) - 1 and matched_terms(reference, informative)
        ]
        partial = bool(named)
    named.sort(key=lambda reference: plain(reference.sku) != exact)
    return NamedReferences(named=named, partial=partial)


def _sku_text(value: str) -> str:
    return _SKU_TEXT.sub("", plain(value))


def exact_sku(text: str, references: Sequence[Reference]) -> Reference | None:
    """La referencia cuyo SKU es ``text`` tal cual («CO-PCOM-P1», «co pcom p1»), o ``None``.

    Solo el SKU exacto: «CO-PCOM-P1S» no es el P1 ni ningún otro portacomidas.
    """

    wanted = _sku_text(text)
    return next((reference for reference in references if _sku_text(reference.sku) == wanted), None)


def _type_nouns(catalog: Catalog) -> dict[str, frozenset[str]]:
    """Raíz → familias de los sustantivos de tipo: la familia, su alias y la primera palabra
    del nombre de cada ficha («portacomidas», «bandeja», «domo», «bowl»)."""

    found: dict[str, set[str]] = {}
    for reference in catalog.references:
        heads = name_terms(reference.name)[:1]
        texts = (reference.family, _FAMILY_ALIASES.get(reference.family, ""), *heads)
        for term in name_terms(" ".join(texts)):
            for stem in stems(term):
                found.setdefault(stem, set()).add(reference.family)
    return {stem: frozenset(families) for stem, families in found.items()}


def _material_words() -> frozenset[str]:
    """Raíces de los materiales y sus nombres coloquiales («icopor», «pulpa», «plástico»)."""

    text = " ".join((*_MATERIAL_ALIASES, *_MATERIAL_ALIASES.values()))
    return frozenset(stem for term in name_terms(text) for stem in stems(term))


def name_family(text: str, catalog: Catalog) -> str | None:
    """Familia que dice el nombre con su sustantivo de tipo («vaso P1» → ``vaso``).

    ``None`` si el nombre no trae un sustantivo de tipo («P1», «Gold») o si el que trae
    corresponde a varias familias.
    """

    nouns = _type_nouns(catalog)
    for term in query_terms(text):
        families = frozenset().union(*(nouns.get(stem, frozenset()) for stem in term))
        if len(families) == 1:
            return next(iter(families))
    return None


def is_generic_name(text: str, catalog: Catalog) -> bool:
    """El nombre solo describe un tipo de producto («vaso de icopor», «portacomidas de icopor»).

    Lo es si todos sus términos son un sustantivo de tipo o un material, sin códigos ni
    números: no nombra una referencia concreta, así que se cotiza como una búsqueda por
    familia (temperatura, la opción más económica y alternativas) y no como una aclaración.
    """

    query = query_terms(text)
    if not query:
        return False
    generic = frozenset(_type_nouns(catalog)) | _material_words()
    return all(
        not any(character.isdigit() for stem in term for character in stem) and term & generic
        for term in query
    )


def resolve_reference(
    text: str,
    references: Sequence[Reference],
    catalog: Catalog,
    *,
    family: str | None = None,
    capacity_oz: float | None = None,
) -> NamedReferences:
    """Referencias que nombra ``text`` (SKU exacto, código o nombre) entre ``references``.

    Un SKU escrito tal cual («CO-PCOM-P1», «co pcom p1») identifica una sola referencia.
    Si no, se busca por nombre con la misma normalización de ``buscar_producto``. Cuando el
    nombre trae varias, la familia y la capacidad que dijo la persona acotan la lista solo
    si dejan alguna: un dato mal pasado no esconde la referencia nombrada.
    """

    exact = exact_sku(text, references)
    if exact is not None:
        return NamedReferences(named=[exact])
    query = query_terms(text)
    if not query:
        return NamedReferences(named=[])
    match = match_named(query, text, references, family, catalog)
    named = match.named
    if len(named) > 1:
        narrowed = [
            reference
            for reference in named
            if (family is None or reference.family == family)
            and (capacity_oz is None or capacity_fits(reference, capacity_oz))
        ]
        named = narrowed or named
    partial = match.partial
    if partial and len(named) == 1:
        # Solo faltó un nombre de familia («vaso J2» cuando el J2 es un contenedor): los
        # términos que distinguen la referencia están todos, así que es esa.
        informative = informative_terms(query, references, family, catalog)
        partial = not informative or matched_terms(named[0], informative) < len(informative)
    return NamedReferences(named=named, partial=partial)
