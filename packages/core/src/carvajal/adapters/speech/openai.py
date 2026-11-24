"""Adaptador de voz de OpenAI: transcripción (STT) y síntesis (TTS) por HTTP (spec 047).

Dos peticiones, sin el SDK de OpenAI (``httpx`` ya es dependencia):

- ``POST {base}/audio/transcriptions`` (multipart) con el audio grabado por el navegador,
  ``language=es`` y una pista de vocabulario comercial; devuelve el texto.
- ``POST {base}/audio/speech`` (JSON) con el texto ya preparado para voz
  (:func:`carvajal.speech.to_speech`), la voz y las instrucciones de estilo; devuelve el
  audio (MP3 por defecto, que todos los navegadores reproducen).

Mismo estilo que el cliente de Jev (:mod:`carvajal.adapters.judge.jev`): plazo por petición
(``VOICE_TIMEOUT_S``), sin reintentos, tope diario por proceso que se reinicia a medianoche
de Bogotá (``VOICE_MAX_CALLS_PER_DAY``) y circuito (3 fallos seguidos → 60 s; clave
rechazada o sin saldo → 15 min). Cualquier fallo sale como :class:`SpeechUnavailable` con un
motivo corto, y el chat vuelve a la voz del navegador.

Privacidad: ni el audio ni el texto se registran ni se guardan. Los logs llevan solo el
tipo de llamada, el estado, la latencia, los tamaños en bytes y el modelo. La clave nunca
aparece en mensajes, logs ni ``repr``.
"""

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

import httpx
import structlog

__all__ = [
    "AUDIO_MEDIA_TYPES",
    "AUTH_OPEN_S",
    "FAILURES_TO_OPEN",
    "FAILURE_OPEN_S",
    "MAX_TRANSCRIPT_CHARS",
    "STT_PROMPT",
    "OpenAISpeechClient",
    "SpeechUnavailable",
    "SynthesizedAudio",
]

logger = structlog.get_logger(__name__)

FAILURES_TO_OPEN: Final = 3
FAILURE_OPEN_S: Final = 60.0
AUTH_OPEN_S: Final = 15 * 60.0
_AUTH_STATUSES: Final = frozenset({401, 403})
_BOGOTA: Final = ZoneInfo("America/Bogota")
# El borrador del chat admite 500 caracteres; una transcripción más larga no es dictado.
MAX_TRANSCRIPT_CHARS: Final = 2000
# Pista de vocabulario para el STT: nombres y unidades que se oyen en el chat comercial.
STT_PROMPT: Final = (
    "Conversación en español de Colombia con el asistente de Carvajal Empaques: "
    "cotización, PDF, vasos, portacomidas, contenedores, tapas, onzas, cajas, unidades, "
    "Bogotá, Medellín, Cali, Barranquilla."
)
AUDIO_MEDIA_TYPES: Final[Mapping[str, str]] = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "wav": "audio/wav",
}
# Extensión que OpenAI usa para reconocer el contenedor del audio subido.
_EXTENSIONS: Final[Mapping[str, str]] = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
}

SpeechKind = Literal["stt", "tts"]
SpeechStatus = Literal["ok", "timeout", "error", "auth", "invalid"]


