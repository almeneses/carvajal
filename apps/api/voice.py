"""Estado de la voz del chat web por el servidor (spec 047): cliente, límites y caché.

Un :class:`VoiceRuntime` por aplicación (``app.state.voice``), construido al primer uso:

- el cliente de OpenAI (:class:`~carvajal.adapters.speech.OpenAISpeechClient`), con su tope
  diario y su circuito por proceso;
- tres límites con ventana deslizante, comunes a dictado y lectura: por sesión del chat
  (``owner_key``, ``VOICE_RATE_LIMIT`` cada ``VOICE_RATE_WINDOW_S`` segundos), por IP
  (``VOICE_IP_RATE_LIMIT`` en la misma ventana) y global por proceso
  (``VOICE_GLOBAL_RATE_PER_MIN`` por minuto). La sesión la puede fabricar el cliente (la
  cookie de visitante no la emite el servidor), así que quien rota identidades choca con
  el límite por IP y, si además rota IP, con el global: no agota el tope diario de golpe;
- una caché pequeña en memoria del audio de las respuestas ya leídas, por id de mensaje,
  para que pulsar «Escuchar» dos veces no pague dos síntesis. Nunca guarda el audio del
  usuario.

:func:`spoken_message` prepara el texto que se sintetiza a partir de una respuesta
guardada: un resumen hablado (:func:`carvajal.speech.spoken_summary`, extractivo y sin LLM,
unos 15-20 s), la cotización corta y las fuentes en una frase. El mismo texto se publica en
el hilo (``data-voz-resumen``) para la lectura de respaldo del navegador.

Los turnos del pedido (spec 056) se leen con la versión hablada de su plantilla
(``flow.spoken``), como en WhatsApp. Lo guardado no lleva el nombre ni el correo de quien
compra, así que el turno en vivo pasa su ``flow`` completo (el del evento ``done``) y su
resumen se recuerda unos minutos en memoria (:class:`LiveSpoken`) para que
``/api/voz/hablar`` lea esos datos en ese turno; después, o en el historial, se lee la
versión guardada («el correo que escribiste»).
"""

import hashlib
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from apps.api.chat.products import has_cards
from apps.api.quote_view import quote_view
from apps.mcp.bridge import spoken_quote
from carvajal.adapters.speech import OpenAISpeechClient, SynthesizedAudio, build_speech
from carvajal.adapters.storage.chat import ChatMessage
from carvajal.infrastructure.settings import Settings
from carvajal.quotation.flow import ORDER_AWAITING
from carvajal.speech import (
    SUMMARY_POINTERS,
    SpokenReply,
    combine,
    spoken_sources,
    spoken_summary,
    to_speech,
)

__all__ = [
    "CACHE_ENTRIES",
    "LIVE_SPOKEN_TTL_S",
    "TRUNCATED_NOTE",
    "LiveSpoken",
    "RateLimiter",
    "VoiceRuntime",
    "build_voice_runtime",
    "cap_text",
    "live_spoken",
    "spoken_message",
]

