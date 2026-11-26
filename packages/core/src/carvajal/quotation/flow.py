"""Conducción del flujo de cotización (spec 054): antes y después de cada turno.

El servicio del chat llama a :func:`before_turn` con el borrador de la conversación y el
mensaje de la persona. Si el mensaje responde a lo que el asistente preguntó («sí», «la 2»,
«eso es todo», «cancélalos», «con mil»…), el turno se atiende aquí sin el modelo
(:class:`Reply`). Si no, va al agente (:class:`Pass`), a veces con otro texto (el pedido que
quedó en espera mientras se preguntaba si cancelar el producto anterior).

Después del turno del agente, :func:`after_turn` mira las cotizaciones que calculó y decide:
opciones (una recomendada + hasta cuatro alternativas), comparativo de cantidades, línea
agregada o, si el turno fue de otro tema, un recordatorio de una línea para retomar.

Nombre ambiguo: si ``cotizar`` devolvió varias referencias para el nombre que dijo la persona,
:func:`after_turn` las guarda (``awaiting="referencia"``) con lo demás de la solicitud. En el
turno siguiente «la 2», el SKU o «el Gold» cotizan esa referencia con el motor, sin el
modelo, y siguen el camino de cualquier cotización (opciones o línea con «¿Algo más?»); «sí»
o «esa» vuelven a mostrar la lista, y otra cosa sale del estado y va al agente.

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
    ReferencePick,
    is_cancel_all,
    is_confirmation,
    is_leaving,
    is_price_insult,
    is_resume,
    is_too_expensive,
    parse_quantities,
    plain,
    reference_quantity,
    removes_whole_line,
    resolve_choice,
    resolve_more,
    resolve_quantity_choice,
    resolve_reference,
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
    Awaiting,
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
from carvajal.quotation.order import (
    Buyer,
    correction_name,
    declines_order,
    find_email,
    is_affirmation,
    is_bare_no,
    name_in,
    name_marked,
    wants_purchase,
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
# Pasos del pedido (spec 055): las reglas los deciden todos; nada de ellos va a Jev.
ORDER_AWAITING: Final = frozenset({"pedido", "datos_pedido", "confirmar_pedido"})
JEV_MIN_CONFIDENCE: Final = 0.7
JEV_MAX_WORDS: Final = 14
# Campos del borrador que vacían la lista de referencias de un nombre ambiguo.
_NO_CHOICES: Final[dict[str, Any]] = {
    "choices": (),
    "choice_request": None,
    "resume_awaiting": None,
}
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
    # Spec 055: con los datos completos y confirmados de quien compra, el servicio crea el
    # pedido. ``private``: el mensaje de la persona trae su nombre o su correo; el turno se
    # guarda y se monitorea sin ellos.
    order: Buyer | None = None
    private: bool = False


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
    unit: str | None = None,
    today: date | None = None,
    dataset: Any = None,
) -> Mapping[str, Any] | None:
    """La misma solicitud de ``base`` con otra referencia o cantidad, con el motor.

    Sirve para elegir una alternativa («la 3»), cambiar la cantidad en la elección («esa,
    son 300») y recalcular una línea vencida. ``unit`` es la unidad de compra de
    ``quantity`` («2 cajas»); sin ella, la cantidad nueva son unidades. Devuelve ``None`` si
    el motor no cotiza.
    """

    from carvajal.quotation.engine import QuoteRequest, quote_request

    request = _mapping(base.get("request"))
    primary = _mapping(base.get("primary"))
    delivery = _mapping(base.get("delivery"))
    presentation = _mapping(primary.get("presentation"))
    units = quantity or request.get("quantity") or presentation.get("units_requested")
    if quantity is None:
        unit = request.get("unit")
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
    orders: bool = False,
) -> Reply | Pass:
    """Atiende sin el modelo la respuesta a la pregunta pendiente, o deja pasar el turno.

    ``orders`` enciende el paso del pedido tras el folio (spec 055): solo con el CRM
    configurado se acepta «hacer el pedido» y se piden nombre y correo.

    ``now`` va en hora de Bogotá: las vigencias de los vendedores son fechas de Colombia y,
    entre las 19:00 y la medianoche, UTC ya va en el día siguiente.
    """

    moment = (now or datetime.now(BOGOTA)).astimezone(BOGOTA)
    if requote_fn is None:
        requote_fn = requote  # se busca al llamar: las pruebas pueden sustituirla
    current = draft if not draft.expired(moment) else QuoteDraft()
    decided = _rules(current, text, requote_fn=requote_fn, now=moment, orders=orders)
    if decided is not None:
        return decided
    if judge is not None and _ask_judge(current, text):
        canonical = _judged(current, text, judge)
        if canonical is not None:
            again = _rules(
                current,
                canonical,
                requote_fn=requote_fn,
                now=moment,
                original=text,
                orders=orders,
            )
            if again is not None:
                logger.info("cotizacion.jev_decidio", awaiting=current.awaiting)
                return again
    return Pass(_leave_reference(_fresh_after_registration(current), moment))


def _leave_reference(draft: QuoteDraft, now: datetime) -> QuoteDraft:
    """Lo que no eligió una candidata (otra pregunta, otro producto) sale de la lista.

    Vuelve la pregunta del carrito que la lista dejó en espera: con «¿Algo más?» o «¿La
    confirmo?» pendientes, «eso es todo» o «sí» siguen cerrando o registrando.
    """

    if draft.awaiting != "referencia" and not draft.choices:
        return draft
    awaiting = _resumed(draft) if draft.awaiting == "referencia" else draft.awaiting
    return draft.touch(now, awaiting=awaiting, **_NO_CHOICES)


def _resumed(draft: QuoteDraft) -> Awaiting | None:
    """Lo que se esperaba antes de la lista, si el carrito sigue ahí para responderlo."""

    return draft.resume_awaiting if draft.lines else None


def _fresh_after_registration(draft: QuoteDraft) -> QuoteDraft:
    if _order_pending(draft):
        return draft  # una pregunta a mitad del pedido no lo pierde (spec 055)
    return QuoteDraft() if draft.state in {"registrada", "cancelada"} else draft


def _order_pending(draft: QuoteDraft) -> bool:
    """Cotización registrada con el pedido a medias (oferta, datos o confirmación)."""

    return (
        draft.state == "registrada"
        and bool(draft.folio)
        and not draft.order
        and draft.awaiting in ORDER_AWAITING
    )


def _order_step(draft: QuoteDraft, text: str, now: datetime) -> Reply | Pass | None:
    """Spec 055: «¿Hacemos el pedido?» → nombre y correo → «¿Lo confirmo?» → pedido.

    Reglas explícitas por paso, sin adivinar: en la oferta solo cuentan como datos un correo
    o un nombre escrito como nombre propio; en ``datos_pedido`` el mensaje es el nombre que
    se pidió (si no es una afirmación ni una muletilla); en ``confirmar_pedido`` todo lo que
    trae un dato lo corrige y solo un «sí» crea el pedido. Otro producto sale del pedido con
    un borrador nuevo. Una pregunta sin datos va al agente sin perder el paso; con datos, los
    datos ganan y nunca salen de las reglas. ``None``: lo decide el resto de las reglas.
    """

    if draft.order:
        if wants_purchase(text) or (draft.awaiting is None and is_confirmation(text)):
            return Reply(render.already_ordered(draft.order), draft, route="pedido")
        return None
    if not draft.folio:
        return None
    awaiting = draft.awaiting
    if awaiting not in ORDER_AWAITING:
        if wants_purchase(text):  # «quiero comprarla» tras «ahora no»
            asking = draft.touch(now, awaiting="datos_pedido", buyer=None, off_flow=0)
            return Reply(render.ask_buyer(), asking, route="pedido")
        return None
    email = find_email(text)
    if email is None and product_family(text) and not name_marked(text):
        return Pass(QuoteDraft())  # «quiero 500 vasos»: otra cotización, borrador nuevo
    if awaiting == "pedido":
        return _offer_step(draft, text, email, now)
    if awaiting == "datos_pedido":
        return _data_step(draft, text, email, now)
    return _confirm_step(draft, text, email, now)


def _declined(draft: QuoteDraft, now: datetime) -> Reply:
    declined = draft.touch(now, awaiting=None, buyer=None, off_flow=0)
    return Reply(render.order_declined(draft.folio), declined, route="pedido")


def _offer_step(draft: QuoteDraft, text: str, email: str | None, now: datetime) -> Reply | None:
    """«¿Quieres hacer el pedido?»: sí, no, o los datos directamente."""

    name = name_marked(text) or name_in(text, strict=True)
    if email or (name and not is_affirmation(text)):
        return _with_buyer(draft, Buyer(name=name, email=email), now)
    if declines_order(text) or is_cancel_all(text):
        return _declined(draft, now)
    if wants_purchase(text) or is_affirmation(text):
        asking = draft.touch(now, awaiting="datos_pedido", off_flow=0)
        return Reply(render.ask_buyer(), asking, route="pedido")
    return None  # otra cosa: el agente responde y el paso se conserva con un recordatorio


def _question_only(text: str) -> bool:
    """Una pregunta que no trae datos antes del «¿»: va al agente."""

    if "?" not in text:
        return False
    before = text.split("¿", 1)[0]
    return not (find_email(text) or name_marked(text) or (before.strip() and name_in(before)))


def _data_step(draft: QuoteDraft, text: str, email: str | None, now: datetime) -> Reply | Pass:
    """Nombre y correo, en uno o dos mensajes: lo que se pidió es lo que se toma."""

    if _question_only(text):
        return Pass(draft)
    marked = name_marked(text)
    if not (email or marked) and (declines_order(text) or is_cancel_all(text)):
        return _declined(draft, now)
    buyer = Buyer.from_json(draft.buyer)
    name = marked or buyer.name
    if name is None and not is_affirmation(text):
        name = name_in(text.split("¿", 1)[0])
    return _with_buyer(draft, Buyer(name=name, email=email or buyer.email), now)


def _confirm_step(draft: QuoteDraft, text: str, email: str | None, now: datetime) -> Reply | Pass:
    """«¿Lo confirmo?»: un dato corrige; «sí» crea el pedido; «no» pregunta qué corregir."""

    buyer = Buyer.from_json(draft.buyer)
    marked = name_marked(text)
    if email or marked:
        return _with_buyer(draft, Buyer(name=marked or buyer.name, email=email or buyer.email), now)
    if (is_affirmation(text) or wants_purchase(text)) and buyer.complete:
        return Reply(render.ask_buyer(), draft, route="pedido", order=buyer)
    if is_bare_no(text):
        return Reply(render.which_correction(), draft, route="pedido")
    if declines_order(text) or is_cancel_all(text):
        corrected = correction_name(text)
        if corrected is None:
            return _declined(draft, now)
        return _with_buyer(draft, Buyer(name=corrected, email=buyer.email), now)
    if _question_only(text):
        return Pass(draft)
    # Un nombre suelto corrige solo escrito como nombre propio («Marta Gómez»): «pues
    # bueno» o «listo entonces» no lo cambian.
    corrected = name_in(text.split("¿", 1)[0], strict=True)
    if corrected and not is_affirmation(text):
        return _with_buyer(draft, Buyer(name=corrected, email=buyer.email), now)
    return _with_buyer(draft, buyer, now)


def _with_buyer(draft: QuoteDraft, buyer: Buyer, now: datetime) -> Reply:
    """Con los dos datos, «¿Lo confirmo?»; si falta uno, se pide ese."""

    if buyer.name and buyer.email:
        confirming = draft.touch(
            now, awaiting="confirmar_pedido", buyer=buyer.to_json(), off_flow=0
        )
        message = render.confirm_buyer(buyer.name, buyer.email)
        return Reply(message, confirming, route="pedido", private=True)
    waiting = draft.touch(now, awaiting="datos_pedido", buyer=buyer.to_json(), off_flow=0)
    message = render.buyer_missing(buyer.name, buyer.email)
    return Reply(message, waiting, route="pedido", private=True)


def _ask_judge(draft: QuoteDraft, text: str) -> bool:
    # Ante la lista de referencias las reglas deciden todo: lo que no elige va al agente.
    # Los datos del pedido (nombre y correo) nunca salen a Jev.
    return (
        draft.awaiting is not None
        and draft.awaiting not in {"referencia", *ORDER_AWAITING}
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
    orders: bool = False,
) -> Reply | Pass | None:
    said = original or text
    if draft.state == "registrada" and orders:
        ordering = _order_step(draft, said, now)
        if ordering is not None:
            return ordering
    if draft.state == "registrada":
        if draft.folio and (is_confirmation(text) or wants_cart(text)):
            # «¿Qué tengo en la cotización?» justo después de registrar: ya tiene folio, no
            # está vacía.
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
            **_NO_CHOICES,
        )
        return Reply(render.leaving(bool(draft.lines)), paused, route="salida")
    if is_too_expensive(text):
        return _too_expensive(draft, now)
    if wants_cart(text):
        # «¿Qué tengo en la cotización?»: el borrador tal cual (o que está vacío), sin el
        # modelo; antes caía al agente, que buscaba folios registrados y se abstenía.
        if not draft.lines:
            pending = render.reminder(draft)
            empty = render.empty_cart()
            if pending is not None:
                # Opciones o comparativo pendientes: el carrito está vacío, la pregunta sigue.
                empty = render.Message(
                    f"{empty.text}\n\n{pending.text}", f"{empty.spoken} {pending.spoken}"
                )
            return Reply(empty, draft)
        # «Oferta» espera un sí/no a la opción más económica: tras mostrar el carrito con
        # «¿Algo más?», un «sí» no debe aceptar la oferta; se vuelve a «¿Algo más?».
        keep = draft.awaiting if draft.awaiting not in (None, "algo_mas", "oferta") else "algo_mas"
        shown = draft.touch(
            now,
            awaiting=keep,
            state=draft.state if keep != "algo_mas" else "carrito",
            offer=None if keep == "algo_mas" else draft.offer,
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
    if found is None and resolve_more(text) in ("close", "close_confirm"):
        # «Eso es todo» ante «¿Cuál te cotizo?» no elige la recomendada: con carrito va al
        # resumen; sin él, la persona no quiso ninguna y se despide como «lo pienso».
        if draft.lines:
            # También «eso es todo, confírmala»: el resumen pide el «sí» (nada se registra
            # mientras se elegía entre opciones).
            closed = draft.touch(
                now, state="resumen", awaiting="confirmar", options=(), comparison=(), current=None
            )
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


def _named_city(text: str) -> str | None:
    """Ciudad del directorio que nombra el mensaje, o ``None``."""

    from carvajal.quotation.engine import named_city

    try:
        return named_city(text)
    except Exception as error:  # el flujo nunca tumba el turno por leer la ciudad
        logger.warning("cotizacion.ciudad_no_leida", error_type=type(error).__name__)
        return None


def _on_reference(
    draft: QuoteDraft, text: str, said: str, requote_fn: Requote, now: datetime
) -> Reply | Pass | None:
    """Respuesta a «¿Cuál quieres?» con las referencias de un nombre ambiguo.

    La elegida se cotiza con el motor con su SKU exacto y lo demás de la solicitud
    (``choice_request``); si el motor pide otro dato (la cantidad o la ciudad), el agente la
    cotiza con ese SKU. «Sí», «esa» o un fragmento que comparten varias vuelven a mostrar la
    lista: el flujo nunca elige por la persona.
    """

    if not draft.choices:
        return None
    found = resolve_reference(text, draft.choices)
    if found == "ask" and len(draft.choices) == 1:
        found = ReferencePick(0)  # una sola candidata: «sí» o «esa» la eligen
    if found is None:
        if resolve_more(text) not in ("close", "close_confirm"):
            return None
        # «Eso es todo» ante la lista no elige ninguna: como ante unas opciones.
        if draft.lines:
            closed = draft.touch(now, state="resumen", awaiting="confirmar", **_NO_CHOICES)
            return Reply(render.summary(closed), closed)
        paused = draft.touch(now, state="explorando", awaiting=None, **_NO_CHOICES)
        return Reply(render.leaving(False), paused, route="salida")
    if isinstance(found, ChoiceOutOfRange):
        message = render.reference_out_of_range(found.asked, len(draft.choices))
        return Reply(message, draft, route="eleccion")
    if not isinstance(found, ReferencePick):  # «sí», «esa», «cualquiera»: no dice cuál
        return Reply(render.which_reference(draft.choices), draft, route="eleccion")
    candidate = draft.choices[found.index]
    sku = str(candidate.get("sku") or "")
    request = dict(draft.choice_request or {})
    # «La 3, en Cali»: la ciudad dicha con la elección manda sobre la de la solicitud.
    city = _named_city(text) or request.get("city")
    request["city"] = city
    base = {
        "request": request,
        "city": city,
        "delivery": {"requested": request.get("delivery")},
        "primary": {},
    }
    # «La 3, 2 cajas»: la cantidad va con su unidad; «el de 90mm» no dice cantidad.
    quantity, unit = reference_quantity(text, candidate, found.index + 1)
    line = (
        requote_fn(base, sku=sku, quantity=quantity, unit=unit, today=now.date()) if sku else None
    )
    resumed = _resumed(draft)
    cleared = replace(draft, **_NO_CHOICES)
    logger.info("cotizacion.referencia_elegida", cotizada=line is not None)
    if line is None:
        name = candidate.get("name") or sku
        prompt = f"{said}\n\n(Elegí {name}, ref. {sku}. Cotízala con ese sku.)"
        return Pass(cleared.touch(now, awaiting=resumed), prompt=prompt)
    changed, message = _present(cleared, line, now)
    return Reply(message, changed)


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
    "referencia": _on_reference,
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
        **_NO_CHOICES,  # «quita la 2» ante la lista de referencias: la lista ya no aplica
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
        **_NO_CHOICES,
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
    choice: Mapping[str, Any] | None = None,
) -> Outcome:
    """Lo que el flujo agrega al turno del agente según las cotizaciones que calculó.

    ``choice`` es la tarjeta ``reference_choice`` de ``cotizar`` (candidatas de un nombre
    ambiguo y la solicitud): sin cotizaciones en el turno, deja la lista esperando respuesta.
    """

    moment = now or datetime.now(UTC)
    calculated = [dict(item) for item in quotes if is_quote(item)]
    if _order_pending(draft):
        if calculated:  # cotizó otra cosa: el pedido de la anterior queda sin hacer
            draft = QuoteDraft()
        else:
            return _order_off_flow(draft, moment)
    current = _fresh_after_registration(draft)
    if not calculated:
        waiting = _awaiting_reference(current, choice, moment)
        if waiting is not None:
            return Outcome(waiting)
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
            **_NO_CHOICES,
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
    changed, message = _present(current, calculated[0], moment)
    return Outcome(changed, message, flow=True)


def _present(
    draft: QuoteDraft, quote: Mapping[str, Any], now: datetime
) -> tuple[QuoteDraft, Message]:
    """Una cotización: opciones si trae alternativas; si no, la línea y «¿Algo más?»."""

    if quote.get("alternatives"):
        changed = draft.touch(
            now,
            options=(dict(quote),),
            comparison=(),
            awaiting="eleccion",
            **_NO_CHOICES,
            current={"family": line_family(quote), "label": line_label(quote)},
            off_flow=0,
            offer=None,
        )
        logger.info("cotizacion.opciones", alternativas=len(quote["alternatives"]))
        return changed, render.options(quote)
    return _add_lines(draft, [quote], now)


def _awaiting_reference(
    draft: QuoteDraft, choice: Mapping[str, Any] | None, now: datetime
) -> QuoteDraft | None:
    """Borrador esperando «¿Cuál quieres?» con las candidatas de ``choice``, o ``None``."""

    if not isinstance(choice, Mapping):
        return None
    listed = tuple(
        dict(item)
        for item in choice.get("candidates") or []
        if isinstance(item, Mapping) and item.get("sku")
    )
    if not listed:
        return None
    request = choice.get("request")
    logger.info("cotizacion.referencias_por_elegir", candidatas=len(listed))
    # «¿Algo más?» o «¿La confirmo?» quedan en espera: vuelven si la respuesta no elige.
    previous = draft.resume_awaiting if draft.awaiting == "referencia" else draft.awaiting
    return draft.touch(
        now,
        awaiting="referencia",
        resume_awaiting=previous if previous in {"algo_mas", "confirmar"} else None,
        choices=listed,
        choice_request=dict(request) if isinstance(request, Mapping) else {},
        options=(),
        comparison=(),
        offer=None,
        off_flow=0,
    )


def _order_off_flow(draft: QuoteDraft, now: datetime) -> Outcome:
    """Pregunta a mitad del pedido: el recordatorio una vez; a la segunda, se suelta."""

    if draft.off_flow >= 1:
        return Outcome(QuoteDraft())
    message = render.order_reminder(draft.awaiting)
    return Outcome(draft.touch(now, off_flow=draft.off_flow + 1), message)


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
