"""Herramienta ``comparar_referencias`` (spec 045, T064; contracts/agent-tools-045.md).

Compara de dos a cuatro referencias concretas del catálogo versionado, lado a lado: ficha
(material, capacidad, unidades por caja, usos), la regla de su material en la guía de
materiales (rotulada: no es la ficha) y el precio unitario publicado. Es determinista y no
consulta la red.

Precio unitario = importe ÷ unidades de la base, siempre con ``Decimal`` y sin estimar ni
convertir monedas:

- **CO**: desde la oferta publicada del contrato comercial (``CommercialBook.offers``),
  ``Offer.amount / Offer.presentation.units_in_basis``.
- **MX**: desde el precio por caja del catálogo (``Catalog.offers``),
  ``ReferencePrice.amount / Reference.units_per_box``.

No usa ``engine._offer_for``: opera sobre ofertas CO y exige una cantidad. Con varias
ofertas de la misma referencia se toma la de menor precio unitario (y se informa cuántas
hay). ``mas_economica`` solo se declara si todas tienen precio y coinciden país, moneda y
``tax_status``; si no, queda ``null`` con un ``aviso``. No recomienda uso (eso es
``buscar_producto``) ni da totales (eso es ``cotizar``).

Fuentes: ``ficha:{sku}:{pais}`` (URL de la ficha), ``precio:{sku}:{sede}`` en CO (el mismo
``chunk_id`` que ``cotizar``) o ``precio:{sku}:MX`` en México, y ``guia:{material}`` con la
transcripción de ``guia_materiales``. Cada cifra devuelta aparece en el ``excerpt`` de su
fuente. Los textos que escribió el modelo nunca vuelven en los datos (solo su posición): su
eco respaldaría cifras inventadas en la compuerta de evidencia (FR-003).

No importa el registro: exporta su esquema, ``DESCRIPTION``, ``status`` y ``run``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field, StringConstraints

from carvajal.agent.reference_names import resolve_reference
from carvajal.agent.toolagent.tools import guia_materiales
from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _book,
    _Draft,
    _is_official,
    _Sources,
)
from carvajal.agent.toolagent.tools.builtin import MAX_NAME_CHARS, MAX_TOOL_OPTIONS
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import Reference
from carvajal.commercial.book import CommercialBook

__all__ = [
    "DESCRIPTION",
    "GUIDE_LABEL",
    "MAX_REFERENCES",
    "MIN_REFERENCES",
    "CompararReferenciasArgs",
    "run",
    "status",
]

MIN_REFERENCES: Final = 2
MAX_REFERENCES: Final = 4
# Rótulo de los datos que salen de la guía y no de la ficha (contrato de ``ficha_producto``).
GUIDE_LABEL: Final = "según la guía de materiales, no la ficha"
# Precisión del precio unitario que ve el modelo; el veredicto usa el cociente exacto.
_UNIT_PRICE_STEP: Final = Decimal("0.0001")

DESCRIPTION: Final = (
    "Compara lado a lado referencias concretas del catálogo: material, capacidad, unidades "
    "por caja, guía de materiales y precio por unidad publicado. Úsala para: diferencias "
    "entre referencias ya nombradas. No la uses para: buscar opciones (usa buscar_producto) "
    "ni precios totales (usa cotizar)."
)

_Referencia = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NAME_CHARS)
]


class CompararReferenciasArgs(BaseModel):
    """Referencias a comparar (el modelo no ve este docstring)."""

    referencias: list[_Referencia] = Field(
        min_length=MIN_REFERENCES,
        max_length=MAX_REFERENCES,
        description=(
            "De dos a cuatro referencias, cada una por su SKU o nombre comercial, como las "
            "nombró la persona."
        ),
    )
    pais: Literal["CO", "MX"] | None = Field(
        default=None,
        description="País del catálogo (CO o MX); sin país se busca en ambos.",
    )


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado fija: no interpola referencias ni país (FR-018)."""

    return "Estoy comparando las referencias que me pediste…"


# --- Precio unitario -----------------------------------------------------------------------


@dataclass(frozen=True)
class _UnitPrice:
    """Precio por unidad de una oferta publicada, con lo que hace falta para citarlo."""

    value: Decimal  # cociente exacto: decide el veredicto
    amount: Decimal
    units: int
    basis: str
    currency: str
    tax_status: str
    seller: str
    url: str
    captured_at: str
    source_type: str
    chunk_id: str
    offers: int = 1


