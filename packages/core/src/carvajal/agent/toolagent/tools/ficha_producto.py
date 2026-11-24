"""Herramienta ``ficha_producto`` (spec 045, T063; contracts/agent-tools-045.md).

Devuelve la ficha de **una** referencia del catálogo y los precios publicados de esa
referencia, cada uno con su fuente, sin totales ni recomendación de uso. Es determinista y
no persiste nada.

- Resolución: SKU exacto (``reference_names.exact_sku``) o nombre
  (``reference_names.resolve_reference``), acotados por país si se pasa. Varias
  coincidencias, o una parcial, devuelven ``needs_input`` con las candidatas: nunca se elige
  por la persona.
- Ficha: ``Catalog.references``. Las fichas CO no declaran temperatura; la regla del
  material sale de ``Catalog.materials`` y va rotulada como de la guía, nunca de la ficha.
- Presentación: en las fichas con «paca x N» o «paquete x N» en el nombre, la presentación
  sale del nombre, como en el motor (``quotation.engine.declared_sizes``); sin paquete
  declarado, ``units_per_box`` no se presenta como caja.
- Precios: ``CommercialBook.offers_for_sku`` (derivado de ``Catalog.offers`` y con la
  presentación que publica el vendedor), en la moneda del país de la referencia.
- Fuentes (research R11): ``ficha:{sku}:{pais}``, ``guia:{material}`` (la de
  ``guia_materiales``, con el mismo extracto) y
  ``precio:{sku}:{sede}`` (el mismo ``chunk_id`` que ``cotizar``; sin sede, el id de la
  oferta). Cada cifra, fecha, ciudad y nombre devuelto está en el ``excerpt``.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from carvajal.agent.reference_names import exact_sku, resolve_reference
from carvajal.agent.toolagent.tools import guia_materiales
from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _book,
    _Draft,
    _is_official,
    _now_bogota,
    _Sources,
)
from carvajal.agent.toolagent.tools.builtin import MAX_NAME_CHARS, MAX_TOOL_OPTIONS
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import MaterialRule, Reference
from carvajal.commercial.book import CommercialBook
from carvajal.commercial.models import Offer
from carvajal.quotation.engine import _NAME_PRESENTATION

__all__ = ["DESCRIPTION", "MAX_CANDIDATES", "FichaProductoArgs", "run", "status"]

# Candidatas que ve el modelo ante un nombre ambiguo (el mismo tope que ``buscar_producto``).
MAX_CANDIDATES = MAX_TOOL_OPTIONS
# Precios por ficha que ve el modelo; el resto se declara con ``total_precios``.
MAX_PRICES = MAX_TOOL_OPTIONS
GUIDE_LABEL = "según la guía de materiales, no la ficha"
_SECTION = "catalogo_empaques"

DESCRIPTION = (
    "Ficha de una referencia concreta del catálogo (material, capacidad, presentación, usos) "
    "y sus precios publicados con su fuente. Úsala para: cómo es un producto que la persona "
    "nombró por código, SKU o nombre. No la uses para: buscar opciones (usa buscar_producto) "
    "ni calcular totales (usa cotizar)."
)


def _optional_text(value: object) -> object:
    """Texto opcional: vacío o solo espacios cuenta como ausente."""

    if isinstance(value, str):
        stripped = " ".join(value.split())
        return stripped or None
    return value


class FichaProductoArgs(BaseModel):
    """Ficha de una referencia del catálogo por SKU exacto o por nombre."""

    sku: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "SKU exacto de la referencia, tal como aparece en los datos de las herramientas "
            "(como CO-PCOM-J2)."
        ),
    )
    nombre: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_NAME_CHARS,
        description=(
            "Referencia que nombró la persona: código (como P1 o J2) o nombre comercial o "
            "coloquial. Una descripción genérica no es una referencia."
        ),
    )
    pais: Literal["CO", "MX"] | None = Field(
        default=None,
        description="País de la ficha (CO o MX), solo si la persona lo dijo; acota la búsqueda.",
    )

    _texts = field_validator("sku", "nombre", mode="before")(_optional_text)

    @model_validator(mode="after")
    def _sku_o_nombre(self) -> Self:
        if self.sku is None and self.nombre is None:
            raise ValueError("indica el sku o el nombre de la referencia")
        return self


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado sin valores: el SKU o el nombre los escribe el modelo (FR-018)."""

    return "Estoy revisando la ficha del producto…"


