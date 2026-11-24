"""``empresas_del_grupo``: unidades y marcas del grupo con su cita literal (spec 045, T077).

Contrato *empresas_del_grupo* (``contracts/agent-tools-045.md``) y research R11 y R12. Lee la
capa institucional curada ``data/institucional/empresas.json`` que ``carvajal.institutional``
carga una vez (``ToolDeps.institutional``). Es determinista y no añade hechos:

- ``ok``: ``unidades: [{n, nombre, url}]`` y ``marcas: [{n, nombre}]`` en el orden del
  archivo. Con ``nombre``, solo las coincidencias, buscadas también en los alias citados
  (por ejemplo, «Propal» → Carvajal Pulpa y Papel, con ``alias: "PROPAL"``). Cada empresa
  registra su propia fuente ``empresa:{clave}`` cuyo ``excerpt`` son sus citas literales de
  la página del núcleo, que nombran la empresa (y el alias), así que todo nombre devuelto
  está respaldado ante la compuerta.
- ``empty``: nombre que no está ni como empresa ni como alias (por ejemplo, «Alpina») →
  «no tengo ese dato en las fuentes de Carvajal», sin fuentes; nunca afirma que no
  pertenece al grupo, porque la lista solo dice lo que el núcleo nombra.
- ``error``: sin capa institucional o con ``empresas.json`` ``no_disponible`` (despliegue
  incompleto) → error declarado, nunca una excepción.

La clave del ``chunk_id`` es el nombre normalizado de la empresa y no el slug de la página:
varias filas citan ``quienes_somos`` y ``SourceRegistry`` deduplica por ``chunk_id``, así que
con el slug de la página compartirían número y solo quedaría la primera cita.

El módulo no importa el registro: exporta su esquema, :data:`DESCRIPTION`, :func:`status`
y :func:`run`, que ``registry.py`` declara en su ``ToolSpec``.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Final

from pydantic import BaseModel, Field, field_validator

from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _Draft,
    _is_official,
    _Sources,
)
from carvajal.institutional import AVAILABLE, Company, InstitutionalData

__all__ = [
    "DESCRIPTION",
    "EXCERPT_SEPARATOR",
    "EmpresasDelGrupoArgs",
    "run",
    "status",
]

MAX_NAME: Final = 80
# Une las citas literales de una empresa en su ``excerpt`` (cada parte es literal).
EXCERPT_SEPARATOR: Final = " … "
_SECTION: Final = "empresas del grupo"
# Una consulta más corta que esto solo cuenta si coincide con un nombre completo: evita que
# «y» o «de» coincidan con «Tecnología y Servicios» o «Soluciones de Comunicación».
_MIN_PARTIAL: Final = 3
# Palabras que nombran al grupo y no a una empresa: «Grupo Carvajal» pide la lista completa
# y «Carvajal Empaques» coincide con «Empaques».
_GENERIC: Final = frozenset(
    {"carvajal", "grupo", "organizacion", "s", "a", "sa", "empresa", "empresas", "marca"}
)

# Lo que ve el modelo (≤ 300 caracteres, sin cifras, ciudades ni SKU; research R10).
DESCRIPTION: Final = (
    "Unidades de negocio y marcas del grupo Carvajal, cada una con su cita oficial. Úsala "
    "para: qué empresas, unidades o marcas tiene Carvajal, o buscar una por su nombre o "
    "alias. No la uses "
    "para: hitos por año (usa historia_carvajal) ni otros temas documentales (usa "
    "buscar_corpus)."
)

_ERROR_MESSAGE: Final = (
    "La lista de empresas del grupo no está disponible en este momento; no se consultó nada. "
    "Puedes buscar en los documentos con buscar_corpus."
)
# Literal del contrato: no afirma que el nombre no pertenezca al grupo.
_EMPTY_MESSAGE: Final = "no tengo ese dato en las fuentes de Carvajal"
_OK_MESSAGE: Final = (
    "Unidades y marcas que nombran las páginas oficiales de Carvajal, cada una con su cita."
)
_MATCH_MESSAGE: Final = (
    "Empresas o marcas del grupo que coinciden con el nombre pedido, cada una con su cita; "
    "alias indica el otro nombre citado con el que coincidió."
)


class EmpresasDelGrupoArgs(BaseModel):
    """Nombre opcional de una empresa o marca del grupo (el modelo no ve esto)."""

    nombre: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_NAME,
        description=(
            "Empresa, unidad o marca como la escribió la persona; omítelo para listarlas todas."
        ),
    )

    @field_validator("nombre", mode="before")
    @classmethod
    def _collapse(cls, value: object) -> object:
        """Colapsa espacios; texto vacío o solo espacios cuenta como ausente."""

        if isinstance(value, str):
            return " ".join(value.split()) or None
        return value


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado fija: nunca interpola el nombre que escribió el modelo (FR-018)."""

    return "Estoy revisando las empresas y marcas del grupo…"


