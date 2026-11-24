"""``historia_carvajal``: hitos literales de la historia oficial (spec 045, T076).

Contrato *historia_carvajal* (``contracts/agent-tools-045.md``) y research R11 y R12. Lee
los hitos que ``carvajal.institutional`` parte de ``data/gold/historia.md`` por sus
encabezados ``## AAAA`` (``ToolDeps.institutional``), con la URL de ``historia`` en
``index.jsonl``. Es determinista y no añade hechos:

- ``ok``: ``hitos: [{n, anio, texto}]`` en orden cronológico, como mucho
  :data:`MAX_MILESTONES`, y ``hay_mas`` si el rango tiene más. Cada hito registra su propia
  fuente ``historia:{anio}`` cuyo ``excerpt`` contiene el año y el texto literal, así que
  todo año, cifra y nombre devuelto está respaldado ante la compuerta.
- ``empty``: ningún hito en el año o el rango pedidos → ``anios_cercanos`` con el año
  anterior y el siguiente que sí tienen hito (o solo uno, en los extremos). Nunca
  interpola ni resume lo que pasó entre dos hitos, y no registra fuentes.
- ``error``: sin capa institucional, con la historia ``no_disponible`` (Gold sin sembrar)
  o sin URL que citar → error declarado, nunca una excepción.

El módulo no importa el registro: exporta su esquema, :data:`DESCRIPTION`, :func:`status`
y :func:`run`, que ``registry.py`` declara en su ``ToolSpec``.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _Draft,
    _is_official,
    _Sources,
)
from carvajal.institutional import AVAILABLE, HISTORY_SLUG, InstitutionalData, Milestone

__all__ = [
    "DESCRIPTION",
    "MAX_MILESTONES",
    "HistoriaCarvajalArgs",
    "run",
    "status",
]

# Tope de hitos por llamada (spec, casos límite): con más, se avisa con ``hay_mas``.
MAX_MILESTONES: Final = 10
MIN_YEAR: Final = 1900
MAX_YEAR: Final = 2100
_SECTION: Final = "historia"

# Lo que ve el modelo (≤ 300 caracteres, sin cifras, ciudades ni SKU; research R10).
DESCRIPTION: Final = (
    "Hitos de la historia oficial de Carvajal por año o por rango de años, con su texto "
    "literal y su fuente. Úsala para: qué pasó en un año, qué hitos hubo entre dos años. "
    "No la uses para: empresas o marcas actuales del grupo (usa empresas_del_grupo) ni "
    "otros temas documentales (usa buscar_corpus)."
)

_ERROR_MESSAGE: Final = (
    "La historia de Carvajal no está disponible en este momento; no se consultó nada. "
    "Puedes buscar en los documentos con buscar_corpus."
)
_EMPTY_MESSAGE: Final = (
    "La historia oficial no registra un hito en ese año o rango; no se interpola ni se "
    "resume lo que pasó entre hitos. anios_cercanos son los años más próximos que sí tienen "
    "hito."
)
_OK_MESSAGE: Final = "Hitos de la historia oficial de Carvajal, con su texto literal."
_MORE_MESSAGE: Final = (
    " El rango tiene más hitos de los que se muestran (hay_mas); pide un rango más corto "
    "para ver el resto."
)


def _optional_year(value: object) -> object:
    """Texto vacío o solo espacios cuenta como ausente; el resto lo valida Pydantic."""

    if isinstance(value, str):
        return value.strip() or None
    return value


class HistoriaCarvajalArgs(BaseModel):
    """Año o rango de años de la historia oficial de Carvajal (el modelo no ve esto)."""

    anio: int | None = Field(
        default=None,
        ge=MIN_YEAR,
        le=MAX_YEAR,
        description="Año exacto, de cuatro cifras. No lo combines con desde ni hasta.",
    )
    desde: int | None = Field(
        default=None,
        ge=MIN_YEAR,
        le=MAX_YEAR,
        description="Primer año del rango, incluido.",
    )
    hasta: int | None = Field(
        default=None,
        ge=MIN_YEAR,
        le=MAX_YEAR,
        description="Último año del rango, incluido; sin él, hasta el hito más reciente.",
    )

    _years = field_validator("anio", "desde", "hasta", mode="before")(_optional_year)

    @model_validator(mode="after")
    def _one_query(self) -> Self:
        if self.anio is not None and (self.desde is not None or self.hasta is not None):
            raise ValueError("usa anio para un año o desde y hasta para un rango, no ambos")
        if self.anio is None and self.desde is None:
            raise ValueError("indica anio o desde")
        if self.desde is not None and self.hasta is not None and self.hasta < self.desde:
            raise ValueError("hasta debe ser mayor o igual que desde")
        return self


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado fija: nunca interpola el año que escribió el modelo (FR-018)."""

    return "Estoy buscando en la historia de Carvajal…"


def _bounds(args: HistoriaCarvajalArgs) -> tuple[int, int]:
    if args.anio is not None:
        return args.anio, args.anio
    first = args.desde if args.desde is not None else MIN_YEAR
    return first, args.hasta if args.hasta is not None else MAX_YEAR


def _nearby(milestones: Sequence[Milestone], first: int, last: int) -> list[int]:
    """Año con hito inmediatamente anterior a ``first`` y siguiente a ``last``."""

    before = [m.year for m in milestones if m.year < first]
    after = [m.year for m in milestones if m.year > last]
    return [*before[-1:], *after[:1]]


def _source(milestone: Milestone, url: str) -> dict[str, Any]:
    return {
        "chunk_id": f"historia:{milestone.year}",
        "slug": HISTORY_SLUG,
        "section": _SECTION,
        "layer": CORE_LAYER,
        "score": 1.0,
        # El año del encabezado y el texto literal: todo lo que devuelve el hito.
        "excerpt": f"{milestone.year}: {milestone.text}",
        "url": url,
        "title": f"Historia de Carvajal ({milestone.year})",
        "source_type": "oficial" if _is_official(url) else "tercero",
    }


def _available(data: InstitutionalData | None) -> InstitutionalData | None:
    if data is None or data.history_status != AVAILABLE:
        return None
    if not data.milestones or not data.history_url:
        return None
    return data


def run(
    args: HistoriaCarvajalArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    """Hitos literales del año o del rango pedidos; ver el docstring del módulo."""

    data = _available(deps.institutional)
    if data is None:
        return _Draft("error", _ERROR_MESSAGE, data={"reason": "historia_no_disponible"})
    milestones = sorted(data.milestones, key=lambda milestone: milestone.year)
    first, last = _bounds(args)
    found = [m for m in milestones if first <= m.year <= last]
    if not found:
        return _Draft(
            "empty", _EMPTY_MESSAGE, data={"anios_cercanos": _nearby(milestones, first, last)}
        )
    shown = found[:MAX_MILESTONES]
    numbers = sources.add([_source(milestone, data.history_url) for milestone in shown])
    more = len(found) > len(shown)
    return _Draft(
        "ok",
        _OK_MESSAGE + (_MORE_MESSAGE if more else ""),
        data={
            "hitos": [
                {"n": number, "anio": milestone.year, "texto": milestone.text}
                for number, milestone in zip(numbers, shown, strict=True)
            ],
            "hay_mas": more,
        },
    )
