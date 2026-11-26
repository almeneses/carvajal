"""Vista del stream SSE (W3.4): tokens, cierre y degradación.

Formato de cable: cada evento ocupa un bloque terminado en una línea en blanco,
con ``event: <nombre>`` y ``data: <json>``. La pregunta inválida (vacía o de más
de 500 caracteres) responde **422** con el mismo formato SSE y un evento
``error``; se elige SSE, y no JSON, para que el cliente tenga un único parser.

El evento ``done`` añade ``voz_resumen``: el resumen hablado de la respuesta guardada
(:func:`apps.api.voice.spoken_message`, el mismo que lee ``/api/voz/hablar``), para que la
lectura de respaldo del navegador diga lo mismo que la del servidor. Si no se puede
preparar, el evento sale sin él y el navegador lee la respuesta como antes.

El campo ``voz=1`` lo envía chat-stream.js solo si la persona habló la pregunta (borrador
dictado o manos libres): el servicio guarda la marca ``voice`` con la pregunta y el
historial ofrece audio solo en esas respuestas. Sin el campo, el turno es escrito.
"""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.service import SERVICE_UNAVAILABLE, ChatProvider
from apps.api.chat.streaming import ChatEvent
from apps.api.observability import capture_exception
from apps.api.security import verify_csrf
from apps.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, encode
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
from apps.api.voice import spoken_message
from carvajal.adapters.storage.chat import ChatMessage

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
    voz: str = Form(default=""),
) -> Response:
    """Responde la pregunta como eventos SSE y persiste el turno al cerrar."""

    # CSRF antes de la cuota y del caso de uso: sin token no se abre el stream.
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
    # Los tokens que genere el LLM quedan asociados a este visitante.
    structlog.contextvars.bind_contextvars(owner_key=owner_key)

    try:
        quota = consume_quota(request, owner_key)
    except SQLAlchemyError as error:
        logger.warning("chat.quota_failed", error=str(error), stream=True)
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
        _sse(
            chat_provider(request),
            owner_key,
            text,
            client_turn_id=client_turn_id.strip() or None,
            thread_id=thread_id.strip() or None,
            spoken=voz.strip() == "1",
        ),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


def _sse(
    provider: ChatProvider,
    owner_key: str,
    question: str,
    *,
    client_turn_id: str | None = None,
    thread_id: str | None = None,
    spoken: bool = False,
) -> Iterator[str]:
    """Traduce los eventos del servicio a bloques SSE.

    La red de seguridad evita que un fallo inesperado del proveedor deje la
    conexión sin cierre ni mensaje: siempre se emite un ``error`` genérico.
    """

    # El generador puede ejecutarse en otra tarea: se reafirma el contexto para
    # que los tokens del adaptador no pierdan el visitante.
    structlog.contextvars.bind_contextvars(owner_key=owner_key)
    saw_token = False
    cerrado = False
    fragments: list[str] = []
    sources: list[dict[str, Any]] = []
    try:
        for event in provider.stream(
            owner_key,
            question,
            client_turn_id=client_turn_id,
            thread_id=thread_id,
            spoken=spoken,
        ):
            saw_token = saw_token or event.name == "token"
            cerrado = event.name in {"done", "error"}
            if event.name == "token":
                fragments.append(str(event.data.get("text", "")))
            elif event.name == "fallback":
                fragments = [str(event.data.get("text", ""))]
            if event.name in {"meta", "fallback"} and isinstance(event.data.get("sources"), list):
                sources = [item for item in event.data["sources"] if isinstance(item, Mapping)]
            if event.name == "done":
                event = _with_spoken_summary(
                    provider, owner_key, event, "".join(fragments), sources
                )
            yield _encode(event)
    except Exception as error:
        logger.warning("chat.stream_failed", error=str(error))
        capture_exception(error)
        if not cerrado:
            yield _encode(
                ChatEvent(
                    "error",
                    {"message": SERVICE_UNAVAILABLE, "incomplete": saw_token},
                )
            )


def _with_spoken_summary(
    provider: ChatProvider,
    owner_key: str,
    event: ChatEvent,
    text: str,
    sources: list[dict[str, Any]],
) -> ChatEvent:
    """``done`` con ``voz_resumen``: de la respuesta guardada o, si no, de lo emitido.

    La respuesta guardada trae además el resultado comercial (la cotización corta). Nunca
    lanza: sin resumen, el ``done`` sale igual que antes.
    """

    try:
        message: ChatMessage | None = None
        message_id = str(event.data.get("message_id") or "")
        lookup = getattr(provider, "assistant_message", None)
        if message_id and callable(lookup):
            found = lookup(owner_key, message_id)
            message = found if isinstance(found, ChatMessage) else None
        if message is None and text.strip():
            message = ChatMessage(
                id=message_id,
                conversation_id="",
                role="assistant",
                content=text,
                sources=[dict(item) for item in sources],
                created_at=datetime.now(UTC),
            )
        summary = spoken_message(message) if message is not None else ""
    except Exception as error:  # el resumen es un extra: nunca rompe el cierre del turno
        logger.warning("chat.spoken_summary_failed", error_type=type(error).__name__)
        return event
    if not summary:
        return event
    return ChatEvent("done", {**event.data, "voz_resumen": summary})


def _encode(event: ChatEvent) -> str:
    """Serializa un evento al formato SSE con nombres de evento."""

    return encode(event.name, event.data)


def _invalid(
    message: str,
    *,
    status_code: int = 422,
    headers: dict[str, str] | None = None,
) -> Response:
    """Respuesta SSE controlada para una pregunta o un cupo que no se puede procesar."""

    return Response(
        content=_encode(ChatEvent("error", {"message": message, "incomplete": False})),
        status_code=status_code,
        media_type=SSE_MEDIA_TYPE,
        headers={"X-Carvajal-Error": "1", **SSE_HEADERS, **(headers or {})},
    )