# --- Resolución ----------------------------------------------------------------------------


def _candidates(references: list[Reference]) -> list[dict[str, str]]:
    return [{"sku": item.sku, "nombre": item.name} for item in references[:MAX_CANDIDATES]]


def _choose(message: str, references: list[Reference], **extra: Any) -> _Draft:
    """La persona debe elegir la referencia; no se registran fuentes ni se elige por ella."""

    return _Draft(
        "needs_input",
        message + " Pregunta a la persona cuál quiere y pide su ficha con el sku; no elijas tú.",
        data={
            "candidatas": _candidates(references),
            "total_candidatas": len(references),
            **extra,
        },
        missing=["referencia"],
    )


def _not_found() -> _Draft:
    # Sin eco del SKU ni del nombre: los escribe el modelo y respaldarían sus cifras.
    return _Draft(
        "empty",
        "Ninguna referencia del catálogo coincide; no hay ficha que mostrar.",
        data={"referencia_encontrada": False},
    )


def _resolve(args: FichaProductoArgs, catalog: Catalog) -> Reference | _Draft:
    """La referencia pedida, o el borrador ``needs_input``/``empty`` que corresponde."""

    references = [
        item for item in catalog.references if args.pais is None or item.country == args.pais
    ]
    named = resolve_reference(args.nombre, references, catalog) if args.nombre is not None else None
    if args.sku is not None:
        exact = exact_sku(args.sku, references)
        if exact is None:
            # El SKU es exacto o no es: no se reinterpreta como nombre de otra referencia.
            return _not_found()
        if named is not None and named.named and exact not in named.named:
            return _choose(
                "El sku y el nombre corresponden a referencias distintas del catálogo.",
                [exact, *named.named],
                conflicto_sku_nombre=True,
            )
        return exact
    if named is None or not named.named:
        return _not_found()
    if named.partial:
        return _choose(
            "Ninguna referencia coincide con todos los términos del nombre; estas coinciden en "
            "parte y no son exactamente la nombrada.",
            named.named,
            parcial=True,
        )
    if len(named.named) > 1:
        return _choose("El nombre coincide con varias referencias del catálogo.", named.named)
    return named.named[0]


# --- Ficha ---------------------------------------------------------------------------------


def _num(value: float | None) -> int | float | None:
    """Número tal como se cita: sin «.0» si es entero (4.0 → 4, 67.8 → 67.8)."""

    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def _name_presentation(reference: Reference) -> dict[str, Any] | None:
    """La «paca x N» o el «paquete x N» del nombre de la ficha, como en el motor."""

    match = _NAME_PRESENTATION.search(reference.name)
    if match is None:
        return None
    return {"presentacion": match.group(1).lower(), "unidades": int(match.group(2))}


def _sizes(reference: Reference) -> dict[str, Any]:
    """Unidades por paquete y por caja que la ficha permite afirmar.

    ``units_per_box`` es la caja si la ficha declara además el paquete. Si no lo declara y el
    nombre dice «paca x N» o «paquete x N», la ficha conoce una sola presentación y no dice
    que sea una caja (``declared_sizes`` del motor): ``unidades_por_caja`` queda ``None``.
    """

    from_name = _name_presentation(reference)
    per_pack = reference.units_per_pack
    if per_pack is None and from_name and from_name["presentacion"] == "paquete":
        per_pack = from_name["unidades"]
    per_box: int | None = reference.units_per_box
    if reference.units_per_pack is None and from_name is not None:
        per_box = None
    return {
        "unidades_por_paquete": per_pack,
        "paquetes_por_caja": reference.packs_per_box,
        "unidades_por_caja": per_box,
        "presentacion_nombre": from_name,
    }


