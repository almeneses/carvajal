"""Herramienta ``convertir_presentacion`` (spec 045, T066; contrato 10).

Convierte una cantidad en cajas, paquetes o pacas de una referencia del catálogo a unidades
con las funciones públicas del motor de cotización (``declared_sizes``, ``conversion`` y
``published_presentations``), sin duplicarlas: el resultado es el mismo que publicaría
``cotizar`` para esa presentación.

- Colombia: la ficha del catálogo y la base de las ofertas publicadas del contrato
  comercial (``Dataset``).
- México: el read model comercial es solo de Colombia, así que cuenta solo la ficha
  (``units_per_pack``/``units_per_box``) con la misma regla del motor; se le pasa un
  ``Dataset`` vacío para que ninguna oferta intervenga.

Si la ficha y el contrato no declaran la presentación, o declaran tamaños distintos, la
herramienta devuelve ``empty`` con ``no_convertible``: nunca supone cuántas unidades trae
una presentación. No calcula precios ni totales (eso es ``cotizar``) y no recibe ciudad.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from carvajal.agent.reference_names import exact_sku, looks_like_sku, resolve_reference
from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _book,
    _Draft,
    _now_bogota,
    _Sources,
)
from carvajal.agent.toolagent.tools.builtin import (
    MAX_NAME_CHARS,
    MAX_TOOL_OPTIONS,
    _offer_source_type,
    _optional_text,
)
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import Reference
from carvajal.commercial.book import plain
from carvajal.quotation import Dataset, Offer, load_dataset
from carvajal.quotation.data import COUNTRY
from carvajal.quotation.engine import conversion, declared_sizes, published_presentations

__all__ = [
    "DESCRIPTION",
    "MAX_TOOL_OPTIONS",
    "NAME",
    "ConvertirPresentacionArgs",
    "run",
    "status",
]

NAME = "convertir_presentacion"
MAX_QUANTITY = 1_000_000
PRESENTATIONS = ("caja", "paquete", "paca")  # las que se convierten a unidades
_UNIT_ALIASES = {
    "unidades": "unidad",
    "cajas": "caja",
    "paquetes": "paquete",
    "pacas": "paca",
}
# Origen de la conversión según las fuentes que declaran el tamaño (``declared_sizes``).
_ORIGINS = {
    frozenset({"catálogo"}): "catalogo",
    frozenset({"contrato"}): "contrato",
    frozenset({"catálogo", "contrato"}): "ambos",
}
_EMPTY_DATASET = Dataset(products=(), offers=(), providers=())

DESCRIPTION = (
    "Convierte una cantidad en cajas, paquetes o pacas de una referencia del catálogo a "
    "unidades, con la presentación que declaran la ficha o la oferta publicada. Úsala para: "
    "cuántas unidades trae una cantidad de cajas o paquetes. No la uses para: precios ni "
    "totales (usa cotizar)."
)

Unidad = Literal["unidad", "paquete", "caja", "paca"]


class ConvertirPresentacionArgs(BaseModel):
    """Referencia, cantidad y presentación a convertir (el modelo no ve este texto)."""

    sku: str | None = Field(
        default=None,
        max_length=MAX_NAME_CHARS,
        description="SKU exacto de la referencia, solo si la persona lo dijo.",
    )
    nombre: str | None = Field(
        default=None,
        max_length=MAX_NAME_CHARS,
        description="Nombre o código de la referencia tal como lo dijo la persona.",
    )
    cantidad: int = Field(
        ge=1,
        le=MAX_QUANTITY,
        description="Cantidad que dijo la persona, en la presentación indicada.",
    )
    unidad: Unidad = Field(
        description="Presentación de la cantidad: unidad, paquete, caja o paca.",
    )

    _sku = field_validator("sku", mode="before")(_optional_text)
    _nombre = field_validator("nombre", mode="before")(_optional_text)

    @field_validator("unidad", mode="before")
    @classmethod
    def _unidad_singular(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = plain(value.strip())
            return _UNIT_ALIASES.get(normalized, normalized)
        return value

    @model_validator(mode="after")
    def _con_referencia(self) -> "ConvertirPresentacionArgs":
        if self.sku is None and self.nombre is None:
            raise ValueError("indica la referencia: sku o nombre")
        return self


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado sin valores: el SKU o el nombre los escribe el modelo (FR-018)."""

    return "Estoy calculando cuántas unidades son…"


# --- Resolución de la referencia -----------------------------------------------------------


