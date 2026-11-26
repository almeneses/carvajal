"""Respuesta de respaldo determinista, sin modelo de lenguaje (spec 044, FR-009, R8).

Cuando el proveedor falla, vence el plazo o devuelve una decisión inválida antes del primer
token, el servicio atiende el turno por el camino de reglas (grafo ``routes``) y redacta con
``render()``: el aviso fijo seguido de un resumen de lo que ese camino obtuvo (cotización,
opciones del catálogo, sedes o fragmentos citados). Todo sale de los campos del resultado:
no se inventa, no se estima y no se convierte ninguna cifra. Si no hay nada que mostrar, el
aviso va con una invitación a reintentar.

Si el turno venció su plazo (R9, FR-010) con resultados ya obtenidos, el servicio redacta
con esos resultados en vez de repetir el camino de reglas y cierra con
:func:`timeout_note`, que declara lo que faltó.

Las formas leídas son las de ``carvajal.agent.commercial_flow`` (``run_commercial`` y
``contract_result``), las del motor ``carvajal.quotation`` y las que pinta
``apps/api/templates/_productos.html``.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from carvajal.application.ask import Source

__all__ = [
    "FALLBACK_NOTICE",
    "MAX_ITEMS",
    "RETRY_INVITATION",
    "TIMEOUT_NOTE",
    "quote_text",
    "render",
    "timeout_note",
]

FALLBACK_NOTICE = "No pude redactar la respuesta; esto es lo que encontré."
"""Aviso fijo de la spec (FR-009); abre toda respuesta de respaldo."""

RETRY_INVITATION = (
    "No encontré información para responder esta consulta. "
    "Intenta de nuevo en unos minutos o reformula la pregunta."
)
"""Invitación a reintentar cuando el camino de reglas no obtuvo resultados."""

TIMEOUT_NOTE = (
    "Se acabó el tiempo antes de completar la respuesta: esto es solo lo que alcancé a "
    "consultar y puede faltar información."
)
"""Cierre del respaldo por plazo vencido con lo obtenido (R9, FR-010)."""

MAX_ITEMS = 3
"""Máximo de opciones, sedes, canales y fragmentos que se listan por sección."""

MAX_EXCERPT_CHARS = 280
"""Largo máximo de un extracto citado; se recorta con «…», sin reescribirlo."""

# Secciones de las citas que el camino comercial deriva de sus propias tarjetas
# (``commercial_sources`` y ``custom_sources``): repetirlas como fragmentos no aporta.
_COMMERCIAL_SECTIONS = frozenset({"catalogo_empaques", "directorio_distribuidores"})

_MATERIALS = {
    "PAPEL": "Papel",
    "PULPA": "Pulpa",
    "EPS": "Unicel (EPS)",
    "PS": "Plástico PS",
    "PET": "Plástico PET",
    "PP": "Plástico PP",
    "ALUMINIO": "Aluminio",
}
_TAX = {"incluido": "incluido", "no_incluido": "no incluido", "no_indicado": "no indicado"}
_SPACES = re.compile(r"\s+")


def render(
    products: Mapping[str, Any] | None = None,
    sources: Sequence[Source | Mapping[str, Any]] = (),
    *,
    note: str = "",
) -> str:
    """Texto de respaldo con el aviso fijo y el resumen determinista del turno.

    ``products`` es ``AgentTurn.products`` del modo ``routes`` (resultado comercial) o las
    tarjetas que alcanzó el modo ``tools``, y ``sources`` sus citas en el orden en que
    viajan en ``meta``/``fallback``: la cita ``[n]`` es ``sources[n-1]``, salvo que la
    entrada traiga su propio ``n``. ``note`` (p. ej. :func:`timeout_note`) cierra el texto.
    """

    result = products or {}
    blocks = [
        *(_quote_block(item) for item in _quotes(result)),
        _options_block(result),
        _sites_block(result),
        _contacts_block(result),
    ]
    commercial = [block for block in blocks if block]
    fragments = _fragments_block(sources, skip_commercial=bool(commercial))
    sections = [*commercial, *([fragments] if fragments else [])] or [RETRY_INVITATION]
    return "\n\n".join([FALLBACK_NOTICE, *sections, *([note] if note else [])])


def timeout_note(missing: int = 0) -> str:
    """Nota del plazo vencido: lo obtenido es parcial y, si las hay, cuántas consultas faltaron.

    ``missing`` cuenta las llamadas que el modelo pidió y no se ejecutaron
    (``ToolAgentState.missing``); no se nombran porque llevan nombres internos (FR-018).
    """

    if missing <= 0:
        return TIMEOUT_NOTE
    if missing == 1:
        return f"{TIMEOUT_NOTE} Quedó 1 consulta sin hacer."
    return f"{TIMEOUT_NOTE} Quedaron {missing} consultas sin hacer."


# --- cotización -------------------------------------------------------------------------


def _quotes(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Cotizaciones emitidas (``kind == "cotizacion"`` con producto principal)."""

    listed = result.get("quotes")
    candidates = listed if isinstance(listed, list) and listed else [result.get("quote")]
    return [
        item
        for item in candidates
        if isinstance(item, Mapping)
        and item.get("kind") == "cotizacion"
        and isinstance(item.get("primary"), Mapping)
    ]