def _ficha_excerpt(reference: Reference, sizes: Mapping[str, Any]) -> str:
    parts = [
        reference.name,
        f"SKU {reference.sku}",
        f"familia {reference.family}",
        f"material {reference.material}",
    ]
    if reference.brand:
        parts.append(f"marca {reference.brand}")
    if reference.capacity_oz is not None:
        parts.append(f"{_num(reference.capacity_oz)} oz")
    if reference.diameter_mm is not None:
        parts.append(f"diámetro {_num(reference.diameter_mm)} mm")
    if sizes["unidades_por_paquete"] is not None:
        parts.append(f"{sizes['unidades_por_paquete']} unidades por paquete")
    if sizes["paquetes_por_caja"] is not None:
        parts.append(f"{sizes['paquetes_por_caja']} paquetes por caja")
    if sizes["unidades_por_caja"] is not None:
        parts.append(f"{sizes['unidades_por_caja']} unidades por caja")
    named = sizes["presentacion_nombre"]
    if named is not None:
        parts.append(f"{named['presentacion']} de {named['unidades']} unidades según el nombre")
    if reference.uses:
        parts.append(f"usos: {', '.join(reference.uses)}")
    if reference.temp_min_c is not None and reference.temp_max_c is not None:
        parts.append(
            f"temperatura de la ficha de {_num(reference.temp_min_c)} a "
            f"{_num(reference.temp_max_c)} °C"
        )
    parts.append(f"país {reference.country}")
    parts.append(f"ficha capturada el {reference.captured_at.isoformat()}")
    return " · ".join(parts)


def _ficha_source(reference: Reference, sizes: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": f"ficha:{reference.sku}:{reference.country}",
        "slug": reference.sku,
        "section": _SECTION,
        "layer": CORE_LAYER,
        "score": 1.0,
        "excerpt": _ficha_excerpt(reference, sizes),
        "url": reference.source_url,
        "title": reference.name,
        "source_type": "oficial" if _is_official(reference.source_url) else "tercero",
    }


def _guide(rule: MaterialRule, sources: _Sources) -> dict[str, Any]:
    """Regla de la guía de materiales, rotulada: nunca es un dato de la ficha.

    La fuente ``guia:{material}`` es la misma de ``guia_materiales`` (y de
    ``comparar_referencias``): el registro deduplica por ``chunk_id`` y conserva la primera,
    así que las tres deben registrar el mismo extracto, que transcribe la fila completa.
    """

    [number] = sources.add([guia_materiales._source(rule)])
    return {
        "n": number,
        "microondas": rule.microwave,
        "temperatura": {"min_c": _num(rule.temp_min_c), "max_c": _num(rule.temp_max_c)},
        "rotulo": GUIDE_LABEL,
    }


# --- Precios -------------------------------------------------------------------------------


def _price_excerpt(offer: Offer, seller: str) -> str:
    """Mismo inicio que el extracto de ``cotizar`` (comparten ``chunk_id``), más la ciudad
    y la vigencia publicada, que esta herramienta también devuelve."""

    city = f" ({offer.city})" if offer.city else ""
    text = (
        f"Precio {offer.amount} {offer.currency} por {offer.presentation.basis} de "
        f"{offer.presentation.units_in_basis} · IVA {offer.tax_status} · publicado por "
        f"{seller}{city} · capturado el {offer.source.captured_at.isoformat()}"
    )
    if offer.source.valid_until is not None:
        text += f" · vigente hasta {offer.source.valid_until.isoformat()}"
    return text


def _prices(
    reference: Reference, book: CommercialBook, today: date, sources: _Sources
) -> tuple[list[dict[str, Any]], int, bool]:
    """Precios publicados de la referencia en su país; nunca se estiman ni se convierten.

    Devuelve los precios que ve el modelo, el total publicado y si alguna vigencia terminó.
    """

    records = [
        (record.offer, record.seller.kind)
        for record in book.offers_for_sku(reference.sku, reference.country, as_of=today)
        if record.offer is not None
    ]
    shown: list[dict[str, Any]] = []
    expired = False
    for offer, seller_kind in records[:MAX_PRICES]:
        seller = offer.source.name
        kind = "tercero" if seller_kind == "distribuidor" else "oficial"
        place = offer.site_id or offer.offer_id
        [number] = sources.add(
            [
                {
                    "chunk_id": f"precio:{reference.sku}:{place}",
                    "slug": f"precio-{reference.sku}",
                    "section": _SECTION,
                    "layer": CORE_LAYER,
                    "score": 1.0,
                    "excerpt": _price_excerpt(offer, seller),
                    "url": offer.source.url or reference.source_url,
                    "title": f"{seller}: {reference.name}",
                    "source_type": kind,
                }
            ]
        )
        price: dict[str, Any] = {
            "n": number,
            "presentacion": offer.presentation.basis,
            "unidades": offer.presentation.units_in_basis,
            "importe": _amount(offer.amount),
            "moneda": offer.currency,
            "iva_incluido": {"incluido": True, "no_incluido": False}.get(offer.tax_status),
            "vendedor": seller,
            "ciudad": offer.city,
            "fecha": offer.source.captured_at.isoformat(),
            "tipo_fuente": kind,
        }
        if offer.source.valid_until is not None:
            price["vigente_hasta"] = offer.source.valid_until.isoformat()
            expired = expired or offer.source.valid_until < today
        shown.append(price)
    return shown, len(records), expired


