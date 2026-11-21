"""Reglas compartidas de las entradas del chat: validación, identidad y cuota.

El formulario (``POST /chat``), el stream web (``POST /chat/stream``) y el stream
de la API (``POST /api/chat/stream``) comparten estas piezas; la FAQ reutiliza
identidad, CSRF y cuota. La vista decide el orden de los controles y el formato
de la respuesta, pero la regla y el mensaje viven una sola vez aquí.

Este módulo no cambia contratos: las constantes y los mensajes son los mismos
que publicaba ``apps.api.views.chat`` y que fijan los tests de caracterización.
"""

from collections.abc import Iterator
from dataclasses import dataclass

import structlog
from fastapi import Request
from fastapi.responses import Response

from apps.api.chat.identity import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    ChatIdentity,
    default_identity,
)
from apps.api.chat.service import SERVICE_UNAVAILABLE, ChatProvider, default_chat_service
from apps.api.observability import capture_exception
from apps.api.security import verify_csrf
from apps.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, encode
from carvajal.application.quota import QuotaResult

__all__ = [
    "CSRF_INVALIDO",
    "EMPTY_QUESTION",
    "LIMITE_ALCANZADO",
    "MAX_QUESTION_CHARS",
    "TOO_LONG_QUESTION",
    "Visitor",
    "chat_provider",
    "consume_quota",
    "invalid_response",
    "request_identity",
    "require_csrf",
    "resolve_visitor",
    "set_identity_cookie",
    "stream_events",
    "validate_question",
]

logger = structlog.get_logger(__name__)

MAX_QUESTION_CHARS = 500
EMPTY_QUESTION = "La pregunta no puede estar vacía."
TOO_LONG_QUESTION = f"La pregunta no puede superar los {MAX_QUESTION_CHARS} caracteres."
LIMITE_ALCANZADO = "Has alcanzado el límite diario de preguntas. Vuelve a intentarlo mañana."
CSRF_INVALIDO = "La sesión del formulario expiró. Recarga la página e inténtalo de nuevo."


@dataclass(frozen=True)
class Visitor:
    """Identidad resuelta de la petición y si el visitante es nuevo."""

    identity: ChatIdentity
    owner_key: str
    new_owner: bool


def validate_question(question: str) -> tuple[str, str | None]:
    """Recorta la pregunta y devuelve el mensaje de error, o ``None`` si es válida."""

    text = question.strip()
    if not text:
        return text, EMPTY_QUESTION
    if len(text) > MAX_QUESTION_CHARS:
        return text, TOO_LONG_QUESTION
    return text, None


def require_csrf(request: Request, token: str) -> bool:
    """Comprueba el CSRF y deja el mismo aviso de log que las vistas."""

    if verify_csrf(request, token):
        return True
    logger.warning("csrf.rejected", path=request.url.path)
    return False


def request_identity(request: Request) -> ChatIdentity:
    """Devuelve la identidad inyectada en ``app.state.chat_identity`` o la del proceso."""

    injected = getattr(request.app.state, "chat_identity", None)
    return injected if injected is not None else default_identity()


def resolve_visitor(request: Request) -> Visitor:
    """Resuelve dueño y condición de nuevo visitante sin emitir la cookie."""

    identity = request_identity(request)
    stored_owner = identity.read(request)
    if stored_owner is None:
        return Visitor(identity, identity.new_owner_key(), True)
    return Visitor(identity, stored_owner, False)


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


def chat_provider(request: Request) -> ChatProvider:
    """Devuelve el doble inyectado en ``app.state.chat`` o el servicio real."""

    injected = getattr(request.app.state, "chat", None)
    return injected if injected is not None else default_chat_service()


def consume_quota(request: Request, owner_key: str) -> QuotaResult:
    """Cobra una petición de la cuota inyectada o de la real; nunca la duplica."""

    quota = getattr(request.app.state, "quota", None)
    if quota is None:
        return QuotaResult(allowed=True)
    return quota(owner_key)


def invalid_response(
    message: str,
    *,
    status_code: int = 422,
    headers: dict[str, str] | None = None,
) -> Response:
    """Respuesta SSE controlada para CSRF, validación o cupo no procesable."""

    return Response(
        content=encode("error", {"message": message, "incomplete": False}),
        status_code=status_code,
        media_type=SSE_MEDIA_TYPE,
        headers={"X-Carvajal-Error": "1", **SSE_HEADERS, **(headers or {})},
    )


def stream_events(
    provider: ChatProvider,
    owner_key: str,
    question: str,
    *,
    client_turn_id: str | None = None,
    thread_id: str | None = None,
    log_event: str,
) -> Iterator[str]:
    """Traduce los eventos del servicio a bloques SSE con cierre de seguridad.

    ``log_event`` conserva el nombre de log de cada entrada (``chat`` o ``api``).
    La red de seguridad evita que un fallo inesperado deje la conexión sin cierre:
    siempre se emite un ``error`` si el proveedor no lo hizo.
    """

    # El generador puede ejecutarse en otra tarea: se reafirma el contexto para
    # que los tokens del adaptador no pierdan el visitante.
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
            yield encode(event.name, event.data)
    except Exception as error:
        logger.warning(log_event, error=str(error))
        capture_exception(error)
        if not closed:
            yield encode(
                "error",
                {"message": SERVICE_UNAVAILABLE, "incomplete": saw_token},
            )
