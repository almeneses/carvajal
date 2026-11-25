"""Foto de un alimento en el chat web (spec 051): ``POST /chat/foto``.

Multipart con ``foto`` (JPEG o PNG, ≤ 10 MB), ``texto`` opcional, ``client_turn_id`` y
``thread_id``; el token CSRF viaja en la cabecera ``X-CSRF-Token`` y se valida antes de leer
el cuerpo, como la voz (spec 047). La respuesta es el mismo SSE de ``/chat/stream`` para que
el navegador use un solo lector; los rechazos también son SSE con un evento ``error``.

La foto solo existe en memoria: se prepara (endereza, escala, sin metadatos), se clasifica y
se descarta. El cupo se cobra solo si el clasificador respondió (FR-019).
"""

import threading
from collections.abc import Callable, Iterator
from typing import Final, Protocol, cast, runtime_checkable

import anyio
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import UploadFile

from apps.api.chat.service import SERVICE_UNAVAILABLE
from apps.api.chat.streaming import ChatEvent
from apps.api.security import verify_csrf
from apps.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, encode
from apps.api.views.chat import (
    CSRF_INVALIDO,
    LIMITE_ALCANZADO,
    MAX_QUESTION_CHARS,
    TOO_LONG_QUESTION,
    chat_provider,
    consume_quota,
    peek_quota,
    request_identity,
    set_identity_cookie,
)
from carvajal.adapters.judge import JevFoodClassifier, build_food_classifier
from carvajal.food.photo import MAX_PHOTO_BYTES, PhotoRejected, PreparedPhoto, prepare_photo
from carvajal.food.profile import FoodProfile
from carvajal.infrastructure.settings import Settings

__all__ = ["CSRF_HEADER", "food_classifier", "photo_available", "router"]

logger = structlog.get_logger(__name__)

router = APIRouter()

CSRF_HEADER: Final = "X-CSRF-Token"
ALLOWED_TYPES: Final = frozenset({"image/jpeg", "image/jpg", "image/png", "image/pjpeg"})
_MULTIPART_OVERHEAD: Final = 16 * 1024
_LOCK = threading.Lock()
_UNSET: Final = object()

PHOTO_OFF: Final = "El análisis de fotos no está disponible en este momento."
LENGTH_REQUIRED: Final = "Falta el tamaño de la foto."
BAD_REQUEST: Final = "No se recibió la foto."
UNSUPPORTED: Final = "La foto debe ser JPEG o PNG."


@runtime_checkable
class PhotoChat(Protocol):
    """Parte del servicio de chat que atiende fotos (``ChatService.photo_stream``)."""

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


def food_classifier(request: Request) -> JevFoodClassifier | None:
    """Clasificador inyectado en ``app.state.food_classifier`` o el real, construido una vez."""

    state = request.app.state
    current = getattr(state, "food_classifier", _UNSET)
    if current is not _UNSET:
        return cast(JevFoodClassifier | None, current)
    with _LOCK:
        current = getattr(state, "food_classifier", _UNSET)
        if current is _UNSET:
            current = build_food_classifier(_settings(request))
            state.food_classifier = current
    return cast(JevFoodClassifier | None, current)


def photo_available(request: Request) -> bool:
    """¿La página del chat ofrece fotos? Clasificador configurado, sin tope ni circuito."""

    classifier = food_classifier(request)
    return classifier is not None and classifier.available()


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else Settings()


def _reject(
    message: str,
    status_code: int,
    reason: str,
    headers: dict[str, str] | None = None,
) -> Response:
    """Rechazo como SSE (un solo lector en el navegador); en el log, solo el motivo."""

    logger.info("photo.rejected", reason=reason, status=status_code)
    return Response(
        content=encode("error", {"message": message, "incomplete": False, "reason": reason}),
        status_code=status_code,
        media_type=SSE_MEDIA_TYPE,
        headers={**SSE_HEADERS, "Cache-Control": "no-store", **(headers or {})},
    )


def _events(events: Iterator[ChatEvent]) -> Iterator[str]:
    for event in events:
        yield encode(event.name, event.data)