def _decimal_text(value: Decimal) -> str:
    """Precio unitario legible: hasta cuatro decimales, nunca menos de dos."""

    text = format(value.quantize(_UNIT_PRICE_STEP, rounding=ROUND_HALF_UP), "f")
    whole, _, decimals = text.partition(".")
    decimals = decimals.rstrip("0").ljust(2, "0")
    return f"{whole}.{decimals}"


def _co_prices(reference: Reference, book: CommercialBook) -> list[_UnitPrice]:
    """Ofertas CO del contrato: importe ÷ unidades de la presentación publicada."""

    prices: list[_UnitPrice] = []
    for offer in book.offers:
        if offer.sku != reference.sku or offer.country != reference.country:
            continue
        seller = book.seller(offer.seller_id)
        url = offer.source.url or ""
        kind = (
            ("tercero" if seller.kind == "distribuidor" else "oficial")
            if seller is not None
            else ("oficial" if _is_official(url) else "tercero")
        )
        units = offer.presentation.units_in_basis
        prices.append(
            _UnitPrice(
                value=offer.amount / Decimal(units),
                amount=offer.amount,
                units=units,
                basis=offer.presentation.basis,
                currency=offer.currency,
                tax_status=offer.tax_status,
                seller=seller.name if seller is not None else offer.source.name,
                url=url,
                captured_at=offer.source.captured_at.isoformat(),
                source_type=kind,
                chunk_id=f"precio:{offer.sku}:{offer.site_id or offer.offer_id}",
            )
        )
    return prices


def _mx_prices(reference: Reference, catalog: Catalog) -> list[_UnitPrice]:
    """Precios MX del catálogo (por caja): importe ÷ unidades por caja de la ficha."""

    prices: list[_UnitPrice] = []
    for offer in catalog.offers(reference):
        suffix = "MX" if offer.source_type == "oficial" else f"MX:{offer.source_name}"
        prices.append(
            _UnitPrice(
                value=offer.amount / Decimal(reference.units_per_box),
                amount=offer.amount,
                units=reference.units_per_box,
                basis="caja",
                currency=offer.currency,
                tax_status=offer.tax_status,
                seller=offer.source_name,
                url=offer.source_url,
                captured_at=offer.captured_at.isoformat(),
                source_type=offer.source_type,
                chunk_id=f"precio:{reference.sku}:{suffix}",
            )
        )
    return prices


def _unit_price(reference: Reference, catalog: Catalog, book: CommercialBook) -> _UnitPrice | None:
    """La oferta de menor precio unitario (desempate estable por ``chunk_id``), o ``None``."""

    prices = (
        _co_prices(reference, book) if reference.country == "CO" else _mx_prices(reference, catalog)
    )
    if not prices:
        return None
    best = min(prices, key=lambda price: (price.value, price.chunk_id))
    return replace(best, offers=len(prices))


# --- Fuentes -------------------------------------------------------------------------------


def _number(value: float | None) -> int | float | None:
    """Cifra como entero cuando no tiene decimales (``8.0`` → ``8``)."""

    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def _ficha_source(reference: Reference) -> dict[str, Any]:
    """La ficha con todas las cifras que la comparación devuelve de ella."""

    parts = [f"{reference.name} (SKU {reference.sku}, {reference.country})", reference.material]
    if reference.capacity_oz is not None:
        parts.append(f"{_number(reference.capacity_oz)} oz")
    if reference.diameter_mm is not None:
        parts.append(f"{_number(reference.diameter_mm)} mm de diámetro")
    if reference.units_per_pack and reference.packs_per_box:
        parts.append(
            f"{reference.units_per_pack} unidades por paquete, "
            f"{reference.packs_per_box} paquetes por caja"
        )
    parts.append(f"{reference.units_per_box} unidades por caja")
    if reference.uses:
        parts.append("usos: " + ", ".join(reference.uses))
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
        "source_type": "oficial" if _is_official(reference.source_url) else "tercero",
    }


def _price_source(reference: Reference, price: _UnitPrice) -> dict[str, Any]:
    """El precio publicado y su cociente por unidad, con quién lo publica y cuándo.

    Sin la ciudad de la sede: una ciudad en la fuente respaldaría una ciudad que la persona
    no dijo (FR-013); el formato sigue el de ``cotizar`` para el mismo ``chunk_id``.
    """

    excerpt = (
        f"Precio {price.amount} {price.currency} por {price.basis} de {price.units} · IVA "
        f"{price.tax_status} · publicado por {price.seller} · capturado el "
        f"{price.captured_at} · precio por unidad: {_decimal_text(price.value)} "
        f"{price.currency}"
    )
    return {
        "chunk_id": price.chunk_id,
        "slug": f"precio-{reference.sku}",
        "section": "catalogo_empaques",
        "layer": CORE_LAYER,
        "score": 1.0,
        "excerpt": excerpt,
        "url": price.url,
        "title": f"{price.seller}: {reference.name}",
        "source_type": price.source_type,
    }


