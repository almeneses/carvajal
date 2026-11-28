"""Plantillas de los mensajes del flujo de cotización (spec 054, T2-T10).

Todo mensaje con cifras sale de aquí y no del modelo: las mismas plantillas sirven a la web
(Markdown y tarjeta), a WhatsApp (``apps.mcp.bridge.to_whatsapp`` pasa ``**x**`` a ``*x*``) y a
la voz (``spoken``: frases cortas, sin emojis ni Markdown, importes con dígitos y «pesos»).

Reglas de estilo (ver la spec):

- Una sola pregunta, al final, con la respuesta sugerida en negrita.
- Una línea por producto; 12 líneas o menos en los mensajes habituales.
- Emojis fijos como marcadores, uno por bloque: 🔎 opciones, ⭐ recomendada, 📊 comparativo,
  ✅ hecho, 🔄 actualizado, 🛒 carrito, 🧾 resumen, 📍 ciudad, 💰 total, 📄 PDF, ↩️ retomar.
- Formato colombiano: ``$206.000``, «und», «c/u», «IVA incl.», fechas «2 dic».
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from zoneinfo import ZoneInfo

from carvajal.quotation.draft import (
    QuoteDraft,
    line_label,
    line_subtotal,
    totals,
    units_of,
)

__all__ = [
    "EXAMPLE_EMAIL",
    "JOKE_REJECTION",
    "ORDER_OFFER",
    "PDF_PROMISE",
    "RECEIPT_PROMISE",
    "Message",
    "added",
    "already_registered",
    "ask_product",
    "cancelled",
    "cart_view",
    "cheaper_offer",
    "comparison",
    "edit_cart",
    "empty_cart",
    "format_date",
    "format_money",
    "format_units",
    "joke",
    "leaving",
    "line_cap",
    "no_lines_yet",
    "option_rows",
    "options",
    "out_of_range",
    "pdf_offer",
    "pending_close",
    "pending_dropped",
    "pending_label",
    "pending_note",
    "receipt_offer",
    "reference_out_of_range",
    "registered",
    "reminder",
    "removed",
    "resume",
    "summary",
    "switch_question",
    "which_reference",
]

JOKE_REJECTION: Final = "Arrancado, vaya cómprele a su madre entonces 😂"
PDF_PROMISE: Final = "📄 Te envío el PDF."
"""Última línea de T6: cada canal la cambia por el PDF que entrega (ver :func:`pdf_offer`)."""
_MONTHS: Final = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)
_BASIS: Final = {
    "caja": ("caja", "cajas"),
    "paquete": ("paquete", "paquetes"),
    "paca": ("paca", "pacas"),
    "unidad": ("unidad", "unidades"),
    "bolsa": ("bolsa", "bolsas"),
}


@dataclass(frozen=True)
class Message:
    """Un mensaje del flujo: ``text`` (Markdown) y su versión hablada ``spoken``.

    ``photos`` son los SKU que el mensaje presenta (la recomendada de unas opciones, lo
    recién agregado): WhatsApp les añade la foto del catálogo, como antes de la spec 054.
    """

    text: str
    spoken: str
    photos: tuple[str, ...] = ()


# --- formato ----------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def format_money(value: Any, currency: str = "COP") -> str | None:
    """``$204.370`` (COP sin decimales cuando son cero); ``None`` si no hay importe."""

    number = _decimal(value)
    if number is None:
        return None
    if number == number.to_integral_value():
        text = f"{int(number):,}".replace(",", ".")
    else:
        whole, _, cents = f"{number:,.2f}".partition(".")
        text = f"{whole.replace(',', '.')},{cents}"
    return f"${text}" if currency.upper() in {"COP", "MXN", "USD"} else f"{text} {currency}"


def format_date(value: Any, *, year: bool = True) -> str | None:
    """``18 nov 2026`` (o ``18 nov``) desde ``date``, ``datetime`` o ISO."""

    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    elif isinstance(value, str) and value.strip():
        try:
            day = date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    else:
        return None
    text = f"{day.day} {_MONTHS[day.month - 1]}"
    return f"{text} {day.year}" if year else text


def format_units(value: Any) -> str:
    number = _decimal(value)
    if number is None:
        return "—"
    return f"{int(number):,}".replace(",", ".")


def _spoken_money(value: Any, currency: str = "COP") -> str:
    money = format_money(value, currency)
    if money is None:
        return "por confirmar"
    return f"{money.removeprefix('$')} pesos" if currency.upper() == "COP" else money


def _say(text: str) -> str:
    """Nombre de producto apto para voz: «12 oz» → «12 onzas», sin símbolos."""

    said = text.replace(" oz", " onzas").replace('"', " pulgadas").replace("″", " pulgadas")
    return " ".join(said.replace("*", "").split())


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _plural(basis: str, count: Any) -> str:
    singular, plural = _BASIS.get(str(basis).lower(), (str(basis), f"{basis}s"))
    number = _decimal(count)
    return singular if number == 1 else plural


def _presentation(line: Mapping[str, Any]) -> Mapping[str, Any]:
    primary = _map(line.get("primary"))
    return _map(primary.get("presentation")) or _map(
        _map(line.get("commercial")).get("presentation")
    )


def _currency(line: Mapping[str, Any]) -> str:
    primary = _map(line.get("primary"))
    return str(primary.get("currency") or _map(line.get("commercial")).get("currency") or "COP")


def _packs(line: Mapping[str, Any]) -> str:
    """«10 paquetes x 50»."""

    presentation = _presentation(line)
    count = presentation.get("count")
    basis = str(presentation.get("basis") or "")
    units = presentation.get("units")
    if not count or not basis:
        return ""
    text = f"{format_units(count)} {_plural(basis, count)}"
    if units and basis != "unidad":
        text += f" x {format_units(units)}"
    return text


def _unit_price(line: Mapping[str, Any]) -> str | None:
    return format_money(_presentation(line).get("unit_price"), _currency(line))


def _city(line: Mapping[str, Any]) -> str:
    return str(
        line.get("city") or _map(_map(line.get("primary")).get("provider")).get("city") or ""
    )


def _seller(line: Mapping[str, Any]) -> str:
    primary = _map(line.get("primary"))
    return str(primary.get("seller") or _map(primary.get("provider")).get("name") or "")


def _seller_label(line: Mapping[str, Any]) -> str:
    """Vendedor y, si publica el precio en otra ciudad que la pedida, esa ciudad.

    «EDEXA (precio de Bogotá)»: en una cotización para Cali, «Cali · EDEXA» haría creer que
    EDEXA vende en Cali a ese precio.
    """

    seller = _seller(line)
    price_city = _remote_city(_map(line.get("primary")))
    if seller and price_city:
        return f"{seller} (precio de {price_city})"
    return seller


def _remote_city(option: Mapping[str, Any]) -> str:
    """Ciudad donde se publica el precio si no es la de la cotización; ``""`` si es la misma."""

    return str(option.get("price_city") or "") if option.get("price_origin") else ""


def _remote_shipping(draft: QuoteDraft) -> str:
    """Nota del resumen cuando un precio se publica en otra ciudad (decisión 4: el envío va
    en el resumen). Ningún dato declara cobertura, así que llevarlo a la ciudad pedida queda
    por confirmar con el vendedor; ``""`` si todos los precios son de la ciudad.
    """

    remote = [_remote_city(_map(line.get("primary"))) for line in draft.lines]
    cities = list(dict.fromkeys(city for city in remote if city))
    if not cities:
        return ""
    origin = " y ".join(cities)
    target = f" a {draft.city}" if draft.city else ""
    return f"🚚 Precios publicados en {origin}: el envío{target} se confirma con el vendedor."


def _spoken_price_city(lines: Sequence[Mapping[str, Any]]) -> str:
    """Frase hablada que dice en qué otra ciudad se publican los precios; ``""`` si en ninguna.

    La voz no lee el rótulo escrito: sin esta frase, un precio de Bogotá sonaría como el
    precio de la ciudad pedida.
    """

    remote = [_remote_city(_map(line.get("primary"))) for line in lines]
    cities = list(dict.fromkeys(city for city in remote if city))
    if not cities:
        return ""
    where = _say(" y ".join(cities))
    if len(lines) == 1:
        return f" Es el precio publicado en {where}."
    if all(remote):
        return f" Son precios publicados en {where}."
    return f" Incluye precios publicados en {where}."


def _count_label(count: int) -> str:
    return "1 producto" if count == 1 else f"{count} productos"


# --- T2: opciones -------------------------------------------------------------------------


def option_rows(quote: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Recomendada + alternativas de una cotización del motor, con su subtotal y precio c/u."""

    primary = _map(quote.get("primary"))
    rows = [
        {
            "index": 0,
            "sku": str(primary.get("sku") or ""),
            "name": str(primary.get("name") or primary.get("sku") or "Producto"),
            "subtotal": _presentation(quote).get("subtotal"),
            "unit_price": _presentation(quote).get("unit_price"),
            "units": _presentation(quote).get("units_requested") or primary.get("units_requested"),
            "material": primary.get("material"),
            "capacity_oz": primary.get("capacity_oz"),
            "currency": _currency(quote),
            "note": "",
            "price_city": primary.get("price_city"),
            "price_origin": primary.get("price_origin"),
            "price_kind": primary.get("price_kind"),
        }
    ]
    for position, alternative in enumerate(quote.get("alternatives") or [], start=1):
        if not isinstance(alternative, Mapping):
            continue
        presentation = _map(alternative.get("presentation"))
        rows.append(
            {
                "index": position,
                "sku": str(alternative.get("sku") or ""),
                "name": str(alternative.get("name") or alternative.get("sku") or "Producto"),
                "subtotal": presentation.get("subtotal"),
                "unit_price": presentation.get("unit_price"),
                "units": presentation.get("units_requested") or alternative.get("units_requested"),
                "material": alternative.get("material"),
                "capacity_oz": alternative.get("capacity_oz"),
                "currency": str(alternative.get("currency") or _currency(quote)),
                "note": str(alternative.get("difference") or ""),
                "price_city": alternative.get("price_city"),
                "price_origin": alternative.get("price_origin"),
                "price_kind": alternative.get("price_kind"),
            }
        )
    return rows


