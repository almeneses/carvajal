"""Herramienta ``consultar_cotizacion`` (spec 045, T080; contrato agent-tools-045.md).

Lee una cotización ya emitida por su folio o, sin folio, las últimas del hilo, tal como se
guardaron: nunca recotiza ni devuelve una cotización nueva (``_Draft.quote`` queda vacío,
así el servicio no sella otro folio).

Reglas (FR-026, principio III, research R11):

- Solo cotizaciones del dueño del turno (``ToolContext.owner_key``, nunca un argumento) y de
  procedencia ``real`` (``QuoteRecord.provenance``, la misma matriz que la lectura autorizada
  de ``apps/api/views/cotizador.py``). Un folio ajeno, inexistente, simulado o ambiguo da el
  mismo ``empty``, sin fuentes ni valores, y la salida no lleva ``procedencia``.
- Sin folio: las reales del hilo, recortadas a las :data:`MAX_LISTED` más recientes en
  Python después de filtrar (el repositorio no acepta ``limit``).
- La vigencia es la que publicó el vendedor (``commercial_valid_until``) comparada con la
  fecha de Bogotá (``_now_bogota``, el mismo reloj de ``cotizar`` y ``fecha_actual``). Si
  el vendedor no la publicó, ``vigente`` es ``None``: no se afirma ni que siga vigente ni
  que venció. La retención de la emisión (``valid_until``) no es una vigencia comercial.
- Fuentes: ``cotizacion:{folio}`` con el bloque de :func:`fallback.quote_text` y una línea
  con cada valor devuelto, literal, más el precio y el envío guardados en el payload.
- ``error`` sin ``quote_lookup`` (modo sin base) o si la base falla; el turno sigue.
"""

import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from pydantic import BaseModel, Field, field_validator

from carvajal.adapters.storage.quotes import QuoteRecord
from carvajal.agent.toolagent.fallback import quote_text
from carvajal.agent.toolagent.tools._base import (
    BOGOTA,
    CORE_LAYER,
    QuoteLookup,
    ToolContext,
    ToolDeps,
    _Draft,
    _is_official,
    _now_bogota,
    _Sources,
)

__all__ = [
    "DESCRIPTION",
    "MAX_LISTED",
    "NOT_FOUND",
    "STATUS",
    "UNAVAILABLE",
    "ConsultarCotizacionArgs",
    "run",
    "status",
]

logger = structlog.get_logger(__name__)

MAX_LISTED = 5
"""Cotizaciones reales del hilo que se devuelven sin folio (las más recientes)."""

_FOLIO = re.compile(r"[0-9a-f]{12}")
_FOLIO_NOISE = re.compile(r"[\s#]+")
_MAX_FOLIO_INPUT = 64

DESCRIPTION = (
    "Lee una cotización ya emitida, por su folio o, sin folio, las últimas de esta "
    "conversación, con su desglose y si sigue en la vigencia del vendedor. Úsala para: "
    "«¿mi cotización sigue vigente?», «muéstrame mi folio». No la uses para: calcular "
    "una cotización nueva o actualizar precios (usa cotizar)."
)

STATUS = "Estoy buscando tu cotización…"

NOT_FOUND = (
    "No encontré esa cotización entre las guardadas de esta persona. No supongas el motivo "
    "ni des valores de cotizaciones; si la persona quiere, ofrece calcular una nueva."
)
"""Única salida ``empty``: igual para folio ajeno, inexistente, simulado o ambiguo."""

UNAVAILABLE = (
    "Las cotizaciones guardadas no están disponibles en este momento. No inventes ni "
    "recalcules sus valores; ofrece intentarlo más tarde o calcular una cotización nueva."
)

_OK = (
    "Cotizaciones guardadas, leídas tal como se emitieron: no se recalcularon. "
    "vigente=true: sigue dentro de la vigencia que publicó el vendedor; vigente=false: esa "
    "vigencia ya terminó, ofrece calcular una nueva; vigente=null: el vendedor no publicó "
    "vigencia, así que no digas que sigue vigente ni que venció: da la fecha del precio y "
    "ofrece una cotización nueva si quiere precios actuales. Cita cada cotización con su n."
)
_MORE = " Hay más cotizaciones en esta conversación; se muestran solo las más recientes."