CACHE_ENTRIES: Final = 32
# Claves que recuerda cada límite; al pasarse se olvida la menos reciente (LRU).
LIMITER_MAX_KEYS: Final = 4096
GLOBAL_KEY: Final = "global"
TRUNCATED_NOTE: Final = "El resto de la respuesta está en pantalla."
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")
# Tarjetas de referencias que se nombran en la voz: más, o con nombres largos, solo se cuentan.
_CARD_NAMES: Final = 4
_CARD_NAME_WORDS: Final = 8
_COUNT_WORDS: Final = {2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis", 7: "siete"}
# Resumen del turno en vivo del pedido (con nombre y correo): cuánto vive y cuántos caben.
LIVE_SPOKEN_TTL_S: Final = 900.0
LIVE_SPOKEN_ENTRIES: Final = 256
# La versión guardada de la confirmación sin datos («a nombre de (tu nombre), con el correo
# el correo que escribiste»), dicha de forma natural.
_NEUTRAL_SPOKEN: Final = (
    ("a nombre de (tu nombre)", "a tu nombre"),
    ("con el correo el correo que escribiste", "con el correo que escribiste"),
    (", (tu nombre).", "."),
    ("(tu nombre)", "tu nombre"),
)
_LIVE_LOCK = threading.Lock()


class RateLimiter:
    """Ventana deslizante por clave: ``hit`` devuelve 0 si pasa o los segundos de espera.

    Guarda como mucho ``max_keys`` claves y olvida la menos reciente (LRU): una avalancha de
    claves inventadas no hace crecer la memoria ni el coste de cada ``hit``, que es O(1)
    amortizado.
    """

    def __init__(
        self,
        limit: int,
        window_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = LIMITER_MAX_KEYS,
    ) -> None:
        self.limit = max(1, int(limit))
        self.window_s = max(1.0, float(window_s))
        self.max_keys = max(1, int(max_keys))
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._hits)

    def hit(self, key: str) -> int:
        with self._lock:
            now = self._clock()
            wait = self._wait(key, now)
            if not wait:
                self._record(key, now)
            return wait

    def wait(self, key: str) -> int:
        """Segundos de espera para ``key`` sin registrar nada (0 si pasaría)."""

        with self._lock:
            return self._wait(key, self._clock())

    def record(self, key: str) -> None:
        """Registra una llamada de ``key`` (tras comprobar con :meth:`wait`)."""

        with self._lock:
            self._record(key, self._clock())

    def _wait(self, key: str, now: float) -> int:
        stamps = self._hits.get(key)
        if not stamps:
            return 0
        while stamps and now - stamps[0] >= self.window_s:
            stamps.popleft()
        if len(stamps) >= self.limit:
            return max(1, round(self.window_s - (now - stamps[0])))
        return 0

    def _record(self, key: str, now: float) -> None:
        stamps = self._hits.get(key)
        if stamps is None:
            stamps = self._hits[key] = deque()
        else:
            self._hits.move_to_end(key)
        stamps.append(now)
        while len(stamps) > self.limit:
            stamps.popleft()
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)


@dataclass
class VoiceRuntime:
    """Cliente de voz (o ``None`` si está apagada), límite por sesión y caché de audio."""

    client: OpenAISpeechClient | None
    limiter: RateLimiter
    max_upload_bytes: int
    tts_max_chars: int
    max_audio_seconds: int = 60
    ip_limiter: RateLimiter | None = None
    global_limiter: RateLimiter | None = None
    _cache: "OrderedDict[str, SynthesizedAudio]" = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _admit_lock: threading.Lock = field(default_factory=threading.Lock)

    def admit(self, owner_key: str, client_ip: str) -> int:
        """Aplica los límites global, por IP y por sesión: 0 si pasa o segundos de espera.

        Primero comprueba los tres y solo si pasan registra la llamada en los tres: un
        rechazo por un límite no gasta cupo de los otros.
        """

        checks: list[tuple[RateLimiter, str]] = [(self.limiter, owner_key)]
        if self.ip_limiter is not None:
            checks.insert(0, (self.ip_limiter, f"ip:{client_ip}"))
        if self.global_limiter is not None:
            checks.insert(0, (self.global_limiter, GLOBAL_KEY))
        with self._admit_lock:
            wait = max(limiter.wait(key) for limiter, key in checks)
            if wait:
                return wait
            for limiter, key in checks:
                limiter.record(key)
        return 0

    def unavailable_reason(self) -> str | None:
        """Motivo para no ofrecer la voz del servidor ahora; ``None`` si está disponible."""

        if self.client is None:
            return "off"
        return self.client.unavailable_status()

    def cache_key(self, message_id: str, text: str) -> str:
        client = self.client
        signature = (
            ""
            if client is None
            else f"{client.tts_model}|{client.tts_voice}|{client.tts_instructions}"
        )
        digest = hashlib.sha256(f"{signature}|{text}".encode()).hexdigest()[:16]
        return f"{message_id}:{digest}"

    def cached(self, key: str) -> SynthesizedAudio | None:
        with self._lock:
            audio = self._cache.get(key)
            if audio is not None:
                self._cache.move_to_end(key)
            return audio

    def remember(self, key: str, audio: SynthesizedAudio) -> None:
        with self._lock:
            self._cache[key] = audio
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)


