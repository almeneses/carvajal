"""Vista del stream SSE (W3.4): tokens, cierre y degradación.

Formato de cable: cada evento ocupa un bloque terminado en una línea en blanco,
con ``event: <nombre>`` y ``data: <json>``. La pregunta inválida (vacía o de más
de 500 caracteres) responde **422** con el mismo formato SSE y un evento
``error``; se elige SSE, y no JSON, para que el cliente tenga un único parser.
"""

import json
from collections.abc import Iterator
from logging import getLogger

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response, StreamingResponse

from apps.api.chat.service import SERVICE_UNAVAILABLE, ChatProvider
from apps.api.chat.streaming import ChatEvent
from apps.api.views.chat import (
    EMPTY_QUESTION,
    MAX_QUESTION_CHARS,
    TOO_LONG_QUESTION,
    chat_provider,
    request_identity,
    set_identity_cookie,
)

__all__ = ["router"]

logger = getLogger(__name__)

router = APIRouter()

SSE_MEDIA_TYPE = "text/event-stream"
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@router.post("/chat/stream")
def chat_stream(request: Request, question: str = Form(default="")) -> Response:
    """Responde la pregunta como eventos SSE y persiste el turno al cerrar."""

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

    response = StreamingResponse(
        _sse(chat_provider(request), owner_key, text),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


def _sse(provider: ChatProvider, owner_key: str, question: str) -> Iterator[str]:
    """Traduce los eventos del servicio a bloques SSE.

    La red de seguridad evita que un fallo inesperado del proveedor deje la
    conexión sin cierre ni mensaje: siempre se emite un ``error`` genérico.
    """

    saw_token = False
    cerrado = False
    try:
        for event in provider.stream(owner_key, question):
            saw_token = saw_token or event.name == "token"
            cerrado = event.name in {"done", "error"}
            yield _encode(event)
    except Exception as error:
        logger.warning("El stream del chat se cortó de forma inesperada: %s", error)
        if not cerrado:
            yield _encode(
                ChatEvent(
                    "error",
                    {"message": SERVICE_UNAVAILABLE, "incomplete": saw_token},
                )
            )


def _encode(event: ChatEvent) -> str:
    """Serializa un evento al formato SSE con nombres de evento."""

    payload = json.dumps(dict(event.data), ensure_ascii=False)
    return f"event: {event.name}\ndata: {payload}\n\n"


def _invalid(message: str) -> Response:
    """Respuesta 422 controlada para una pregunta que no se puede procesar."""

    return Response(
        content=_encode(ChatEvent("error", {"message": message, "incomplete": False})),
        status_code=422,
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
