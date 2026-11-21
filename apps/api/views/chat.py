"""Vista del chat (W3.3): formulario, turnos e historial con cookie firmada."""

from collections.abc import Sequence
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.identity import ChatIdentity
from apps.api.chat.service import (
    SERVICE_UNAVAILABLE,
    ChatFailure,
    ThreadAccessError,
)
from apps.api.observability import REQUEST_ID_PATTERN, capture_exception
from apps.api.runtime import templates
from apps.api.views.chat_rules import (
    CSRF_INVALIDO,
    EMPTY_QUESTION,
    LIMITE_ALCANZADO,
    MAX_QUESTION_CHARS,
    TOO_LONG_QUESTION,
    chat_provider,
    consume_quota,
    request_identity,
    require_csrf,
    resolve_visitor,
    set_identity_cookie,
    validate_question,
)
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage.chat import ChatMessage, ConversationSummary
from carvajal.application.ask import LLMError
from carvajal.infrastructure.monitoring import monitoring_status
from carvajal.infrastructure.settings import Settings

__all__ = [
    "CSRF_INVALIDO",
    "EMPTY_QUESTION",
    "LIMITE_ALCANZADO",
    "MAX_QUESTION_CHARS",
    "SERVICE_UNAVAILABLE",
    "TOO_LONG_QUESTION",
    "chat_provider",
    "consume_quota",
    "request_identity",
    "require_csrf",
    "resolve_visitor",
    "router",
    "set_identity_cookie",
    "validate_question",
]

logger = structlog.get_logger(__name__)

router = APIRouter()

REQUEST_ID_VALIDO = REQUEST_ID_PATTERN
USEFUL_VALORES = {
    "1": True,
    "true": True,
    "si": True,
    "sí": True,
    "0": False,
    "false": False,
    "no": False,
}


@router.get("/chat", response_class=HTMLResponse)
def chat(
    request: Request,
    thread: str | None = None,
    panel: str | None = None,
) -> Response:
    """Muestra el hilo solicitado o el activo, con el panel de hilos."""

    identity = request_identity(request)
    owner_key = identity.read(request)
    new_owner = owner_key is None
    thread_id = (thread or "").strip() or None
    messages: Sequence[ChatMessage] = []
    if owner_key is None:
        owner_key = identity.new_owner_key()
    else:
        structlog.contextvars.bind_contextvars(owner_key=owner_key)
        try:
            provider = chat_provider(request)
            if thread_id:
                messages = provider.history(owner_key, thread_id=thread_id)
            else:
                messages = provider.history(owner_key)
        except ThreadAccessError as error:
            raise HTTPException(status_code=404, detail="El hilo solicitado no existe.") from error
        except SQLAlchemyError as error:
            logger.warning("chat.history_failed", error=str(error))
            return _page(
                request,
                [],
                thread_id=thread_id,
                error=SERVICE_UNAVAILABLE,
                status_code=503,
            )

    if thread_id is None and messages:
        thread_id = messages[0].conversation_id
    response = _page(
        request,
        messages,
        thread_id=thread_id,
        conversations=[] if new_owner else _conversations(request, owner_key),
        panel_hilos=panel == "hilos",
    )
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

    visitor = resolve_visitor(request)
    identity = visitor.identity
    owner_key = visitor.owner_key
    new_owner = visitor.new_owner
    structlog.contextvars.bind_contextvars(owner_key=owner_key)
    if not require_csrf(request, csrf_token):
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=visitor.new_owner,
            question="",
            message=CSRF_INVALIDO,
            status_code=403,
        )
    try:
        conversation = chat_provider(request).new_conversation(owner_key)
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
    response = RedirectResponse(
        url=f"/chat?thread={quote(conversation.id)}&panel=hilos",
        status_code=303,
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


@router.post("/chat/feedback", status_code=204)
def chat_feedback(
    request: Request,
    request_id: str = Form(default=""),
    useful: str = Form(default=""),
    csrf_token: str = Form(default=""),
) -> Response:
    """Guarda la marca útil / no útil de un turno (spec 038, US4).

    Exige CSRF, no consume cuota y no guarda texto. La marca se identifica por
    el ``request_id`` del evento ``done``; si el turno no se muestreó, la marca
    se conserva igual y no se crea ninguna traza (FR-012).
    """

    if not require_csrf(request, csrf_token):
        return Response(status_code=403)
    if REQUEST_ID_VALIDO.fullmatch(request_id.strip()) is None:
        return Response(status_code=400)
    valor = USEFUL_VALORES.get(useful.strip().lower())
    if valor is None:
        return Response(status_code=400)
    try:
        _record_feedback(request, request_id.strip(), valor)
    except SQLAlchemyError as error:
        logger.warning("chat.feedback_failed", error=str(error))
        return Response(status_code=503)
    return Response(status_code=204)


def _record_feedback(request: Request, request_id: str, useful: bool) -> None:
    """Marca inyectada en ``app.state.feedback`` o la real sobre PostgreSQL."""

    injected = getattr(request.app.state, "feedback", None)
    if injected is not None:
        injected(request_id, useful)
        return
    from apps.api import runtime
    from apps.api.chat.feedback import SqlFeedbackRepository

    with runtime.session_factory()() as session:
        SqlFeedbackRepository(session).record(request_id, useful)
        session.commit()


@router.post("/chat")
def chat_turn(
    request: Request,
    question: str = Form(default=""),
    csrf_token: str = Form(default=""),
    thread_id: str = Form(default=""),
) -> Response:
    """Responde un turno y redirige al hilo activo solo si se guardó."""

    hilo = thread_id.strip() or None
    visitor = resolve_visitor(request)
    identity = visitor.identity
    owner_key = visitor.owner_key
    new_owner = visitor.new_owner
    # Los tokens que genere el LLM quedan asociados a este visitante.
    structlog.contextvars.bind_contextvars(owner_key=owner_key)

    # CSRF antes de la cuota y del caso de uso: un token ajeno no cobra cupo.
    if not require_csrf(request, csrf_token):
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question=question,
            message=CSRF_INVALIDO,
            status_code=403,
            thread_id=hilo,
        )

    text, invalida = validate_question(question)
    if invalida is not None:
        return _error_page(
            request,
            identity,
            owner_key,
            new_owner=new_owner,
            question=question,
            message=invalida,
            status_code=422,
            thread_id=hilo,
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
            thread_id=hilo,
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
            thread_id=hilo,
        )

    try:
        provider = chat_provider(request)
        if hilo:
            provider.ask(owner_key, text, thread_id=hilo)
        else:
            provider.ask(owner_key, text)
    except ThreadAccessError as error:
        raise HTTPException(status_code=404, detail="El hilo solicitado no existe.") from error
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
            thread_id=hilo,
        )

    destino = f"/chat?thread={quote(hilo)}" if hilo else "/chat"
    response = RedirectResponse(url=destino, status_code=303)
    set_identity_cookie(response, request, identity, owner_key)
    return response


