"""Vista del chat (W3.3): formulario, turnos e historial con cookie firmada."""

from collections.abc import Sequence
from dataclasses import replace
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Form, HTTPException, Request
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
    ThreadAccessError,
    default_chat_service,
)
from apps.api.observability import REQUEST_ID_PATTERN, capture_exception
from apps.api.runtime import templates
from apps.api.security import verify_csrf
from apps.api.views.cotizacion_pdf import owner_pdf_link
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage.chat import ChatMessage, ConversationSummary
from carvajal.application.ask import LLMError
from carvajal.application.quota import QuotaResult
from carvajal.infrastructure.monitoring import monitoring_status
from carvajal.infrastructure.settings import Settings
from carvajal.quotation.render import pdf_offer

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
    nuevo: str | None = None,
) -> Response:
    """Muestra el hilo solicitado o el activo, con el panel de hilos.

    ``panel=hilos`` abre el panel al cargar; ``nuevo=1`` marca el hilo recién creado por
    «Nuevo hilo»: el chat vacío queda a la vista con un aviso breve de que el anterior se
    guardó, y el foco va al compositor.
    """

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
    conversations = [] if new_owner else _conversations(request, owner_key)
    response = _page(
        request,
        messages,
        thread_id=thread_id,
        conversations=conversations,
        panel_hilos=panel == "hilos",
        hilo_nuevo=nuevo == "1" and not messages and _has_saved_thread(conversations, thread_id),
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

    Redirige al hilo nuevo sin abrir el panel de hilos (decisión del owner del
    2026-11-26; antes abría el panel encima del chat, PR #114): ``nuevo=1`` solo
    muestra el aviso de que el hilo anterior quedó guardado en «Hilos».
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
        url=f"/chat?thread={quote(conversation.id)}&nuevo=1",
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

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
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
    client_turn_id: str = Form(default=""),
    thread_id: str = Form(default=""),
) -> Response:
    """Responde un turno y redirige al hilo activo solo si se guardó."""

    text = question.strip()
    hilo = thread_id.strip() or None
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
            thread_id=hilo,
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
            thread_id=hilo,
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
        turn_id = client_turn_id.strip() or None
        provider.ask(
            owner_key,
            text,
            client_turn_id=turn_id,
            thread_id=hilo,
        )
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


def peek_quota(request: Request, owner_key: str) -> QuotaResult:
    """Dice si quedaría cupo sin cobrarlo (``app.state.quota_peek``); sin inyección, sí."""

    peek = getattr(request.app.state, "quota_peek", None)
    if peek is None:
        return QuotaResult(allowed=True)
    return peek(owner_key)


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
    thread_id: str | None = None,
    conversations: Sequence[ConversationSummary] | None = None,
    panel_hilos: bool = False,
    hilo_nuevo: bool = False,
) -> Response:
    """Página completa del chat con su hilo, compositor y panel de hilos."""

    if error is not None:
        headers = {"X-Carvajal-Error": "1", **(headers or {})}
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "messages": _with_pdf_links(request, messages),
            "question": question,
            "error": error,
            "thread_id": thread_id or "",
            "conversations": conversations or [],
            "panel_hilos": panel_hilos,
            "hilo_nuevo": hilo_nuevo,
            "monitoring": getattr(
                request.app.state,
                "monitoring",
                monitoring_status(Settings()).enabled,
            ),
            # Spec 047: el chat ofrece la voz del servidor; si falla, vuelve a la del navegador.
            "voz_servidor": _voice_available(request),
            "voz_max_segundos": _voice_max_seconds(request),
            "voz_plazo_segundos": _voice_client_timeout(request),
            # Spec 051: el botón de foto solo aparece con el clasificador disponible.
            "foto_disponible": _photo_available(request),
        },
        status_code=status_code,
        headers=headers,
    )


def _with_pdf_links(request: Request, messages: Sequence[ChatMessage]) -> list[ChatMessage]:
    """Historial con el enlace al PDF en cada cotización registrada (T6, spec 054).

    El texto guardado es el de todos los canales («📄 Te envío el PDF.»); en la web la línea
    pasa a ser el enlace del folio, que el dueño abre con su cookie. Se arma al mostrar el
    hilo, así no queda guardada una URL de un host ni un token que venza.
    """

    link = owner_pdf_link(request)
    return [
        replace(message, content=pdf_offer(message.content, link))
        if message.role == "assistant" and message.content
        else message
        for message in messages
    ]


def _photo_available(request: Request) -> bool:
    from apps.api.views.foto import photo_available

    try:
        return photo_available(request)
    except Exception as error:  # la página del chat nunca cae por la foto
        logger.warning("photo.flag_failed", error_type=type(error).__name__)
        return False


def _voice_available(request: Request) -> bool:
    from apps.api.views.voz import voice_available

    try:
        return voice_available(request)
    except Exception as error:  # la página del chat nunca cae por la voz
        logger.warning("voice.flag_failed", error_type=type(error).__name__)
        return False


def _voice_max_seconds(request: Request) -> int:
    settings = getattr(request.app.state, "settings", None)
    return settings.voice_max_audio_seconds if isinstance(settings, Settings) else 60


def _voice_client_timeout(request: Request) -> int:
    """Plazo del navegador para ``/api/voz/*``: el de OpenAI más margen para la subida."""

    settings = getattr(request.app.state, "settings", None)
    timeout = settings.voice_timeout_s if isinstance(settings, Settings) else 20.0
    return int(timeout) + 15


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


def _has_saved_thread(
    conversations: Sequence[ConversationSummary],
    thread_id: str | None,
) -> bool:
    """Si hay otro hilo con mensajes: solo entonces es cierto que «el anterior quedó guardado».

    Un visitante que pulsa «Nuevo hilo» sin haber escrito no tiene hilo anterior, y pulsarlo
    dos veces seguidas solo deja un «Hilo vacío»: en ambos casos el aviso no se muestra.
    """

    return any(c.id != thread_id and c.preview for c in conversations)


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