def _amount(value: Decimal) -> str:
    """Importe exacto como texto (JSON no admite ``Decimal`` y no se redondea)."""

    return str(value)


# --- Herramienta ---------------------------------------------------------------------------


def run(args: FichaProductoArgs, deps: ToolDeps, context: ToolContext, sources: _Sources) -> _Draft:
    """Ficha y precios publicados de una referencia (ver el docstring del módulo)."""

    catalog = deps.catalog or load_catalog()
    if args.pais is not None and args.pais not in catalog.countries:
        return _Draft(
            "empty",
            "El catálogo cargado no tiene referencias de ese país.",
            data={"referencia_encontrada": False, "pais_catalogo": args.pais},
        )
    resolved = _resolve(args, catalog)
    if isinstance(resolved, _Draft):
        return resolved
    reference = resolved
    today = deps.today or _now_bogota(deps).date()
    sizes = _sizes(reference)
    [number] = sources.add([_ficha_source(reference, sizes)])
    rule = catalog.materials.get(reference.material)
    guide = _guide(rule, sources) if rule is not None else None
    prices, total, expired = _prices(reference, _book(deps), today, sources)
    temperature = (
        {"min_c": _num(reference.temp_min_c), "max_c": _num(reference.temp_max_c)}
        if reference.temp_min_c is not None and reference.temp_max_c is not None
        else None
    )
    ficha: dict[str, Any] = {
        "n": number,
        "sku": reference.sku,
        "nombre": reference.name,
        "familia": reference.family,
        "material": reference.material,
        "marca": reference.brand,
        "capacidad_oz": _num(reference.capacity_oz),
        "diametro_mm": _num(reference.diameter_mm),
        "unidades_por_paquete": sizes["unidades_por_paquete"],
        "paquetes_por_caja": sizes["paquetes_por_caja"],
        "unidades_por_caja": sizes["unidades_por_caja"],
        "usos": list(reference.uses),
        "pais": reference.country,
        "fecha_ficha": reference.captured_at.isoformat(),
        "temperatura_ficha": temperature,
        "guia_material": guide,
        "precios": prices,
    }
    if sizes["presentacion_nombre"] is not None:
        ficha["presentacion_nombre"] = sizes["presentacion_nombre"]
    notes: list[str] = []
    if temperature is None and guide is not None:
        notes.append(
            "La ficha no declara temperatura: la de guia_material es de la guía de materiales "
            "y se dice así, nunca como dato de la ficha."
        )
    elif temperature is None:
        notes.append("Ni la ficha ni la guía de materiales declaran temperatura para esta.")
    if prices:
        notes.append(
            "Los precios son de referencia por la presentación que publica cada vendedor, en "
            "la moneda de su país; no son totales ni confirman existencias."
        )
    else:
        notes.append(
            "Ninguna fuente publica precio para esta referencia; no se estima ni se convierte "
            "desde otro país."
        )
    if total > len(prices):
        ficha["total_precios"] = total
        notes.append("Se muestran solo algunos de los precios publicados.")
    if expired:
        notes.append("La vigencia publicada de algún precio terminó; puede haber cambiado.")
    message = f"Ficha de la referencia con {len(prices)} precio(s) publicado(s). " + " ".join(notes)
    return _Draft("ok", message, data={"ficha": ficha})
