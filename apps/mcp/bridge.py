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
  firmado a la última cotización de la conversación activa, sin pasar por el LLM pero
  consumiendo el mismo cupo diario que cualquier mensaje.
- **Formato**: el Markdown del modelo se pasa al formato de WhatsApp (``*negrita*``,
  ``_cursiva_``, sin encabezados ni enlaces Markdown) de forma determinista, sin LLM.
- **Reintentos**: :meth:`WhatsAppBridge.respond` acepta un ``client_turn_id`` (el webhook
  lo deriva del ``message_id``). Si el dueño ya tiene ese turno guardado, el servicio lo
  reemite sin cobrar cupo, sin abrir hilo nuevo y sin emitir otros folios. Si no, el
  ``client_turn_id`` se reserva de forma atómica (:mod:`carvajal.application.deliveries`)
  antes de abrir hilo, cobrar cupo o llamar al LLM: un reintento que llega con el primer
  turno en curso recibe ``in_progress`` sin efectos, y uno posterior a una respuesta sin
  turno (nueva conversación sin texto, cupo agotado, enlace al PDF, PDF sin URL pública)
  recibe la misma respuesta. Así el webhook también entrega el PDF: un reintento del
  pedido «PDF» no vuelve a cobrar cupo y devuelve el mismo enlace. Si un intento falla
  después de cobrar el cupo o abrir el hilo nuevo, la reserva se libera anotándolo y el
  reintento no los repite.