class LiveSpoken:
    """Resúmenes hablados del turno en vivo que llevan datos que no se guardan (spec 056).

    La confirmación del pedido dice el nombre y el correo de quien compra, pero el turno se
    guarda sin ellos. El stream recuerda aquí, por dueño e id de mensaje, el resumen
    completo, y ``/api/voz/hablar`` lo prefiere al guardado: la nota de voz de ese turno dice
    los datos reales. Solo en memoria del proceso, como mucho ``ttl_s`` segundos y
    ``max_entries`` turnos (LRU); nunca va a la base ni a los logs.
    """

    def __init__(
        self,
        *,
        ttl_s: float = LIVE_SPOKEN_TTL_S,
        max_entries: int = LIVE_SPOKEN_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max(1, int(max_entries))
        self._clock = clock
        self._lock = threading.Lock()
        self._items: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def remember(self, owner_key: str, message_id: str, text: str) -> None:
        if not owner_key or not message_id or not text:
            return
        with self._lock:
            key = (owner_key, message_id)
            self._items[key] = (self._clock(), text)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def get(self, owner_key: str, message_id: str) -> str | None:
        with self._lock:
            key = (owner_key, message_id)
            found = self._items.get(key)
            if found is None:
                return None
            if self._clock() - found[0] >= self.ttl_s:
                del self._items[key]
                return None
            return found[1]


def live_spoken(state: Any) -> LiveSpoken:
    """El :class:`LiveSpoken` de la app (``app.state.live_spoken``), creado al primer uso."""

    found = getattr(state, "live_spoken", None)
    if isinstance(found, LiveSpoken):
        return found
    with _LIVE_LOCK:
        found = getattr(state, "live_spoken", None)
        if not isinstance(found, LiveSpoken):
            found = LiveSpoken()
            state.live_spoken = found
    return found


def build_voice_runtime(settings: Settings) -> VoiceRuntime:
    """Runtime con la configuración dada; sin red (el cliente no conecta al construirse)."""

    return VoiceRuntime(
        client=build_speech(settings),
        limiter=RateLimiter(settings.voice_rate_limit, settings.voice_rate_window_s),
        max_upload_bytes=settings.voice_upload_limit_bytes,
        tts_max_chars=settings.voice_tts_max_chars,
        max_audio_seconds=settings.voice_max_audio_seconds,
        ip_limiter=RateLimiter(settings.voice_ip_rate_limit, settings.voice_rate_window_s),
        global_limiter=RateLimiter(settings.voice_global_rate_per_min, 60),
    )


def _quotes(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Cotizaciones del resultado comercial guardado (``quotes`` o, antes de la 044, ``quote``)."""

    quotes = result.get("quotes")
    if isinstance(quotes, list) and quotes:
        return [item for item in quotes if isinstance(item, Mapping)]
    single = result.get("quote")
    return [single] if isinstance(single, Mapping) else []


def _card_name(option: Mapping[str, Any]) -> str:
    name = str(option.get("name") or "").strip()
    return to_speech(name).text.rstrip(" .") if name else ""


def _cards_phrase(result: Mapping[str, Any]) -> str:
    """Lo que la voz dice de las tarjetas de un turno comercial sin cotización.

    Las referencias del catálogo se nombran («Te muestro dos referencias en pantalla: Vaso
    de papel 12 onzas y Vaso de papel 16 onzas.») o, si son muchas o largas, se cuentan; el
    precio y las cajas quedan en la tarjeta. Otras tarjetas (ficha, distribuidores, sedes)
    se avisan con «El detalle completo está en pantalla.».
    """

    options = [item for item in result.get("options") or [] if isinstance(item, Mapping)]
    if options and not result.get("ficha"):
        count = len(options)
        noun = (
            "una referencia" if count == 1 else f"{_COUNT_WORDS.get(count, str(count))} referencias"
        )
        names = [_card_name(option) for option in options]
        named = (
            count <= _CARD_NAMES
            and all(names)
            and all(len(name.split()) <= _CARD_NAME_WORDS for name in names)
            and len({name.casefold() for name in names}) == count
        )
        if named:
            listed = names[0] if count == 1 else f"{', '.join(names[:-1])} y {names[-1]}"
            return f"Te muestro {noun} en pantalla: {listed}."
        return f"Te muestro {noun} en pantalla."
    return SUMMARY_POINTERS["web"] if has_cards(dict(result)) else ""


def _before_question(summary: str, phrase: str) -> str:
    """Añade ``phrase`` al resumen, antes de la pregunta final si la hay, sin repetirla."""

    if not phrase or phrase in summary:
        return summary
    paragraphs = [part.strip() for part in summary.split("\n\n") if part.strip()]
    if paragraphs and paragraphs[-1].endswith("?"):
        last = paragraphs.pop()
        # La pregunta final puede ir en el mismo párrafo que otra frase: se separa.
        start = last.rfind("¿")
        if start > 0 and last[:start].rstrip().endswith((".", "!", "?", "…")):
            paragraphs.append(last[:start].rstrip())
            last = last[start:]
        paragraphs.extend([phrase, last])
    else:
        paragraphs.append(phrase)
    return "\n\n".join(paragraphs)


def _stored_flow(message: ChatMessage) -> Mapping[str, Any] | None:
    """El ``flow`` (spec 054) del resultado comercial guardado con la respuesta, si lo hay."""

    for source in message.sources or []:
        if isinstance(source, Mapping) and source.get("result_type") == "commercial":
            result = source.get("result")
            flow = result.get("flow") if isinstance(result, Mapping) else None
            if isinstance(flow, Mapping):
                return flow
    return None


def _order_turn(flow: Mapping[str, Any]) -> bool:
    """¿Es un turno del pedido (spec 056)? Oferta del folio, pasos pendientes o el recibo."""

    return bool(
        flow.get("order_offer") or flow.get("order") or flow.get("awaiting") in ORDER_AWAITING
    )


def _neutral(spoken: str) -> str:
    for old, new in _NEUTRAL_SPOKEN:
        spoken = spoken.replace(old, new)
    return spoken


def _order_spoken(message: ChatMessage, live: Mapping[str, Any] | None) -> str | None:
    """Versión hablada de un turno del pedido hecho solo de plantilla; ``None`` si no lo es.

    Se decide con el ``flow`` guardado (el texto guardado es su ``text``, salvo las líneas
    del PDF, que el hilo enlaza): un turno del agente con la plantilla añadida se resume como
    cualquier otro. Se lee el ``spoken`` del turno en
    vivo (``live``, con los datos de quien compra) o, sin él, el guardado en tono neutro.
    """

    stored = _stored_flow(message)
    reference = stored if stored is not None else live
    if reference is None or not _order_turn(reference):
        return None
    if _template_lines(str(reference.get("text") or "")) != _template_lines(message.content):
        return None
    if live is not None and str(live.get("spoken") or "").strip():
        return str(live["spoken"]).strip()
    spoken = str((stored or {}).get("spoken") or "").strip()
    return _neutral(spoken) if spoken else None


def _template_lines(text: str | None) -> list[str]:
    """Las líneas del texto sin las del PDF («📄 …»): el hilo las cambia por su enlace."""

    return [line.strip() for line in (text or "").splitlines() if not line.startswith("📄")]


def _final_question(spoken: str) -> str:
    """La pregunta con la que termina ``spoken`` («¿Quieres hacer el pedido?»), o ``""``."""

    text = spoken.strip()
    start = text.rfind("¿")
    return text[start:] if text.endswith("?") and start >= 0 else ""


def spoken_message(message: ChatMessage, *, flow: Mapping[str, Any] | None = None) -> str:
    """Texto para el TTS de una respuesta guardada: su resumen hablado.

    La prosa se resume (:func:`carvajal.speech.spoken_summary`, canal ``web``): la primera
    frase, la lista en una frase, la pregunta final y las fuentes en una frase, con «El
    detalle completo está en pantalla.» si se omitió algo. Una respuesta corta se lee
    completa. Con cotización se lee solo su versión corta
    (:func:`apps.mcp.bridge.spoken_quote`), las fuentes y el mismo aviso. Sin cotización
    pero con tarjetas comerciales (referencias del catálogo, ficha, distribuidores), se
    nombran o se cuentan las referencias, o se añade el aviso, antes de la pregunta final:
    sus datos no están en el texto. Sin citas, Markdown, emojis ni URL; los enlaces no se
    leen.

    Un turno del pedido (spec 056) se lee con la versión hablada de su plantilla: la del
    turno en vivo si llega ``flow`` (el del evento ``done``, con el nombre y el correo reales)
    o la guardada, sin ellos. En el folio con la oferta del pedido, tras la cotización corta
    se hace la pregunta («¿Quieres hacer el pedido?»).
    """

    ordered = _order_spoken(message, flow if isinstance(flow, Mapping) else None)
    quotes: list[SpokenReply] = []
    cards: list[str] = []
    listed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in message.sources or []:
        if not isinstance(source, Mapping):
            continue
        if source.get("result_type") == "commercial":
            result = source.get("result")
            if isinstance(result, Mapping):
                for payload in _quotes(result):
                    view = quote_view(payload)
                    if view is not None:
                        quotes.append(spoken_quote(view))
                phrase = _cards_phrase(result)
                if phrase and phrase not in cards:
                    cards.append(phrase)
            continue
        title = str(source.get("title") or source.get("slug") or "").strip()
        url = str(source.get("url") or "").strip()
        key = url or title
        if key and key not in seen:
            seen.add(key)
            listed.append((title, url))
    if ordered and not quotes:
        return ordered
    if not quotes:
        summary = spoken_summary(message.content or "", channel="web", sources=listed[:5]).text
        # Las tarjetas no están en el texto: sin esta frase, la voz no diría que existen.
        for phrase in cards:
            summary = _before_question(summary, phrase)
        return summary.strip()
    # Con cotización se lee solo su versión corta: la prosa del agente repite las cifras.
    parts = list(quotes)
    if listed:
        parts.append(SpokenReply(spoken_sources(listed[:5])))
    parts.append(SpokenReply(SUMMARY_POINTERS["web"]))
    question = _final_question(ordered or "")
    if question:
        # Spec 056: la oferta del pedido es la pregunta pendiente y cierra la nota.
        parts.append(SpokenReply(question))
    return combine(parts).text.strip()


def cap_text(text: str, limit: int) -> str:
    """Recorta en el último fin de frase antes de ``limit`` y avisa del resto en pantalla."""

    if len(text) <= limit:
        return text
    budget = max(1, limit - len(TRUNCATED_NOTE) - 1)
    # Los fines de frase se buscan en el texto completo: buscarlos en el trozo recortado
    # haría que el punto de miles de «1.234.500» cortado justo detrás pareciera un final
    # (``$`` casaría con el borde del recorte) y se leería «1.».
    ends = [match.end() for match in _SENTENCE_END.finditer(text) if match.end() <= budget]
    if ends:
        head = text[: ends[-1]]
    else:
        # Sin fin de frase: en el último espacio que haya en el texto completo dentro del
        # presupuesto, para no partir una palabra ni una cifra.
        cut = text.rfind(" ", 0, budget + 1)
        head = text[:cut] if cut > 0 else text[:budget]
    return f"{head.rstrip()} {TRUNCATED_NOTE}"
