"""Voz del chat web por el servidor (spec 047): ``POST /api/voz/transcribir`` y ``/hablar``.

El navegador conserva su voz (spec 040) como respaldo: cualquier respuesta distinta de 200
(503 con ``reason``, 429, 4xx) hace que el chat vuelva al dictado y a la lectura del
navegador. Las dos rutas exigen, en este orden:

1. Voz del servidor encendida (``VOICE_PROVIDER=openai`` con clave): si no, **503**
   ``{"reason": "off"}`` sin leer el cuerpo.
2. CSRF de doble cookie, por cabecera ``X-CSRF-Token`` o campo ``csrf_token`` (**403**).
3. La cookie firmada del chat (la misma identidad que el chat; sin ella, **403**
   ``no_session``) y el límite por sesión (**429** con ``Retry-After``).
4. Tope diario y circuito del cliente (**503** ``cap`` / ``circuit_open``).

``/transcribir`` recibe el audio de ``MediaRecorder`` (``audio/webm``, ``audio/ogg`` o
``audio/mp4``) en el campo ``audio`` de un multipart, comprueba el tamaño antes de leer el
cuerpo y la firma del contenedor, y devuelve ``{"texto": …}``. El audio no se guarda ni se
registra.

``/hablar`` **no sintetiza texto libre**: recibe ``message_id``, el id de una respuesta del
asistente de la conversación de quien llama (lo expone ``data-voz-mensaje`` en el hilo y el
evento ``done`` del stream), la carga de la base, la prepara con
:func:`apps.api.voice.spoken_message`, la recorta a ``VOICE_TTS_MAX_CHARS`` y devuelve el
audio MP3 sin caché (``no-store``). Un id ajeno, de otro dueño o de un
mensaje del usuario responde **404**: no es un TTS público.
"""

import re
import threading
from typing import Any, Final, cast

import anyio
import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import UploadFile

from apps.api.security import verify_csrf
from apps.api.views.chat import chat_provider, request_identity
from apps.api.voice import VoiceRuntime, build_voice_runtime, cap_text, spoken_message
from carvajal.adapters.speech import SpeechUnavailable
from carvajal.infrastructure.settings import Settings

__all__ = [
    "ALLOWED_AUDIO_TYPES",
    "CSRF_HEADER",
    "MESSAGES",
    "router",
    "voice_available",
    "voice_runtime",
]

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/voz")

CSRF_HEADER: Final = "X-CSRF-Token"
ALLOWED_AUDIO_TYPES: Final = frozenset({"audio/webm", "audio/ogg", "audio/mp4"})
# Cabecera multipart y campos pequeños además del audio.
_MULTIPART_OVERHEAD: Final = 16 * 1024
# Un audio de menos de esto no tiene voz que transcribir.
_MIN_AUDIO_BYTES: Final = 512
_MESSAGE_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
_RUNTIME_LOCK = threading.Lock()

UNAVAILABLE: Final = "La voz del servidor no está disponible; se usa la voz del navegador."
MESSAGES: Final[dict[str, str]] = {
    "off": UNAVAILABLE,
    "cap": UNAVAILABLE,
    "circuit_open": UNAVAILABLE,
    "timeout": UNAVAILABLE,
    "error": UNAVAILABLE,
    "auth": UNAVAILABLE,
    "invalid": UNAVAILABLE,
    "db": UNAVAILABLE,
    "csrf": "La sesión expiró. Recarga la página e inténtalo de nuevo.",
    "no_session": "Abre el chat antes de usar la voz.",
    "rate_limited": "Usaste la voz muchas veces seguidas. Espera un momento.",
    "length_required": "Falta el tamaño del audio.",
    "too_large": "El audio es demasiado largo.",
    "bad_request": "No se recibió el audio.",
    "unsupported_type": "Formato de audio no admitido.",
    "empty": "No se oyó ninguna voz.",
    "invalid_message": "Mensaje no válido.",
    "not_found": "No encontré esa respuesta en tu conversación.",
    "no_text": "Esa respuesta no tiene texto para leer.",
}


def voice_runtime(request: Request) -> VoiceRuntime:
    """Runtime inyectado en ``app.state.voice`` o el real, construido una vez por app."""

    state = request.app.state
    runtime = getattr(state, "voice", None)
    if runtime is not None:
        return cast(VoiceRuntime, runtime)
    with _RUNTIME_LOCK:
        runtime = getattr(state, "voice", None)
        if runtime is None:
            runtime = build_voice_runtime(_settings(request))
            state.voice = runtime
    return runtime


def voice_available(request: Request) -> bool:
    """¿La página del chat debe ofrecer la voz del servidor? Sin construir el cliente."""

    runtime = getattr(request.app.state, "voice", None)
    if runtime is not None:
        return cast(VoiceRuntime, runtime).unavailable_reason() is None
    settings = _settings(request)
    from carvajal.adapters.speech import usable_key

    return settings.voice_server_configured and bool(usable_key(settings))


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else Settings()


def _reject(status_code: int, reason: str, headers: dict[str, str] | None = None) -> JSONResponse:
    # Solo el motivo: ni el audio, ni el texto, ni el id del mensaje.
    logger.info("voice.rejected", reason=reason, status=status_code)
    return JSONResponse(
        {"detail": MESSAGES.get(reason, UNAVAILABLE), "reason": reason},
        status_code=status_code,
        headers={"Cache-Control": "no-store", **(headers or {})},
    )