@router.post("/chat/foto")
async def chat_foto(request: Request) -> Response:
    """Analiza la foto y responde con el turno en SSE (ver el docstring del módulo)."""

    if not verify_csrf(request, request.headers.get(CSRF_HEADER, "").strip()):
        logger.warning("csrf.rejected", path=request.url.path)
        return _reject(CSRF_INVALIDO, 403, "csrf")
    declared = request.headers.get("content-length", "").strip()
    if not declared.isdigit():
        return _reject(LENGTH_REQUIRED, 411, "length_required")
    if int(declared) > MAX_PHOTO_BYTES + _MULTIPART_OVERHEAD:
        return _reject(PhotoRejected("grande").message, 413, "grande")
    classifier = food_classifier(request)
    if classifier is None:
        return _reject(PHOTO_OFF, 503, "off")
    blocked = classifier.unavailable_status()
    if blocked is not None:
        return _reject(PHOTO_OFF, 503, blocked)
    try:
        # ``max_part_size`` solo limita las partes sin archivo (``texto`` y los ids): Starlette
        # vuelca el archivo a un temporal y su tamaño ya lo acotó el ``Content-Length``.
        form = await request.form(max_files=1, max_fields=4, max_part_size=4096)
    except Exception:  # multipart roto: no se describe el contenido
        return _reject(BAD_REQUEST, 400, "bad_request")
    upload: UploadFile | None = None
    try:
        candidate = form.get("foto")
        if not isinstance(candidate, UploadFile):
            return _reject(BAD_REQUEST, 400, "bad_request")
        upload = candidate
        media_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
        if media_type not in ALLOWED_TYPES:
            return _reject(UNSUPPORTED, 415, "formato")
        raw = await upload.read(MAX_PHOTO_BYTES + 1)
        note = str(form.get("texto") or "").strip()
        client_turn_id = str(form.get("client_turn_id") or "").strip() or None
        thread_id = str(form.get("thread_id") or "").strip() or None
    finally:
        if upload is not None:
            await upload.close()
        await form.close()
    if len(note) > MAX_QUESTION_CHARS:
        return _reject(TOO_LONG_QUESTION, 422, "too_long")
    try:
        photo = await anyio.to_thread.run_sync(prepare_photo, raw)
    except PhotoRejected as error:
        status = 413 if error.reason == "grande" else 415 if error.reason == "formato" else 422
        return _reject(error.message, status, error.reason)
    finally:
        del raw

    provider = chat_provider(request)
    if not isinstance(provider, PhotoChat):
        return _reject(PHOTO_OFF, 503, "off")
    identity = request_identity(request)
    owner_key = identity.read(request)
    new_owner = owner_key is None
    if owner_key is None:
        owner_key = identity.new_owner_key()
    structlog.contextvars.bind_contextvars(owner_key=owner_key)
    response = StreamingResponse(
        _events(
            provider.photo_stream(
                owner_key,
                note,
                analyze=_analyzer(classifier, photo),
                charge=_charger(request, owner_key),
                precheck=_prechecker(request, owner_key),
                client_turn_id=client_turn_id,
                thread_id=thread_id,
            )
        ),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


def _analyzer(classifier: JevFoodClassifier, photo: PreparedPhoto) -> Callable[[], FoodProfile]:
    def analyze() -> FoodProfile:
        profile, _call = classifier.classify(photo)
        return profile

    return analyze


def _charger(request: Request, owner_key: str) -> Callable[[], str | None]:
    def charge() -> str | None:
        try:
            quota = consume_quota(request, owner_key)
        except SQLAlchemyError as error:
            logger.warning("chat.quota_failed", error_type=type(error).__name__, photo=True)
            return SERVICE_UNAVAILABLE
        return None if quota.allowed else LIMITE_ALCANZADO

    return charge


def _prechecker(request: Request, owner_key: str) -> Callable[[], str | None]:
    """Sin cupo, la foto no se envía a Jev; un fallo al consultarlo lo deja a ``charge``."""

    def precheck() -> str | None:
        try:
            quota = peek_quota(request, owner_key)
        except SQLAlchemyError as error:
            logger.warning("chat.quota_peek_failed", error_type=type(error).__name__)
            return None
        return None if quota.allowed else LIMITE_ALCANZADO

    return precheck