def _page(
    request: Request,
    messages: Sequence[ChatMessage],
    *,
    question: str = "",
    error: str | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    thread_id: str | None = None,
    conversations: Sequence[ConversationSummary] | None = None,
    panel_hilos: bool = False,
) -> Response:
    """Página completa del chat con su hilo, compositor y panel de hilos."""

    if error is not None:
        headers = {"X-Carvajal-Error": "1", **(headers or {})}
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "messages": messages,
            "question": question,
            "error": error,
            "thread_id": thread_id or "",
            "conversations": conversations or [],
            "panel_hilos": panel_hilos,
            "monitoring": getattr(
                request.app.state,
                "monitoring",
                monitoring_status(Settings()).enabled,
            ),
        },
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
    thread_id: str | None = None,
) -> Response:
    """Página completa de error, con formulario e historial si se puede leer."""

    try:
        history = _history(request, None if new_owner else owner_key, thread_id)
    except ThreadAccessError:
        history = []
        thread_id = None
    response = _page(
        request,
        history,
        question=question,
        error=message,
        status_code=status_code,
        headers=headers,
        thread_id=thread_id,
        conversations=_conversations(request, None if new_owner else owner_key),
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


def _history(
    request: Request,
    owner_key: str | None,
    thread_id: str | None = None,
) -> Sequence[ChatMessage]:
    """Historial de un hilo del dueño; vacío si no hay dueño o BD."""

    if owner_key is None:
        return []
    provider = chat_provider(request)
    try:
        if thread_id:
            return provider.history(owner_key, thread_id=thread_id)
        return provider.history(owner_key)
    except ThreadAccessError:
        raise
    except SQLAlchemyError as error:
        logger.warning("chat.history_failed", error=str(error))
        return []


def _conversations(
    request: Request,
    owner_key: str | None,
) -> Sequence[ConversationSummary]:
    """Hilos del dueño para el panel lateral; vacío si el proveedor no los expone."""

    if owner_key is None:
        return []
    provider = chat_provider(request)
    listar = getattr(provider, "conversations", None)
    if listar is None:
        return []
    try:
        return listar(owner_key)
    except SQLAlchemyError as error:
        logger.warning("chat.conversations_failed", error=str(error))
        return []