class ConsultarCotizacionArgs(BaseModel):
    """Folio opcional; el dueño y el hilo salen del estado del grafo, nunca del modelo."""

    folio: str | None = Field(
        default=None,
        description=(
            "Folio de la cotización, el código hexadecimal que aparece tras «Folio #». "
            "Omítelo para ver las últimas cotizaciones de esta conversación."
        ),
    )

    @field_validator("folio", mode="before")
    @classmethod
    def _normalize_folio(cls, value: Any) -> str | None:
        """Quita espacios y «#» y pasa a minúsculas; exige los doce dígitos hexadecimales."""

        if value is None:
            return None
        if not isinstance(value, str) or len(value) > _MAX_FOLIO_INPUT:
            raise ValueError("el folio es un código hexadecimal de doce caracteres")
        folio = _FOLIO_NOISE.sub("", value).lower()
        if not folio:
            return None
        if _FOLIO.fullmatch(folio) is None:
            raise ValueError("el folio es un código hexadecimal de doce caracteres")
        return folio


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado fija: nunca interpola el folio que escribió el modelo."""

    return STATUS


def run(
    args: ConsultarCotizacionArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    """Cotizaciones reales del dueño, con su desglose, vigencia y fuentes."""

    lookup = deps.quote_lookup
    if lookup is None:
        return _Draft("error", UNAVAILABLE)
    owner = context.owner_key
    if not owner:
        # Sin dueño no hay a quién atribuir una cotización: se responde como inexistente.
        return _Draft("empty", NOT_FOUND)
    try:
        candidates = _candidates(lookup, args.folio, owner, context.thread_id)
    except Exception as error:  # la base cae: error declarado, el turno sigue
        logger.warning("tool_agent.quote_lookup_failed", error=type(error).__name__)
        return _Draft("error", UNAVAILABLE)
    real = [
        record
        for record in candidates
        if record.owner_key == owner and record.provenance == "real" and record.folio
    ]
    if not real:
        return _Draft("empty", NOT_FOUND)
    listed = real[-MAX_LISTED:]
    today = _now_bogota(deps).date()
    quotes = []
    for record in listed:
        quote = _quote_data(record, today)
        numbers = sources.add(_sources_for(record, quote, today))
        quotes.append({"n": numbers[0], **quote})
    message = _OK + (_MORE if len(real) > len(listed) else "")
    return _Draft(
        "ok",
        message,
        data={"fecha_consulta": today.isoformat(), "cotizaciones": quotes},
    )


# --- Lectura -------------------------------------------------------------------------------


def _candidates(
    lookup: QuoteLookup, folio: str | None, owner: str, thread_id: str
) -> list[QuoteRecord]:
    """Filas del dueño por folio o del hilo; sin filtrar todavía la procedencia."""

    if folio is not None:
        record = _record(lookup.get(folio, owner))
        return [record] if record is not None and record.folio == folio else []
    if not thread_id:
        return []
    records = [_record(item) for item in lookup.for_conversation(thread_id, owner)]
    return [
        record
        for record in records
        if record is not None and str(record.get("conversation_id") or thread_id) == thread_id
    ]


def _record(item: Any) -> QuoteRecord | None:
    if isinstance(item, QuoteRecord):
        return item
    if isinstance(item, Mapping):
        return QuoteRecord(dict(item))
    return None


# --- Datos ---------------------------------------------------------------------------------


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _issued(record: QuoteRecord) -> str | None:
    """Fecha de emisión en Bogotá; un instante sin zona se guardó en UTC."""

    moment: Any = record.issued_at
    if moment is None:
        moment = _mapping(record.payload.get("quote")).get("issued_at")
        if isinstance(moment, str):
            try:
                moment = datetime.fromisoformat(moment)
            except ValueError:
                return None
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(BOGOTA).date().isoformat()


def _presentation(presentation: Mapping[str, Any]) -> str | None:
    count, basis, units = (presentation.get(key) for key in ("count", "basis", "units"))
    if basis and units is not None and count is not None:
        return f"{count} {basis} de {units} unidades"
    if basis and units is not None:
        return f"{basis} de {units} unidades"
    return None


def _quote_data(record: QuoteRecord, today: date) -> dict[str, Any]:
    """Salida del contrato: solo valores guardados, sin estimar ni convertir."""

    payload = record.payload
    primary = _mapping(payload.get("primary"))
    source = _mapping(record.commercial.get("source"))
    presentation = record.presentation
    shipping = _mapping(record.commercial.get("shipping"))
    valid_until = record.commercial_valid_until
    requested = presentation.get("units_requested", primary.get("units_requested"))
    return {
        "folio": record.folio,
        "emitida": _issued(record),
        "referencia": primary.get("name"),
        "ciudad": payload.get("city"),
        "presentacion": _presentation(presentation),
        "unidades": {"pedidas": requested, "compradas": presentation.get("units_bought")},
        "subtotal": _text(record.subtotal),
        "iva": {"estado": record.tax_status, "tasa": _text(record.tax_rate)},
        "envio": {"estado": shipping.get("status"), "costo": _text(record.shipping_cost)},
        "total": _text(record.total),
        "moneda": record.currency,
        "vendedor": primary.get("seller") or source.get("name"),
        "fecha_precio": primary.get("captured_at") or source.get("captured_at"),
        "vigencia_comercial": None if valid_until is None else valid_until.isoformat(),
        "vigente": None if valid_until is None else today <= valid_until,
    }


# --- Fuentes -------------------------------------------------------------------------------


def _facts(quote: Mapping[str, Any], today: date) -> str:
    """Una línea con cada valor devuelto, literal, para que la compuerta lo respalde."""

    currency = quote["moneda"] or ""
    units = quote["unidades"]
    tax = quote["iva"]
    shipping = quote["envio"]
    parts = [f"Folio #{quote['folio']}"]
    optional = (
        ("emitida el {}", quote["emitida"]),
        ("referencia {}", quote["referencia"]),
        ("ciudad {}", quote["ciudad"]),
        ("presentación {}", quote["presentacion"]),
        ("unidades pedidas {}", units["pedidas"]),
        ("unidades compradas {}", units["compradas"]),
        ("subtotal {} " + currency, quote["subtotal"]),
        ("IVA {}", tax["estado"]),
        ("tasa de IVA {}", tax["tasa"]),
        ("envío {}", shipping["estado"]),
        ("costo de envío {} " + currency, shipping["costo"]),
        ("vendedor {}", quote["vendedor"]),
        ("precio capturado el {}", quote["fecha_precio"]),
    )
    parts.extend(
        template.format(value).strip() for template, value in optional if value not in (None, "")
    )
    total = quote["total"]
    parts.append(f"total {total} {currency}".strip() if total else "total pendiente")
    consulted = f"consultada el {today.isoformat()} en America/Bogota"
    if quote["vigencia_comercial"] is None:
        parts.append(f"sin vigencia publicada por el vendedor ({consulted})")
    else:
        state = "vigente" if quote["vigente"] else "vencida"
        parts.append(
            f"vigencia del vendedor hasta {quote['vigencia_comercial']}: {state} ({consulted})"
        )
    return "Cotización guardada: " + " · ".join(parts) + "."


def _sources_for(
    record: QuoteRecord, quote: Mapping[str, Any], today: date
) -> list[dict[str, Any]]:
    """``cotizacion:{folio}`` primero (su ``n`` es el de la cotización) y las guardadas."""

    payload = record.payload
    primary = _mapping(payload.get("primary"))
    commercial = record.commercial
    source = _mapping(commercial.get("source"))
    folio = quote["folio"]
    url = str(primary.get("source_url") or source.get("url") or "")
    source_type = "oficial" if url and _is_official(url) else "tercero"
    block = quote_text(payload)
    excerpt = "\n".join(part for part in (block, _facts(quote, today)) if part)
    entries: list[dict[str, Any]] = [
        _entry(f"cotizacion:{folio}", folio, excerpt, url, f"Cotización #{folio}", source_type)
    ]
    price = _price_excerpt(record, quote)
    if url and price:
        title = " ".join(str(item) for item in (quote["vendedor"], quote["referencia"]) if item)
        entries.append(
            _entry(f"cotizacion:{folio}:precio", folio, price, url, title or url, source_type)
        )
    shipping = _mapping(commercial.get("shipping"))
    shipping_url = str(shipping.get("source_url") or "")
    if shipping_url:
        note = str(shipping.get("note") or "").strip()
        excerpt = f"Condiciones de envío guardadas en la cotización #{folio}"
        excerpt += f": {note}" if note else "."
        shipping_type = "oficial" if _is_official(shipping_url) else "tercero"
        entries.append(
            _entry(
                f"cotizacion:{folio}:envio",
                folio,
                excerpt,
                shipping_url,
                f"Envío de la cotización #{folio}",
                shipping_type,
            )
        )
    return entries


def _price_excerpt(record: QuoteRecord, quote: Mapping[str, Any]) -> str:
    """Precio publicado que respaldó la cotización, como lo citó ``cotizar`` al emitirla."""

    presentation = record.presentation
    price = presentation.get("price_per_presentation")
    if price is None:
        return ""
    text = f"Precio {price} {quote['moneda'] or ''}".rstrip()
    if presentation.get("basis") and presentation.get("units") is not None:
        text += f" por {presentation['basis']} de {presentation['units']}"
    details = [
        f"IVA {quote['iva']['estado']}" if quote["iva"]["estado"] else "",
        f"publicado por {quote['vendedor']}" if quote["vendedor"] else "",
        f"capturado el {quote['fecha_precio']}" if quote["fecha_precio"] else "",
    ]
    return " · ".join([text, *(item for item in details if item)])


def _entry(
    chunk_id: str, folio: str, excerpt: str, url: str, title: str, source_type: str
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "slug": f"cotizacion-{folio}",
        "section": "cotizador",
        "layer": CORE_LAYER,
        "score": 1.0,
        "excerpt": excerpt,
        "url": url,
        "title": title,
        "source_type": source_type,
    }
