"""Vista del stream SSE (W3.4): tokens, cierre y degradación.

Formato de cable: cada evento ocupa un bloque terminado en una línea en blanco,
con ``event: <nombre>`` y ``data: <json>``. La pregunta inválida (vacía o de más
de 500 caracteres) responde **422** con el mismo formato SSE y un evento
``error``; se elige SSE, y no JSON, para que el cliente tenga un único parser.

Las reglas de validación, identidad y cuota y la traducción de eventos viven en
``apps.api.views.chat_rules``, compartidas con el formulario y el stream de API.
"""

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.service import SERVICE_UNAVAILABLE
from apps.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from apps.api.views.chat_rules import (
    CSRF_INVALIDO,
    LIMITE_ALCANZADO,
    chat_provider,
    consume_quota,
    invalid_response,
    require_csrf,
    resolve_visitor,
    set_identity_cookie,
    stream_events,
    validate_question,
)

__all__ = ["router"]

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post("/chat/stream")
def chat_stream(
    request: Request,
    question: str = Form(default=""),
    csrf_token: str = Form(default=""),
    client_turn_id: str = Form(default=""),
    thread_id: str = Form(default=""),
) -> Response:
    """Responde la pregunta como eventos SSE y persiste el turno al cerrar."""

    # CSRF antes de la cuota y del caso de uso: sin token no se abre el stream.
    if not require_csrf(request, csrf_token):
        return invalid_response(CSRF_INVALIDO, status_code=403)

    text, invalida = validate_question(question)
    if invalida is not None:
        return invalid_response(invalida)

    visitor = resolve_visitor(request)
    # Los tokens que genere el LLM quedan asociados a este visitante.
    structlog.contextvars.bind_contextvars(owner_key=visitor.owner_key)

    try:
        quota = consume_quota(request, visitor.owner_key)
    except SQLAlchemyError as error:
        logger.warning("chat.quota_failed", error=str(error), stream=True)
        response = invalid_response(SERVICE_UNAVAILABLE, status_code=503)
        if visitor.new_owner:
            set_identity_cookie(response, request, visitor.identity, visitor.owner_key)
        return response
    if not quota.allowed:
        response = invalid_response(
            LIMITE_ALCANZADO,
            status_code=429,
            headers={"Retry-After": str(quota.retry_after)},
        )
        if visitor.new_owner:
            set_identity_cookie(response, request, visitor.identity, visitor.owner_key)
        return response

    response = StreamingResponse(
        stream_events(
            chat_provider(request),
            visitor.owner_key,
            text,
            client_turn_id=client_turn_id.strip() or None,
            thread_id=thread_id.strip() or None,
            log_event="chat.stream_failed",
        ),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
    if visitor.new_owner:
        set_identity_cookie(response, request, visitor.identity, visitor.owner_key)
    return response