class SpeechUnavailable(Exception):
    """La voz del servidor no atendió; ``reason`` dice por qué, sin datos del usuario.

    Motivos: ``off`` (sin configurar), ``cap`` (tope diario), ``circuit_open``,
    ``timeout``, ``error`` (red o HTTP), ``auth`` (clave rechazada o sin saldo) e
    ``invalid`` (respuesta con otra forma).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SynthesizedAudio:
    """Audio sintetizado y su tipo MIME."""

    content: bytes
    media_type: str


@dataclass(frozen=True)
class _Outcome:
    status: SpeechStatus
    category: str = ""
    payload: Any = None


class OpenAISpeechClient:
    """Cliente HTTP de voz de OpenAI con plazo, tope diario y circuito.

    ``transport`` permite inyectar ``httpx.MockTransport`` en pruebas; ``clock`` (segundos
    monotónicos) y ``now`` (fecha y hora con zona) permiten fijar el circuito y el día.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        stt_model: str = "gpt-4o-mini-transcribe",
        tts_model: str = "gpt-4o-mini-tts",
        tts_voice: str = "coral",
        tts_instructions: str = "",
        timeout_s: float = 20.0,
        max_calls_per_day: int = 500,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.stt_model = stt_model
        self.tts_model = tts_model
        self.tts_voice = tts_voice
        self.tts_instructions = tts_instructions.strip()
        self.timeout_s = float(timeout_s)
        self.max_calls_per_day = int(max_calls_per_day)
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._http = httpx.Client(transport=transport, timeout=httpx.Timeout(self.timeout_s))
        self.calls_today = 0
        self._day: date | None = None
        self.failures = 0
        self.open_until: float | None = None
        self.last_error: str | None = None
        self._cap_logged = False

    def __repr__(self) -> str:  # la clave nunca sale en un ``repr``
        return (
            f"OpenAISpeechClient(base_url={self.base_url!r}, stt_model={self.stt_model!r}, "
            f"tts_model={self.tts_model!r})"
        )

    @property
    def configured(self) -> bool:
        return bool(self._key)

    # -- Disponibilidad (sin red) --

    def unavailable_status(self) -> str | None:
        """Motivo por el que no se llamaría ahora; ``None`` si se puede llamar."""

        if not self.configured:
            return "off"
        with self._lock:
            if self.open_until is not None and self._clock() < self.open_until:
                return "circuit_open"
            self._roll_day()
            if self.calls_today >= self.max_calls_per_day:
                return "cap"
        return None

    def available(self) -> bool:
        return self.unavailable_status() is None

    def _roll_day(self) -> None:
        today = self._now().astimezone(_BOGOTA).date()
        if self._day != today:
            self._day = today
            self.calls_today = 0
            self._cap_logged = False

    def _reserve(self) -> None:
        """Cuenta la llamada contra el tope o lanza :class:`SpeechUnavailable`."""

        blocked = self.unavailable_status()
        if blocked is not None:
            if blocked == "cap" and not self._cap_logged:
                self._cap_logged = True
                logger.warning("voice.cap_reached", calls_today=self.calls_today)
            raise SpeechUnavailable(blocked)
        with self._lock:
            self.calls_today += 1

    # -- Llamadas --

    def transcribe(self, audio: bytes, media_type: str, *, language: str = "es") -> str:
        """Texto del audio (``audio/webm``, ``audio/ogg``, ``audio/mp4``…); nunca lo guarda."""

        self._reserve()
        base_type = media_type.split(";", 1)[0].strip().lower()
        extension = _EXTENSIONS.get(base_type, "webm")
        started = self._clock()
        outcome = self._post(
            "/audio/transcriptions",
            files={"file": (f"voz.{extension}", audio, base_type or "application/octet-stream")},
            data={
                "model": self.stt_model,
                "language": language,
                "response_format": "json",
                "prompt": STT_PROMPT,
            },
            expect_json=True,
        )
        text = ""
        if outcome.status == "ok":
            payload = outcome.payload
            raw = payload.get("text") if isinstance(payload, Mapping) else None
            if isinstance(raw, str):
                text = " ".join(raw.split())[:MAX_TRANSCRIPT_CHARS]
            else:
                outcome = _Outcome("invalid", "invalid")
        self._finish("stt", self.stt_model, outcome, started, bytes_in=len(audio))
        return text

    def synthesize(self, text: str, *, audio_format: str = "mp3") -> SynthesizedAudio:
        """Audio del texto con la voz y el estilo configurados."""

        self._reserve()
        body: dict[str, Any] = {
            "model": self.tts_model,
            "input": text,
            "voice": self.tts_voice,
            "response_format": audio_format,
        }
        if self.tts_instructions:
            body["instructions"] = self.tts_instructions
        started = self._clock()
        outcome = self._post("/audio/speech", json=body, expect_json=False)
        content = b""
        if outcome.status == "ok":
            if isinstance(outcome.payload, bytes) and outcome.payload:
                content = outcome.payload
            else:
                outcome = _Outcome("invalid", "invalid")
        self._finish("tts", self.tts_model, outcome, started, bytes_out=len(content))
        return SynthesizedAudio(content, AUDIO_MEDIA_TYPES.get(audio_format, "audio/mpeg"))

    def _post(self, path: str, *, expect_json: bool, **kwargs: Any) -> _Outcome:
        try:
            response = self._http.post(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self._key}"},
                **kwargs,
            )
        except httpx.TimeoutException:
            return _Outcome("timeout", "timeout")
        except httpx.HTTPError:
            return _Outcome("error", "red")
        except Exception as error:  # nunca escapa del adaptador
            # Clave con caracteres no ASCII, cliente cerrado…: solo el tipo, nunca el texto.
            logger.warning("voice.request_failed", error_type=type(error).__name__)
            return _Outcome("error", "red")
        code = response.status_code
        if code in _AUTH_STATUSES:
            return _Outcome("auth", "auth")
        if code == 429 and _error_code(response) == "insufficient_quota":
            return _Outcome("auth", "insufficient_quota")
        if code != 200:
            return _Outcome("error", "http_5xx" if code >= 500 else f"http_{code}")
        if not expect_json:
            return _Outcome("ok", payload=response.content)
        try:
            return _Outcome("ok", payload=response.json())
        except ValueError:
            return _Outcome("invalid", "invalid")

    def _finish(
        self,
        kind: SpeechKind,
        model: str,
        outcome: _Outcome,
        started: float,
        *,
        bytes_in: int = 0,
        bytes_out: int = 0,
    ) -> None:
        """Circuito, log sin contenido y :class:`SpeechUnavailable` si falló."""

        latency_ms = max(0, round((self._clock() - started) * 1000))
        self._after(outcome)
        logger.info(
            "voice.call",
            kind=kind,
            status=outcome.status,
            latency_ms=latency_ms,
            model=model,
            bytes_in=bytes_in,
            bytes_out=bytes_out,
        )
        if outcome.status != "ok":
            raise SpeechUnavailable(outcome.status)

    def _after(self, outcome: _Outcome) -> None:
        with self._lock:
            if outcome.status == "ok":
                self.failures = 0
                return
            self.last_error = outcome.category or outcome.status
            if outcome.status == "auth":
                self.open_until = self._clock() + AUTH_OPEN_S
                self.failures = 0
                logger.warning("voice.auth_or_credits", open_s=AUTH_OPEN_S)
                return
            self.failures += 1
            if self.failures >= FAILURES_TO_OPEN:
                self.open_until = self._clock() + FAILURE_OPEN_S
                self.failures = 0
                logger.warning(
                    "voice.circuit_open", open_s=FAILURE_OPEN_S, last_error=self.last_error
                )

    def close(self) -> None:
        self._http.close()


def _error_code(response: httpx.Response) -> str:
    """``error.code`` de una respuesta de error de OpenAI, o ``""``."""

    try:
        payload = response.json()
    except ValueError:
        return ""
    error = payload.get("error") if isinstance(payload, Mapping) else None
    code = error.get("code") if isinstance(error, Mapping) else None
    return code if isinstance(code, str) else ""