"""

import hmac
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from typing import Any, Protocol

import structlog

from apps.api.chat.streaming import ChatEvent
from apps.api.quote_view import QuoteView, quote_view
from carvajal.application.deliveries import DeliveryClaim, DeliveryStore, InMemoryDeliveryStore
from carvajal.application.quota import QuotaResult
from carvajal.domain.phone import normalize_e164

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
    "TURN_IN_PROGRESS",
    "BridgeReply",
    "ChatTurns",
    "TurnLookup",
    "WhatsAppBridge",
    "canonical_chat_id",
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
# Para el relé, no para el cliente: el mismo mensaje se está atendiendo en otro intento.
TURN_IN_PROGRESS = "El mensaje se está atendiendo; reintenta en unos segundos."


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

PhotoUrl = Callable[[str], str | None]
"""``sku -> URL`` pública de la foto del catálogo, o ``None`` si no tiene."""

Quota = Callable[[str], QuotaResult]

TurnLookup = Callable[[str, str], str | None]
"""``(owner_key, client_turn_id) -> thread_id`` del turno ya guardado, o ``None``."""


@dataclass(frozen=True)
class BridgeReply:
    """Texto listo para WhatsApp y folios de las cotizaciones emitidas en el turno.

    ``in_progress`` indica que otro intento atiende el mismo ``client_turn_id``: no hay
    nada que enviar al cliente y el relé debe reintentar más tarde.
    """

    text: str
    quote_ids: tuple[str, ...] = ()
    in_progress: bool = False


def _no_limit(_owner_key: str) -> QuotaResult:
    return QuotaResult(allowed=True)


_CHANNEL_PREFIX = "whatsapp:"
# JID de un chat individual (``573100000000@s.whatsapp.net``, con dispositivo ``:12``).
_JID_SUFFIX = re.compile(r"(?::\d+)?@(?:s\.whatsapp\.net|c\.us)$")
_PHONE_SEPARATORS = re.compile(r"[\s\-().]")
_PHONE = re.compile(r"\+?\d{8,15}")


def canonical_chat_id(chat_id: str) -> str:
    """Identificador canónico del chat: el número E.164 si lo es, o el texto normalizado.

    ``+573100000000``, ``+57 310 000 0000``, ``+57.310.000.0000``,
    ``whatsapp:+573100000000``, ``573100000000`` y ``573100000000@s.whatsapp.net`` dan
    ``+573100000000``, el mismo número que normaliza el webhook (:func:`normalize_e164`).
    Lo que no es un número (un JID de grupo, un alias) queda sin espacios, guiones ni
    paréntesis y en minúsculas.
    """

    raw = chat_id.strip().lower()
    if raw.startswith(_CHANNEL_PREFIX):
        raw = raw[len(_CHANNEL_PREFIX) :]
    candidate = _PHONE_SEPARATORS.sub("", _JID_SUFFIX.sub("", raw))
    if _PHONE.fullmatch(candidate):
        try:
            return normalize_e164(candidate if candidate.startswith("+") else f"+{candidate}")
        except ValueError:
            pass
    return re.sub(r"[\s\-()]", "", chat_id).lower()


def owner_key_for(chat_id: str, secret: bytes) -> str:
    """``owner_key`` estable y opaco para un chat de WhatsApp.

    El identificador pasa por :func:`canonical_chat_id`: el mismo número cae en el mismo
    hilo por el webhook (E.164) y por el servidor MCP (número con o sin ``+``, con
    separadores, ``whatsapp:`` o JID).
    """

    normalized = canonical_chat_id(chat_id)
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


def whatsapp_quote(view: QuoteView, photo_url: str | None = None) -> str:
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
    if photo_url:
        lines.append(f"📷 Foto: {photo_url}")
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
    photo_url: PhotoUrl | None = None
    turns: TurnLookup | None = None
    deliveries: DeliveryStore | None = field(default_factory=InMemoryDeliveryStore)

    def owner_key(self, chat_id: str) -> str:
        """``owner_key`` del chat (ver :func:`owner_key_for`)."""

        return owner_key_for(chat_id, self.secret)

    def consult(self, message: str, chat_id: str, *, new_conversation: bool = False) -> str:
        """Responde ``message`` en el hilo del chat ``chat_id``; nunca lanza.

        Con ``new_conversation`` se abre un hilo nuevo antes del turno (equivale al botón
        «Nueva conversación» de la web); si además no hay mensaje, solo se confirma.
        """

        return self.respond(message, chat_id, new_conversation=new_conversation).text

    def respond(
        self,
        message: str,
        chat_id: str,
        *,
        new_conversation: bool = False,
        client_turn_id: str | None = None,
    ) -> BridgeReply:
        """Como :meth:`consult`, con los folios y un ``client_turn_id`` para reintentos.

        Si ``turns`` encuentra ``client_turn_id`` en un hilo del dueño, el turno se reemite
        desde ese hilo: no se abre conversación nueva ni se cobra cupo, y el servicio de
        chat devuelve el mismo texto y los mismos folios. Si no, ``deliveries`` reserva el
        ``client_turn_id`` antes de cualquier efecto: si otro intento lo tiene, se devuelve
        ``in_progress``; si ya se atendió sin dejar turno, la misma respuesta. Un fallo
        libera la reserva. Solo lanza ``ValueError`` si ``chat_id`` no es válido.
        """

        chat = " ".join(chat_id.split())
        if not chat or len(chat) > MAX_CHAT_ID_CHARS:
            raise ValueError("conversacion debe ser el identificador del chat (1-200 caracteres).")
        text = message.strip()
        if len(text) > MAX_MESSAGE_CHARS:
            return BridgeReply(TOO_LONG_MESSAGE)
        owner = self.owner_key(chat)
        structlog.contextvars.bind_contextvars(owner_key=owner, channel="whatsapp")
        try:
            try:
                replayed = self._replay(owner, text, client_turn_id)
                if replayed is not None:
                    return replayed
                claim = self._claim(owner, client_turn_id)
            except Exception as error:  # el canal nunca recibe una traza interna
                logger.warning("whatsapp.turn_failed", error=repr(error))
                return BridgeReply(SERVICE_UNAVAILABLE)
            if claim is not None and claim.status != "acquired":
                return self._claimed_elsewhere(claim, owner, text, client_turn_id)
            effects = _Effects(
                quota=claim is not None and claim.quota_charged,
                thread=claim is not None and claim.thread_opened,
            )
            try:
                reply, streamed, final = self._fresh_turn(
                    owner, text, new_conversation, client_turn_id, effects
                )
            except Exception as error:
                logger.warning("whatsapp.turn_failed", error=repr(error))
                reply, streamed, final = BridgeReply(SERVICE_UNAVAILABLE), False, False
            if claim is not None and client_turn_id:
                self._settle(
                    owner, client_turn_id, reply, claim, effects, streamed=streamed, final=final
                )
            return reply
        finally:
            structlog.contextvars.unbind_contextvars("owner_key", "channel")

    def _replay(self, owner: str, text: str, client_turn_id: str | None) -> BridgeReply | None:
        """Reemite el turno ya guardado, o ``None`` si este ``client_turn_id`` no tiene turno."""

        thread = self._stored_thread(owner, text, client_turn_id)
        if thread is None:
            return None
        logger.info("whatsapp.turn_replayed")
        turn = _collect(
            self.chat.stream(owner, text, client_turn_id=client_turn_id, thread_id=thread)
        )
        return self._render(turn, owner)

    def _stored_thread(self, owner: str, text: str, client_turn_id: str | None) -> str | None:
        """Hilo donde ya está guardado este turno, o ``None`` si es un turno nuevo."""

        if self.turns is None or not client_turn_id or not text:
            return None
        return self.turns(owner, client_turn_id)

    def _claim(self, owner: str, client_turn_id: str | None) -> DeliveryClaim | None:
        """Reserva el ``client_turn_id``; ``None`` si no hay reserva (sin id o sin almacén)."""

        if self.deliveries is None or not client_turn_id:
            return None
        return self.deliveries.claim(owner, client_turn_id)

    def _claimed_elsewhere(
        self, claim: DeliveryClaim, owner: str, text: str, client_turn_id: str | None
    ) -> BridgeReply:
        """Respuesta para un ``client_turn_id`` que ya tomó otro intento."""

        if claim.status == "pending":
            logger.info("whatsapp.turn_in_progress")
            return BridgeReply(TURN_IN_PROGRESS, in_progress=True)
        if claim.reply is not None:
            logger.info("whatsapp.delivery_replayed")
            return BridgeReply(claim.reply)
        # El primer intento guardó su turno justo después de la primera búsqueda.
        try:
            replayed = self._replay(owner, text, client_turn_id)
        except Exception as error:
            logger.warning("whatsapp.turn_failed", error=repr(error))
            return BridgeReply(SERVICE_UNAVAILABLE)
        return replayed if replayed is not None else BridgeReply(SERVICE_UNAVAILABLE)

    def _fresh_turn(
        self,
        owner: str,
        text: str,
        new_conversation: bool,
        client_turn_id: str | None,
        effects: "_Effects | None" = None,
    ) -> tuple[BridgeReply, bool, bool]:
        """Atiende un turno nuevo: ``(respuesta, el servicio lo ejecutó, es definitiva)``.

        ``effects`` trae lo que un intento anterior fallido de este mensaje ya hizo (cupo
        cobrado, hilo nuevo abierto): no se repite, y lo que hace este intento se anota ahí.
        Una respuesta no definitiva (fallo transitorio) libera la reserva para el reintento.
        """

        effects = effects if effects is not None else _Effects()
        if new_conversation and not effects.thread:
            self.chat.new_conversation(owner)
            effects.thread = True
        if new_conversation and not text:
            return BridgeReply(NEW_CONVERSATION), False, True
        if not text:
            return BridgeReply(EMPTY_MESSAGE), False, True
        if not effects.quota:
            if not self.quota(owner).allowed:
                logger.info("whatsapp.quota_exhausted")
                return BridgeReply(LIMIT_REACHED), False, True
            effects.quota = True
        if is_pdf_request(text):
            # Sin turno ni LLM: un reintento recibe el mismo enlace desde la reserva.
            pdf, final = self._pdf(owner)
            return BridgeReply(pdf), False, final
        turn = _collect(self.chat.stream(owner, text, client_turn_id=client_turn_id))
        reply = self._render(turn, owner)
        return reply, True, reply.text != SERVICE_UNAVAILABLE

    def _settle(
        self,
        owner: str,
        client_turn_id: str,
        reply: BridgeReply,
        claim: DeliveryClaim,
        effects: "_Effects",
        *,
        streamed: bool,
        final: bool,
    ) -> None:
        """Cierra la reserva: libera ante un fallo y, si no hubo turno, guarda la respuesta.

        Un turno ejecutado queda en ``messages`` y se reemite desde allí; solo las
        respuestas cortas sin turno se guardan aquí: constantes o el enlace firmado al PDF,
        nunca el texto del cliente. Un fallo transitorio (servicio caído, no se pudo leer la
        última cotización) libera la reserva anotando el cupo cobrado y el hilo abierto; el
        PDF sin URL pública es definitivo y se guarda como cualquier respuesta corta. Solo
        se toca la reserva de este intento (``claim.token``).
        """

        if self.deliveries is None:
            return
        try:
            if not final:
                self.deliveries.release(
                    owner,
                    client_turn_id,
                    claim.token,
                    quota_charged=effects.quota,
                    thread_opened=effects.thread,
                )
            else:
                self.deliveries.finish(
                    owner, client_turn_id, None if streamed else reply.text, claim.token
                )
        except Exception as error:
            # La reserva caduca sola; un turno guardado se sigue reemitiendo desde ``messages``.
            logger.warning("whatsapp.delivery_settle_failed", error=repr(error))

    def _render(self, turn: _Turn, owner: str) -> BridgeReply:
        if turn.error is not None and not turn.text.strip():
            return BridgeReply(SERVICE_UNAVAILABLE)
        blocks = [to_whatsapp(turn.text)] if turn.text.strip() else []
        quotes = self._quote_blocks(turn.folios, owner)
        blocks.extend(quotes)
        listed = _sources_block(turn.sources)
        if listed:
            blocks.append(listed)
        if quotes and self.pdf_link is not None and self._latest_view(owner) is not None:
            blocks.append(PDF_OFFER)
        if turn.error is not None:
            # Corte después del primer token: el turno quedó guardado como incompleto.
            blocks.append("_(La respuesta quedó incompleta; vuelve a preguntarme.)_")
        reply = "\n\n".join(block for block in blocks if block)
        if not reply:
            return BridgeReply(SERVICE_UNAVAILABLE)
        return BridgeReply(reply, tuple(turn.folios))

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
                photo = self._photo(view.sku)
                blocks.append(whatsapp_quote(view, photo))
        return blocks

    def _photo(self, sku: str) -> str | None:
        if self.photo_url is None or not sku:
            return None
        try:
            return self.photo_url(sku)
        except Exception as error:  # una foto nunca tumba la cotización
            logger.warning("whatsapp.photo_lookup_failed", sku=sku, error=repr(error))
            return None

    def _latest_view(self, owner: str) -> QuoteView | None:
        """Última cotización del hilo activo; un fallo de BD no ofrece PDF."""

        if self.latest_quote is None:
            return None
        try:
            payload = self.latest_quote(owner)
        except Exception as error:
            logger.warning("whatsapp.latest_quote_failed", error=repr(error))
            return None
        return quote_view(payload) if payload else None

    def _pdf(self, owner: str) -> tuple[str, bool]:
        """Enlace firmado al PDF de la última cotización del hilo (sin LLM, con cupo).

        Devuelve el texto y si es definitivo: sin URL pública (``pdf_link``) o sin lector
        de cotizaciones el PDF no estará disponible en un reintento (definitivo); un fallo
        al leer la última cotización o al firmar el enlace es transitorio.
        """

        if self.pdf_link is None or self.latest_quote is None:
            return PDF_UNAVAILABLE, True
        try:
            payload = self.latest_quote(owner)
        except Exception as error:
            logger.warning("whatsapp.latest_quote_failed", error=repr(error))
            return PDF_UNAVAILABLE, False
        view = quote_view(payload) if payload else None
        if view is None or not view.folio:
            return NO_QUOTE_FOR_PDF, True
        try:
            url = self.pdf_link(view.folio, owner, view.valid_until_date)
        except Exception as error:
            logger.warning("whatsapp.pdf_link_failed", error=repr(error))
            return PDF_UNAVAILABLE, False
        logger.info("whatsapp.pdf_link", folio=view.folio)
        lines = [f"📄 Aquí está tu cotización #{view.folio} en PDF:", url]
        if view.valid_until:
            lines.append(f"_El enlace funciona hasta el {view.valid_until}._")
        return "\n".join(lines), True


@dataclass
class _Effects:
    """Efectos de un mensaje ya hechos: cupo cobrado y hilo nuevo abierto."""

    quota: bool = False
    thread: bool = False
