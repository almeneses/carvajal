"""Puente entre un chat de WhatsApp y el mismo turno del chat web.

Cada mensaje entra por :meth:`WhatsAppBridge.consult`, que ejecuta un turno completo del
:class:`~apps.api.chat.service.ChatService` (modo ``tools``, con el filtro de evidencia, la
abstención, el respaldo determinista, el tope de cotizaciones y los folios) y devuelve un
texto listo para WhatsApp.

- **Identidad**: el identificador del chat (número o JID) nunca se guarda en claro. El
  ``owner_key`` es un HMAC-SHA256 con el secreto del despliegue, así cada chat tiene su
  propio hilo, su memoria comercial y su cupo diario, igual que un visitante de la web.
- **Cupo**: se cobra el mismo ``DAILY_REQUESTS_LIMIT`` que en la web, por chat.
- **Cotizaciones**: la web las pinta como tarjetas HTML; aquí se leen de la tabla
  ``quotes`` por folio y se redactan con :func:`whatsapp_quote` (folio, importes, total y
  vigencia). Si hay URL pública, se ofrece el PDF y la palabra «PDF» devuelve un enlace
  firmado a la última cotización del chat, sin pasar por el LLM ni cobrar cupo.
- **Formato**: el Markdown del modelo se pasa al formato de WhatsApp (``*negrita*``,
  ``_cursiva_``, sin encabezados ni enlaces Markdown) de forma determinista, sin LLM.
"""

import hmac
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any, Protocol

import structlog

from apps.api.chat.streaming import ChatEvent
from apps.api.quote_view import QuoteView, quote_view
from carvajal.application.quota import QuotaResult

__all__ = [
    "EMPTY_MESSAGE",
    "LIMIT_REACHED",
    "MAX_CHAT_ID_CHARS",
    "MAX_MESSAGE_CHARS",
    "NEW_CONVERSATION",
    "NO_QUOTE_FOR_PDF",
    "OWNER_PREFIX",
    "PDF_OFFER",
    "PDF_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "TOO_LONG_MESSAGE",
    "ChatTurns",
    "WhatsAppBridge",
    "is_pdf_request",
    "owner_key_for",
    "to_whatsapp",
    "whatsapp_quote",
]

logger = structlog.get_logger(__name__)

# Mismo tope que el formulario del chat web (``apps.api.views.chat.MAX_QUESTION_CHARS``).
MAX_MESSAGE_CHARS = 500
MAX_CHAT_ID_CHARS = 200
MAX_LISTED_SOURCES = 5
OWNER_PREFIX = "wa_"
_DOMAIN = b"carvajal:whatsapp:v1:"

EMPTY_MESSAGE = "Escríbeme tu pregunta y con gusto te ayudo."
TOO_LONG_MESSAGE = (
    f"Tu mensaje es muy largo. Resúmelo en menos de {MAX_MESSAGE_CHARS} caracteres, por favor."
)
LIMIT_REACHED = "Llegaste al límite de consultas de hoy. Vuelve a escribirme mañana."
SERVICE_UNAVAILABLE = (
    "En este momento no puedo consultar la información de Carvajal. Intenta de nuevo en "
    "unos minutos."
)
NEW_CONVERSATION = "Listo, empezamos una conversación nueva. ¿En qué te puedo ayudar?"
PDF_OFFER = "¿Quieres esta cotización en PDF? Responde *PDF* 📄"
NO_QUOTE_FOR_PDF = (
    "Todavía no tengo una cotización en esta conversación. Pídeme una, por ejemplo: "
    "*Cotiza 400 portacomidas espumados J1 en Bogotá*."
)
PDF_UNAVAILABLE = "Por ahora no puedo generar el PDF. Intenta de nuevo más tarde."


class ChatTurns(Protocol):
    """Lo que el puente usa del servicio de chat (``ChatService`` lo cumple)."""

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        """Emite el turno como eventos y lo persiste."""

        ...

    def new_conversation(self, owner_key: str) -> Any:
        """Crea un hilo nuevo y lo deja activo para el dueño."""

        ...


QuoteLookup = Callable[[str, str], Mapping[str, Any] | None]
"""``(folio, owner_key) -> payload`` de la cotización guardada, o ``None``."""

LatestQuote = Callable[[str], Mapping[str, Any] | None]
"""``owner_key -> payload`` de la última cotización del chat, o ``None``."""

PdfLink = Callable[[str, str, date | None], str]
"""``(folio, owner_key, válida_hasta) -> URL`` firmada del PDF."""

Quota = Callable[[str], QuotaResult]


def _no_limit(_owner_key: str) -> QuotaResult:
    return QuotaResult(allowed=True)


def owner_key_for(chat_id: str, secret: bytes) -> str:
    """``owner_key`` estable y opaco para un chat de WhatsApp.

    El identificador se normaliza (sin espacios, guiones ni paréntesis, en minúsculas)
    para que ``+57 310 000 0000`` y ``+573100000000`` caigan en el mismo hilo.
    """

    normalized = re.sub(r"[\s\-()]", "", chat_id).lower()
    digest = hmac.new(secret, _DOMAIN + normalized.encode("utf-8"), sha256).hexdigest()
    return f"{OWNER_PREFIX}{digest[:32]}"


