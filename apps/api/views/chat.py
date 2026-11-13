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

__all__ = ["router"]

logger = getLogger(__name__)

router = APIRouter()

MAX_QUESTION_CHARS = 500
EMPTY_QUESTION = "La pregunta no puede estar vacía."
TOO_LONG_QUESTION = f"La pregunta no puede superar los {MAX_QUESTION_CHARS} caracteres."
SERVICE_UNAVAILABLE = "El servicio de chat no está disponible en este momento."


@router.get("/chat", response_class=HTMLResponse)
def chat(request: Request) -> Response:
    """Muestra el historial de la conversación activa del visitante."""

    identity = _identity(request)
    owner_key = identity.read(request)
    new_owner = owner_key is None
    if owner_key is None:
        owner_key = identity.new_owner_key()
        messages: Sequence[ChatMessage] = []
    else:
        try:
            messages = _provider(request).history(owner_key)
        except SQLAlchemyError as error:
            logger.warning("Fallo al leer el historial del chat: %s", error)
            return _page(request, [], error=SERVICE_UNAVAILABLE, status_code=503)

    response = _page(request, messages)
    if new_owner:
        _set_cookie(response, request, identity, owner_key)
    return response


@router.post("/chat")
def chat_turn(request: Request, question: str = Form(default="")) -> Response:
    """Responde un turno y redirige a ``/chat`` solo si la respuesta se guardó."""

    text = question.strip()
    identity = _identity(request)
    stored_owner = identity.read(request)
    new_owner = stored_owner is None
    owner_key = stored_owner or identity.new_owner_key()

    if not text:
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question=question,
            message=EMPTY_QUESTION,
            status_code=422,
        )
    if len(text) > MAX_QUESTION_CHARS:
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question=question,
            message=TOO_LONG_QUESTION,
            status_code=422,
        )

    try:
        _provider(request).ask(owner_key, text)
    except (EmbeddingError, LLMError, SQLAlchemyError) as error:
        logger.warning("Fallo al responder en el chat: %s", error)
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question=question,
            message=SERVICE_UNAVAILABLE,
            status_code=503,
        )

    response = RedirectResponse(url="/chat", status_code=303)
    _set_cookie(response, request, identity, owner_key)
    return response


def _provider(request: Request) -> ChatProvider:
    """Doble inyectado en ``app.state.chat`` o servicio real perezoso."""

    injected = getattr(request.app.state, "chat", None)
    return injected if injected is not None else default_chat_service()


def _identity(request: Request) -> ChatIdentity:
    """Identidad inyectada en ``app.state.chat_identity`` o la del proceso."""

    injected = getattr(request.app.state, "chat_identity", None)
    return injected if injected is not None else default_identity()


def _set_cookie(
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
    question: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    """Página completa del chat con su hilo de mensajes."""

    return templates.TemplateResponse(
        request,
        "chat.html",
        {"messages": messages, "question": question, "error": error},
        status_code=status_code,
    )


def _error_page(
    request: Request,
    identity: ChatIdentity,
    owner_key: str,
    *,
    new_owner: bool,
    question: str,
    message: str,
    status_code: int,
) -> Response:
    """Página completa de error, con formulario e historial si se puede leer."""

    response = _page(
        request,
        _history(request, None if new_owner else owner_key),
        question=question,
        error=message,
        status_code=status_code,
    )
    if new_owner:
        _set_cookie(response, request, identity, owner_key)
    return response


def _history(request: Request, owner_key: str | None) -> Sequence[ChatMessage]:
    """Historial previo para una página de error; vacío si no hay dueño o BD."""

    if owner_key is None:
        return []
    try:
        return _provider(request).history(owner_key)
    except SQLAlchemyError as error:
        logger.warning("Fallo al leer el historial durante un error del chat: %s", error)
        return []