# --- Resolución ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Resolution:
    resolved: list[Reference]
    ambiguous: list[dict[str, Any]]
    missing: list[int]


def _resolve(
    texts: Sequence[str], references: Sequence[Reference], catalog: Catalog
) -> _Resolution:
    """Resuelve cada texto como ``ficha_producto``: SKU exacto o nombre único y completo.

    Una coincidencia parcial (le falta un término) no se da por la referencia nombrada: se
    ofrece como candidata, igual que varias coincidencias.
    """

    resolved: list[Reference] = []
    ambiguous: list[dict[str, Any]] = []
    missing: list[int] = []
    for position, text in enumerate(texts, start=1):
        match = resolve_reference(text, references, catalog)
        if len(match.named) == 1 and not match.partial:
            resolved.append(match.named[0])
        elif match.named:
            ambiguous.append(
                {
                    "posicion": position,
                    "opciones": [
                        {"sku": item.sku, "nombre": item.name}
                        for item in match.named[:MAX_TOOL_OPTIONS]
                    ],
                    "total_coincidencias": len(match.named),
                }
            )
        else:
            missing.append(position)
    return _Resolution(resolved=resolved, ambiguous=ambiguous, missing=missing)


# --- Comparación ---------------------------------------------------------------------------


def _row(
    reference: Reference,
    catalog: Catalog,
    book: CommercialBook,
    sources: _Sources,
) -> tuple[dict[str, Any], _UnitPrice | None]:
    [ficha_n] = sources.add([_ficha_source(reference)])
    rule = catalog.materials.get(reference.material)
    temperature: dict[str, Any] | None = None
    if rule is not None:
        [guide_n] = sources.add([guia_materiales._source(rule)])
        temperature = {
            "min_c": _number(rule.temp_min_c),
            "max_c": _number(rule.temp_max_c),
            "n": guide_n,
            "rotulo": GUIDE_LABEL,
        }
    price = _unit_price(reference, catalog, book)
    unit_price: dict[str, Any] | None = None
    if price is not None:
        [price_n] = sources.add([_price_source(reference, price)])
        unit_price = {
            "valor": _decimal_text(price.value),
            "moneda": price.currency,
            "iva": price.tax_status,
            "vendedor": price.seller,
            "n": price_n,
            "ofertas": price.offers,
        }
    row = {
        "n": ficha_n,
        "sku": reference.sku,
        "nombre": reference.name,
        "pais": reference.country,
        "material": reference.material,
        "capacidad_oz": _number(reference.capacity_oz),
        "microondas": rule.microwave if rule is not None else None,
        "temperatura_guia": temperature,
        "unidades_por_caja": reference.units_per_box,
        "usos": list(reference.uses),
        "precio_unitario": unit_price,
    }
    return row, price


def _text(value: Any) -> str:
    if value is None:
        return "no declarado"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "no declarado"
    return str(value)


def _differences(rows: Sequence[Mapping[str, Any]], comparable: bool) -> list[str]:
    """Una línea por atributo que cambia entre las referencias (sin juicios de uso)."""

    def line(label: str, values: Sequence[str]) -> str:
        pairs = "; ".join(f"{row['sku']}: {value}" for row, value in zip(rows, values, strict=True))
        return f"{label}: {pairs}"

    found: list[str] = []
    if len({row["pais"] for row in rows}) > 1:
        found.append(line("País", [row["pais"] for row in rows]))
    if len({row["material"] for row in rows}) > 1:
        found.append(line("Material", [row["material"] for row in rows]))
    capacities = [row["capacidad_oz"] for row in rows]
    if len(set(capacities)) > 1:
        found.append(
            line(
                "Capacidad",
                [f"{value} oz" if value is not None else _text(None) for value in capacities],
            )
        )
    boxes = [row["unidades_por_caja"] for row in rows]
    if len(set(boxes)) > 1:
        found.append(line("Unidades por caja", [str(value) for value in boxes]))
    microwave = [row["microondas"] for row in rows]
    if len(set(microwave)) > 1:
        found.append(line(f"Microondas ({GUIDE_LABEL})", [_text(value) for value in microwave]))
    temperatures = [
        f"de {temp['min_c']} °C a {temp['max_c']} °C" if (temp := row["temperatura_guia"]) else None
        for row in rows
    ]
    if len(set(temperatures)) > 1:
        found.append(line(f"Temperatura ({GUIDE_LABEL})", [_text(value) for value in temperatures]))
    uses = [tuple(row["usos"]) for row in rows]
    if len(set(uses)) > 1:
        found.append(line("Usos", [_text(list(value)) for value in uses]))
    if comparable:
        prices = [row["precio_unitario"] for row in rows]
        if len({price["valor"] for price in prices}) > 1:
            found.append(
                line(
                    "Precio por unidad",
                    [
                        f"{price['valor']} {price['moneda']} (IVA {price['iva']})"
                        for price in prices
                    ],
                )
            )
    return found