REFERENCE_PRICE: Final = "Precio de referencia del catálogo; se confirma con un distribuidor."


def _where(row: Mapping[str, Any]) -> str:
    """Rótulo de la alternativa: precio de otra ciudad o de referencia (no publicado)."""

    if row.get("price_origin"):
        return f" (precio de {row['price_city']})"
    if row.get("price_kind") == "convertido":
        return " (precio de referencia)"
    return ""


def _option_detail(row: Mapping[str, Any]) -> str:
    detail = []
    if row.get("material"):
        detail.append(str(row["material"]))
    capacity = _decimal(row.get("capacity_oz"))
    if capacity:
        detail.append(f"{capacity.normalize():f} oz")
    return ", ".join(detail)


def options(quote: Mapping[str, Any], *, reason: str = "") -> Message:
    """T2: 🔎 una recomendada y hasta cuatro alternativas, con el total para la cantidad."""

    rows = option_rows(quote)
    best = rows[0]
    units = best.get("units")
    city = _city(quote)
    head = "🔎 **Opciones"
    if units:
        head += f" · {format_units(units)} und"
    if city:
        head += f" · {city}"
    head += "**"
    money = format_money(best.get("subtotal"), str(best["currency"])) or "precio por confirmar"
    lines = [head, "", f"⭐ **Recomendada: {best['name']}** · {money}"]
    detail = reason or _option_detail(best)
    if detail:
        lines.append(f"   {detail}")
    if best.get("price_origin"):
        # Precio publicado en otra ciudad: se dice quién lo publica y dónde.
        lines.append(f"   📍 {best['price_origin']}")
    elif best.get("price_kind") == "convertido":
        # Precio del catálogo convertido, no publicado por un vendedor (prueba de cotización
        # del 2026-11-27: los cubiertos salían sin decirlo).
        lines.append(f"   📍 {REFERENCE_PRICE}")
    if len(rows) > 1:
        lines += ["", "**Alternativas**"]
        for row in rows[1:]:
            price = format_money(row.get("subtotal"), str(row["currency"])) or "sin precio"
            where = _where(row)
            lines.append(f"{row['index']}. {row['name']} · {price}{where}")
        lines += ["", "¿Cuál te cotizo? Di **esa** o un número."]
        question = "¿Cuál te cotizo?"
    else:
        lines += ["", "¿Te la agrego a la cotización? Di **esa** o **sí**."]
        question = "¿Te la agrego a la cotización?"
    said_money = _spoken_money(best.get("subtotal"), str(best["currency"]))
    spoken = f"Te recomiendo {_say(str(best['name']))}: {said_money}"
    if units:
        spoken += f" por {format_units(units)} unidades"
    spoken += "."
    if best.get("price_origin") and best.get("price_city"):
        spoken += f" Es el precio publicado en {_say(str(best['price_city']))}."
    elif best.get("price_kind") == "convertido":
        spoken += " Es un precio de referencia del catálogo."
    if len(rows) > 1:
        count = len(rows) - 1
        spoken += f" Te dejo {count} alternativa{'s' if count > 1 else ''} por escrito."
    photos = (str(best["sku"]),) if best.get("sku") else ()
    return Message("\n".join(lines), f"{spoken} {question}", photos)


