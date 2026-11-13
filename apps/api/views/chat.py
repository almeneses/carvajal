"""Vista del chat (W3.3): formulario, turnos e historial con cookie firmada."""

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.identity import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    ChatIdentity,
    default_identity,
)
from apps.api.chat.service import (
    SERVICE_UNAVAILABLE,
    ChatFailure,
    ChatProvider,
    default_chat_service,
)
from apps.api.observability import capture_exception
from apps.api.runtime import templates
from apps.api.security import verify_csrf
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage.chat import ChatMessage
from carvajal.application.ask import LLMError
from carvajal.application.quota import QuotaResult

__all__ = [
    "CSRF_INVALIDO",
    "LIMITE_ALCANZADO",
    "chat_provider",
    "consume_quota",
    "request_identity",
    "router",
    "set_identity_cookie",
]

logger = structlog.get_logger(__name__)

router = APIRouter()

MAX_QUESTION_CHARS = 500
EMPTY_QUESTION = "La pregunta no puede estar vacía."
TOO_LONG_QUESTION = f"La pregunta no puede superar los {MAX_QUESTION_CHARS} caracteres."
LIMITE_ALCANZADO = "Has alcanzado el límite diario de preguntas. Vuelve a intentarlo mañana."
CSRF_INVALIDO = "La sesión del formulario expiró. Recarga la página e inténtalo de nuevo."


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
        structlog.contextvars.bind_contextvars(owner_key=owner_key)
        try:
            messages = chat_provider(request).history(owner_key)
        except SQLAlchemyError as error:
            logger.warning("chat.history_failed", error=str(error))
            return _page(request, [], error=SERVICE_UNAVAILABLE, status_code=503)

    response = _page(request, messages)
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


@router.post("/chat/new")
def new_chat(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Crea un hilo nuevo del mismo dueño y vuelve al chat.

    La memoria de largo plazo vive en el Store por dueño, no por hilo: abrir un
    hilo nuevo demuestra que la preferencia se recupera entre hilos.
    """

    identity = request_identity(request)
    stored_owner = identity.read(request)
    new_owner = stored_owner is None
    owner_key = stored_owner or identity.new_owner_key()
    structlog.contextvars.bind_contextvars(owner_key=owner_key)
    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question="",
            message=CSRF_INVALIDO,
            status_code=403,
        )
    try:
        chat_provider(request).new_conversation(owner_key)
    except SQLAlchemyError as error:
        logger.warning("chat.new_thread_failed", error=str(error))
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question="",
            message=SERVICE_UNAVAILABLE,
            status_code=503,
        )
    response = RedirectResponse(url="/chat", status_code=303)
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


@router.post("/chat")
def chat_turn(
    request: Request,
    question: str = Form(default=""),
    csrf_token: str = Form(default=""),
) -> Response:
    """Responde un turno y redirige a ``/chat`` solo si la respuesta se guardó."""

    text = question.strip()
    identity = request_identity(request)
    stored_owner = identity.read(request)
    new_owner = stored_owner is None
    owner_key = stored_owner or identity.new_owner_key()
    # Los tokens que genere el LLM quedan asociados a este visitante.
    structlog.contextvars.bind_contextvars(owner_key=owner_key)

    # CSRF antes de la cuota y del caso de uso: un token ajeno no cobra cupo.
    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question=question,
            message=CSRF_INVALIDO,
            status_code=403,
        )

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
        quota = consume_quota(request, owner_key)
    except SQLAlchemyError as error:
        logger.warning("chat.quota_failed", error=str(error))
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question=question,
            message=SERVICE_UNAVAILABLE,
            status_code=503,
        )
    if not quota.allowed:
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question=question,
            message=LIMITE_ALCANZADO,
            status_code=429,
            headers={"Retry-After": str(quota.retry_after)},
        )

    try:
        chat_provider(request).ask(owner_key, text)
    except (ChatFailure, EmbeddingError, LLMError, SQLAlchemyError) as error:
        logger.warning("chat.ask_failed", error=str(error))
        if isinstance(error, LLMError):
            capture_exception(error)
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


def consume_quota(request: Request, owner_key: str) -> QuotaResult:
    """Cobra una petición de la cuota inyectada o de la real; nunca la duplica."""

    quota = getattr(request.app.state, "quota", None)
    if quota is None:
        return QuotaResult(allowed=True)
    return quota(owner_key)


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
    question: str = "",
    error: str | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    """Página completa del chat con su hilo de mensajes."""

    if error is not None:
        headers = {"X-Carvajal-Error": "1", **(headers or {})}
    return templates.TemplateResponse(
        request,
        "chat.html",
        {"messages": messages, "question": question, "error": error},
        status_code=status_code,
        headers=headers,
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
    headers: dict[str, str] | None = None,
) -> Response:
    """Página completa de error, con formulario e historial si se puede leer."""

    response = _page(
        request,
        _history(request, None if new_owner else owner_key),
        question=question,
        error=message,
        status_code=status_code,
        headers=headers,
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


def _history(request: Request, owner_key: str | None) -> Sequence[ChatMessage]:
    """Historial previo para una página de error; vacío si no hay dueño o BD."""

    if owner_key is None:
        return []
    try:
        return chat_provider(request).history(owner_key)
    except SQLAlchemyError as error:
        logger.warning("chat.history_failed", error=str(error))
        return []
