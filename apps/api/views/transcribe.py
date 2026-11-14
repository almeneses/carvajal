"""Voz a texto del chat: sube una grabación y devuelve la pregunta transcrita.

El audio no se guarda ni se registra en logs. El endpoint valida CSRF, tamaño y
MIME antes de llamar al servicio; el límite por IP es independiente de la cuota
diaria de preguntas para que una grabación fallida no consuma un turno.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from apps.api.runtime import transcriber as default_transcriber
from apps.api.security import verify_csrf
from apps.api.views.chat import CSRF_INVALIDO, MAX_QUESTION_CHARS
from carvajal.application.transcription import Transcriber, TranscriptionError
from carvajal.infrastructure.settings import Settings

__all__ = [
    "ALLOWED_AUDIO_TYPES",
    "MAX_TRANSCRIPT_CHARS",
    "TranscriptionRateLimiter",
    "router",
    "transcriber_for",
]

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api")

# Formatos que producen MediaRecorder y los navegadores al grabar voz.
ALLOWED_AUDIO_TYPES = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
    }
)
MAX_TRANSCRIPT_CHARS = 2_000
TOO_LARGE = "La grabación es demasiado larga. Intenta con una pregunta más corta."
INVALID_AUDIO = "El archivo de audio no es válido o no tiene un formato admitido."
EMPTY_TRANSCRIPT = "No se reconoció voz en la grabación. Inténtalo de nuevo."
UNAVAILABLE = "La transcripción de voz no está disponible en este momento."


class TranscriptionRateLimiter:
    """Límite en memoria por visitante; el despliegue usa un solo worker."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = max(0, limit)
        self._window = max(1, window_seconds)
        self._recent: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """Anota la petición; devuelve ``(permitida, segundos_de_espera)``."""

        if self._limit == 0:
            return True, 0
        now = time.monotonic()
        with self._lock:
            marks = self._recent.get(key, deque())
            while marks and now - marks[0] >= self._window:
                marks.popleft()
            if len(marks) >= self._limit:
                retry_after = max(1, math.ceil(self._window - (now - marks[0])))
                self._recent[key] = marks
                return False, retry_after
            marks.append(now)
            self._recent[key] = marks
            if len(self._recent) > 1_024:
                self._prune(now)
            return True, 0

    def _prune(self, now: float) -> None:
        """Descarta visitantes cuya ventana ya expiró."""

        for key, marks in list(self._recent.items()):
            while marks and now - marks[0] >= self._window:
                marks.popleft()
            if not marks:
                del self._recent[key]


def transcriber_for(request: Request) -> Transcriber | None:
    """Devuelve el doble inyectado en ``app.state.transcriber`` o el real."""

    injected = getattr(request.app.state, "transcriber", None)
    return injected if injected is not None else default_transcriber()


def _audio_type(value: str | None) -> str:
    """Normaliza ``audio/webm;codecs=opus`` a ``audio/webm``."""

    return (value or "").split(";", 1)[0].strip().lower()


def _limit_for(request: Request) -> tuple[bool, int]:
    """Consulta el limitador de la app; sin limitador, permite la petición."""

    limiter = getattr(request.app.state, "transcription_limits", None)
    if limiter is None:
        return True, 0
    key = request.client.host if request.client is not None else "desconocido"
    return limiter.allow(key)


@router.post("/chat/transcribe")
def transcribe(
    request: Request,
    audio: Annotated[UploadFile, File()],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    """Convierte una grabación en texto para rellenar la pregunta del chat."""

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return JSONResponse({"detail": CSRF_INVALIDO}, status_code=403)

    allowed, retry_after = _limit_for(request)
    if not allowed:
        return JSONResponse(
            {
                "detail": (
                    "Has alcanzado el límite de transcripciones. "
                    f"Inténtalo de nuevo en {retry_after} segundos."
                )
            },
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    content_type = _audio_type(audio.content_type)
    if content_type not in ALLOWED_AUDIO_TYPES:
        return JSONResponse({"detail": INVALID_AUDIO}, status_code=415)

    settings = Settings()
    data = audio.file.read(settings.transcription_max_bytes + 1)
    if len(data) > settings.transcription_max_bytes:
        return JSONResponse({"detail": TOO_LARGE}, status_code=413)
    if not data:
        return JSONResponse({"detail": INVALID_AUDIO}, status_code=422)

    transcriber = transcriber_for(request)
    if transcriber is None:
        return JSONResponse({"detail": UNAVAILABLE}, status_code=503)

    try:
        text = transcriber.transcribe(
            data,
            filename=audio.filename or "pregunta.webm",
            content_type=content_type,
            language=settings.transcription_language,
        )
    except TranscriptionError as error:
        logger.warning("transcription.failed", error=str(error))
        return JSONResponse({"detail": str(error)}, status_code=503)
    except Exception as error:  # el proveedor no debe tumbar la API
        logger.exception("transcription.unexpected", error=str(error))
        return JSONResponse({"detail": UNAVAILABLE}, status_code=503)

    text = text.strip()[:MAX_TRANSCRIPT_CHARS]
    if not text:
        return JSONResponse({"detail": EMPTY_TRANSCRIPT}, status_code=422)

    logger.info("transcription.completed", bytes=len(data), chars=len(text))
    return JSONResponse(
        {
            "text": text,
            "truncated": len(text) > MAX_QUESTION_CHARS,
        }
    )
