"""Puente entre un chat de WhatsApp y el mismo turno del chat web.

Cada mensaje entra por :meth:`WhatsAppBridge.consult`, que ejecuta un turno completo del
:class:`~apps.api.chat.service.ChatService` (modo ``tools``, con el filtro de evidencia, la
abstención, el respaldo determinista, el tope de cotizaciones y los folios) y devuelve un
texto listo para WhatsApp.

- **Identidad**: el identificador del chat (número o JID) nunca se guarda en claro. El
  ``owner_key`` es un HMAC-SHA256 con el secreto del despliegue, así cada chat tiene su
  propio hilo, su memoria comercial y su cupo diario, igual que un visitante de la web.
- **Cupo**: se cobra el mismo ``DAILY_REQUESTS_LIMIT`` que en la web, por chat.
- **Cotizaciones** (spec 054): el servicio arma el mensaje con las plantillas del flujo
  (``carvajal.quotation.render``: opciones, comparativo, carrito con total parcial,
  resumen y registro), iguales en la web; aquí solo se pasan a formato de WhatsApp y, del
  texto del agente, quedan sus comentarios (:func:`quote_remarks`). Al registrarse con el
  «sí», el PDF se adjunta solo; «sí, mándame el PDF» confirma la cotización pendiente. La
  palabra «PDF» sigue devolviendo la última cotización registrada, sin LLM y con cupo.
  Los turnos guardados antes de la 054 se redactan con :func:`whatsapp_quote`.
- **Formato**: el Markdown del modelo se pasa al formato de WhatsApp (``*negrita*``,
  ``_cursiva_``, sin encabezados ni enlaces Markdown) de forma determinista, sin LLM.
- **Voz**: ``consult(..., voice=True)`` devuelve un **resumen hablado** del turno para el
  TTS de la pasarela (:func:`carvajal.speech.spoken_summary`, extractivo y sin LLM): sin
  citas, Markdown, emojis ni URL; fuentes en una frase, unidades y folios en palabras. Si el
  resumen dejó algo fuera (o hay cotización), detrás va la respuesta escrita completa, tras
  :data:`~carvajal.speech.TEXT_HEADER`, para enviarla como mensaje de texto; si no, los
  enlaces (PDF, foto, fuentes) tras :data:`~carvajal.speech.LINKS_HEADER`. El mensaje
  transcrito pasa antes por :func:`carvajal.speech.normalize_transcript` («1,800» →
  «1.800»). Sin ``voice`` la respuesta es la de siempre.
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
import os
import re
import stat
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from typing import Any, Final, Protocol, cast
from urllib.parse import urlparse

import structlog

from apps.api.chat.streaming import ChatEvent
from apps.api.quote_view import QuoteView, quote_view
from carvajal.application.deliveries import DeliveryClaim, DeliveryStore, InMemoryDeliveryStore
from carvajal.application.quota import QuotaResult
from carvajal.domain.phone import normalize_e164
from carvajal.food.photo import MAX_PHOTO_BYTES, PhotoRejected, PreparedPhoto, prepare_photo
from carvajal.food.profile import FoodProfile
from carvajal.food.render import PHOTO_REJECTED, PHOTO_UNAVAILABLE
from carvajal.infrastructure.metrics import WHATSAPP_EVENTS
from carvajal.quotation.render import RECEIPT_PROMISE
from carvajal.speech import (
    SUMMARY_POINTERS,
    SpeechLink,
    SpokenReply,
    combine,
    normalize_transcript,
    speak_folio_tail,
    spoken_sources,
    spoken_summary,
    to_speech,
)

__all__ = [
    "ATTACH_PDF_PREFIX",
    "EMPTY_MESSAGE",
    "LIMIT_REACHED",
    "MAX_CHAT_ID_CHARS",
    "MAX_MESSAGE_CHARS",
    "NEW_CONVERSATION",
    "NO_QUOTE_FOR_PDF",
    "OWNER_PREFIX",
    "PDF_OFFER",
    "PDF_OFFER_SPOKEN",
    "PDF_UNAVAILABLE",
    "PHOTO_REJECTED",
    "PHOTO_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "TOO_LONG_MESSAGE",
    "TURN_IN_PROGRESS",
    "BridgeReply",
    "ChatTurns",
    "TurnLookup",
    "WhatsAppBridge",
    "canonical_chat_id",
    "is_new_conversation_request",
    "is_pdf_request",
    "owner_key_for",
    "quote_remarks",
    "read_inbound_photo",
    "split_pdf_request",
    "spoken_quote",
    "to_whatsapp",
    "whatsapp_quote",
]

logger = structlog.get_logger(__name__)

# Mismo tope que el formulario del chat web (``apps.api.views.chat.MAX_QUESTION_CHARS``).
MAX_MESSAGE_CHARS = 500
MAX_CHAT_ID_CHARS = 200
MAX_LISTED_SOURCES = 5
# Con citas en el texto se listan todas las citadas, con este tope por si el modelo cita de más.
MAX_CITED_SOURCES = 10
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
# Fotos por mensaje del flujo (spec 054): la recomendada o lo recién agregado.
MAX_FLOW_PHOTOS = 3
NEW_CONVERSATION = "Listo, empezamos una conversación nueva. ¿En qué te puedo ayudar?"
PDF_OFFER = "¿Quieres esta cotización en PDF? Responde *PDF* 📄"
PDF_OFFER_SPOKEN = "Si quieres el detalle, respóndeme PDF y te lo mando por escrito."
NO_QUOTE_FOR_PDF = (
    "Todavía no tengo una cotización en esta conversación. Pídeme una, por ejemplo: "
    "*Cotiza 400 portacomidas espumados J1 en Bogotá*."
)
PDF_UNAVAILABLE = "Por ahora no puedo generar el PDF. Intenta de nuevo más tarde."
# Spec 054: con una cotización por confirmar, pedir el PDF es confirmarla. El turno recibe
# esta forma, que el flujo reconoce por reglas, y no la del cliente: «envíame la cotización
# en pdf por favor» o «el pe de efe» dictado no son «sí» para el flujo, y «quiero el pdf de
# la cotización» se leería como agregar un producto.
PDF_CONFIRMATION = "sí, mándame el PDF"
# Para el relé, no para el cliente: el mismo mensaje se está atendiendo en otro intento.
TURN_IN_PROGRESS = "El mensaje se está atendiendo; reintenta en unos segundos."
ATTACH_PDF_PREFIX = "ADJUNTAR_PDF: "
"""Línea que el agente de OpenClaw convierte en un documento adjunto (ver docs)."""


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
# Spec 056: ``(folio, owner_key) -> URL`` del recibo del pedido.
OrderLink = Callable[[str, str], str]
"""``(folio, owner_key, válida_hasta) -> URL`` firmada del PDF."""

PhotoUrl = Callable[[str], str | None]
"""``sku -> URL`` pública de la foto del catálogo, o ``None`` si no tiene."""

Quota = Callable[[str], QuotaResult]

TurnLookup = Callable[[str, str], str | None]
"""``(owner_key, client_turn_id) -> thread_id`` del turno ya guardado, o ``None``."""

PhotoReader = Callable[[str], bytes]
"""``referencia del adjunto -> bytes``; lanza ``ValueError`` si no es un adjunto válido."""


class FoodClassifier(Protocol):
    """Clasificador de fotos (``JevFoodClassifier`` lo cumple)."""

    def unavailable_status(self) -> str | None: ...

    def classify(self, photo: PreparedPhoto) -> tuple[FoodProfile, Any]: ...


class PhotoTurns(Protocol):
    """Turno de foto del servicio de chat (``ChatService.photo_stream``)."""

    def photo_stream(
        self,
        owner_key: str,
        note: str,
        *,
        analyze: Callable[[], FoodProfile],
        charge: Callable[[], str | None],
        precheck: Callable[[], str | None] | None = None,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]: ...


# Nombre que OpenClaw da a los adjuntos (UUID + extensión); nunca una ruta.
_MEDIA_PREFIX = "media://inbound/"
_MEDIA_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:jpe?g|png)", re.IGNORECASE)


def read_inbound_photo(reference: str, directory: str) -> bytes:
    """Lee un adjunto de OpenClaw solo desde ``directory`` (spec 051, FR-024).

    Acepta ``media://inbound/<archivo>`` o ``<archivo>``; rechaza rutas, ``..``, enlaces
    simbólicos, archivos que no son regulares y los de más de 10 MB, sin leer nada fuera de
    la carpeta. Lanza ``ValueError`` con un motivo corto (nunca la ruta).
    """

    name = reference.strip()
    if name.startswith(_MEDIA_PREFIX):
        name = name[len(_MEDIA_PREFIX) :]
    if not directory or not _MEDIA_NAME.fullmatch(name) or ".." in name:
        raise ValueError("referencia no válida")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(os.path.join(directory, name), flags)
    except OSError:
        raise ValueError("adjunto no encontrado") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("no es un archivo")
        if info.st_size > MAX_PHOTO_BYTES:
            raise ValueError("demasiado grande")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read(MAX_PHOTO_BYTES + 1)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class BridgeReply:
    """Texto listo para WhatsApp y folios de las cotizaciones emitidas en el turno.

    ``in_progress`` indica que otro intento atiende el mismo ``client_turn_id``: no hay
    nada que enviar al cliente y el relé debe reintentar más tarde. ``spoken`` es la
    versión para voz del mismo turno (un resumen si ``spoken.omitted``); si falta
    (respuestas cortas o reemitidas desde la reserva), :meth:`voice` la deriva de ``text``.
    """

    text: str
    quote_ids: tuple[str, ...] = ()
    in_progress: bool = False
    spoken: SpokenReply | None = field(default=None, compare=False, repr=False)

    def voice(self) -> SpokenReply:
        """Texto para el TTS y enlaces aparte (ver :mod:`carvajal.speech`)."""

        if self.spoken is not None:
            return self.spoken
        return spoken_summary(self.text, channel="whatsapp")

    def voice_message(self) -> str:
        """La respuesta con ``voz=true``: resumen hablado y, si omitió algo, el texto escrito.

        Ver :meth:`SpokenReply.render <carvajal.speech.SpokenReply.render>`: con resumen, la
        respuesta escrita completa va tras :data:`~carvajal.speech.TEXT_HEADER` (con sus
        enlaces); si no, los enlaces tras :data:`~carvajal.speech.LINKS_HEADER`.
        """

        spoken = self.voice()
        return spoken.render(written=self.text if spoken.omitted else "")


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


# Letras que un transcriptor (STT) puede devolver en cirílico o griego por parecerse a
# las latinas (una «ф» cirílica en «efe»): se leen como su par latino antes de buscar «PDF».
# Se escriben con su código para que el archivo no tenga caracteres ambiguos.
_HOMOGLYPHS = str.maketrans(
    {
        "\u0430": "a",  # a cirílica
        "\u0435": "e",  # ie cirílica
        "\u0451": "e",  # io cirílica
        "\u043e": "o",  # o cirílica
        "\u0440": "p",  # er cirílica
        "\u0441": "c",  # es cirílica
        "\u0434": "d",  # de cirílica
        "\u0444": "f",  # ef cirílica
        "\u043c": "m",  # em cirílica
        "\u0456": "i",  # i cirílica
        "\u03c1": "p",  # rho griega
        "\u03b5": "e",  # épsilon griega
        "\u03c6": "f",  # phi griega
        "\u03b4": "d",  # delta griega
    }
)
# «PDF» escrito: «pdf», «P.D.F.», «p d f» (ya sin tildes ni puntuación).
_PDF_TYPED = re.compile(r"(?<![a-z0-9])p ?d ?f(?![a-z0-9])")
# «PDF» oído por un STT: «pe de efe», «pe de fe», «p de f», «pedefe», «impedefe», «MPDF»,
# «em pe de efe»… Solo letras p, d y f en orden, con «e» opcionales entre ellas: ninguna
# palabra común del español tiene esa forma, así que no se confunde con una frase normal.
_PDF_SPOKEN = re.compile(
    r"(?<![a-z0-9])(?:[ie]? ?m ?)?p ?(?:e ?)?d ?(?:e ?)?(?:e ?)?f(?: ?e)?(?![a-z0-9])"
)
_PDF_WORDS = re.compile(r"[a-z0-9]+")
# Palabras que acompañan el pedido sin cambiarlo (ya sin tildes).
_PDF_FILLER = {
    "si",
    "el",
    "la",
    "en",
    "un",
    "una",
    "de",
    "y",
    "a",
    "por",
    "favor",
    "porfa",
    "porfi",
    "porfavor",
    "quiero",
    "dame",
    "envia",
    "enviame",
    "enviamelo",
    "envialo",
    "manda",
    "mandame",
    "mandamelo",
    "mandalo",
    "genera",
    "generar",
    "generame",
    "pasa",
    "pasame",
    "pasamelo",
    "me",
    "lo",
    "cotizacion",
    "archivo",
    "gracias",
    "ok",
    "okay",
    "vale",
    "bueno",
    "claro",
    "dale",
    "listo",
    "pdf",
}


def _fold(text: str) -> str:
    """Minúsculas, sin tildes, sin homoglifos y con la puntuación como espacio."""

    decomposed = unicodedata.normalize("NFKD", text.lower().translate(_HOMOGLYPHS))
    plain = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character) and unicodedata.category(character) != "Cf"
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.translate(_HOMOGLYPHS)).split())


# Mensajes que solo piden empezar de nuevo. Se comparan enteros, ya plegados (sin tildes,
# mayúsculas ni signos): «empecemos de nuevo, quiero vasos» no es un reinicio sin más.
_NEW_CONVERSATION_COMMANDS = frozenset(
    {
        "new",
        "nuevo",
        "nueva",
        "reset",
        "reiniciar",
        "reinicia",
        "nueva conversacion",
        "conversacion nueva",
        "nuevo chat",
        "chat nuevo",
        "empezar de nuevo",
        "empecemos de nuevo",
        "empieza de nuevo",
        "comenzar de nuevo",
        "comencemos de nuevo",
        "volver a empezar",
        "borrar conversacion",
        "borra la conversacion",
    }
)


def is_new_conversation_request(message: str) -> bool:
    """«/new», «/nuevo», «Nueva conversación», «empecemos de nuevo»: abrir un hilo nuevo.

    Un comando con barra («/new») cuenta siempre; una frase, solo si es todo el mensaje.
    """

    text = message.strip()
    if not text:
        return False
    folded = _fold(text)
    if text.startswith("/"):
        return folded in {"new", "nuevo", "nueva", "reset", "reiniciar"}
    return folded in _NEW_CONVERSATION_COMMANDS - {"new", "nuevo", "nueva", "reset"}


def is_pdf_request(message: str, *, spoken: bool = False) -> bool:
    """«PDF», «sí, el PDF», «envíame la cotización en pdf»…: pedido explícito del PDF.

    Solo cuenta si el mensaje es corto y todas sus palabras son de relleno: «¿el PDF trae
    el IVA?» es una pregunta para el agente, no un pedido del archivo. Tildes, mayúsculas,
    puntos y espacios entre letras no cuentan («P.D.F.», «p d f», «pdF»). Con ``spoken``
    (el mensaje llegó como nota de voz y lo transcribió la pasarela) también valen las
    formas en que un STT suele oír «PDF»: «pe de efe», «impedefe», «MPDF» o «el pe de efe»
    con letras cirílicas.
    """

    folded = _fold(message)
    # Cada sustitución deja espacios dobles; se colapsan para que la siguiente vea «m pdf»
    # (de «M P D F» o «em p d f») y no «m  pdf», que no casaría.
    folded = " ".join(_PDF_TYPED.sub(" pdf ", folded).split())
    if spoken:
        folded = " ".join(_PDF_SPOKEN.sub(" pdf ", folded).split())
    words = _PDF_WORDS.findall(folded)
    return "pdf" in words and len(words) <= 8 and all(word in _PDF_FILLER for word in words)


# «… en Bogotá y genérame el PDF», «…, y mándalo en pdf»: cláusula final con «pdf».
_PDF_TAIL = re.compile(
    r"(?:\s*[,;.]\s*|\s+)(?:y|e)\s+[^,;.\n]*\bp\.?\s?d\.?\s?f\b[^,;.\n]*[.!?¡¿]*\s*$",
    re.IGNORECASE,
)
# «cotiza en PDF 400 vasos», «cotízame con pdf…»: «en/con (el) pdf» dentro del pedido.
_PDF_INLINE = re.compile(r"\s*\b(?:en|con)\s+(?:un\s+|el\s+)?pdf\b", re.IGNORECASE)


def split_pdf_request(message: str) -> tuple[str, bool]:
    """``(pedido sin la parte del PDF, si además pidió el PDF)``.

    «Cotiza 400 J1 en Bogotá y genera el PDF» → («Cotiza 400 J1 en Bogotá», True): el
    agente cotiza y el puente adjunta el PDF de esa cotización, sin pedirle al modelo que
    entienda el archivo. Una pregunta sobre el PDF («¿el PDF trae el IVA?») no se toca.
    """

    for pattern in (_PDF_TAIL, _PDF_INLINE):
        cleaned = " ".join(pattern.sub("", message).split())
        if cleaned != " ".join(message.split()) and cleaned:
            return cleaned, True
    return message, False


def _units(value: int | None) -> str:
    return "?" if value is None else f"{value:,}".replace(",", ".")


_BULLET_LINE = re.compile(r"^\s*(?:[-*+•·▪◦]|\d+[.)])\s+")
# Una cifra como la escriben el agente y ``format_money``: «$37.510», «1.234,50», «9 oz».
_NUMBER = re.compile(r"\d(?:[\d.,]*\d)?")
# Las citas «[1]» no son cifras de la cotización.
_CITATION = re.compile(r"\[\d+\]")
# Una línea del bloque dicha por el agente: «Total: …», «*Precio por paquete:* …».
_QUOTE_FIELD = re.compile(
    r"^\W*(?:total|subtotal|iva|env[ií]o|presentaci[oó]n|precio|producto|referencia|sku"
    r"|cantidad|vigencia|ciudad|folio)\b[^:\n]{0,30}:",
    re.IGNORECASE,
)
# Fin de frase: tras «.», «!», «?» o «…» (y una cita «[1]» pegada) viene un espacio.
# «$37.510» no parte la frase porque tras su punto sigue una cifra.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_WORD = re.compile(r"[^\W\d_]+")
# Palabras con las que el agente repite la cotización sin decir nada nuevo («El total queda
# en $37.510», «Te comparto la cotización»), sin tildes. Ninguna niega ni condiciona: «no»,
# «sube», «mínimo» o «incluye» no están, y una frase que las use dice algo propio.
_RESTATEMENT_WORDS = """
a al aqui cada caja cajas comparto con cop cotizacion cotizo cuesta cuestan de del e el
en es esta estan este la las le les lo los mi o para paquete paquetes pesos por precio
precios queda quedan quedaria sale salen se seria serian sku son su sus te tienes total
tu tus u un una und unidad unidades unitario unos unas vale valen y
"""
_RESTATEMENT = frozenset(_RESTATEMENT_WORDS.split())


def _numbers(text: str) -> set[str]:
    """Las cifras de ``text`` sin separadores ni citas: «$37.510 [1]» → {«37510»}."""

    return {re.sub(r"[.,]", "", number) for number in _NUMBER.findall(_CITATION.sub("", text))}


def _words(text: str) -> set[str]:
    """Las palabras de ``text`` en minúsculas y sin tildes: «Envío» → «envio»."""

    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return set(_WORD.findall(plain))


def _repeats_quote(fragment: str, quoted_numbers: set[str], quoted_words: set[str]) -> bool:
    """``True`` si la frase solo repite la cotización, en cifras y en contenido.

    Se queda toda frase que aporte algo: una pregunta («¿Te confirmo por $37.510?»), una
    cifra que el bloque no trae (un plazo, otro precio) o una palabra que ni el bloque ni una
    simple reformulación tienen («sube en enero», «el mínimo es…», «no incluye»). Una línea
    del bloque dicha por el agente («Total: pendiente…») se quita si no trae cifras nuevas.
    """

    if fragment.rstrip().endswith("?"):
        return False
    if not _numbers(fragment) <= quoted_numbers:
        return False
    if _QUOTE_FIELD.match(fragment):
        return True
    return not (_words(_CITATION.sub("", fragment)) - quoted_words - _RESTATEMENT)


def quote_remarks(text: str, quoted: str) -> str:
    """Lo que el agente dijo además de la cotización, sin repetir sus cifras.

    ``quoted`` es lo que el mensaje ya dice de la cotización (sus bloques, el SKU, la marca y
    el material). Frase por frase, en párrafos y en viñetas, se quitan las que solo lo
    repiten: las líneas del bloque («Total: …», «Precio por paquete: …») y las frases cuyas
    cifras y palabras ya están en él («El total queda en $37.510»). Se conserva lo demás tal
    como lo escribió el agente: preguntas, avisos («el precio de $3.751 sube en enero»),
    respuestas («El mínimo es de 10 paquetes»), viñetas como «- No incluye IVA» y cifras
    nuevas («- Entrega en 5 días»). Un encabezado con «:» se queda si sobrevive algo debajo.
    """

    quoted_numbers, quoted_words = _numbers(quoted), _words(quoted)
    kept: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        lines: list[str] = []
        header: str | None = None
        for line in paragraph.splitlines():
            if not line.strip():
                continue
            if line.rstrip().endswith(":") and not _BULLET_LINE.match(line):
                header = line.strip()
                continue
            bullet = _BULLET_LINE.match(line)
            marker = bullet.group(0).strip() + " " if bullet else ""
            body = line[bullet.end() :] if bullet else line
            sentences = [
                sentence
                for sentence in _SENTENCE_END.split(body.strip())
                if not _repeats_quote(sentence, quoted_numbers, quoted_words)
            ]
            if not sentences:
                continue
            if header is not None:
                lines.append(header)
                header = None
            lines.append(marker + " ".join(sentences))
        if lines:
            kept.append("\n".join(lines))
    return "\n\n".join(kept)


def whatsapp_quote(view: QuoteView, photo_url: str | None = None) -> str:
    """Bloque de WhatsApp de una cotización emitida, con las cifras del payload."""

    if view.folio:
        lines = [f"🧾 *Cotización #{view.folio}*"]
    else:
        # Spec 053: sin folio es una propuesta; se registra cuando la persona dice «sí».
        lines = ["🧾 *Propuesta de cotización* (sin folio, por confirmar)"]
    seller = view.seller
    if seller and view.price_city:
        # Precio publicado en otra ciudad: «Cali · EDEXA» haría creer que EDEXA vende en Cali.
        seller += f" (precio de {view.price_city})"
    place = " · ".join(part for part in (view.city, seller) if part)
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
        if view.converted:
            notes.append(f"Precio de referencia del {view.price_date}")
        else:
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


# Una advertencia más larga que esto no se dice: queda en el texto y en el PDF.
_SPOKEN_WARNING_CHARS = 80


def _spoken_tax(tax: str) -> str:
    """«Incluido (19 %)» → «con IVA incluido»; «No incluido» → «sin incluir IVA»."""

    lowered = tax.lower()
    if lowered.startswith("incluido"):
        return " con IVA incluido"
    if lowered.startswith("no incluido"):
        return " sin incluir IVA"
    return ""


def _spoken_shipping(shipping: str, currency: str) -> str:
    """El envío en una frase, con la moneda de la cotización (no siempre pesos)."""

    if shipping.startswith("$"):
        return f"El envío cuesta {shipping} {currency}."
    if shipping[:1].isdigit():
        # Moneda sin signo: ``format_money`` ya la escribe detrás («1.234 EUR»).
        return f"El envío cuesta {shipping}."
    lowered = shipping.lower()
    if lowered == "gratis":
        return "El envío es gratis."
    if lowered.startswith("por confirmar"):
        return "El envío se confirma con el vendedor."
    return ""


def spoken_quote(view: QuoteView, photo_url: str | None = None) -> SpokenReply:
    """La misma cotización que :func:`whatsapp_quote`, en versión corta para la voz (~15 s).

    Dice solo lo esencial con las cifras exactas del payload: cuántas presentaciones y de
    cuántas unidades, el producto, la ciudad, el total con el IVA y el envío. La
    presentación con su precio, el subtotal, el vendedor, las fechas y el folio quedan en el
    texto de WhatsApp y en el PDF. :func:`~carvajal.speech.to_speech` lee importes y
    unidades. La foto no se lee: va como enlace aparte.
    """

    product = view.product
    count, label, per = (
        view.presentations,
        view.presentations_label.lower(),
        view.units_per_presentation,
    )
    if count is not None and label and per:
        verb = "Es" if count == 1 else "Son"
        head = f"{verb} {_units(count)} {label} de {_units(per)} unidades de {product}"
    elif count is not None and label:
        head = f"{'Es' if count == 1 else 'Son'} {_units(count)} {label} de {product}"
    elif view.units_requested is not None:
        head = f"Son {_units(view.units_requested)} unidades de {product}"
    else:
        head = f"Es una cotización de {product}"
    if view.city:
        head += f" en {view.city}"
    sentences: list[str] = []
    if view.total:
        sentences.append(
            f"{head}, por un total de {view.total} {view.currency}{_spoken_tax(view.tax)}."
        )
    else:
        sentences.append(f"{head}.")
        pending = f": {view.pending[0].rstrip('.')}" if view.pending else ""
        sentences.append(f"El total está pendiente{pending}.")
    if view.price_city:
        # Precio publicado en otra ciudad: el rótulo va en la advertencia, que es larga y la
        # voz no lee; sin esta frase, el precio de Bogotá sonaría como el de la ciudad pedida.
        who = f"que publica {view.seller}" if view.seller else "publicado"
        sentences.append(f"Es el precio {who} en {view.price_city}.")
    shipping = _spoken_shipping(view.shipping, view.currency)
    if shipping:
        sentences.append(shipping)
    if view.warning and len(view.warning) <= _SPOKEN_WARNING_CHARS:
        sentences.append(f"Ojo: {view.warning.rstrip('.')}.")
    spoken = to_speech(" ".join(sentences))
    links = (SpeechLink(f"Foto de {view.product}", photo_url),) if photo_url else ()
    return SpokenReply(spoken.text, spoken.links + links)


_CITE: Final = re.compile(r"\[(\d{1,3})\]")


def _numbered(sources: Iterable[Mapping[str, Any]]) -> list[tuple[int, Mapping[str, Any]]]:
    """Fuentes con su número de cita (``n``, o su posición si no lo trae)."""

    numbered: list[tuple[int, Mapping[str, Any]]] = []
    for position, source in enumerate(sources, start=1):
        try:
            number = int(source.get("n", position))
        except (TypeError, ValueError):
            number = position
        numbered.append((number, source))
    return numbered


def _selected(
    sources: Iterable[Mapping[str, Any]], text: str = ""
) -> list[tuple[list[int], str, str]]:
    """``(números, título, url)`` de las fuentes que se listan bajo el texto.

    Si el texto cita ``[n]``, se listan exactamente esas, cada una con su número: antes se
    listaban las cinco primeras sin repetir URL, y la cita [2] de la segunda sede (misma URL
    del directorio) o las [7] y [8] de la historia quedaban sin fuente. Sin citas, las
    :data:`MAX_LISTED_SOURCES` primeras. Dos citas con el mismo título y URL van juntas.
    """

    numbered = _numbered(sources)
    cited = {int(n) for n in _CITE.findall(text or "")}
    if cited:
        chosen = sorted(((n, s) for n, s in numbered if n in cited), key=lambda pair: pair[0])
        if chosen:
            numbered = list({n: (n, s) for n, s in chosen}.values())[:MAX_CITED_SOURCES]
        else:
            numbered = numbered[:MAX_LISTED_SOURCES]
    rows: list[tuple[list[int], str, str]] = []
    index: dict[tuple[str, str], int] = {}
    for number, source in numbered:
        url = str(source.get("url") or "").strip()
        title = _source_label(source, url)
        key = (title, url)
        if not (title or url):
            continue
        if key in index:
            rows[index[key]][0].append(number)
            continue
        if not cited and len(rows) >= MAX_LISTED_SOURCES:
            break
        index[key] = len(rows)
        rows.append(([number], title, url))
    return rows


def drop_orphan_citations(text: str, sources: Iterable[Mapping[str, Any]]) -> str:
    """Quita las citas ``[n]`` del texto que no tienen fuente en el turno («[11]» sin lista)."""

    known = {number for number, _ in _numbered(sources)}
    if not known:
        return text
    cleaned = _CITE.sub(lambda m: m.group(0) if int(m.group(1)) in known else "", text)
    if cleaned == text:
        return text
    # Sin dobles espacios ni un espacio antes de la puntuación donde estaba la cita.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return re.sub(r"[ \t]+([.,;:)])", r"\1", cleaned)


def _listed_sources(sources: Iterable[Mapping[str, Any]], text: str = "") -> list[tuple[str, str]]:
    """Pares ``(título, url)`` de las fuentes que lista :func:`_sources_block`."""

    return [(title, url) for _numbers, title, url in _selected(sources, text)]


def _spoken_sources(sources: Iterable[Mapping[str, Any]], text: str = "") -> SpokenReply:
    """Las fuentes de :func:`_sources_block` en una frase; sus URL, como enlaces aparte."""

    listed = _listed_sources(sources, text)
    links = tuple(SpeechLink(title or "Fuente", url) for title, url in listed if url)
    return SpokenReply(spoken_sources(listed), links)


_HASH_LIKE = re.compile(r"^[0-9a-f]{16,}$")


def _source_label(source: Mapping[str, Any], url: str) -> str:
    """Nombre legible de la fuente: título, sección o dominio; nunca un hash interno."""

    for key in ("title", "section", "slug"):
        value = str(source.get(key) or "").strip()
        if value and not _HASH_LIKE.match(value.lower()):
            return value
    if url:
        return urlparse(url).netloc.removeprefix("www.") or url
    return "Documento del corpus de Carvajal"


def _sources_block(sources: Iterable[Mapping[str, Any]], text: str = "") -> str:
    lines = []
    for numbers, title, url in _selected(sources, text):
        label = "".join(f"[{n}]" for n in numbers)
        label = f"{label} {title}" if title else label
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
    # Spec 054: mensaje de la plantilla del flujo (texto y versión hablada), si lo hubo.
    flow: Mapping[str, Any] | None = None


def _collect(events: Iterable[ChatEvent]) -> _Turn:
    fragments: list[str] = []
    sources: list[Mapping[str, Any]] = []
    folios: list[str] = []
    flow: Mapping[str, Any] | None = None
    error: str | None = None
    for event in events:
        if event.name == "token":
            fragments.append(str(event.data.get("text", "")))
        elif event.name == "fallback":
            # Como en la web (``chat-stream.js``): el fallback reemplaza lo recibido.
            fragments = [str(event.data.get("text", ""))]
        elif event.name == "meta":
            raw = event.data.get("sources") or []
            sources = [item for item in raw if isinstance(item, Mapping)]
        elif event.name == "done":
            folios = [str(folio) for folio in event.data.get("quote_ids") or [] if folio]
            raw = event.data.get("flow")
            flow = raw if isinstance(raw, Mapping) and raw.get("text") else None
        elif event.name == "error":
            error = str(event.data.get("message") or SERVICE_UNAVAILABLE)
    return _Turn("".join(fragments), sources, folios, error, flow)


def _count_message(voice: bool) -> None:
    """Spec 055: un mensaje que el puente atiende por primera vez (solo conteos, sin texto)."""

    WHATSAPP_EVENTS.count("mensajes")
    if voice:
        WHATSAPP_EVENTS.count("notas_de_voz")


@dataclass(frozen=True)
class WhatsAppBridge:
    """Atiende un mensaje de WhatsApp con un turno del chat web."""

    chat: ChatTurns
    secret: bytes
    quotes: QuoteLookup | None = None
    quota: Quota = _no_limit
    # Mira el cupo sin reservarlo: una foto no va a Jev si el cupo ya se agotó (spec 051).
    quota_peek: Quota = _no_limit
    latest_quote: LatestQuote | None = None
    pdf_link: PdfLink | None = None
    pdf_attachment: PdfLink | None = None
    # Spec 056: recibo del pedido (``folio, owner_key -> URL``) adjunto o como enlace.
    order_attachment: OrderLink | None = None
    order_link: OrderLink | None = None
    photo_url: PhotoUrl | None = None
    turns: TurnLookup | None = None
    deliveries: DeliveryStore | None = field(default_factory=InMemoryDeliveryStore)
    # Spec 051: adjuntos de OpenClaw y clasificador de fotos; sin ellos, no hay fotos.
    photo_reader: PhotoReader | None = None
    food_classifier: FoodClassifier | None = None

    def owner_key(self, chat_id: str) -> str:
        """``owner_key`` del chat (ver :func:`owner_key_for`)."""

        return owner_key_for(chat_id, self.secret)

    def consult(
        self,
        message: str,
        chat_id: str,
        *,
        new_conversation: bool = False,
        voice: bool = False,
        photo: str = "",
    ) -> str:
        """Responde ``message`` en el hilo del chat ``chat_id``; nunca lanza.

        Con ``new_conversation`` se abre un hilo nuevo antes del turno (equivale al botón
        «Nueva conversación» de la web); si además no hay mensaje, solo se confirma. Con
        ``voice`` la respuesta es un resumen apto para leerse en voz alta y lo escrito va
        aparte (:meth:`BridgeReply.voice_message`). Con ``photo`` (spec 051), el adjunto de
        OpenClaw se analiza y ``message`` es el texto opcional que lo acompaña; ``voice``
        aplica igual (OpenClaw puede juntar una nota de voz y una foto en el mismo turno).
        """

        if photo.strip():
            reply = self.photo(
                message, chat_id, photo, new_conversation=new_conversation, voice=voice
            )
            return reply.voice_message() if voice else reply.text
        reply = self.respond(message, chat_id, new_conversation=new_conversation, voice=voice)
        return reply.voice_message() if voice else reply.text

    def respond(
        self,
        message: str,
        chat_id: str,
        *,
        new_conversation: bool = False,
        client_turn_id: str | None = None,
        voice: bool = False,
    ) -> BridgeReply:
        """Como :meth:`consult`, con los folios y un ``client_turn_id`` para reintentos.

        ``voice`` indica que el mensaje llegó como nota de voz: la transcripción pasa por
        :func:`~carvajal.speech.normalize_transcript` (miles al estilo colombiano) y el
        pedido del PDF acepta además lo que un STT suele oír por «PDF»
        (:func:`is_pdf_request`). La respuesta es la misma; quien la envía elige el texto o
        :meth:`BridgeReply.voice_message`.

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
        if voice:
            text = normalize_transcript(text)
        if is_new_conversation_request(text):
            # «/new», «empezar de nuevo»…: se abre un hilo nuevo aunque el agente de
            # OpenClaw no pase ``nueva=true`` (lo reenvía como texto o lo olvida).
            new_conversation, text = True, ""
        if len(text) > MAX_MESSAGE_CHARS:
            return BridgeReply(TOO_LONG_MESSAGE)
        owner = self.owner_key(chat)
        structlog.contextvars.bind_contextvars(owner_key=owner, channel="whatsapp")
        try:
            try:
                replayed = self._replay(owner, text, client_turn_id)
                if replayed is not None:
                    # Spec 055: un reintento de OpenClaw no es un mensaje nuevo en /metricas.
                    WHATSAPP_EVENTS.count("reintentos")
                    return replayed
                claim = self._claim(owner, client_turn_id)
            except Exception as error:  # el canal nunca recibe una traza interna
                logger.warning("whatsapp.turn_failed", error=repr(error))
                _count_message(voice)
                WHATSAPP_EVENTS.count("errores")
                return BridgeReply(SERVICE_UNAVAILABLE)
            if claim is not None and claim.status != "acquired":
                WHATSAPP_EVENTS.count("reintentos")
                return self._claimed_elsewhere(claim, owner, text, client_turn_id)
            _count_message(voice)
            effects = _Effects(
                quota=claim is not None and claim.quota_charged,
                thread=claim is not None and claim.thread_opened,
            )
            try:
                reply, streamed, final = self._fresh_turn(
                    owner, text, new_conversation, client_turn_id, effects, voice=voice
                )
            except Exception as error:
                logger.warning("whatsapp.turn_failed", error=repr(error))
                WHATSAPP_EVENTS.count("errores")
                reply, streamed, final = BridgeReply(SERVICE_UNAVAILABLE), False, False
            if claim is not None and client_turn_id:
                self._settle(
                    owner, client_turn_id, reply, claim, effects, streamed=streamed, final=final
                )
            return reply
        finally:
            structlog.contextvars.unbind_contextvars("owner_key", "channel")

    def photo(
        self,
        message: str,
        chat_id: str,
        photo: str,
        *,
        new_conversation: bool = False,
        voice: bool = False,
    ) -> BridgeReply:
        """Turno de una foto de alimento (spec 051, US4); nunca lanza.

        Sin carpeta de adjuntos, sin clasificador o con el clasificador caído responde
        :data:`PHOTO_UNAVAILABLE`; un adjunto inválido, :data:`PHOTO_REJECTED`. En ninguno de
        esos casos se cobra cupo: se cobra solo si el clasificador respondió. Con el cupo ya
        agotado responde :data:`LIMIT_REACHED` sin enviar la foto al clasificador. Con
        ``voice``, el texto que acompaña la foto es una transcripción y se normaliza como en
        :meth:`respond`.
        """

        chat = " ".join(chat_id.split())
        if not chat or len(chat) > MAX_CHAT_ID_CHARS:
            raise ValueError("conversacion debe ser el identificador del chat (1-200 caracteres).")
        text = message.strip()
        if voice:
            text = normalize_transcript(text)
        if len(text) > MAX_MESSAGE_CHARS:
            return BridgeReply(TOO_LONG_MESSAGE)
        owner = self.owner_key(chat)
        structlog.contextvars.bind_contextvars(owner_key=owner, channel="whatsapp")
        _count_message(voice)
        WHATSAPP_EVENTS.count("fotos")
        try:
            classifier = self.food_classifier
            turns = self.chat
            if (
                self.photo_reader is None
                or classifier is None
                or classifier.unavailable_status() is not None
                or not hasattr(turns, "photo_stream")
            ):
                logger.info("whatsapp.photo_unavailable")
                WHATSAPP_EVENTS.count("fotos_no_disponibles")
                return BridgeReply(PHOTO_UNAVAILABLE)
            try:
                prepared = prepare_photo(self.photo_reader(photo))
            except PhotoRejected as error:
                logger.info("whatsapp.photo_rejected", reason=error.reason)
                WHATSAPP_EVENTS.count("fotos_rechazadas")
                return BridgeReply(f"{error.message} {PHOTO_REJECTED}")
            except ValueError as error:
                logger.info("whatsapp.photo_rejected", reason=str(error))
                WHATSAPP_EVENTS.count("fotos_rechazadas")
                return BridgeReply(PHOTO_REJECTED)
            # El hilo recién abierto va explícito: no depende de cuál quede como activo.
            created = self.chat.new_conversation(owner) if new_conversation else None
            thread_id = getattr(created, "id", None)

            def analyze() -> FoodProfile:
                profile, _call = classifier.classify(prepared)
                return profile

            def charge() -> str | None:
                try:
                    allowed = self.quota(owner).allowed
                except Exception as error:  # como ``_charger`` en la web: sin cupo no hay turno
                    logger.warning("whatsapp.quota_failed", error_type=type(error).__name__)
                    return SERVICE_UNAVAILABLE
                return None if allowed else LIMIT_REACHED

            def precheck() -> str | None:
                try:
                    allowed = self.quota_peek(owner).allowed
                except Exception as error:  # sin poder mirarlo, decide ``charge``
                    logger.warning("whatsapp.quota_peek_failed", error_type=type(error).__name__)
                    return None
                return None if allowed else LIMIT_REACHED

            events = cast(PhotoTurns, turns).photo_stream(
                owner,
                text,
                analyze=analyze,
                charge=charge,
                precheck=precheck,
                thread_id=thread_id,
            )
            turn = _collect(events)
            if turn.error is not None and not turn.text.strip():
                return BridgeReply(turn.error)
            return self._render(turn, owner)
        except Exception as error:  # el canal nunca recibe una traza interna
            logger.warning("whatsapp.photo_failed", error_type=type(error).__name__)
            WHATSAPP_EVENTS.count("errores")
            return BridgeReply(SERVICE_UNAVAILABLE)
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
            WHATSAPP_EVENTS.count("errores")
            return BridgeReply(SERVICE_UNAVAILABLE)
        return replayed if replayed is not None else BridgeReply(SERVICE_UNAVAILABLE)

    def _fresh_turn(
        self,
        owner: str,
        text: str,
        new_conversation: bool,
        client_turn_id: str | None,
        effects: "_Effects | None" = None,
        *,
        voice: bool = False,
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
                WHATSAPP_EVENTS.count("sin_cupo")
                return BridgeReply(LIMIT_REACHED), False, True
            effects.quota = True
        pending = False
        if is_pdf_request(text, spoken=voice):
            pending = self._awaiting_confirmation(owner)
            if not pending:
                # Sin turno ni LLM: un reintento recibe el mismo enlace desde la reserva.
                pdf, final = self._pdf(owner)
                return pdf, False, final
        # Spec 054: con una cotización por confirmar, pedir el PDF es un «sí»: el turno la
        # registra y el PDF se adjunta solo.
        question, wants_pdf = (PDF_CONFIRMATION, False) if pending else split_pdf_request(text)
        turn = _collect(self.chat.stream(owner, question, client_turn_id=client_turn_id))
        reply = self._render(turn, owner)
        attached = turn.flow is not None and bool(turn.folios)  # el flujo ya adjuntó el PDF
        if wants_pdf and reply.quote_ids and not attached:
            reply = self._with_pdf(reply, owner)
        return reply, True, reply.text != SERVICE_UNAVAILABLE

    def _awaiting_confirmation(self, owner: str) -> bool:
        """El hilo activo tiene una cotización por confirmar (spec 054); ``False`` si falla."""

        check = getattr(self.chat, "awaiting_confirmation", None)
        if not callable(check):
            return False
        try:
            return bool(check(owner))
        except Exception as error:
            logger.warning("whatsapp.awaiting_failed", error=repr(error))
            return False

    def _with_pdf(self, reply: BridgeReply, owner: str) -> BridgeReply:
        """La cotización del turno con su PDF ya entregado, en vez de la oferta."""

        pdf, _final = self._pdf(owner)
        if pdf.text in {PDF_UNAVAILABLE, NO_QUOTE_FOR_PDF}:
            return reply
        text = reply.text.removesuffix(PDF_OFFER).rstrip()
        said = reply.voice()
        without_offer = SpokenReply(
            said.text.replace(PDF_OFFER_SPOKEN, "").strip(), said.links, said.omitted
        )
        spoken = combine([without_offer, pdf.voice()])
        return BridgeReply(f"{text}\n\n{pdf.text}", reply.quote_ids, spoken=spoken)

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
        """El turno en formato de WhatsApp y, en paralelo, su versión para voz."""

        if turn.error is not None and not turn.text.strip():
            return BridgeReply(SERVICE_UNAVAILABLE)
        if turn.flow is not None:
            return self._render_flow(turn, owner)
        issued = self._quote_views(turn.folios, owner)
        quotes = issued
        blocks = [whatsapp_quote(view, photo) for view, photo in quotes]
        # Con cotización, el bloque ya trae las cifras: del texto del agente solo quedan
        # sus comentarios (una nota o una pregunta de seguimiento), después del bloque.
        # El SKU, la marca y el material no se pintan en el bloque, pero nombran el mismo
        # producto que el agente lista («Vaso … Wau (PP), SKU 7704918100199»).
        quoted = "\n".join(
            [*blocks, *(f"{view.sku} {view.brand} {view.material}" for view, _ in quotes)]
        )
        prose = quote_remarks(turn.text, quoted) if quotes else turn.text
        prose = drop_orphan_citations(prose, turn.sources)
        if prose.strip():
            blocks.append(to_whatsapp(prose))
        listed = _sources_block(turn.sources, prose)
        if listed:
            blocks.append(listed)
        pdf_enabled = self.pdf_link is not None or self.pdf_attachment is not None
        # El PDF solo existe para cotizaciones registradas: una propuesta no lo ofrece.
        offer = bool(issued) and pdf_enabled and self._latest_view(owner) is not None
        if offer:
            blocks.append(PDF_OFFER)
        spoken: list[SpokenReply] = []
        if quotes:
            # Con cotización, la voz dice solo la cotización corta: la introducción del
            # agente repite sus cifras y alarga la nota (~30 s). Sus enlaces sí se conservan.
            if turn.text.strip():
                spoken.append(SpokenReply("", to_speech(turn.text).links))
            spoken.extend(spoken_quote(view, photo) for view, photo in quotes)
            if listed:
                spoken.append(_spoken_sources(turn.sources, prose))
            # El folio, la presentación con su precio y el subtotal van por escrito.
            closing = PDF_OFFER_SPOKEN if offer else SUMMARY_POINTERS["whatsapp"]
            spoken.append(SpokenReply(closing, omitted=True))
        elif turn.text.strip():
            # Resumen hablado (~15-20 s); el texto completo se envía por escrito.
            spoken.append(
                spoken_summary(
                    drop_orphan_citations(turn.text, turn.sources),
                    channel="whatsapp",
                    sources=_listed_sources(turn.sources, prose),
                )
            )
        elif listed:
            spoken.append(_spoken_sources(turn.sources, prose))
        if turn.error is not None:
            # Corte después del primer token: el turno quedó guardado como incompleto.
            blocks.append("_(La respuesta quedó incompleta; vuelve a preguntarme.)_")
            spoken.append(SpokenReply("La respuesta quedó incompleta; vuelve a preguntarme."))
        reply = "\n\n".join(block for block in blocks if block)
        if not reply:
            return BridgeReply(SERVICE_UNAVAILABLE)
        voice = combine(spoken)
        # Un turno que solo trae enlaces no deja la nota de voz vacía.
        if not voice.text:
            voice = SpokenReply("Te envío la información por escrito.", voice.links, voice.omitted)
        return BridgeReply(reply, tuple(turn.folios), spoken=voice)

    def _quote_views(self, folios: Sequence[str], owner: str) -> list[tuple[QuoteView, str | None]]:
        """Cotizaciones del turno con su foto; un folio ilegible se omite sin romper el turno."""

        if self.quotes is None:
            return []
        views: list[tuple[QuoteView, str | None]] = []
        for folio in folios:
            try:
                payload = self.quotes(folio, owner)
            except Exception as error:
                logger.warning("whatsapp.quote_lookup_failed", folio=folio, error=repr(error))
                continue
            view = quote_view(payload) if payload else None
            if view is not None:
                views.append((view, self._photo(view.sku)))
        return views

    def _render_flow(self, turn: _Turn, owner: str) -> BridgeReply:
        """Turno del flujo de cotización (spec 054): comentario del agente y la plantilla.

        La plantilla ya trae las cifras: del texto del agente solo quedan sus comentarios
        (una nota o una pregunta que no repite cifras). La voz dice la versión hablada de la
        plantilla (≤ 45 palabras) y el detalle va por escrito. Al registrarse, el PDF se
        adjunta en lugar de ofrecerlo.
        """

        assert turn.flow is not None
        template = str(turn.flow.get("text") or "")
        prose = turn.text.replace(template, "").strip()
        remarks = quote_remarks(prose, template) if prose else ""
        blocks: list[str] = []
        remarks = drop_orphan_citations(remarks, turn.sources)
        if remarks.strip():
            blocks.append(to_whatsapp(remarks))
        listed = _sources_block(turn.sources, remarks)
        if listed:
            blocks.append(listed)
        written = self._with_receipt(to_whatsapp(template), turn.flow, owner)
        pdf_enabled = self.pdf_link is not None or self.pdf_attachment is not None
        if turn.folios and not pdf_enabled:
            written = written.replace("\n📄 Te envío el PDF.", "")
        blocks.append(written)
        # Spec 054 dejó la cotización en plantillas sin la foto que traía ``whatsapp_quote``
        # (2026-11-25): la recomendada o lo recién agregado vuelven a llevar su foto.
        photos = self._flow_photos(turn.flow)
        if photos:
            blocks.append("\n".join(f"📷 Foto: {url}" for _, url in photos))
        spoken: list[SpokenReply] = []
        if remarks.strip():
            spoken.append(SpokenReply(to_speech(remarks).text))
        said = str(turn.flow.get("spoken") or "") or to_speech(template).text
        spoken.append(SpokenReply(said, omitted=True))
        if photos:
            # La foto no se lee: va como enlace aparte de la nota de voz.
            links = tuple(SpeechLink("Foto del producto", url) for _, url in photos)
            spoken.append(SpokenReply("", links))
        if turn.error is not None:
            blocks.append("_(La respuesta quedó incompleta; vuelve a preguntarme.)_")
        voice = combine(spoken)
        reply = BridgeReply("\n\n".join(blocks), tuple(turn.folios), spoken=voice)
        if turn.folios and pdf_enabled:
            return self._attach_pdf(reply, owner)
        return reply

    def _with_receipt(self, written: str, flow: Mapping[str, Any], owner: str) -> str:
        """Pedido confirmado (spec 056): la promesa del recibo → el PDF adjunto o su enlace.

        Sin forma de entregarlo, la promesa se quita: no se promete un PDF que no llega.
        """

        # La promesa ya pasó por ``to_whatsapp``: se busca en ese formato y, por si la
        # conversión la dejara igual o cambiara, también en el original.
        order = flow.get("order")
        promise = next(
            (
                f"\n{form}"
                for form in (to_whatsapp(RECEIPT_PROMISE), RECEIPT_PROMISE)
                if f"\n{form}" in written
            ),
            None,
        )
        if promise is None or not isinstance(order, Mapping):
            return written
        folio = str(order.get("folio") or "")
        name = str(order.get("name") or "")
        try:
            if folio and self.order_attachment is not None:
                url = self.order_attachment(folio, owner)
                logger.info("whatsapp.order_attachment", folio=folio)
                block = (
                    f"\n📄 Te envío el recibo de tu pedido {name} en PDF.\n{ATTACH_PDF_PREFIX}{url}"
                )
                return written.replace(promise, block, 1)
            if folio and self.order_link is not None:
                url = self.order_link(folio, owner)
                logger.info("whatsapp.order_link", folio=folio)
                block = f"\n📄 Recibo de tu pedido {name} en PDF. Ábrelo aquí:\n\n{url}"
                return written.replace(promise, block, 1)
        except Exception as error:
            logger.warning("whatsapp.order_link_failed", error_type=type(error).__name__)
        return written.replace(promise, "", 1)

    def _attach_pdf(self, reply: BridgeReply, owner: str) -> BridgeReply:
        """La cotización recién registrada con su PDF ya entregado (spec 054)."""

        pdf, _final = self._pdf(owner)
        if pdf.text in {PDF_UNAVAILABLE, NO_QUOTE_FOR_PDF}:
            return reply
        text = reply.text.replace("\n📄 Te envío el PDF.", "")
        spoken = combine([reply.voice(), SpokenReply("", pdf.voice().links)])
        return BridgeReply(f"{text}\n\n{pdf.text}", reply.quote_ids, spoken=spoken)

    def _flow_photos(self, flow: Mapping[str, Any]) -> list[tuple[str, str]]:
        """``(sku, url)`` de las fotos del mensaje del flujo; hasta tres y sin repetir."""

        found: list[tuple[str, str]] = []
        for sku in flow.get("photos") or []:
            url = self._photo(str(sku))
            if url and all(url != seen for _, seen in found):
                found.append((str(sku), url))
            if len(found) == MAX_FLOW_PHOTOS:
                break
        return found

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

    def _pdf(self, owner: str) -> tuple[BridgeReply, bool]:
        """Enlace firmado al PDF de la última cotización del hilo (sin LLM, con cupo).

        Devuelve la respuesta y si es definitiva: sin URL pública (``pdf_link``) o sin
        lector de cotizaciones el PDF no estará disponible en un reintento (definitiva); un
        fallo al leer la última cotización o al firmar el enlace es transitorio.
        """

        if (self.pdf_link is None and self.pdf_attachment is None) or self.latest_quote is None:
            return BridgeReply(PDF_UNAVAILABLE), True
        try:
            payload = self.latest_quote(owner)
        except Exception as error:
            logger.warning("whatsapp.latest_quote_failed", error=repr(error))
            return BridgeReply(PDF_UNAVAILABLE), False
        view = quote_view(payload) if payload else None
        if view is None or not view.folio:
            return BridgeReply(NO_QUOTE_FOR_PDF), True
        until = f" (válida hasta el {view.valid_until})" if view.valid_until else ""
        # En voz, solo el final del folio; el PDF y su vigencia van por escrito.
        said = (
            "Te mando por escrito el PDF de tu cotización terminada en "
            f"{speak_folio_tail(view.folio)}."
        )
        try:
            if self.pdf_attachment is not None:
                # OpenClaw descarga la URL desde el servidor y la envía como documento:
                # no depende de que el navegador del cliente abra un enlace http.
                url = self.pdf_attachment(view.folio, owner, view.valid_until_date)
                intro = f"📄 Te envío tu cotización #{view.folio} en PDF{until}."
                text = f"{intro}\n{ATTACH_PDF_PREFIX}{url}"
                logger.info("whatsapp.pdf_attachment", folio=view.folio)
                WHATSAPP_EVENTS.count("pdf_enviados")
                return BridgeReply(text, spoken=SpokenReply(said)), True
            assert self.pdf_link is not None
            url = self.pdf_link(view.folio, owner, view.valid_until_date)
        except Exception as error:
            logger.warning("whatsapp.pdf_link_failed", error=repr(error))
            return BridgeReply(PDF_UNAVAILABLE), False
        logger.info("whatsapp.pdf_link", folio=view.folio)
        WHATSAPP_EVENTS.count("pdf_enviados")
        # El enlace va solo y al final, sin formato alrededor: así WhatsApp lo detecta
        # entero y se puede tocar desde el celular.
        text = f"📄 Tu cotización #{view.folio} en PDF{until}. Ábrela aquí:\n\n{url}"
        link = SpeechLink(f"Cotización {view.folio} en PDF", url)
        return BridgeReply(text, spoken=SpokenReply(said, (link,))), True


@dataclass
class _Effects:
    """Efectos de un mensaje ya hechos: cupo cobrado y hilo nuevo abierto."""

    quota: bool = False
    thread: bool = False
