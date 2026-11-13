"""Contrato HTTP/SSE para el frontend propio del M2.

El frontend (proceso separado) consume estos endpoints JSON/SSE. El backend
controla credenciales, modelo, ruta, memoria y herramientas; el navegador solo
pinta tokens y citas. La cookie firmada y el CSRF son los mismos del shell
Jinja2, de modo que ambas entradas comparten identidad y cuota.
"""

from collections.abc import Iterator

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.service import SERVICE_UNAVAILABLE, ChatProvider
from apps.api.chat.streaming import ChatEvent
from apps.api.observability import capture_exception
from apps.api.security import verify_csrf
from apps.api.views.chat import (
    CSRF_INVALIDO,
    EMPTY_QUESTION,
    LIMITE_ALCANZADO,
    MAX_QUESTION_CHARS,
    TOO_LONG_QUESTION,
    chat_provider,
    consume_quota,
    request_identity,
    set_identity_cookie,
)
from apps.api.views.stream import SSE_HEADERS, SSE_MEDIA_TYPE, _encode, _invalid

__all__ = ["router"]

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api")


def _owner(request: Request) -> tuple[str, bool]:
    """Dueño de la cookie o uno nuevo; el segundo valor indica si es nuevo."""

    identity = request_identity(request)
    stored = identity.read(request)
    if stored is None:
        return identity.new_owner_key(), True
    return stored, False


@router.get("/csrf")
def csrf(request: Request) -> JSONResponse:
    """Entrega el token que el formulario del frontend debe repetir."""

    return JSONResponse({"token": getattr(request.state, "csrf_token", "")})


@router.get("/health")
def health() -> JSONResponse:
    """Resumen para el frontend: base, documentos y estado de Qdrant."""

    from sqlalchemy import func, select

    from apps.api.runtime import qdrant_store, session_factory
    from carvajal.adapters.storage import tables

    try:
        with session_factory().begin() as session:
            documents = session.scalar(select(func.count()).select_from(tables.documents))
            chunks = session.scalar(select(func.count()).select_from(tables.chunks))
    except (SQLAlchemyError, OSError) as error:
        logger.warning("api.health_failed", error=str(error))
        return JSONResponse({"status": "degraded", "detail": "base de datos no disponible"}, 503)
    index = qdrant_store().health()
    healthy = index.get("status") in {"ok", "not_configured"}
    return JSONResponse(
        {
            "status": "ok" if healthy else "degraded",
            "documents": documents or 0,
            "chunks": chunks or 0,
            "index": index,
        },
        status_code=200 if healthy else 503,
    )


@router.get("/chat/history")
def history(request: Request) -> JSONResponse:
    """Historial del hilo activo, suficiente para recargar la página."""

    identity = request_identity(request)
    owner_key = identity.read(request)
    if owner_key is None:
        return JSONResponse({"thread_id": None, "messages": []})
    try:
        messages = chat_provider(request).history(owner_key)
    except SQLAlchemyError as error:
        logger.warning("api.history_failed", error=str(error))
        return JSONResponse({"status": "degraded", "detail": SERVICE_UNAVAILABLE}, 503)
    return JSONResponse(
        {
            "thread_id": messages[0].conversation_id if messages else None,
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "sources": message.sources or [],
                    "status": message.status,
                    "created_at": message.created_at.isoformat(),
                }
                for message in messages
            ],
        }
    )


@router.post("/chat/new")
def new_thread(
    request: Request,
    csrf_token: str = Form(default=""),
) -> JSONResponse:
    """Crea un hilo nuevo del mismo dueño para demostrar la memoria del Store."""

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return JSONResponse({"detail": CSRF_INVALIDO}, 403)
    owner_key, new_owner = _owner(request)
    identity = request_identity(request)
    try:
        conversation = chat_provider(request).new_conversation(owner_key)
    except SQLAlchemyError as error:
        logger.warning("api.new_thread_failed", error=str(error))
        return JSONResponse({"detail": SERVICE_UNAVAILABLE}, 503)
    response = JSONResponse({"thread_id": conversation.id})
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


@router.post("/chat/stream")
def chat_stream(
    request: Request,
    question: str = Form(default=""),
    csrf_token: str = Form(default=""),
    client_turn_id: str = Form(default=""),
    thread_id: str = Form(default=""),
) -> Response:
    """SSE con el mismo contrato que el shell Jinja2."""

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _invalid(CSRF_INVALIDO, status_code=403)

    text = question.strip()
    if not text:
        return _invalid(EMPTY_QUESTION)
    if len(text) > MAX_QUESTION_CHARS:
        return _invalid(TOO_LONG_QUESTION)

    identity = request_identity(request)
    owner_key = identity.read(request)
    new_owner = owner_key is None
    if owner_key is None:
        owner_key = identity.new_owner_key()
    structlog.contextvars.bind_contextvars(owner_key=owner_key)

    try:
        quota = consume_quota(request, owner_key)
    except SQLAlchemyError as error:
        logger.warning("api.quota_failed", error=str(error))
        response = _invalid(SERVICE_UNAVAILABLE, status_code=503)
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response
    if not quota.allowed:
        response = _invalid(
            LIMITE_ALCANZADO,
            status_code=429,
            headers={"Retry-After": str(quota.retry_after)},
        )
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response

    response = StreamingResponse(
        _api_sse(
            chat_provider(request),
            owner_key,
            text,
            client_turn_id=client_turn_id.strip() or None,
            thread_id=thread_id.strip() or None,
        ),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


def _api_sse(
    provider: ChatProvider,
    owner_key: str,
    question: str,
    *,
    client_turn_id: str | None,
    thread_id: str | None,
) -> Iterator[str]:
    """Igual que el stream Jinja2, pero reafirmando el contexto del visitante."""

    structlog.contextvars.bind_contextvars(owner_key=owner_key)
    saw_token = False
    closed = False
    try:
        for event in provider.stream(
            owner_key,
            question,
            client_turn_id=client_turn_id,
            thread_id=thread_id,
        ):
            saw_token = saw_token or event.name == "token"
            closed = event.name in {"done", "error"}
            yield _encode(event)
    except Exception as error:
        logger.warning("api.stream_failed", error=str(error))
        capture_exception(error)
        if not closed:
            yield _encode(
                ChatEvent("error", {"message": SERVICE_UNAVAILABLE, "incomplete": saw_token})
            )