def _csrf_ok(request: Request, field_value: str = "") -> bool:
    token = request.headers.get(CSRF_HEADER, "").strip() or field_value.strip()
    return verify_csrf(request, token)


def _gate(request: Request, runtime: VoiceRuntime) -> tuple[str | None, JSONResponse | None]:
    """Identidad, límite por sesión y disponibilidad: ``(owner_key, rechazo)``."""

    owner_key = request_identity(request).read(request)
    if owner_key is None:
        return None, _reject(403, "no_session")
    structlog.contextvars.bind_contextvars(owner_key=owner_key)
    wait = runtime.limiter.hit(owner_key)
    if wait:
        return owner_key, _reject(429, "rate_limited", {"Retry-After": str(wait)})
    reason = runtime.unavailable_reason()
    if reason is not None:
        return owner_key, _reject(503, reason)
    return owner_key, None


def _sniff(audio: bytes, media_type: str) -> bool:
    """La firma del contenedor coincide con el tipo declarado (WebM, Ogg o MP4)."""

    if media_type == "audio/webm":
        return audio.startswith(b"\x1a\x45\xdf\xa3")
    if media_type == "audio/ogg":
        return audio.startswith(b"OggS")
    if media_type == "audio/mp4":
        return audio[4:8] == b"ftyp"
    return False


@router.post("/transcribir")
async def transcribir(request: Request) -> Response:
    """Transcribe el audio dictado y devuelve ``{"texto": …}`` para revisarlo en el borrador."""

    runtime = voice_runtime(request)
    if runtime.client is None:
        return _reject(503, "off")
    # El tamaño se comprueba antes de leer el cuerpo: un audio enorme no llega a parsearse.
    declared = request.headers.get("content-length", "").strip()
    if not declared.isdigit():
        return _reject(411, "length_required")
    if int(declared) > runtime.max_upload_bytes + _MULTIPART_OVERHEAD:
        return _reject(413, "too_large")
    header_ok = _csrf_ok(request)
    try:
        form = await request.form(max_files=1, max_fields=4, max_part_size=4096)
    except Exception:  # cuerpo multipart roto: no se describe el contenido
        return _reject(400, "bad_request")
    upload: UploadFile | None = None
    try:
        field = form.get("csrf_token")
        if not (header_ok or _csrf_ok(request, field if isinstance(field, str) else "")):
            logger.warning("csrf.rejected", path=request.url.path)
            return _reject(403, "csrf")
        _, rejected = _gate(request, runtime)
        if rejected is not None:
            return rejected
        candidate = form.get("audio")
        if not isinstance(candidate, UploadFile):
            return _reject(400, "bad_request")
        upload = candidate
        media_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
        if media_type not in ALLOWED_AUDIO_TYPES:
            return _reject(415, "unsupported_type")
        audio = await upload.read(runtime.max_upload_bytes + 1)
        if len(audio) > runtime.max_upload_bytes:
            return _reject(413, "too_large")
        if len(audio) < _MIN_AUDIO_BYTES:
            return _reject(422, "empty")
        if not _sniff(audio, media_type):
            return _reject(415, "unsupported_type")
        client = runtime.client
        try:
            text = await anyio.to_thread.run_sync(
                lambda: client.transcribe(audio, upload.content_type or media_type)
            )
        except SpeechUnavailable as error:
            return _reject(503, error.reason)
    finally:
        if upload is not None:
            await upload.close()
        await form.close()
    if not text:
        return _reject(422, "empty")
    return JSONResponse({"texto": text}, headers={"Cache-Control": "no-store"})


@router.post("/hablar")
def hablar(
    request: Request,
    message_id: str = Form(default=""),
    csrf_token: str = Form(default=""),
) -> Response:
    """Audio de una respuesta del asistente de la conversación de quien llama."""

    runtime = voice_runtime(request)
    if runtime.client is None:
        return _reject(503, "off")
    if not _csrf_ok(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _reject(403, "csrf")
    identifier = message_id.strip()
    if _MESSAGE_ID.fullmatch(identifier) is None:
        return _reject(422, "invalid_message")
    owner_key, rejected = _gate(request, runtime)
    if rejected is not None or owner_key is None:
        return rejected or _reject(403, "no_session")
    provider: Any = chat_provider(request)
    lookup = getattr(provider, "assistant_message", None)
    if lookup is None:
        return _reject(503, "off")
    try:
        message = lookup(owner_key, identifier)
    except SQLAlchemyError as error:
        logger.warning("voice.message_lookup_failed", error_type=type(error).__name__)
        return _reject(503, "db")
    if message is None:
        return _reject(404, "not_found")
    text = cap_text(spoken_message(message), runtime.tts_max_chars)
    if not text:
        return _reject(422, "no_text")
    key = runtime.cache_key(identifier, text)
    audio = runtime.cached(key)
    if audio is None:
        client = runtime.client
        try:
            audio = client.synthesize(text)
        except SpeechUnavailable as error:
            return _reject(503, error.reason)
        runtime.remember(key, audio)
    return Response(
        content=audio.content,
        media_type=audio.media_type,
        headers={"Cache-Control": "private, no-store"},
    )
