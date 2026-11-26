"""Conducción del flujo de cotización (spec 054): antes y después de cada turno.

El servicio del chat llama a :func:`before_turn` con el borrador de la conversación y el
mensaje de la persona. Si el mensaje responde a lo que el asistente preguntó («sí», «la 2»,
«eso es todo», «cancélalos», «con mil»…), el turno se atiende aquí sin el modelo
(:class:`Reply`). Si no, va al agente (:class:`Pass`), a veces con otro texto (el pedido que
quedó en espera mientras se preguntaba si cancelar el producto anterior).

Después del turno del agente, :func:`after_turn` mira las cotizaciones que calculó y decide:
opciones (una recomendada + hasta cuatro alternativas), comparativo de cantidades, línea
agregada o, si el turno fue de otro tema, un recordatorio de una línea para retomar.

Orden de decisión (determinista primero):

1. Reglas de ``carvajal.agent.followup`` contra ``draft.awaiting``.
2. Jev (Choice), solo si hay una pregunta pendiente, el mensaje es corto y ninguna regla
   decidió. Su clase se traduce a una frase canónica y se vuelve a pasar por las reglas; con
   confianza baja o Jev caído, el turno sigue al agente (*fail-open*).
3. El agente.

Las preguntas de otro tema (Carvajal, su historia o temas ajenos) no rompen el flujo: el
agente las responde como siempre y se agrega «↩️ Seguimos con tu cotización…» con la pregunta
pendiente, en los dos primeros turnos fuera del flujo. El borrador se conserva siete días.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Final, Protocol
from zoneinfo import ZoneInfo

import structlog

from carvajal.agent.commercial_intent import product_family
from carvajal.agent.followup import (
    Choice,
    ChoiceOutOfRange,
    is_cancel_all,
    is_confirmation,
    is_leaving,
    is_price_insult,
    is_resume,
    is_too_expensive,
    parse_quantities,
    plain,
    removes_whole_line,
    resolve_choice,
    resolve_more,
    resolve_quantity_choice,
    resolve_remove,
    resolve_switch,
    strip_assent,
    wants_cart,
    wants_change,
    wants_edit,
    wants_switch,
)
from carvajal.quotation import render
from carvajal.quotation.draft import (
    QuoteDraft,
    add_line,
    expired_lines,
    is_quote,
    line_family,
    line_label,
    remove_line,
    same_sku_line,
    units_of,
)
from carvajal.quotation.render import Message, option_rows

__all__ = [
    "JEV_MIN_CONFIDENCE",
    "OFF_FLOW_REMINDERS",
    "REPLY_CLASSES",
    "Outcome",
    "Pass",
    "Reply",
    "ReplyJudge",
    "after_turn",
    "before_turn",
    "requote",
]

logger = structlog.get_logger(__name__)

BOGOTA: Final = ZoneInfo("America/Bogota")
OFF_FLOW_REMINDERS: Final = 2
JEV_MIN_CONFIDENCE: Final = 0.7
JEV_MAX_WORDS: Final = 14
REPLY_CLASSES: Final = (
    "confirmar",
    "agregar",
    "cerrar",
    "cancelar",
    "conservar",
    "pausar",
    "rechazo_insulto",
    "otro",
)
# Frase canónica de cada clase de Jev: se vuelve a pasar por las reglas.
_CANONICAL: Final = {
    "confirmar": "sí",
    "cerrar": "eso es todo",
    "cancelar": "cancelar",
    "conservar": "conservar",
    "pausar": "lo voy a pensar",
    "rechazo_insulto": "muy caro, ladrones",
}
_FAMILY_LABEL: Final = {
    "vaso": "los vasos",
    "tapa": "las tapas",
    "contenedor": "los contenedores",
    "charola": "las bandejas",
    "cubierto": "los cubiertos",
    "envase": "los envases",
    "plato": "los platos",
}
_ALSO: Final = re.compile(r"\b(?:tambien|ademas|agrega\w*|sumale|anade\w*|y\s+unos?|y\s+unas?)\b")


class ReplyJudge(Protocol):
    """Clasificador de respuestas cortas (Jev Choice sobre :data:`REPLY_CLASSES`)."""

    def classify_reply(self, *, awaiting: str, reply: str) -> tuple[str, float]: ...


Requote = Callable[..., Mapping[str, Any] | None]


@dataclass(frozen=True)
class Reply:
    """Turno determinista: el mensaje y el borrador nuevo; ``register`` pide el folio."""

    message: Message
    draft: QuoteDraft
    route: str = "cotizacion"
    register: bool = False


@dataclass(frozen=True)
class Pass:
    """El turno va al agente, con ``prompt`` en lugar del texto de la persona si lo hay."""

    draft: QuoteDraft
    prompt: str | None = None


@dataclass(frozen=True)
class Outcome:
    """Lo que agrega el flujo al turno del agente: borrador y mensaje (o ``None``)."""

    draft: QuoteDraft
    message: Message | None = None
    flow: bool = False  # el turno cotizó (opciones, comparativo o líneas)


# --- recotizar sin el modelo ------------------------------------------------------------


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def requote(
    base: Mapping[str, Any],
    *,
    sku: str | None = None,
    quantity: int | None = None,
    today: date | None = None,
    dataset: Any = None,
) -> Mapping[str, Any] | None:
    """La misma solicitud de ``base`` con otra referencia o cantidad, con el motor.

    Sirve para elegir una alternativa («la 3»), cambiar la cantidad en la elección («esa,
    son 300») y recalcular una línea vencida. Devuelve ``None`` si el motor no cotiza.
    """

    from carvajal.quotation.engine import QuoteRequest, quote_request

    request = _mapping(base.get("request"))
    primary = _mapping(base.get("primary"))
    delivery = _mapping(base.get("delivery"))
    presentation = _mapping(primary.get("presentation"))
    units = quantity or request.get("quantity") or presentation.get("units_requested")
    unit = request.get("unit") if quantity is None else None
    try:
        result = quote_request(
            QuoteRequest(
                sku=sku or str(primary.get("sku") or "") or None,
                quantity=int(units) if units else None,
                unit=unit,
                city=str(base.get("city") or "") or None,
                delivery=delivery.get("requested"),
                use=request.get("use"),
                temperature=request.get("temperature"),
                microwave=request.get("microwave"),
            ),
            today=today,
            dataset=dataset,
        )
    except Exception as error:  # el flujo nunca tumba el turno por recotizar
        logger.warning("cotizacion.requote_failed", error_type=type(error).__name__)
        return None
    return result if is_quote(result) else None


# --- antes del turno ----------------------------------------------------------------------


def before_turn(
    draft: QuoteDraft,
    text: str,
    *,
    requote_fn: Requote | None = None,
    judge: ReplyJudge | None = None,
    now: datetime | None = None,
) -> Reply | Pass:
    """Atiende sin el modelo la respuesta a la pregunta pendiente, o deja pasar el turno.

    ``now`` va en hora de Bogotá: las vigencias de los vendedores son fechas de Colombia y,
    entre las 19:00 y la medianoche, UTC ya va en el día siguiente.
    """

    moment = (now or datetime.now(BOGOTA)).astimezone(BOGOTA)
    if requote_fn is None:
        requote_fn = requote  # se busca al llamar: las pruebas pueden sustituirla
    current = draft if not draft.expired(moment) else QuoteDraft()
    decided = _rules(current, text, requote_fn=requote_fn, now=moment)
    if decided is not None:
        return decided
    if judge is not None and _ask_judge(current, text):
        canonical = _judged(current, text, judge)
        if canonical is not None:
            again = _rules(current, canonical, requote_fn=requote_fn, now=moment, original=text)
            if again is not None:
                logger.info("cotizacion.jev_decidio", awaiting=current.awaiting)
                return again
    return Pass(_fresh_after_registration(current))


def _fresh_after_registration(draft: QuoteDraft) -> QuoteDraft:
    return QuoteDraft() if draft.state in {"registrada", "cancelada"} else draft


def _ask_judge(draft: QuoteDraft, text: str) -> bool:
    return (
        draft.awaiting is not None
        and "?" not in text
        and 0 < len(plain(text).split()) <= JEV_MAX_WORDS
    )


def _judged(draft: QuoteDraft, text: str, judge: ReplyJudge) -> str | None:
    try:
        choice, confidence = judge.classify_reply(awaiting=str(draft.awaiting), reply=text)
    except Exception as error:  # Jev caído, sin cupo o respuesta inválida: sigue el agente
        logger.info("cotizacion.jev_no_disponible", error_type=type(error).__name__)
        return None
    if confidence < JEV_MIN_CONFIDENCE or choice not in _CANONICAL:
        return None
    if choice == "rechazo_insulto" and confidence < 0.85:
        return None  # la broma solo con mucha certeza
    return _CANONICAL[choice]


def _rules(
    draft: QuoteDraft,
    text: str,
    *,
    requote_fn: Requote,
    now: datetime,
    original: str | None = None,
) -> Reply | Pass | None:
    said = original or text
    if draft.state == "registrada":
        if draft.folio and is_confirmation(text):
            return Reply(render.already_registered(draft.folio), draft, route="confirmacion")
        draft = QuoteDraft()
    elif draft.state == "cancelada":
        draft = QuoteDraft()
    if is_price_insult(text):
        paused = draft.touch(now, state="pausada" if draft.lines else draft.state, awaiting=None)
        return Reply(render.joke(), paused, route="rechazo")
    if is_cancel_all(text) and draft.active:
        return Reply(render.cancelled(None), QuoteDraft().touch(now, state="cancelada"))
    if is_resume(text):
        if draft.lines:
            resumed = draft.touch(now, state="carrito", awaiting="algo_mas", off_flow=0)
            return Reply(render.resume(resumed), resumed)
        return Reply(render.empty_cart(), draft)
    if is_leaving(text) and not parse_quantities(text):
        paused = draft.touch(
            now,
            state="pausada" if draft.lines else "explorando",
            awaiting=None,
            options=(),
            comparison=(),
            current=None,
            offer=None,
        )
        return Reply(render.leaving(bool(draft.lines)), paused, route="salida")
    if is_too_expensive(text):
        return _too_expensive(draft, now)
    if wants_cart(text):
        # «¿Qué tengo en la cotización?»: el borrador tal cual (o que está vacío), sin el
        # modelo; antes caía al agente, que buscaba folios registrados y se abstenía.
        if not draft.lines:
            return Reply(render.empty_cart(), draft)
        keep = draft.awaiting if draft.awaiting not in (None, "algo_mas") else "algo_mas"
        shown = draft.touch(
            now, awaiting=keep, state=draft.state if keep != "algo_mas" else "carrito"
        )
        return Reply(render.cart_view(shown), shown)
    if draft.lines and draft.awaiting in _EDITABLE and wants_change(text):
        return _edit(draft, now)
    handler = _AWAITING.get(draft.awaiting or "")
    if handler is not None:
        handled = handler(draft, text, said, requote_fn, now)
        if handled is not None:
            return handled
    removal = _removal(draft, text, now)
    if removal is not None:
        return removal
    return _switch(draft, text, said, now)


def _too_expensive(draft: QuoteDraft, now: datetime) -> Reply:
    if draft.cheaper_offered:
        paused = draft.touch(now, state="pausada" if draft.lines else draft.state, awaiting=None)
        return Reply(render.leaving(bool(draft.lines)), paused, route="salida")
    row = None
    if draft.options:
        rows = [item for item in option_rows(draft.options[0]) if item.get("subtotal")]
        cheapest = min(rows, key=lambda item: float(item["subtotal"]), default=None)
        if cheapest is not None and cheapest["index"] != 0:
            row = cheapest
    changed = draft.touch(
        now,
        cheaper_offered=True,
        offer=dict(row) if row else None,
        awaiting="oferta" if row else draft.awaiting,
    )
    return Reply(render.cheaper_offer(row), changed, route="precio")


def _on_confirm(
    draft: QuoteDraft, text: str, said: str, requote_fn: Requote, now: datetime
) -> Reply | Pass | None:
    if wants_edit(text):  # primero: con un cambio pedido nunca se registra
        return _edit_request(draft, text, said, now)
    if is_confirmation(text) or resolve_more(text) == "close_confirm":
        if not draft.lines:
            return Reply(render.empty_cart(), draft.touch(now, awaiting=None))
        return _register_or_refresh(draft, requote_fn, now)
    if plain(text) in {"no", "nop", "todavia no", "aun no", "espera"}:
        return _edit(draft, now)
    more = resolve_more(text)
    if more == "add":
        return _add_request(draft, said, now)
    return None


def _edit(draft: QuoteDraft, now: datetime) -> Reply:
    """«Cambiar algo»: el carrito numerado y cómo cambiarlo; el borrador sigue intacto.

    Queda en «¿Algo más?»: «quita la 2» quita, otro producto se agrega (otra cantidad del
    mismo actualiza su línea) y «eso es todo» vuelve al resumen para confirmar.
    """

    if not draft.lines:
        return Reply(render.empty_cart(), draft.touch(now, awaiting=None))
    edited = draft.touch(now, awaiting="algo_mas", state="carrito", off_flow=0)
    return Reply(render.edit_cart(edited), edited)


def _edit_request(draft: QuoteDraft, text: str, said: str, now: datetime) -> Reply | Pass:
    """«Quiero cambiar los vasos por platos»: un cambio dicho lo hace el agente, sin Jev.

    Así Jev no puede leerlo como un «sí» y registrar el carrito sin cambiar, y el «¿La
    confirmo?» queda desarmado: un «sí» posterior pide un producto, no registra. «Quiero
    quitar la 2» lo resuelve la regla de quitar; con una cantidad («quiero quitarle 100 a
    los vasos»), algo de la línea («quiero quitarle la tapa a los vasos») o varias líneas
    es cambiar el carrito de otra forma, y lo hace el agente.
    """

    if removes_whole_line(text):
        removal = _removal(draft, text, now)
        if removal is not None:
            return removal
    if not draft.lines:
        return Pass(draft.touch(now, awaiting=None, off_flow=0), prompt=strip_assent(said))
    editing = draft.touch(now, awaiting="algo_mas", state="carrito", off_flow=0)
    return Pass(editing, prompt=strip_assent(said))


def _register_or_refresh(draft: QuoteDraft, requote_fn: Requote, now: datetime) -> Reply | Pass:
    stale = expired_lines(draft, now.date())
    if not stale:
        return Reply(render.registered(draft, "", None), draft, route="confirmacion", register=True)
    refreshed = []
    for line in draft.lines:
        if line_label(line) in stale:
            fresh = requote_fn(line, today=now.date())
            if fresh is None:
                prompt = f"Vuelve a cotizar {line_label(line)}: su precio venció."
                return Pass(draft.touch(now, awaiting=None), prompt=prompt)
            refreshed.append(dict(fresh))
        else:
            refreshed.append(line)
    updated = draft.touch(now, lines=tuple(refreshed), awaiting="confirmar", state="resumen")
    note = "Los precios de " + ", ".join(stale) + " vencieron; los recalculé."
    return Reply(render.summary(updated, note=note), updated)


def _on_more(
    draft: QuoteDraft, text: str, said: str, requote_fn: Requote, now: datetime
) -> Reply | Pass | None:
    if wants_edit(text):  # antes que cerrar o agregar: «eso es todo, pero quita la 2»
        return _edit_request(draft, text, said, now)
    more = resolve_more(text)
    if more == "close":
        if not draft.lines:
            return Reply(render.empty_cart(), draft.touch(now, awaiting=None))
        closed = draft.touch(now, state="resumen", awaiting="confirmar")
        return Reply(render.summary(closed), closed)
    if more == "close_confirm" and draft.lines:
        return _register_or_refresh(draft, requote_fn, now)
    if more == "add":
        return _add_request(draft, said, now)
    if is_confirmation(text) and draft.lines:
        # «Sí» a «¿Algo más?» es agregar; «confírmala» ya lo resolvió ``resolve_more``.
        return Reply(render.ask_product(), draft.touch(now, awaiting=None, state="carrito"))
    return None


def _add_request(draft: QuoteDraft, said: str, now: datetime) -> Reply | Pass:
    content = strip_assent(said)
    folded = plain(content)
    if not folded or folded in {
        "si",
        "claro",
        "dale",
        "ok",
        "obvio",
        "bueno",
        "listo",
        "otro",
        "otra",
        "algo mas",
        "mas",
    }:
        return Reply(render.ask_product(), draft.touch(now, awaiting=None, state="carrito"))
    if folded in {
        "agregar",
        "agrega",
        "agregar otro",
        "agrega otro",
        "otro producto",
        "agregar otro producto",
    }:
        return Reply(render.ask_product(), draft.touch(now, awaiting=None, state="carrito"))
    return Pass(draft.touch(now, awaiting=None, off_flow=0), prompt=content)


def _quantity_in_choice(text: str, choice: int) -> int | None:
    """Cantidad dicha junto a la elección («esa, son 300»), sin el número de la opción."""

    quantities = [value for value in parse_quantities(text) if not (value == choice and value <= 9)]
    return quantities[0] if quantities else None


def _on_choice(
    draft: QuoteDraft, text: str, said: str, requote_fn: Requote, now: datetime
) -> Reply | Pass | None:
    if not draft.options:
        return None
    base = draft.options[0]
    rows = option_rows(base)
    alternatives = len(rows) - 1
    found = resolve_choice(text, alternatives)
    if found is None and alternatives == 0 and is_confirmation(text):
        found = Choice(0)
    if found is None and resolve_more(text) == "close":
        # «Eso es todo» ante «¿Cuál te cotizo?» no elige la recomendada: con carrito va al
        # resumen; sin él, la persona no quiso ninguna y se despide como «lo pienso».
        if draft.lines:
            closed = draft.touch(now, state="resumen", awaiting="confirmar", options=())
            return Reply(render.summary(closed), closed)
        paused = draft.touch(now, state="explorando", awaiting=None, options=(), offer=None)
        return Reply(render.leaving(False), paused, route="salida")
    if found is None:
        return None
    if alternatives == 0 and found == ChoiceOutOfRange(1):
        found = Choice(0)
    if isinstance(found, ChoiceOutOfRange):
        return Reply(render.out_of_range(found.asked, alternatives), draft, route="eleccion")
    row = rows[found.index]
    quantity = _quantity_in_choice(text, found.index)
    if found.index == 0 and (quantity is None or quantity == units_of(base)):
        line: Mapping[str, Any] | None = base
    else:
        line = requote_fn(base, sku=row["sku"], quantity=quantity, today=now.date())
    if line is None:
        prompt = f"{said}\n\n(Elegí {row['name']}, ref. {row['sku']}. Cotízala.)"
        return Pass(draft.touch(now, awaiting=None, options=()), prompt=prompt)
    return _added_reply(draft, [line], now)


def _on_quantity(
    draft: QuoteDraft, text: str, said: str, requote_fn: Requote, now: datetime
) -> Reply | Pass | None:
    quantities = tuple(units_of(item) for item in draft.comparison)
    chosen = resolve_quantity_choice(text, quantities)
    if chosen is None:
        return None
    line = next(item for item in draft.comparison if units_of(item) == chosen)
    return _added_reply(draft, [line], now)


def _on_switch(
    draft: QuoteDraft, text: str, said: str, requote_fn: Requote, now: datetime
) -> Reply | Pass | None:
    answer = resolve_switch(text)
    if answer is None:
        return None
    pending = draft.pending_request
    base = draft.touch(
        now,
        awaiting=None,
        pending_request=None,
        options=(),
        comparison=(),
        current=None,
        switch_drops_last=False,
    )
    if answer == "cancel" and draft.switch_drops_last and draft.lines:
        base = replace(base, lines=draft.lines[:-1])
    logger.info("cotizacion.cambio_producto", decision=answer)
    if not pending:
        label = str((draft.current or {}).get("label") or "")
        return Reply(render.cancelled(label or None), base)
    return Pass(base, prompt=pending)


def _on_offer(
    draft: QuoteDraft, text: str, said: str, requote_fn: Requote, now: datetime
) -> Reply | Pass | None:
    if draft.offer is None:
        return None
    if is_confirmation(text) or resolve_choice(text, 0) == Choice(0):
        line = None
        if draft.options:
            line = requote_fn(draft.options[0], sku=str(draft.offer.get("sku")), today=now.date())
        if line is None:
            prompt = f"(Cotiza {draft.offer.get('name')}, ref. {draft.offer.get('sku')}.)"
            return Pass(draft.touch(now, awaiting=None, offer=None), prompt=prompt)
        return _added_reply(draft.touch(now, offer=None), [line], now)
    if plain(text) in {"no", "nop", "no gracias", "asi esta bien", "no asi esta bien"}:
        paused = draft.touch(
            now, awaiting=None, offer=None, state="pausada" if draft.lines else "explorando"
        )
        return Reply(render.leaving(bool(draft.lines)), paused, route="salida")
    return None


# Preguntas tras las que «quiero cambiar algo» edita el carrito; en «¿Cancelo o conservo?»
# (``cambio_producto``) «cambiar» es otra respuesta y lo decide ``resolve_switch``.
_EDITABLE: Final = frozenset({"confirmar", "algo_mas", None})

_AWAITING: Final[dict[str, Callable[..., Reply | Pass | None]]] = {
    "confirmar": _on_confirm,
    "algo_mas": _on_more,
    "eleccion": _on_choice,
    "cantidad": _on_quantity,
    "cambio_producto": _on_switch,
    "oferta": _on_offer,
}


def _removal(draft: QuoteDraft, text: str, now: datetime) -> Reply | None:
    labels = tuple(line_label(line) for line in draft.lines)
    index = resolve_remove(text, labels)
    if index is None:
        return None
    lines = remove_line(draft, index).lines
    changed = draft.touch(
        now,
        lines=lines,
        awaiting="algo_mas" if lines else None,
        state="carrito" if lines else "explorando",
    )
    return Reply(render.removed(changed, labels[index]), changed)


def _switch(draft: QuoteDraft, text: str, said: str, now: datetime) -> Reply | None:
    """«No, mejor vasos» con otro producto en curso: se pregunta antes de cancelar (T7)."""

    family = product_family(text)
    if family is None or "?" in text:
        return None
    folded = plain(text)
    if draft.current and draft.awaiting in {"eleccion", "cantidad", None}:
        current_family = draft.current.get("family")
        if current_family and family != current_family and not _ALSO.search(folded):
            label = str(
                draft.current.get("label") or _FAMILY_LABEL.get(str(current_family), "lo anterior")
            )
            asked = draft.touch(
                now, awaiting="cambio_producto", pending_request=said, switch_drops_last=False
            )
            logger.info("cotizacion.pregunta_cambio", con_lineas=bool(draft.lines))
            return Reply(render.switch_question(label), asked)
    if draft.awaiting == "algo_mas" and draft.lines and wants_switch(text):
        last = draft.lines[-1]
        if line_family(last) and family != line_family(last):
            asked = draft.touch(
                now,
                awaiting="cambio_producto",
                pending_request=said,
                switch_drops_last=True,
                current={"family": line_family(last), "label": line_label(last)},
            )
            return Reply(render.switch_question(line_label(last)), asked)
    return None


def _added_reply(draft: QuoteDraft, lines: Sequence[Mapping[str, Any]], now: datetime) -> Reply:
    updated, message = _add_lines(draft, lines, now)
    return Reply(message, updated)


def _add_lines(
    draft: QuoteDraft,
    lines: Sequence[Mapping[str, Any]],
    now: datetime,
    *,
    several_note: bool = False,
) -> tuple[QuoteDraft, Message]:
    current = replace(draft, state="carrito") if draft.state != "carrito" else draft
    updated_any = False
    for line in lines:
        if same_sku_line(current, line) is not None:
            updated_any = True
        current, action = add_line(current, line)
        if action == "tope":
            capped = current.touch(
                now, awaiting="algo_mas", options=(), comparison=(), current=None
            )
            return capped, render.line_cap()
    changed = current.touch(
        now,
        state="carrito",
        awaiting="algo_mas",
        options=(),
        comparison=(),
        current=None,
        offer=None,
        off_flow=0,
        pending_request=None,
    )
    logger.info("cotizacion.linea_agregada", lineas=len(changed.lines))
    message = render.added(
        changed,
        [changed.lines[_index(changed, line)] for line in lines],
        updated=updated_any and len(lines) == 1,
        several_note=several_note,
    )
    return changed, message


def _index(draft: QuoteDraft, line: Mapping[str, Any]) -> int:
    sku = str((line.get("primary") or {}).get("sku") or "")
    for index, item in enumerate(draft.lines):
        if str((item.get("primary") or {}).get("sku") or "") == sku:
            return index
    return len(draft.lines) - 1


# --- después del turno --------------------------------------------------------------------


def after_turn(
    draft: QuoteDraft,
    quotes: Sequence[Mapping[str, Any]],
    text: str,
    *,
    now: datetime | None = None,
) -> Outcome:
    """Lo que el flujo agrega al turno del agente según las cotizaciones que calculó."""

    moment = now or datetime.now(UTC)
    current = _fresh_after_registration(draft)
    calculated = [dict(item) for item in quotes if is_quote(item)]
    if not calculated:
        return _without_quotes(current, text, moment)
    skus: list[str] = []
    for item in calculated:
        sku = str(item["primary"].get("sku") or "")
        if sku not in skus:
            skus.append(sku)
    if len(calculated) >= 2 and len(skus) == 1:
        ordered = tuple(sorted(calculated, key=units_of))
        changed = current.touch(
            moment,
            comparison=ordered,
            options=(),
            awaiting="cantidad",
            current={"family": line_family(ordered[0]), "label": line_label(ordered[0])},
            off_flow=0,
        )
        logger.info("cotizacion.comparativo", cantidades=len(ordered))
        return Outcome(changed, render.comparison(ordered), flow=True)
    if len(skus) >= 2:
        firsts = []
        for sku in skus:
            firsts.append(next(item for item in calculated if item["primary"].get("sku") == sku))
        several = any(item.get("alternatives") for item in firsts)
        changed, message = _add_lines(current, firsts, moment, several_note=several)
        return Outcome(changed, message, flow=True)
    quote = calculated[0]
    if quote.get("alternatives"):
        changed = current.touch(
            moment,
            options=(quote,),
            comparison=(),
            awaiting="eleccion",
            current={"family": line_family(quote), "label": line_label(quote)},
            off_flow=0,
            offer=None,
        )
        logger.info("cotizacion.opciones", alternativas=len(quote["alternatives"]))
        return Outcome(changed, render.options(quote), flow=True)
    changed, message = _add_lines(current, [quote], moment)
    return Outcome(changed, message, flow=True)


def _without_quotes(draft: QuoteDraft, text: str, now: datetime) -> Outcome:
    """Turno sin cotización: producto en curso o pregunta de otro tema con recordatorio."""

    family = product_family(text)
    if family and draft.awaiting in {None, "algo_mas"} and not draft.options:
        # «Cotiza platos» sin cantidad: el agente pide el dato; queda el producto en curso.
        label = _FAMILY_LABEL.get(family, family)
        changed = draft.touch(
            now, current={"family": family, "label": label}, awaiting=None, off_flow=0
        )
        return Outcome(changed)
    if not draft.active or draft.awaiting is None:
        return Outcome(draft)
    if draft.off_flow >= OFF_FLOW_REMINDERS:
        return Outcome(draft)
    message = render.reminder(draft)
    if message is None:
        return Outcome(draft)
    return Outcome(draft.touch(now, off_flow=draft.off_flow + 1), message)