def _words(text: str) -> tuple[str, ...]:
    """Palabras sin tildes ni mayúsculas ni signos («Educación.» → ``educacion``)."""

    plain = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in plain if not unicodedata.combining(char)).casefold()
    return tuple(re.findall(r"[a-z0-9]+", plain))


def _core(text: str) -> tuple[str, ...]:
    """Palabras que distinguen una empresa (sin «Carvajal», «grupo», etc.)."""

    return tuple(word for word in _words(text) if word not in _GENERIC)


def _contains(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    """``needle`` aparece en ``haystack`` como secuencia de palabras completas."""

    size = len(needle)
    return size > 0 and any(
        tuple(haystack[start : start + size]) == tuple(needle)
        for start in range(len(haystack) - size + 1)
    )


def _partial(query: tuple[str, ...], name: tuple[str, ...]) -> bool:
    if _contains(query, name):  # «Editorial Norma» contiene «Norma»
        return True
    return len(" ".join(query)) >= _MIN_PARTIAL and _contains(name, query)  # «Pulpa»


def _match(company: Company, query: tuple[str, ...], *, exact: bool) -> str | None:
    """``""`` si coincide el nombre, el alias con que coincidió, o ``None`` si no coincide."""

    names = [("", company.name), *((alias.name, alias.name) for alias in company.aliases)]
    for label, name in names:
        core = _core(name)
        if (core == query) if exact else _partial(query, core):
            return label
    return None


def _select(companies: Sequence[Company], name: str | None) -> list[tuple[Company, str]] | None:
    """Empresas pedidas con el alias que coincidió; ``None`` si ``name`` no coincide."""

    query = _core(name or "")
    if not query:  # sin nombre o solo «Grupo Carvajal»: la lista completa
        return [(company, "") for company in companies]
    for exact in (True, False):  # una coincidencia exacta excluye las parciales
        found = [
            (company, label)
            for company in companies
            if (label := _match(company, query, exact=exact)) is not None
        ]
        if found:
            return found
    return None


def _source(company: Company) -> dict[str, Any]:
    quotes = [company.quote, *(alias.quote for alias in company.aliases)]
    return {
        "chunk_id": f"empresa:{'-'.join(_words(company.name))}",
        "slug": company.slug,
        "section": _SECTION,
        "layer": CORE_LAYER,
        "score": 1.0,
        # Solo citas literales de la página del núcleo; cada una nombra la empresa o el alias.
        "excerpt": EXCERPT_SEPARATOR.join(quotes),
        "url": company.url,
        "title": company.name,
        "source_type": "oficial" if _is_official(company.url) else "tercero",
    }


def _available(data: InstitutionalData | None) -> InstitutionalData | None:
    if data is None or data.companies_status != AVAILABLE or not data.companies:
        return None
    return data


def _entry(number: int, company: Company, alias: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"n": number, "nombre": company.name}
    if company.kind == "unidad":
        entry["url"] = company.url
    if alias:
        entry["alias"] = alias
    return entry


def run(
    args: EmpresasDelGrupoArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    """Unidades y marcas citadas, todas o las que coinciden; ver el docstring del módulo."""

    data = _available(deps.institutional)
    if data is None:
        return _Draft("error", _ERROR_MESSAGE, data={"reason": "empresas_no_disponibles"})
    selected = _select(data.companies, args.nombre)
    if selected is None:
        return _Draft("empty", _EMPTY_MESSAGE)
    numbers = sources.add([_source(company) for company, _ in selected])
    entries = [
        (company.kind, _entry(number, company, alias))
        for number, (company, alias) in zip(numbers, selected, strict=True)
    ]
    return _Draft(
        "ok",
        _MATCH_MESSAGE if args.nombre and _core(args.nombre) else _OK_MESSAGE,
        data={
            "unidades": [entry for kind, entry in entries if kind == "unidad"],
            "marcas": [entry for kind, entry in entries if kind == "marca"],
        },
    )