# --- Formato de WhatsApp -------------------------------------------------------------------

_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BOLD_UNDERSCORE = re.compile(r"__(?=\S)(.+?)(?<=\S)__")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BULLET = re.compile(r"^(\s*)[*+]\s+", re.MULTILINE)
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$", re.MULTILINE)
_BLANKS = re.compile(r"\n{3,}")


def to_whatsapp(text: str) -> str:
    """Markdown del modelo → formato de WhatsApp, sin tocar cifras ni citas ``[n]``.

    Las viñetas ``*`` pasan a ``-`` antes de convertir la negrita, para que un asterisco
    de lista no se confunda con el de WhatsApp.
    """

    result = _RULE.sub("", text)
    result = _BULLET.sub(r"\1- ", result)
    result = _HEADING.sub(r"*\1*", result)
    result = _BOLD.sub(r"*\1*", result)
    result = _BOLD_UNDERSCORE.sub(r"*\1*", result)
    result = _LINK.sub(r"\1 (\2)", result)
    result = _BLANKS.sub("\n\n", result)
    return result.strip()


_PDF_WORDS = re.compile(r"[a-záéíóúñ]+")
_PDF_FILLER = {
    "si",
    "sí",
    "el",
    "la",
    "en",
    "un",
    "una",
    "de",
    "por",
    "favor",
    "porfa",
    "porfavor",
    "quiero",
    "dame",
    "envia",
    "envía",
    "enviame",
    "envíame",
    "manda",
    "mandame",
    "mándame",
    "genera",
    "generar",
    "generame",
    "genérame",
    "pasa",
    "pasame",
    "pásame",
    "me",
    "lo",
    "cotizacion",
    "cotización",
    "gracias",
    "ok",
    "dale",
    "listo",
    "pdf",
}


def is_pdf_request(message: str) -> bool:
    """«PDF», «sí, el PDF», «envíame la cotización en pdf»…: pedido explícito del PDF.

    Solo cuenta si el mensaje es corto y todas sus palabras son de relleno: «¿el PDF trae
    el IVA?» es una pregunta para el agente, no un pedido del archivo.
    """

    words = _PDF_WORDS.findall(message.lower())
    return "pdf" in words and len(words) <= 8 and all(word in _PDF_FILLER for word in words)


def _units(value: int | None) -> str:
    return "?" if value is None else f"{value:,}".replace(",", ".")


def whatsapp_quote(view: QuoteView) -> str:
    """Bloque de WhatsApp de una cotización emitida, con las cifras del payload."""

    lines = [f"🧾 *Cotización #{view.folio}*" if view.folio else "🧾 *Cotización*"]
    place = " · ".join(part for part in (view.city, view.seller) if part)
    if place:
        lines.append(f"📍 {place}")
    lines += ["", f"*{view.product}*"]
    if view.basis:
        detail = view.basis
        if view.units_per_presentation:
            detail += f" x {_units(view.units_per_presentation)}"
        if view.price_per_presentation:
            detail += f" a {view.price_per_presentation}"
        lines.append(f"• Presentación: {detail}")
    if view.units_requested is not None and view.presentations is not None:
        buy = f"• Pides {_units(view.units_requested)} → compras {_units(view.presentations)}"
        buy += f" {view.presentations_label} ({_units(view.units_bought)} und."
        if view.units_surplus:
            buy += f", sobran {_units(view.units_surplus)}"
        lines.append(buy + ")")
    lines += [
        f"• Subtotal: {view.subtotal or 'pendiente'}",
        f"• IVA: {view.tax.lower()}",
        f"• Envío: {view.shipping.lower() if not view.shipping.startswith('$') else view.shipping}",
        "",
    ]
    if view.total:
        lines.append(f"💰 *Total: {view.total} {view.currency}*")
    else:
        lines.append("⏳ *Total pendiente*")
        lines += [f"_{item}_" for item in view.pending]
    notes = []
    if view.source_name and view.price_date:
        notes.append(f"Precio publicado por {view.source_name} el {view.price_date}")
    if view.valid_until:
        notes.append(f"válida hasta {view.valid_until}")
    if notes:
        lines.append(f"_{' · '.join(notes)}_")
    if view.warning:
        lines.append(f"_⚠️ {view.warning}_")
    return "\n".join(lines)