def quote_text(quote: Mapping[str, Any]) -> str:
    """Bloque de texto de una cotización emitida; ``""`` si no es una cotización.

    Lo usan los canales sin tarjetas HTML (WhatsApp por MCP) para mostrar la misma
    cotización que pinta la web, con su folio, a partir del payload guardado en ``quotes``.
    """

    if quote.get("kind") != "cotizacion" or not isinstance(quote.get("primary"), Mapping):
        return ""
    return _quote_block(quote)


def _quote_block(quote: Mapping[str, Any]) -> str:
    primary: Mapping[str, Any] = quote["primary"]
    presentation = _mapping(primary.get("presentation"))
    commercial = _mapping(quote.get("commercial"))
    currency = str(primary.get("currency") or commercial.get("currency") or "COP")
    city = quote.get("city")
    title = f"Cotización en {city}" if city else "Cotización"
    issued = _mapping(quote.get("quote"))
    folio = issued.get("folio") or issued.get("id")
    if folio:
        title += f" (Folio #{folio})"
    lines = [f"{title}:"]
    if primary.get("name"):
        lines.append(f"- Producto: {primary['name']}")
    basis = presentation.get("basis")
    if basis:
        detail = f"{basis}"
        if presentation.get("units") is not None:
            detail += f" de {_number(presentation['units'])} unidades"
        price = presentation.get("price_per_presentation")
        if price is not None:
            detail += f" a {_money(price, currency)}"
        lines.append(f"- Presentación: {detail}")
    units = _units_line(presentation, primary)
    if units:
        lines.append(f"- Unidades: {units}")
    subtotal = presentation.get("subtotal", commercial.get("subtotal"))
    if subtotal is not None:
        lines.append(f"- Subtotal: {_money(subtotal, currency)}")
    tax = _mapping(primary.get("tax") or commercial.get("tax"))
    tax_status = tax.get("status") or primary.get("tax_status")
    if tax_status:
        tax_text = _TAX.get(str(tax_status), str(tax_status).replace("_", " "))
        if tax.get("rate") is not None:
            tax_text += f" (tasa {tax['rate']})"
        lines.append(f"- IVA: {tax_text}")
    shipping = _shipping(_mapping(commercial.get("shipping")), currency)
    if shipping:
        lines.append(f"- Envío: {shipping}")
    total = quote.get("total")
    lines.append(
        f"- Total: {_money(total, currency)}" if total is not None else "- Total pendiente"
    )
    if primary.get("price_origin"):
        # Precio publicado en otra ciudad: quién lo publica y dónde, para no presentarlo
        # como precio de la ciudad de la cotización.
        lines.append(f"- Origen del precio: {primary['price_origin']}")
    return "\n".join(lines)


def _units_line(presentation: Mapping[str, Any], primary: Mapping[str, Any]) -> str:
    requested = presentation.get("units_requested", primary.get("units_requested"))
    parts: list[str] = []
    if requested is not None:
        parts.append(f"pides {_number(requested)}")
    count, basis = presentation.get("count"), presentation.get("basis")
    if count is not None and basis:
        bought = presentation.get("units_bought")
        text = f"compras {_number(count)} {_plural(str(basis), count)}"
        if bought is not None:
            text += f" ({_number(bought)} unidades)"
        parts.append(text)
    if presentation.get("units_surplus") is not None:
        parts.append(f"sobran {_number(presentation['units_surplus'])}")
    return "; ".join(parts)