def out_of_range(asked: int, alternatives: int) -> Message:
    """T10: la persona nombró una alternativa que no existe."""

    if alternatives <= 0:
        text = "Solo tengo la recomendada. Di **esa** para cotizarla."
    else:
        text = (
            f"Tengo {alternatives} alternativa{'s' if alternatives > 1 else ''}. Di **esa** para "
            f"la recomendada o un número del **1 al {alternatives}**."
        )
    return Message(f"No tengo la opción {asked}. {text}", f"No tengo la opción {asked}. {text}")


# --- Referencias de un nombre ambiguo ----------------------------------------------------


def which_reference(choices: Sequence[Mapping[str, Any]], *, city: str | None = None) -> Message:
    """🔎 Las referencias que coinciden con el nombre, numeradas, y «¿Cuál te cotizo?».

    Sale cuando la respuesta a la lista no eligió ninguna («sí», «esa», «cualquiera»): el
    flujo no elige por la persona. Con ``city`` (la persona respondió solo la ciudad), abre
    diciendo que cotizará allí.
    """

    lines = []
    if city:
        lines += [f"📍 Listo, la cotizo en **{city}**.", ""]
    lines += ["🔎 **Varias referencias coinciden con ese nombre**", ""]
    for index, item in enumerate(choices, start=1):
        lines.append(f"{index}. {item.get('name') or item.get('sku')} (SKU {item.get('sku')})")
    lines += ["", "¿Cuál te cotizo? Di el **número**, el nombre o el SKU."]
    count = len(choices)
    spoken = (
        f"Hay {count} referencias que coinciden; te las dejo por escrito. ¿Cuál te cotizo? "
        "Dime el número."
    )
    if city:
        spoken = f"Listo, en {_say(city)}. {spoken}"
    return Message("\n".join(lines), spoken)


def reference_out_of_range(asked: int, count: int) -> Message:
    """La persona nombró una posición que no está en la lista de referencias."""

    text = (
        f"No tengo la opción {asked}: son {count} referencias. Di un número del "
        f"**1 al {count}**, el nombre o el SKU."
    )
    return Message(text, f"No tengo la opción {asked}. Dime un número del 1 al {count}.")


# --- T3: comparativo de cantidades ------------------------------------------------------


def comparison(quotes: Sequence[Mapping[str, Any]]) -> Message:
    """T3: 📊 el mismo producto en varias cantidades, con total y precio por unidad."""

    first = quotes[0]
    head = f"📊 **{line_label(first)}"
    if _city(first):
        head += f" · {_city(first)}"
    head += "**"
    lines = [head]
    # Precio publicado en otra ciudad: quién lo publica y dónde, como en las opciones.
    origins = dict.fromkeys(
        str(_map(quote.get("primary")).get("price_origin") or "") for quote in quotes
    )
    lines += [f"📍 {origin}" for origin in origins if origin]
    lines.append("")
    said: list[str] = []
    prices: list[Decimal] = []
    for quote in quotes:
        units = units_of(quote)
        money = format_money(line_subtotal(quote), _currency(quote)) or "sin precio"
        # Si las cajas completas traen más de lo pedido, se dice cuántas unidades se compran:
        # «500 salen en 166.644» callaba que son 3 cajas de 200 (600 unidades).
        bought = _decimal(_presentation(quote).get("units_bought"))
        extra = bought if bought and bought > units else None
        row = f"• **{format_units(units)}** → {_packs(quote)}"
        if extra is not None:
            row += f" ({format_units(extra)} und.)"
        row += f" · {money}"
        unit = _unit_price(quote)
        if unit:
            row += f" ({unit} c/u)"
        lines.append(row)
        price = _decimal(_presentation(quote).get("unit_price"))
        if price:
            prices.append(price)
        phrase = f"{format_units(units)} salen en {_spoken_money(line_subtotal(quote))}"
        if extra is not None:
            phrase += f", porque se compran {format_units(extra)} unidades"
        said.append(phrase)
    if len(prices) == len(quotes) and prices and max(prices) > 0:
        saving = (max(prices) - min(prices)) / max(prices) * 100
        if saving >= 5:
            lines += ["", f"La cantidad mayor sale {saving:.0f} % más barata por unidad."]
    lines += ["", "¿Con cuál cantidad la armo?"]
    spoken = "; ".join(said) + f".{_spoken_price_city(quotes)} ¿Con cuál cantidad la armo?"
    return Message("\n".join(lines), spoken[0].upper() + spoken[1:])


# --- T4: línea agregada y carrito -------------------------------------------------------