def _sources_block(sources: Iterable[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for number, source in enumerate(sources, start=1):
        if len(lines) >= MAX_LISTED_SOURCES:
            break
        title = str(source.get("title") or source.get("slug") or "").strip()
        url = str(source.get("url") or "").strip()
        key = url or title
        if not key or key in seen:
            continue
        seen.add(key)
        label = f"[{source.get('n', number)}] {title}" if title else f"[{number}]"
        lines.append(f"{label} — {url}" if url else label)
    if not lines:
        return ""
    return "*Fuentes:*\n" + "\n".join(lines)


@dataclass(frozen=True)
class _Turn:
    text: str
    sources: list[Mapping[str, Any]]
    folios: list[str]
    error: str | None


def _collect(events: Iterable[ChatEvent]) -> _Turn:
    fragments: list[str] = []
    sources: list[Mapping[str, Any]] = []
    folios: list[str] = []
    error: str | None = None
    for event in events:
        if event.name in {"token", "fallback"}:
            fragments.append(str(event.data.get("text", "")))
        elif event.name == "meta":
            raw = event.data.get("sources") or []
            sources = [item for item in raw if isinstance(item, Mapping)]
        elif event.name == "done":
            folios = [str(folio) for folio in event.data.get("quote_ids") or [] if folio]
        elif event.name == "error":
            error = str(event.data.get("message") or SERVICE_UNAVAILABLE)
    return _Turn("".join(fragments), sources, folios, error)


@dataclass(frozen=True)
class WhatsAppBridge:
    """Atiende un mensaje de WhatsApp con un turno del chat web."""

    chat: ChatTurns
    secret: bytes
    quotes: QuoteLookup | None = None
    quota: Quota = _no_limit
    latest_quote: LatestQuote | None = None
    pdf_link: PdfLink | None = None

    def owner_key(self, chat_id: str) -> str:
        """``owner_key`` del chat (ver :func:`owner_key_for`)."""

        return owner_key_for(chat_id, self.secret)

    def consult(self, message: str, chat_id: str, *, new_conversation: bool = False) -> str:
        """Responde ``message`` en el hilo del chat ``chat_id``; nunca lanza.

        Con ``new_conversation`` se abre un hilo nuevo antes del turno (equivale al botón
        «Nueva conversación» de la web); si además no hay mensaje, solo se confirma.
        """

        chat = " ".join(chat_id.split())
        if not chat or len(chat) > MAX_CHAT_ID_CHARS:
            raise ValueError("conversacion debe ser el identificador del chat (1-200 caracteres).")
        text = message.strip()
        if len(text) > MAX_MESSAGE_CHARS:
            return TOO_LONG_MESSAGE
        owner = self.owner_key(chat)
        structlog.contextvars.bind_contextvars(owner_key=owner, channel="whatsapp")
        try:
            if new_conversation:
                self.chat.new_conversation(owner)
                if not text:
                    return NEW_CONVERSATION
            if not text:
                return EMPTY_MESSAGE
            if is_pdf_request(text):
                return self._pdf(owner)
            if not self.quota(owner).allowed:
                logger.info("whatsapp.quota_exhausted")
                return LIMIT_REACHED
            turn = _collect(self.chat.stream(owner, text))
        except Exception as error:  # el canal nunca recibe una traza interna
            logger.warning("whatsapp.turn_failed", error=repr(error))
            return SERVICE_UNAVAILABLE
        finally:
            structlog.contextvars.unbind_contextvars("owner_key", "channel")
        return self._render(turn, owner)

    def _render(self, turn: _Turn, owner: str) -> str:
        if turn.error is not None and not turn.text.strip():
            return SERVICE_UNAVAILABLE
        blocks = [to_whatsapp(turn.text)] if turn.text.strip() else []
        quotes = self._quote_blocks(turn.folios, owner)
        blocks.extend(quotes)
        listed = _sources_block(turn.sources)
        if listed:
            blocks.append(listed)
        if quotes and self.pdf_link is not None and self.latest_quote is not None:
            blocks.append(PDF_OFFER)
        if turn.error is not None:
            # Corte después del primer token: el turno quedó guardado como incompleto.
            blocks.append("_(La respuesta quedó incompleta; vuelve a preguntarme.)_")
        return "\n\n".join(block for block in blocks if block) or SERVICE_UNAVAILABLE

    def _quote_blocks(self, folios: Sequence[str], owner: str) -> list[str]:
        if self.quotes is None:
            return []
        blocks: list[str] = []
        for folio in folios:
            try:
                payload = self.quotes(folio, owner)
            except Exception as error:
                logger.warning("whatsapp.quote_lookup_failed", folio=folio, error=repr(error))
                continue
            view = quote_view(payload) if payload else None
            if view is not None:
                blocks.append(whatsapp_quote(view))
        return blocks

    def _pdf(self, owner: str) -> str:
        """Enlace firmado al PDF de la última cotización del chat (sin LLM ni cupo)."""

        if self.pdf_link is None or self.latest_quote is None:
            return PDF_UNAVAILABLE
        payload = self.latest_quote(owner)
        view = quote_view(payload) if payload else None
        if view is None or not view.folio:
            return NO_QUOTE_FOR_PDF
        url = self.pdf_link(view.folio, owner, view.valid_until_date)
        logger.info("whatsapp.pdf_link", folio=view.folio)
        lines = [f"📄 Aquí está tu cotización #{view.folio} en PDF:", url]
        if view.valid_until:
            lines.append(f"_El enlace funciona hasta el {view.valid_until}._")
        return "\n".join(lines)
