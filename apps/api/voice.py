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
guardada: el mismo :func:`carvajal.speech.to_speech` de WhatsApp, la cotización corta y las
fuentes en una frase.
"""

import hashlib
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from apps.api.quote_view import quote_view
from apps.mcp.bridge import spoken_quote
from carvajal.adapters.speech import OpenAISpeechClient, SynthesizedAudio, build_speech
from carvajal.adapters.storage.chat import ChatMessage
from carvajal.infrastructure.settings import Settings
from carvajal.speech import SpokenReply, combine, spoken_sources, to_speech

__all__ = [
    "CACHE_ENTRIES",
    "TRUNCATED_NOTE",
    "RateLimiter",
    "VoiceRuntime",
    "build_voice_runtime",
    "cap_text",
    "spoken_message",
]

CACHE_ENTRIES: Final = 32
# Claves que recuerda cada límite; al pasarse se olvida la menos reciente (LRU).
LIMITER_MAX_KEYS: Final = 4096
GLOBAL_KEY: Final = "global"
TRUNCATED_NOTE: Final = "El resto de la respuesta está en pantalla."
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


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


def spoken_message(message: ChatMessage) -> str:
    """Texto para el TTS de una respuesta guardada: prosa, cotizaciones y fuentes.

    Sin citas, Markdown, emojis ni URL (:func:`carvajal.speech.to_speech`); cada cotización
    en su versión corta (:func:`apps.mcp.bridge.spoken_quote`) y las fuentes en una frase.
    Los enlaces no se leen.
    """

    parts: list[SpokenReply] = [to_speech(message.content or "")]
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
                        parts.append(spoken_quote(view))
            continue
        title = str(source.get("title") or source.get("slug") or "").strip()
        url = str(source.get("url") or "").strip()
        key = url or title
        if key and key not in seen:
            seen.add(key)
            listed.append((title, url))
    if listed:
        parts.append(SpokenReply(spoken_sources(listed[:5])))
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