def _candidates(references: Sequence[Reference]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for reference in references:
        if reference.sku in seen:
            continue
        seen.add(reference.sku)
        found.append({"sku": reference.sku, "nombre": reference.name, "pais": reference.country})
    return found[:MAX_TOOL_OPTIONS]


def _ask_which(references: Sequence[Reference], *, conflict: bool = False) -> _Draft:
    candidates = _candidates(references)
    message = (
        "El SKU y el nombre señalan referencias distintas; pregunta cuál es."
        if conflict
        else "El nombre coincide con varias referencias; pregunta cuál es."
    )
    data: dict[str, Any] = {"candidatas": candidates}
    if not conflict and len({item.sku for item in references}) > len(candidates):
        data["truncado"] = True
    return _Draft("needs_input", message, data=data, missing=["referencia"])


def _not_found() -> _Draft:
    return _Draft(
        "empty",
        "No encontré esa referencia en el catálogo.",
        data={"referencia_encontrada": False},
    )


def _resolve(args: ConvertirPresentacionArgs, catalog: Catalog) -> Reference | _Draft:
    """La referencia que nombró la persona, o el borrador que pide aclarar o declara ausencia.

    Como el motor: un SKU exacto fija la referencia; si el nombre fija otra distinta, se
    pregunta cuál. Un SKU con forma de SKU que no existe es ``empty``; uno sin esa forma
    («J2») se busca como nombre solo si no hay ``nombre``.
    """

    references = catalog.references
    by_sku = exact_sku(args.sku, references) if args.sku else None
    if by_sku is not None:
        if args.nombre:
            match = resolve_reference(args.nombre, references, catalog)
            skus = {item.sku for item in match.named}
            if match.named and not match.partial and by_sku.sku not in skus:
                return _ask_which([by_sku, *match.named], conflict=True)
        return by_sku
    if args.sku and looks_like_sku(args.sku):
        # Con forma de SKU, el SKU es exacto o no es (como ``ficha_producto``): no se
        # reinterpreta como nombre, que coincidiría en parte con fichas ajenas.
        return _not_found()
    text = args.nombre or args.sku or ""
    match = resolve_reference(text, references, catalog)
    if not match.named:
        return _not_found()
    if len(match.named) > 1 or match.partial:
        return _ask_which(match.named)
    return match.named[0]


# --- Fuentes -------------------------------------------------------------------------------


def _ficha_source(reference: Reference) -> dict[str, Any]:
    """La ficha del catálogo con los tamaños que declara (el nombre trae la «paca x N»)."""

    parts = [reference.name, reference.material]
    if reference.units_per_pack:
        parts.append(f"{reference.units_per_pack} unidades por paquete")
        if reference.packs_per_box:
            parts.append(f"{reference.packs_per_box} paquetes por caja")
        parts.append(f"{reference.units_per_box} unidades por caja")
    else:
        # Sin paquete la ficha conoce una sola presentación y no dice si es caja o paquete
        # (regla de ``declared_sizes``): no se rotula como caja.
        parts.append(f"presentación de {reference.units_per_box} unidades")
    parts.append(f"ficha capturada el {reference.captured_at.isoformat()}")
    return {
        "chunk_id": f"ficha:{reference.sku}:{reference.country}",
        "slug": reference.sku,
        "section": "catalogo_empaques",
        "layer": CORE_LAYER,
        "score": 1.0,
        "excerpt": " · ".join(parts),
        "url": reference.source_url,
        "title": reference.name,
        "source_type": "oficial",
    }


def _offer_sources(
    reference: Reference, offers: Sequence[Offer], deps: ToolDeps
) -> list[dict[str, Any]]:
    """Una fuente ``precio:`` por sede para las ofertas del contrato que declaran el tamaño.

    El excerpt dice quién la publica, la presentación y su fecha; no la ciudad: la
    herramienta no recibe ciudad y no debe sugerir una.
    """

    book = _book(deps)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for offer in offers:
        if offer.site_id in seen:
            continue
        seen.add(offer.site_id)
        entries.append(
            {
                "chunk_id": f"precio:{offer.sku}:{offer.site_id}",
                "slug": f"precio-{offer.sku}",
                "section": "cotizador",
                "layer": CORE_LAYER,
                "score": 1.0,
                "excerpt": (
                    f"Oferta publicada por {offer.provider}: {offer.basis} de "
                    f"{offer.units_in_basis} unidades · capturada el "
                    f"{offer.captured_at.isoformat()}"
                ),
                "url": offer.source_url,
                "title": f"{offer.provider}: {reference.name}",
                "source_type": _offer_source_type(book, offer.sku, offer.site_id, offer.source_url),
            }
        )
    return entries


# --- Conversión ----------------------------------------------------------------------------


def _dataset(reference: Reference, deps: ToolDeps) -> Dataset:
    """Read model comercial de Colombia; vacío para México (cuenta solo la ficha)."""

    if reference.country != COUNTRY:
        return _EMPTY_DATASET
    if deps.dataset is not None:
        return deps.dataset
    day = deps.today or _now_bogota(deps).date()
    return load_dataset(day.isoformat())


def _declared(reference: Reference, dataset: Dataset) -> list[str]:
    """Presentaciones con un solo tamaño declarado («caja de 1000»), sin contradicciones."""

    found: list[str] = []
    for unit in PRESENTATIONS:
        sizes = declared_sizes(reference, unit, dataset)
        if len(sizes) == 1:
            [size] = sizes
            found.append(f"{unit} de {size}")
    return found


def _identity(reference: Reference, dataset: Dataset, ficha_n: int) -> _Draft:
    return _Draft(
        "needs_input",
        "La cantidad ya está en unidades: no hay nada que convertir. Si la persona pregunta "
        "por cajas, paquetes o pacas, pide la cantidad en esa presentación.",
        data={
            "n": ficha_n,
            "sku": reference.sku,
            "nombre": reference.name,
            "pais": reference.country,
            "presentaciones_declaradas": _declared(reference, dataset),
        },
        missing=["unidad"],
    )


def _not_convertible(
    reference: Reference,
    unit: str,
    dataset: Dataset,
    ficha_n: int,
    sources: _Sources,
    deps: ToolDeps,
) -> _Draft:
    sizes = declared_sizes(reference, unit, dataset)
    published = published_presentations(reference, dataset)
    # Solo se citan las ofertas cuya presentación se lista (las del nombre no son ofertas).
    listed = [
        offer
        for offer in dataset.offers_for(reference.sku)
        if f"{offer.basis} de {offer.units_in_basis}" in published
    ]
    offer_ns = sources.add(_offer_sources(reference, listed, deps)) if listed else []
    contradictory = len(sizes) > 1
    message = (
        "No se puede convertir con certeza: la ficha y la oferta publicada declaran tamaños "
        "distintos para esa presentación."
        if contradictory
        else "No se puede convertir con certeza: ni la ficha ni la oferta publicada declaran "
        "esa presentación para esta referencia."
    )
    return _Draft(
        "empty",
        message,
        data={
            "no_convertible": True,
            "motivo": "contradictorio" if contradictory else "no_declarado",
            "n": ficha_n,
            "fuentes": [ficha_n, *offer_ns],
            "sku": reference.sku,
            "nombre": reference.name,
            "pais": reference.country,
            "unidad_pedida": unit,
            "presentaciones_publicadas": published,
            "presentaciones_declaradas": _declared(reference, dataset),
        },
    )


def run(
    args: ConvertirPresentacionArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    """Convierte ``cantidad`` en ``unidad`` a unidades de la referencia nombrada."""

    catalog = deps.catalog or load_catalog()
    resolved = _resolve(args, catalog)
    if isinstance(resolved, _Draft):
        return resolved
    reference = resolved
    dataset = _dataset(reference, deps)
    [ficha_n] = sources.add([_ficha_source(reference)])
    if args.unidad == "unidad":
        return _identity(reference, dataset, ficha_n)
    converted = conversion(reference, args.cantidad, args.unidad, dataset)
    if converted is None:
        return _not_convertible(reference, args.unidad, dataset, ficha_n, sources, deps)
    [(size, origins)] = declared_sizes(reference, args.unidad, dataset).items()
    origin = _ORIGINS[frozenset(origins)]
    numbers = [ficha_n]
    if "contrato" in origins:
        offers = [
            offer
            for offer in dataset.offers_for(reference.sku)
            if offer.basis == args.unidad and offer.units_in_basis == size
        ]
        numbers.extend(sources.add(_offer_sources(reference, offers, deps)))
    # La ficha respalda la conversión si la declara; si solo la declara el contrato, la oferta.
    number = numbers[1] if "catálogo" not in origins and len(numbers) > 1 else ficha_n
    return _Draft(
        "ok",
        "Conversión con la presentación declarada; preséntala tal cual, sin recalcularla.",
        data={
            "sku": reference.sku,
            "nombre": reference.name,
            "pais": reference.country,
            "conversion": {
                "n": number,
                "texto": converted.as_dict()["text"],
                "cantidad": converted.quantity,
                "unidad": converted.unit,
                "unidades_por_presentacion": converted.units_per,
                "unidades": converted.units,
                "origen": origin,
                "fuentes": numbers,
            },
        },
    )