def _verdict(
    rows: Sequence[Mapping[str, Any]], prices: Sequence[_UnitPrice | None]
) -> tuple[str | None, str | None, bool]:
    """``(mas_economica, aviso, comparable)``: veredicto solo con precios homogéneos."""

    if any(price is None for price in prices):
        missing = [row["sku"] for row, price in zip(rows, prices, strict=True) if price is None]
        return (
            None,
            "No se declara la más económica: sin precio publicado para "
            + ", ".join(missing)
            + "; no se estima.",
            False,
        )
    priced = [price for price in prices if price is not None]
    if len({row["pais"] for row in rows}) > 1:
        return (
            None,
            "No se comparan precios entre referencias de distinto país; no se convierte "
            "entre monedas.",
            False,
        )
    if len({price.currency for price in priced}) > 1:
        return None, "No se comparan precios en monedas distintas; no se convierte.", False
    if len({price.tax_status for price in priced}) > 1:
        return (
            None,
            "No se declara la más económica: los precios no tratan el IVA igual (incluido, "
            "no incluido o no indicado).",
            False,
        )
    lowest = min(price.value for price in priced)
    winners = [row["sku"] for row, price in zip(rows, priced, strict=True) if price.value == lowest]
    if len(winners) > 1:
        return None, "Las referencias tienen el mismo precio por unidad publicado.", True
    return winners[0], None, True


def run(
    args: CompararReferenciasArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    """Compara las referencias nombradas; nunca estima precios ni recomienda uso."""

    catalog = deps.catalog or load_catalog()
    if args.pais is not None and args.pais not in catalog.countries:
        return _Draft(
            "empty",
            "El catálogo cargado no tiene referencias de ese país.",
            data={"pais_catalogo": args.pais},
        )
    references = [
        reference
        for reference in catalog.references
        if args.pais is None or reference.country == args.pais
    ]
    found = _resolve(args.referencias, references, catalog)
    if found.ambiguous:
        data: dict[str, Any] = {"candidatas": found.ambiguous}
        if found.missing:
            data["no_encontradas"] = found.missing
        return _Draft(
            "needs_input",
            "Alguna referencia coincide con varias fichas; la persona debe elegir cuál "
            "(posición en la lista de referencias pedidas).",
            data=data,
            missing=["referencia"],
        )
    unique: list[Reference] = []
    repeated: list[str] = []
    for reference in found.resolved:
        if any(item.sku == reference.sku for item in unique):
            repeated.append(reference.sku)
        else:
            unique.append(reference)
    if len(unique) < MIN_REFERENCES:
        data = {
            "encontradas": [{"sku": item.sku, "nombre": item.name} for item in unique],
            "no_encontradas": found.missing,
        }
        if repeated:
            data["misma_referencia"] = repeated
        return _Draft(
            "empty",
            "No hay dos referencias distintas del catálogo para comparar (las no encontradas "
            "van por su posición en la lista pedida).",
            data=data,
        )
    book = _book(deps)
    built = [_row(reference, catalog, book, sources) for reference in unique]
    rows = [row for row, _ in built]
    prices = [price for _, price in built]
    cheapest, notice, comparable = _verdict(rows, prices)
    comparison: dict[str, Any] = {
        "filas": rows,
        "diferencias": _differences(rows, comparable),
        "mas_economica": cheapest,
        "aviso": notice,
    }
    data = {"comparacion": comparison}
    if found.missing:
        data["no_encontradas"] = found.missing
    if repeated:
        data["misma_referencia"] = repeated
    message = (
        f"Comparación de {len(rows)} referencias del catálogo. Los precios son de referencia "
        "por unidad, sin totales ni envío; no es una recomendación de uso."
    )
    if cheapest is not None:
        message += " La más económica por unidad es la indicada en mas_economica."
    return _Draft("ok", message, data=data)
