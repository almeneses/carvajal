"""Estado de la voz del chat web por el servidor (spec 047): cliente, límites y caché.

Un :class:`VoiceRuntime` por aplicación (``app.state.voice``), construido al primer uso:

- el cliente de OpenAI (:class:`~carvajal.adapters.speech.OpenAISpeechClient`), con su tope
  diario y su circuito por proceso;
- un límite por sesión del chat (``owner_key``) con ventana deslizante, común a dictado y
  lectura (``VOICE_RATE_LIMIT`` llamadas cada ``VOICE_RATE_WINDOW_S`` segundos);
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
TRUNCATED_NOTE: Final = "El resto de la respuesta está en pantalla."
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


class RateLimiter:
    """Ventana deslizante por clave: ``hit`` devuelve 0 si pasa o los segundos de espera."""

    def __init__(
        self, limit: int, window_s: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = max(1, int(limit))
        self.window_s = max(1.0, float(window_s))
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> int:
        now = self._clock()
        with self._lock:
            stamps = self._hits.setdefault(key, deque())
            while stamps and now - stamps[0] >= self.window_s:
                stamps.popleft()
            if len(stamps) >= self.limit:
                return max(1, round(self.window_s - (now - stamps[0])))
            stamps.append(now)
            self._prune(now)
            return 0

    def _prune(self, now: float) -> None:
        """Descarta claves sin marcas vigentes para no crecer sin límite."""

        if len(self._hits) < 1024:
            return
        stale = [
            key
            for key, stamps in self._hits.items()
            if not stamps or now - stamps[-1] >= self.window_s
        ]
        for key in stale:
            del self._hits[key]


@dataclass
class VoiceRuntime:
    """Cliente de voz (o ``None`` si está apagada), límite por sesión y caché de audio."""

    client: OpenAISpeechClient | None
    limiter: RateLimiter
    max_upload_bytes: int
    tts_max_chars: int
    _cache: "OrderedDict[str, SynthesizedAudio]" = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

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
        max_upload_bytes=settings.voice_max_upload_bytes,
        tts_max_chars=settings.voice_tts_max_chars,
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
    head = text[:budget]
    ends = [match.end() for match in _SENTENCE_END.finditer(head)]
    cut = ends[-1] if ends else head.rfind(" ")
    head = head[:cut] if cut > 0 else head
    return f"{head.rstrip()} {TRUNCATED_NOTE}"
