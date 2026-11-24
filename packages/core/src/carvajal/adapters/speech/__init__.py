"""Voz del chat web por el servidor (spec 047): transcripción y síntesis con OpenAI.

:func:`build_speech` construye el cliente solo si ``VOICE_PROVIDER=openai`` y hay una clave
utilizable (``Settings.voice_server_configured``); si no, devuelve ``None`` y el chat sigue
con la voz del navegador (spec 040). :func:`voice_health` arma el bloque ``voice`` de
``/health`` sin llamar a OpenAI.
"""

from typing import Any

import httpx
import structlog

from carvajal.adapters.speech.openai import (
    OpenAISpeechClient,
    SpeechUnavailable,
    SynthesizedAudio,
)
from carvajal.infrastructure.settings import Settings

__all__ = [
    "OpenAISpeechClient",
    "SpeechUnavailable",
    "SynthesizedAudio",
    "build_speech",
    "usable_key",
    "voice_health",
]

logger = structlog.get_logger(__name__)


def usable_key(settings: Settings) -> str:
    """Clave de OpenAI sin espacios, o ``""`` si falta o no sirve como cabecera HTTP."""

    key = settings.openai_api_key.get_secret_value().strip()
    return key if key.isascii() and key.isprintable() else ""


def build_speech(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> OpenAISpeechClient | None:
    """Cliente de voz con la configuración dada; ``None`` si la voz del servidor está apagada."""

    if not settings.voice_server_configured:
        return None
    key = usable_key(settings)
    if not key:
        # Nunca la clave: solo que no sirve.
        logger.warning("voice.invalid_key", reason="caracteres_no_ascii")
        return None
    return OpenAISpeechClient(
        api_key=key,
        base_url=settings.voice_openai_base_url,
        stt_model=settings.voice_stt_model,
        tts_model=settings.voice_tts_model,
        tts_voice=settings.voice_tts_voice,
        tts_instructions=settings.voice_tts_instructions,
        timeout_s=settings.voice_timeout_s,
        max_calls_per_day=settings.voice_max_calls_per_day,
        transport=transport,
    )


def voice_health(settings: Settings, client: Any = None, *, detail: bool = False) -> dict[str, Any]:
    """Bloque ``voice`` de ``/health``: proveedor, configuración y estado, sin red ni secretos.

    Sin cliente construido (aún nadie usó la voz), ``available`` es la configuración. No
    cambia el ``status`` global: el chat atiende igual con la voz del navegador.

    ``/health`` es público: ``calls_today`` y ``last_error`` (cuánto falta para el tope,
    si la clave está rechazada o sin saldo) solo salen con ``detail``, que la vista pasa
    cuando quien pregunta es el operador.
    """

    configured = settings.voice_provider == "openai" and bool(usable_key(settings))
    built = isinstance(client, OpenAISpeechClient)
    block: dict[str, Any] = {
        "provider": settings.voice_provider,
        "configured": configured,
        "available": client.available() if built else configured,
        "stt_model": settings.voice_stt_model if configured else None,
        "tts_model": settings.voice_tts_model if configured else None,
    }
    if detail:
        block["calls_today"] = client.calls_today if built else 0
        block["last_error"] = client.last_error if built else None
    return block