def _shipping(shipping: Mapping[str, Any], currency: str) -> str:
    """Estado del envío tal como lo declara el motor; sin costo respaldado no hay cifra."""

    if not shipping:
        return ""
    note = str(shipping.get("note") or "").strip()
    if shipping.get("requested") is None:
        return f"pendiente ({note})" if note else "pendiente"
    if shipping.get("status") == "not_requested" or shipping.get("requested") is False:
        return "no aplica (retiro)"
    if shipping.get("status") == "confirmed":
        if shipping.get("free"):
            return "gratis"
        if shipping.get("cost") is not None:
            return _money(shipping["cost"], currency)
    return f"por confirmar ({note})" if note else "por confirmar"


# --- opciones, sedes y canales ----------------------------------------------------------


def _options_block(result: Mapping[str, Any]) -> str:
    """Hasta tres opciones con material; la ficha del contrato manda, como en las tarjetas."""

    ficha = result.get("ficha")
    options = [ficha] if isinstance(ficha, Mapping) else _list(result.get("options"))
    lines = []
    for option in options[:MAX_ITEMS]:
        name = option.get("name")
        if not name:
            continue
        material = option.get("material")
        label = _MATERIALS.get(str(material), str(material)) if material else ""
        lines.append(f"- {name}" + (f" · {label}" if label else ""))
    return "\n".join(["Opciones del catálogo:", *lines]) if lines else ""


def _sites_block(result: Mapping[str, Any]) -> str:
    """Hasta tres sedes con su contacto (teléfono y dirección publicados)."""

    sites = _list(result.get("sites"))
    channel = _mapping(result.get("channel"))
    if not sites:
        sites = _list(channel.get("distributors"))
    lines = []
    for site in sites[:MAX_ITEMS]:
        name = site.get("name")
        if not name:
            continue
        contact = " · ".join(
            str(value) for value in (site.get("phone"), site.get("address")) if value
        )
        lines.append(f"- {name}: {contact or 'sin contacto publicado'}")
    if not lines:
        return ""
    request = _mapping(result.get("request"))
    city = request.get("city") or channel.get("city") or sites[0].get("city")
    header = f"Dónde comprar en {city}:" if city else "Dónde comprar:"
    return "\n".join([header, *lines])


def _contacts_block(result: Mapping[str, Any]) -> str:
    """Canales oficiales de una necesidad a la medida (``custom_need_result``)."""

    lines = [
        f"- {contact['channel']}: {contact.get('url') or 'sin enlace publicado'}"
        for contact in _list(result.get("contacts"))[:MAX_ITEMS]
        if contact.get("channel")
    ]
    return "\n".join(["Canales de contacto:", *lines]) if lines else ""


# --- fragmentos -------------------------------------------------------------------------


def _fragments_block(
    sources: Iterable[Source | Mapping[str, Any]], *, skip_commercial: bool
) -> str:
    """Hasta tres extractos citados ``[n]`` con su título."""

    lines: list[str] = []
    for position, source in enumerate(sources, start=1):
        if len(lines) == MAX_ITEMS:
            break
        if skip_commercial and _field(source, "section") in _COMMERCIAL_SECTIONS:
            continue
        excerpt = _SPACES.sub(" ", str(_field(source, "excerpt") or "")).strip()
        if not excerpt:
            continue
        if len(excerpt) > MAX_EXCERPT_CHARS:
            excerpt = excerpt[:MAX_EXCERPT_CHARS].rstrip() + "…"
        number = _field(source, "n") or position
        title = _field(source, "title") or _field(source, "slug") or "Fuente"
        lines.append(f"- [{number}] {title}: «{excerpt}»")
    return "\n".join(["Fragmentos de las fuentes:", *lines]) if lines else ""


# --- utilidades -------------------------------------------------------------------------


def _field(source: Source | Mapping[str, Any], name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _plural(word: str, count: Any) -> str:
    """Plural regular en español para la presentación (``paquete`` → ``paquetes``)."""

    if str(count) == "1" or not word or word.endswith("s"):
        return word
    return word + ("s" if word[-1] in "aeiouáéó" else "es")


def _number(value: Any) -> str:
    """Cifra exacta con separador de miles «.» y decimales con «,»; no redondea."""

    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    sign = "-" if amount < 0 else ""
    text = format(abs(amount), "f")
    whole, _, decimals = text.partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    if decimals and decimals.strip("0"):
        return f"{sign}{grouped},{decimals}"
    return f"{sign}{grouped}"


def _money(value: Any, currency: str) -> str:
    return f"{_number(value)} {currency}"
