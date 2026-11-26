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

from carvajal.quotation.draft import (
    QuoteDraft,
    line_label,
    line_subtotal,
    totals,
    units_of,
)

__all__ = [
    "JOKE_REJECTION",
    "PDF_PROMISE",
    "Message",
    "added",
    "already_registered",
    "ask_product",
    "cancelled",
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
    "option_rows",
    "options",
    "out_of_range",
    "pdf_offer",
    "registered",
    "reminder",
    "removed",
    "resume",
    "summary",
    "switch_question",
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
            }
        )
    return rows


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
    if len(rows) > 1:
        lines += ["", "**Alternativas**"]
        for row in rows[1:]:
            price = format_money(row.get("subtotal"), str(row["currency"])) or "sin precio"
            lines.append(f"{row['index']}. {row['name']} · {price}")
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


# --- T3: comparativo de cantidades ------------------------------------------------------


def comparison(quotes: Sequence[Mapping[str, Any]]) -> Message:
    """T3: 📊 el mismo producto en varias cantidades, con total y precio por unidad."""

    first = quotes[0]
    head = f"📊 **{line_label(first)}"
    if _city(first):
        head += f" · {_city(first)}"
    head += "**"
    lines = [head, ""]
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
    spoken = "; ".join(said) + ". ¿Con cuál cantidad la armo?"
    return Message("\n".join(lines), spoken[0].upper() + spoken[1:])


# --- T4: línea agregada y carrito -------------------------------------------------------


def _cart_lines(lines: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = []
    for index, line in enumerate(lines, start=1):
        money = format_money(line_subtotal(line), _currency(line)) or "precio por confirmar"
        units = format_units(units_of(line))
        rows.append(f"{index}. {line_label(line)} × {units} · {money}")  # noqa: RUF001
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
        place = " · ".join(part for part in (_city(line), _seller(line)) if part)
        if place:
            text_lines.append(f"📍 {place}")
        text_lines += ["", "¿Algo más para tu cotización?"]
        said = _spoken_money(line_subtotal(line), _currency(line))
        spoken += f": {said}. ¿Algo más para tu cotización?"
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
        f". Llevas {_count_label(len(draft.lines))} y el total parcial es de "
        f"{_spoken_money(summary.products, summary.currency)}. ¿Algo más?"
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


def edit_cart(draft: QuoteDraft) -> Message:
    """🛒 el carrito numerado y cómo cambiarlo: «Cambiar algo» o «no» al resumen (T5)."""

    summary = totals(draft.lines)
    last = len(draft.lines)
    lines = [f"🛒 **Tu cotización · {_count_label(last)}**"]
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
    sellers = {_seller(line) for line in draft.lines if _seller(line)}
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
        if len(sellers) > 1 and _seller(line):
            row += f" · {_seller(line)}"
        lines.append(row)
    products = format_money(result.products, result.currency) or "$0"
    lines += ["", f"Productos {products} · {result.tax} · {_shipping_text(draft)}"]
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
        f"Son {_count_label(len(draft.lines))}{city}, total {said_total}. "
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
    text = (
        f"Te entiendo. La opción más económica que tengo es **{row['name']}** a **{money}**"
        f"{detail}. ¿Te la cotizo? Responde **sí** o **no**."
    )
    spoken = (
        f"Te entiendo. La opción más económica es {_say(str(row['name']))}, a "
        f"{_spoken_money(row.get('subtotal'), str(row.get('currency') or 'COP'))}. ¿Te la cotizo?"
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