def _cart_lines(lines: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = []
    for index, line in enumerate(lines, start=1):
        money = format_money(line_subtotal(line), _currency(line)) or "precio por confirmar"
        units = format_units(units_of(line))
        row = f"{index}. {line_label(line)} × {units} · {money}"  # noqa: RUF001
        price_city = _remote_city(_map(line.get("primary")))
        if price_city:
            row += f" (precio de {price_city})"
        rows.append(row)
    return rows


def _partial(draft: QuoteDraft) -> str:
    summary = totals(draft.lines)
    money = format_money(summary.products, summary.currency) or "$0"
    text = f"**Total parcial: {money}** ({summary.tax})"
    if summary.unpriced:
        text += f"\nSin precio, fuera del total: {', '.join(summary.unpriced)}"
    return text


def added(
    draft: QuoteDraft,
    new_lines: Sequence[Mapping[str, Any]],
    *,
    updated: bool = False,
    several_note: bool = False,
) -> Message:
    """T4: ✅ lo agregado y, con dos o más productos, el carrito con el total parcial."""

    summary = totals(draft.lines)
    if len(new_lines) == 1:
        line = new_lines[0]
        verb = "🔄 Actualicé" if updated else "✅ Agregué"
        name = f"**{format_units(units_of(line))} {line_label(line)}**"
        money = format_money(line_subtotal(line), _currency(line)) or "precio por confirmar"
        said_verb = "Actualicé" if updated else "Agregué"
        spoken = f"{said_verb} {format_units(units_of(line))} {_say(line_label(line))}"
    else:
        verb, name, money = "✅ Agregué", f"**{len(new_lines)} productos**", ""
        spoken = f"Agregué {len(new_lines)} productos"
    if len(draft.lines) == 1 and len(new_lines) == 1:
        line = new_lines[0]
        detail = [part for part in (_packs(line), f"**{money}**") if part]
        unit = _unit_price(line)
        text_lines = [f"{verb} {name}", " · ".join(detail) + (f" ({unit} c/u)" if unit else "")]
        place = " · ".join(part for part in (_city(line), _seller_label(line)) if part)
        if place:
            text_lines.append(f"📍 {place}")
        text_lines += ["", "¿Algo más para tu cotización?"]
        said = _spoken_money(line_subtotal(line), _currency(line))
        spoken += f": {said}.{_spoken_price_city(new_lines)} ¿Algo más para tu cotización?"
        return Message("\n".join(text_lines), spoken, _skus(new_lines))
    first = f"{verb} {name}" + (f" · {money}" if money else "")
    text_lines = [first]
    if several_note:
        text_lines.append(
            "Usé la recomendada de cada producto; si quieres ver alternativas, dímelo."
        )
    text_lines += ["", f"🛒 **Tu cotización · {_count_label(len(draft.lines))}**"]
    text_lines += _cart_lines(draft.lines)
    text_lines += [_partial(draft), "", "¿Algo más?"]
    spoken += (
        f".{_spoken_price_city(new_lines)} Llevas {_count_label(len(draft.lines))} y el total "
        f"parcial es de {_spoken_money(summary.products, summary.currency)}. ¿Algo más?"
    )
    return Message("\n".join(text_lines), spoken, _skus(new_lines))


def _skus(lines: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """SKU de las líneas, sin repetir y en su orden."""

    found = (str(_map(line.get("primary")).get("sku") or "") for line in lines)
    return tuple(dict.fromkeys(sku for sku in found if sku))


def resume(draft: QuoteDraft) -> Message:
    """↩️ el carrito guardado y la pregunta «¿Algo más?»."""

    summary = totals(draft.lines)
    lines = [f"↩️ **Tu cotización · {_count_label(len(draft.lines))}**"]
    lines += _cart_lines(draft.lines)
    lines += [_partial(draft), "", "¿Algo más o la cerramos? Di **eso es todo** para cerrarla."]
    spoken = (
        f"Retomamos tu cotización: {_count_label(len(draft.lines))}, total parcial de "
        f"{_spoken_money(summary.products, summary.currency)}. ¿Algo más o la cerramos?"
    )
    return Message("\n".join(lines), spoken)


def cart_view(draft: QuoteDraft) -> Message:
    """🛒 el carrito a pedido de la persona («¿qué tengo en la cotización?»), sin cambiarlo."""

    if not draft.lines:
        return empty_cart()
    summary = totals(draft.lines)
    lines = [f"🛒 **Tu cotización · {_count_label(len(draft.lines))}**"]
    lines += _cart_lines(draft.lines)
    lines += [_partial(draft)]
    spoken = (
        f"Llevas {_count_label(len(draft.lines))}, total parcial de "
        f"{_spoken_money(summary.products, summary.currency)}."
    )
    pending = reminder(draft)
    if pending is not None and draft.awaiting not in (None, "algo_mas"):
        lines += ["", pending.text]
        spoken += f" {pending.spoken}"
    else:
        lines += ["", "¿Algo más o la cerramos? Di **eso es todo** para cerrarla."]
        spoken += " ¿Algo más o la cerramos?"
    return Message("\n".join(lines), spoken)


def removed(draft: QuoteDraft, label: str) -> Message:
    if not draft.lines:
        text = f"Listo, quité **{label}**. Tu cotización quedó vacía. ¿Qué quieres cotizar?"
        return Message(
            text, f"Listo, quité {_say(label)}. Tu cotización quedó vacía. ¿Qué quieres cotizar?"
        )
    summary = totals(draft.lines)
    lines = [
        f"Listo, quité **{label}**.",
        "",
        f"🛒 **Tu cotización · {_count_label(len(draft.lines))}**",
    ]
    lines += _cart_lines(draft.lines)
    lines += [_partial(draft), "", "¿Algo más?"]
    spoken = (
        f"Listo, quité {_say(label)}. Llevas {_count_label(len(draft.lines))} y el total parcial "
        f"es de {_spoken_money(summary.products, summary.currency)}. ¿Algo más?"
    )
    return Message("\n".join(lines), spoken)


def edit_cart(draft: QuoteDraft, *, missing: int | None = None) -> Message:
    """🛒 el carrito numerado y cómo cambiarlo: «Cambiar algo» o «no» al resumen (T5).

    ``missing`` es el número de una línea que no existe («cambia la 2» con un producto).
    """

    summary = totals(draft.lines)
    last = len(draft.lines)
    lines = []
    lead = ""
    if missing:
        lead = f"Tu cotización no tiene un producto {missing}: tiene {_count_label(last)}."
        lines += [lead, ""]
    lines.append(f"🛒 **Tu cotización · {_count_label(last)}**")
    lines += _cart_lines(draft.lines)
    lines += [
        _partial(draft),
        "",
        f"Puedes quitar un producto (**quita la {last}**), cambiar una cantidad o agregar "
        "otro. ¿Qué cambio? Si ya está bien, di **eso es todo**.",
    ]
    spoken = (
        f"Llevas {_count_label(last)} y el total parcial es de "
        f"{_spoken_money(summary.products, summary.currency)}. "
        "¿Qué cambio? Puedo quitar un producto, cambiar una cantidad o agregar otro."
    )
    if lead:
        spoken = f"Tu cotización no tiene un producto {missing}. {spoken}"
    return Message("\n".join(lines), spoken)


def line_cap() -> Message:
    text = (
        "Tu cotización ya tiene 10 productos, el máximo. Di **eso es todo** para cerrarla o "
        "quita uno para agregar otro."
    )
    return Message(text, text.replace("**", ""))


def ask_product() -> Message:
    return Message(
        "¿Qué producto agregamos? Ej.: **500 vasos de 12 oz**",
        "¿Qué producto agregamos?",
    )


def empty_cart() -> Message:
    text = "Todavía no tienes productos en la cotización. ¿Qué quieres cotizar?"
    return Message(text, text)


# --- T5: resumen --------------------------------------------------------------------------


def _shipping_text(draft: QuoteDraft) -> str:
    summary = totals(draft.lines)
    if not summary.shipping_requested:
        return "Envío no incluido"
    if summary.shipping is None:
        return "Envío por confirmar"
    if summary.shipping == 0:
        return "Envío gratis"
    return f"Envío {format_money(summary.shipping, summary.currency)}"


def summary(draft: QuoteDraft, *, note: str = "") -> Message:
    """T5: 🧾 el resumen completo y «¿La confirmo y te envío el PDF?»."""

    result = totals(draft.lines)
    sellers = {_seller_label(line) for line in draft.lines if _seller_label(line)}
    lines = []
    if note:
        lines += [note, ""]
    lines.append("🧾 **Resumen de tu cotización**")
    place = draft.city or ""
    if len(sellers) == 1:
        place = " · ".join(part for part in (place, next(iter(sellers))) if part)
    if place:
        lines.append(f"📍 {place}")
    lines.append("")
    for index, line in enumerate(draft.lines, start=1):
        money = format_money(line_subtotal(line), _currency(line)) or "precio por confirmar"
        row = f"{index}. {line_label(line)} · {format_units(units_of(line))} und"
        packs = _packs(line)
        if packs:
            row += f" ({packs})"
        row += f" · {money}"
        if len(sellers) > 1 and _seller_label(line):
            row += f" · {_seller_label(line)}"
        lines.append(row)
    products = format_money(result.products, result.currency) or "$0"
    lines += ["", f"Productos {products} · {result.tax} · {_shipping_text(draft)}"]
    remote = _remote_shipping(draft)
    if remote:
        lines.append(remote)
    if result.total is not None:
        total = format_money(result.total, result.currency)
        lines.append(f"💰 **Total: {total} {result.currency}**")
        said_total = _spoken_money(result.total, result.currency)
    else:
        lines.append(f"💰 **Total productos: {products} {result.currency}** (envío por confirmar)")
        said_total = f"{_spoken_money(result.products, result.currency)} más el envío"
    dates = _dates(draft)
    if dates:
        lines.append(f"_{dates}_")
    if result.unpriced:
        lines.append(f"Sin precio, fuera del total: {', '.join(result.unpriced)}")
    lines += ["", "¿La confirmo y te envío el PDF? Responde **sí** o dime qué cambio."]
    city = f" para {draft.city}" if draft.city else ""
    spoken = (
        f"{'Es' if len(draft.lines) == 1 else 'Son'} {_count_label(len(draft.lines))}{city}, "
        f"total {said_total}.{_spoken_price_city(draft.lines)} "
        "¿La confirmo y te mando el PDF?"
    )
    if note:
        spoken = f"{note.replace('**', '')} {spoken}"
    return Message("\n".join(lines), spoken)


def _dates(draft: QuoteDraft) -> str:
    captured = []
    valid = []
    for line in draft.lines:
        primary = _map(line.get("primary"))
        source = _map(primary.get("source")) or _map(_map(line.get("commercial")).get("source"))
        raw = source.get("captured_at") or primary.get("captured_at")
        if raw:
            captured.append(str(raw)[:10])
        until = _map(line.get("commercial")).get("valid_until")
        if until:
            valid.append(str(until)[:10])
    parts = []
    if captured:
        parts.append(f"Precios del {format_date(min(captured), year=False)}")
    if valid:
        parts.append(f"vigencia del vendedor hasta {format_date(min(valid), year=False)}")
    return " · ".join(parts)


# --- T6: registrada -----------------------------------------------------------------------


def registered(draft: QuoteDraft, folio: str, valid_until: date | str | None) -> Message:
    """T6: ✅ folio, total y el aviso del PDF."""

    from carvajal.speech import speak_folio_tail

    result = totals(draft.lines)
    total = format_money(
        result.total if result.total is not None else result.products, result.currency
    )
    until = format_date(valid_until, year=False)
    lines = [f"✅ **Cotización #{folio} registrada**"]
    detail = f"{_count_label(len(draft.lines))} · Total **{total}**"
    if result.total is None:
        detail += " (envío por confirmar)"
    if until:
        detail += f" · válida hasta {until}"
    lines += [detail, PDF_PROMISE]
    spoken = (
        f"Listo, quedó registrada tu cotización terminada en {speak_folio_tail(folio)}. "
        "Te envío el PDF."
    )
    return Message("\n".join(lines), spoken)


_REGISTERED_FOLIO: Final = re.compile(r"^✅ \*\*Cotización #([^\s*]+) registrada\*\*$", re.M)


def pdf_offer(text: str, link: Callable[[str], str | None]) -> str:
    """T6 en un canal que enlaza el PDF (la web): la promesa pasa a ser el enlace del folio.

    ``link`` da la URL del PDF de un folio o ``None`` si no se puede ofrecer; sin URL la línea
    se quita para no prometer un PDF que no llega. Un texto que no es T6 vuelve igual. Solo
    cambia el texto escrito: la versión hablada (``spoken``) no lee enlaces.
    """

    found = _REGISTERED_FOLIO.search(text)
    promise = f"\n{PDF_PROMISE}"
    if found is None or promise not in text:
        return text
    url = link(found.group(1))
    offer = f"\n📄 [Descarga el PDF de tu cotización]({url})" if url else ""
    return text.replace(promise, offer, 1)


def already_registered(folio: str) -> Message:
    text = (
        f"Esa cotización ya quedó registrada con el folio **#{folio}**. ¿Quieres cotizar algo más?"
    )
    return Message(text, "Esa cotización ya quedó registrada. ¿Quieres cotizar algo más?")


# --- Spec 056: pedido en el CRM ---------------------------------------------------------

EXAMPLE_EMAIL: Final = "ana@example.com"
"""El correo del ejemplo fijo de :func:`ask_buyer`, de un dominio reservado (RFC 2606).

No es de nadie: se guarda tal cual y, si alguien lo copia como su correo, el flujo lo trata
como un dato que falta (nunca llega al CRM ni recibe la confirmación)."""

ORDER_OFFER: Final = (
    "🛒 ¿Quieres hacer el pedido? Te pido tu nombre y tu correo y te llega la confirmación."
)


def order_offer(message: Message) -> Message:
    """El folio registrado con la oferta de hacer el pedido al final."""

    return Message(
        f"{message.text}\n{ORDER_OFFER}",
        f"{message.spoken} ¿Quieres hacer el pedido?",
        message.photos,
    )


def ask_buyer() -> Message:
    text = (
        "¡Perfecto! Para el pedido envíame tu **nombre** y tu **correo** "
        f"(por ejemplo: «Ana Pérez, {EXAMPLE_EMAIL}»). A ese correo te llega la confirmación."
    )
    return Message(text, "¡Perfecto! Para el pedido dime tu nombre y tu correo.")


def buyer_missing(name: str | None, email: str | None) -> Message:
    if name and not email:
        text = f"Gracias, {name}. ¿A qué **correo** te envío la confirmación del pedido?"
        return Message(text, f"Gracias, {name}. ¿A qué correo te envío la confirmación?")
    if email and not name:
        text = "¿A **nombre** de quién hago el pedido?"
        return Message(text, "¿A nombre de quién hago el pedido?")
    text = (
        f"No alcancé a leer tu nombre y tu correo. Envíamelos así: «Ana Pérez, {EXAMPLE_EMAIL}». "
        "Si prefieres no hacer el pedido ahora, escribe «ahora no»."
    )
    return Message(text, "No alcancé a entender tu nombre y tu correo. ¿Me los repites?")


def confirm_buyer(name: str, email: str) -> Message:
    """Los datos leídos, antes de crear el pedido: la persona los ve y los corrige."""

    text = (
        "📋 Voy a hacer el pedido con estos datos:\n"
        f"- Nombre: **{name}**\n"
        f"- Correo: {email}\n"
        "¿Lo confirmo? Si algo está mal, escríbeme el dato correcto."
    )
    from carvajal.quotation.order import spoken_address

    spoken = (
        f"Voy a hacer el pedido a nombre de {name}, con el correo {spoken_address(email)}. "
        "¿Lo confirmo?"
    )
    return Message(text, spoken)


def which_correction() -> Message:
    text = (
        "¿Qué dato corrijo? Escríbeme el **nombre** o el **correo** correcto, o **ahora no** "
        "para dejar el pedido."
    )
    return Message(text, "¿Qué dato corrijo? Dime el nombre o el correo correcto.")


BOGOTA: Final = ZoneInfo("America/Bogota")
RECEIPT_PROMISE: Final = "📄 Te envío el recibo del pedido en PDF."
"""Última línea del pedido confirmado: cada canal la cambia por el PDF (:func:`receipt_offer`)."""

_ORDER_PLACED: Final = re.compile(
    r"^🧾 \*\*Pedido (\S+) confirmado\*\* · cotización #([^\s*]+)$", re.M
)


def order_placed(
    order: str, email: str, folio: str | None, draft: QuoteDraft | None = None
) -> Message:
    """Recibo del pedido en el chat: número, fecha, líneas, total y el PDF de Odoo."""

    reference = f" · cotización #{folio}" if folio else ""
    lines = [f"🧾 **Pedido {order} confirmado**{reference}"]
    today = format_date(datetime.now(BOGOTA).date())
    if today:
        lines.append(f"Fecha: {today}")
    if draft is not None and draft.lines:
        lines += _cart_lines(draft.lines)
        result = totals(draft.lines)
        if not result.priced:
            lines.append("Total por confirmar con el vendedor")
        else:
            total = result.total if result.total is not None else result.products
            money = format_money(total, result.currency)
            pending = " (envío por confirmar)" if result.total is None else ""
            if result.unpriced:
                pending += " sin " + ", ".join(result.unpriced) + " (precio por confirmar)"
            lines.append(f"Total **{money}**{pending} · {result.tax}")
    lines.append(
        f"La confirmación del pedido llega a {email}. Un asesor te contacta para coordinar "
        "el pago y la entrega."
    )
    if folio:
        lines.append(RECEIPT_PROMISE)
    spoken = (
        "Listo, tu pedido quedó confirmado. La confirmación llega a tu correo, te envío el "
        "recibo por escrito y un asesor te contacta para el pago y la entrega."
    )
    return Message("\n".join(lines), spoken)


def receipt_offer(text: str, link: Callable[[str], str | None]) -> str:
    """El pedido confirmado en un canal que enlaza el PDF (la web): la promesa → el enlace.

    ``link`` da la URL del recibo del folio o ``None``; sin URL la línea se quita para no
    prometer un PDF que no llega. Un texto que no es un pedido confirmado vuelve igual.
    """

    found = _ORDER_PLACED.search(text)
    promise = f"\n{RECEIPT_PROMISE}"
    if found is None or promise not in text:
        return text
    url = link(found.group(2))
    offer = f"\n📄 [Descarga el recibo de tu pedido {found.group(1)}]({url})" if url else ""
    return text.replace(promise, offer, 1)


def order_expired(labels: Sequence[str]) -> Message:
    """Los precios de la cotización vencieron antes del pedido: hay que cotizar de nuevo."""

    text = (
        "Los precios de " + ", ".join(labels) + " ya vencieron, así que no puedo confirmar el "
        "pedido con esa cotización. Pídeme cotizarlos de nuevo y hacemos el pedido."
    )
    return Message(
        text, "Los precios de la cotización vencieron. Cotizamos de nuevo y hacemos el pedido."
    )


# Una pregunta a mitad del pedido sin dato en las fuentes (medios de pago, plazos): en vez de
# la abstención genérica, que el flujo quitaba y dejaba la pregunta sin respuesta.
ORDER_ASIDE: Final = (
    "Ese dato no lo tengo verificado: los medios de pago y la entrega los coordina un asesor "
    "cuando se crea el pedido."
)


def order_reminder(awaiting: str | None) -> Message | None:
    """↩️ tras una pregunta a mitad del pedido: la pregunta pendiente, una sola vez."""

    if awaiting == "pedido":
        text = "↩️ ¿Seguimos con el pedido de tu cotización? Responde **sí** o **ahora no**."
        return Message(text, "¿Seguimos con el pedido de tu cotización?")
    if awaiting == "datos_pedido":
        text = "↩️ Para el pedido me faltan tu **nombre** y tu **correo**."
        return Message(text, "Para el pedido me faltan tu nombre y tu correo.")
    if awaiting == "confirmar_pedido":
        text = "↩️ ¿Confirmo el pedido con los datos que me diste? Responde **sí** o corrígelos."
        return Message(text, "¿Confirmo el pedido con los datos que me diste?")
    return None


def order_failed() -> Message:
    text = (
        "No pude crear el pedido en este momento. Tu cotización sigue registrada: responde "
        "**sí** en unos minutos y lo intento de nuevo, o **ahora no** para dejarlo."
    )
    return Message(
        text, "No pude crear el pedido ahora. Responde sí en unos minutos y lo reintento."
    )


def already_ordered(order: str) -> Message:
    text = f"Ese pedido ya quedó creado con el número **{order}**. ¿Quieres cotizar algo más?"
    return Message(text, "Ese pedido ya quedó creado. ¿Quieres cotizar algo más?")


def order_cancel_request(order: str) -> Message:
    """Tras el pedido creado, «cancela el pedido»: no se anula desde el chat."""

    text = (
        f"Tu pedido **{order}** ya quedó confirmado y no se cancela desde el chat. Para "
        "cambiarlo o cancelarlo, díselo al asesor que te contacta para el pago y la entrega: "
        "él lo gestiona."
    )
    spoken = (
        "Tu pedido ya quedó confirmado y no se cancela desde el chat. Para cambiarlo o "
        "cancelarlo, díselo al asesor que te contacta: él lo gestiona."
    )
    return Message(text, spoken)


def lids_not_added(labels: Sequence[str]) -> Message:
    """Las tapas que el flujo no agregó porque no le sirven a los vasos de la cotización."""

    names = ", ".join(f"**{label}**" for label in labels)
    said = ", ".join(_say(label) for label in labels)
    many = len(labels) > 1
    serve = "no les sirven" if many else "no le sirve"
    text = (
        f"No agregué {names}: {serve} a los vasos de tu cotización. Si "
        f"{'las' if many else 'la'} quieres igual, pídemel{'as' if many else 'a'} por su nombre."
    )
    return Message(text, f"No agregué {said}: {serve} a tus vasos.")


# --- productos pendientes (a la espera de un dato) -----------------------------------------

# (plural, artículo, femenino) por familia del catálogo.
_FAMILY_NOUN: Final = {
    "vaso": ("vasos", "los", False),
    "tapa": ("tapas", "las", True),
    "contenedor": ("contenedores", "los", False),
    "charola": ("bandejas", "las", True),
    "cubierto": ("cubiertos", "los", False),
    "envase": ("envases", "los", False),
    "plato": ("platos", "los", False),
}


def _noun(family: str) -> tuple[str, str, bool]:
    return _FAMILY_NOUN.get(family, (family or "productos", "los", False))


def pending_label(item: Mapping[str, Any]) -> str:
    """«500 vasos de 9 oz» de un producto pendiente (sin artículo)."""

    noun, _article, _feminine = _noun(str(item.get("family") or ""))
    quantity = item.get("quantity")
    label = f"{format_units(quantity)} {noun}" if isinstance(quantity, int) and quantity else noun
    capacity = item.get("capacity_oz")
    if isinstance(capacity, (int, float)) and capacity:
        label += f" de {capacity:g} oz"
    return label


def _pending_ask(item: Mapping[str, Any], *, bold: bool = True) -> str:
    """Qué falta de un producto pendiente, como subordinada: «si los 500 vasos … son …»."""

    family = str(item.get("family") or "")
    _noun_text, article, feminine = _noun(family)
    label = str(item.get("label") or pending_label(item))
    name = f"{article} **{label}**" if bold else f"{article} {label}"
    missing = [str(part) for part in item.get("missing") or []]
    if "temperatura" in missing:
        if family == "vaso":
            return f"si {name} son para bebida caliente o fría"
        return f"si la comida de {name} va caliente o fría"
    if "cantidad" in missing:
        many = "cuántas" if feminine else "cuántos"
        return f"{many} unidades de {name} necesitas"
    if "ciudad" in missing:
        return f"en qué ciudad cotizo {name}"
    return f"un dato de {name}"


def _pending_list(items: Sequence[Mapping[str, Any]], *, bold: bool = True) -> str:
    asks = [_pending_ask(item, bold=bold) for item in items]
    if len(asks) == 1:
        return asks[0]
    return "; ".join(asks[:-1]) + f" y {asks[-1]}"


def _held_text(held: int) -> str:
    if not held:
        return ""
    return " Las tapas para esos vasos las agrego cuando los cotice."


def pending_note(items: Sequence[Mapping[str, Any]], *, held: int = 0) -> Message:
    """↩️ Lo que sigue esperando un dato de un turno anterior (no se pierde)."""

    text = f"↩️ También me falta saber {_pending_list(items)}.{_held_text(held)}"
    return Message(text, f"También me falta saber {_pending_list(items, bold=False)}.")


def pending_close(
    items: Sequence[Mapping[str, Any]], *, held: int = 0, again: bool = False
) -> Message:
    """«Eso es todo» con productos pendientes: se pregunta antes de cerrar sin ellos.

    ``again``: la persona respondió «sí» sin el dato; se le pide el dato directamente.
    """

    lead = "Perfecto. Dime" if again else "Antes de cerrar: me falta saber"
    said_lead = "Perfecto. Dime" if again else "Antes de cerrar, me falta saber"
    text = (
        f"{lead} {_pending_list(items)}.{_held_text(held)} "
        "Dímelo y lo agrego, o di **eso es todo** otra vez y la cierro sin eso."
    )
    spoken = (
        f"{said_lead} {_pending_list(items, bold=False)}. Dímelo y lo agrego, o di eso es "
        "todo otra vez y la cierro sin eso."
    )
    if again:
        text = text.replace(" Dímelo y lo agrego, o", " Con eso lo agrego; si prefieres,")
        spoken = spoken.replace(" Dímelo y lo agrego, o", " Con eso lo agrego; si prefieres,")
    return Message(text, spoken)


def no_lines_yet(
    number: int | None, items: Sequence[Mapping[str, Any]], *, held: int = 0
) -> Message:
    """Un cambio sin productos en el carrito y con productos esperando un dato.

    ``number`` es la línea nombrada («cambia la 2»); ``None`` si no se nombró una que se pueda
    decir (o se pidió cambiar algo del carrito vacío).
    """

    what = f"un producto {number}" if number else "productos que cambiar"
    text = (
        f"Tu cotización todavía no tiene {what}: me falta saber "
        f"{_pending_list(items)} para agregarlos.{_held_text(held)}"
    )
    spoken = (
        f"Tu cotización todavía no tiene {what}. Me falta saber {_pending_list(items, bold=False)}."
    )
    return Message(text, spoken)


def pending_dropped(items: Sequence[Mapping[str, Any]], *, held: int = 0) -> str:
    """Nota del resumen cuando se cierra sin los pendientes."""

    labels = ", ".join(f"**{item.get('label') or pending_label(item)}**" for item in items)
    lids = " ni las tapas que esperaban esos vasos" if held else ""
    return f"Cerré sin {labels}{lids}: les faltaba un dato."


def order_receipt(order: str, folio: str) -> Message:
    """Tras el pedido creado, «mándame el recibo»: el recibo otra vez, sin el modelo.

    Repite la cabecera del pedido confirmado y la promesa del PDF: cada canal la cambia por
    el enlace o el adjunto (:func:`receipt_offer`), igual que en la confirmación.
    """

    text = f"🧾 **Pedido {order} confirmado** · cotización #{folio}\n{RECEIPT_PROMISE}"
    return Message(text, "Te envío por escrito el recibo de tu pedido.")


NO_ORDER_RECEIPT: Final = (
    "No encuentro un pedido confirmado en esta conversación. Cuando confirmes uno, aquí te "
    "dejo su recibo."
)


def no_order_receipt() -> Message:
    """«Mándame el recibo» sin pedido en el hilo: respuesta fija (el modelo inventaría uno)."""

    return Message(NO_ORDER_RECEIPT, "No encuentro un pedido confirmado en esta conversación.")


def order_status(order: str) -> Message:
    """Tras el pedido creado, «¿cómo va mi pedido?»: el estado no se consulta desde el chat."""

    text = (
        f"Tu pedido **{order}** quedó confirmado. Un asesor te contacta para coordinar el pago "
        "y la entrega, y él te da el estado del despacho."
    )
    spoken = (
        "Tu pedido quedó confirmado. Un asesor te contacta para coordinar el pago y la "
        "entrega, y él te da el estado del despacho."
    )
    return Message(text, spoken)


def order_declined(folio: str | None) -> Message:
    reference = f" #{folio}" if folio else ""
    text = (
        f"Listo, tu cotización{reference} queda registrada sin pedido. Si cambias de opinión, "
        "escribe «hacer el pedido» en tu próximo mensaje. ¿Te ayudo con algo más?"
    )
    return Message(
        text, "Listo, tu cotización queda registrada sin pedido. ¿Te ayudo con algo más?"
    )


# --- T7-T9: cambio, cancelación y salida -----------------------------------------------


def switch_question(label: str) -> Message:
    """T7: ¿cancelo lo que llevábamos o lo conservo?"""

    text = (
        f"Llevábamos **{label}**. ¿Qué hago con eso?\n\n"
        f"1. **Cancelar** {label}\n"
        "2. **Conservar** y agregar lo nuevo"
    )
    spoken = f"Llevábamos {_say(label)}. ¿Lo cancelo o lo conservo y agrego lo nuevo?"
    return Message(text, spoken)


def cancelled(label: str | None = None) -> Message:
    """T8: cancelación de un producto o de toda la cotización."""

    if label:
        text = f"Listo, quité **{label}**."
        return Message(text, f"Listo, quité {_say(label)}.")
    text = "Listo, cancelé la cotización. ¿Qué te gustaría cotizar?"
    return Message(text, text)


def leaving(has_lines: bool) -> Message:
    """T9: despedida sin compra, sin insistir."""

    if has_lines:
        text = (
            "Con gusto. Guardé lo que armamos por 7 días: escríbeme **retomar** cuando quieras 📦"
        )
        spoken = "Con gusto. Guardé lo que armamos por siete días; dime retomar cuando quieras."
        return Message(text, spoken)
    return Message(
        "Con gusto. Cuando quieras, escríbeme y seguimos 📦",
        "Con gusto. Cuando quieras, escríbeme y seguimos.",
    )


def not_now() -> Message:
    """«Ahora no» a una propuesta del agente: sin insistir y sin abstenerse."""

    return Message(
        "Listo, lo dejamos así. ¿Te ayudo con algo más?",
        "Listo, lo dejamos así. ¿Te ayudo con algo más?",
    )


def joke() -> Message:
    """Rechazo con insulto («muy caro, ladrones»): la respuesta pedida por el owner."""

    return Message(JOKE_REJECTION, JOKE_REJECTION.replace("😂", "").strip())


def cheaper_offer(row: Mapping[str, Any] | None) -> Message:
    """«Está caro»: una sola vez, la opción más económica que ya se calculó."""

    if row is None:
        text = (
            "Te entiendo. Son los precios que publican los vendedores. Si quieres, busco una "
            "opción más sencilla: ¿para qué lo necesitas?"
        )
        return Message(text, text)
    money = format_money(row.get("subtotal"), str(row.get("currency") or "COP"))
    units = row.get("units")
    detail = f" ({format_units(units)} und)" if units else ""
    # Precio publicado en otra ciudad: se dice dónde, como en las opciones.
    price_city = _remote_city(row)
    where = f", precio publicado en {price_city}" if price_city else ""
    text = (
        f"Te entiendo. La opción más económica que tengo es **{row['name']}** a **{money}**"
        f"{detail}{where}. ¿Te la cotizo? Responde **sí** o **no**."
    )
    spoken = (
        f"Te entiendo. La opción más económica es {_say(str(row['name']))}, a "
        f"{_spoken_money(row.get('subtotal'), str(row.get('currency') or 'COP'))}"
        f"{_say(where)}. ¿Te la cotizo?"
    )
    return Message(text, spoken)


# --- Recordatorio fuera del flujo -----------------------------------------------------------


def reminder(draft: QuoteDraft) -> Message | None:
    """↩️ una línea para volver a la cotización tras una pregunta de otro tema."""

    result = totals(draft.lines)
    money = format_money(result.products, result.currency)
    if draft.awaiting == "eleccion" and draft.options:
        name = line_label(draft.options[0])
        text = f"↩️ Sigo con las opciones de **{name}**: ¿cuál te cotizo? Di **esa** o un número."
        return Message(text, "Cuando quieras, seguimos con las opciones: ¿cuál te cotizo?")
    if draft.awaiting == "referencia" and draft.choices:
        text = "↩️ ¿Cuál de las referencias te cotizo? Di el **número**, el nombre o el SKU."
        return Message(text, "Cuando quieras, dime cuál de las referencias te cotizo.")
    if draft.awaiting == "cantidad" and draft.comparison:
        text = f"↩️ ¿Con cuál cantidad armo la cotización de **{line_label(draft.comparison[0])}**?"
        return Message(text, "Cuando quieras, dime con cuál cantidad armo la cotización.")
    if draft.awaiting == "confirmar" and draft.lines:
        text = (
            f"↩️ Tu cotización está lista para confirmar ({money}). ¿La confirmo? Responde **sí**."
        )
        return Message(text, "Tu cotización sigue lista para confirmar. ¿La confirmo?")
    if draft.awaiting == "cambio_producto" and draft.current:
        label = str(draft.current.get("label") or "lo anterior")
        text = f"↩️ ¿Cancelo **{label}** o lo conservo? Responde **1** o **2**."
        return Message(text, f"¿Cancelo {_say(label)} o lo conservo?")
    if draft.lines:
        text = (
            f"↩️ Seguimos con tu cotización ({_count_label(len(draft.lines))} · {money}). ¿Algo más?"
        )
        return Message(
            text, f"Seguimos con tu cotización de {_count_label(len(draft.lines))}. ¿Algo más?"
        )
    return None
