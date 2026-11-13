"""Vista del chat (W3.3): formulario, turnos e historial con cookie firmada."""

from collections.abc import Sequence
from logging import getLogger

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.identity import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    ChatIdentity,
    default_identity,
)
from apps.api.chat.service import ChatProvider, default_chat_service
from apps.api.runtime import templates
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage.chat import ChatMessage
from carvajal.application.ask import LLMError

__all__ = ["chat_provider", "request_identity", "router", "set_identity_cookie"]

logger = getLogger(__name__)

router = APIRouter()

MAX_QUESTION_CHARS = 500
EMPTY_QUESTION = "La pregunta no puede estar vacía."
TOO_LONG_QUESTION = f"La pregunta no puede superar los {MAX_QUESTION_CHARS} caracteres."
SERVICE_UNAVAILABLE = "El servicio de chat no está disponible en este momento."


@router.get("/chat", response_class=HTMLResponse)
def chat(request: Request) -> Response:
    """Muestra el historial de la conversación activa del visitante."""

    identity = request_identity(request)
    owner_key = identity.read(request)
    new_owner = owner_key is None
    if owner_key is None:
        owner_key = identity.new_owner_key()
        messages: Sequence[ChatMessage] = []
    else:
        try:
            messages = chat_provider(request).history(owner_key)
        except SQLAlchemyError as error:
            logger.warning("Fallo al leer el historial del chat: %s", error)
            return _page(request, [], error=SERVICE_UNAVAILABLE, status_code=503)

    response = _page(request, messages)
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


@router.post("/chat")
def chat_turn(request: Request, question: str = Form(default="")) -> Response:
    """Responde un turno y redirige a ``/chat`` solo si la respuesta se guardó."""

    text = question.strip()
    identity = request_identity(request)
    owner_key = identity.read(request)
    if owner_key is None:
        owner_key = identity.new_owner_key()

    if not text:
        return _error(request, EMPTY_QUESTION, 422)
    if len(text) > MAX_QUESTION_CHARS:
        return _error(request, TOO_LONG_QUESTION, 422)

    try:
        chat_provider(request).ask(owner_key, text)
    except (EmbeddingError, LLMError, SQLAlchemyError) as error:
        logger.warning("Fallo al responder en el chat: %s", error)
        return _error(request, SERVICE_UNAVAILABLE, 503)

    response = RedirectResponse(url="/chat", status_code=303)
    set_identity_cookie(response, request, identity, owner_key)
    return response


def chat_provider(request: Request) -> ChatProvider:
    """Devuelve el doble inyectado en ``app.state.chat`` o el servicio real."""

    injected = getattr(request.app.state, "chat", None)
    return injected if injected is not None else default_chat_service()


def request_identity(request: Request) -> ChatIdentity:
    """Devuelve la identidad inyectada en ``app.state.chat_identity`` o la del proceso."""

    injected = getattr(request.app.state, "chat_identity", None)
    return injected if injected is not None else default_identity()


def set_identity_cookie(
    response: Response,
    request: Request,
    identity: ChatIdentity,
    owner_key: str,
) -> None:
    """Emite la cookie firmada, ``Secure`` solo cuando la petición es HTTPS."""

    response.set_cookie(
        COOKIE_NAME,
        identity.issue(owner_key),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def _page(
    request: Request,
    messages: Sequence[ChatMessage],
    *,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    """Página completa del chat con su hilo de mensajes."""

    return templates.TemplateResponse(
        request,
        "chat.html",
        {"messages": messages, "error": error},
        status_code=status_code,
    )


def _error(request: Request, message: str, status_code: int) -> Response:
    """Parcial de error para un POST que no llegó a persistir."""

    return templates.TemplateResponse(
        request,
        "_chat_thread.html",
        {"messages": [], "error": message},
        status_code=status_code,
    )
