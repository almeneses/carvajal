"""Vista del Cotizador: conversación, motor determinista e historial aislado.

Los hilos del Cotizador viven en las mismas tablas del chat, bajo un espacio de nombres
derivado del dueño firmado (``cot:<owner_key>``). El cliente nunca envía ese valor.
"""

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

import structlog
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from apps.api import runtime
from apps.api.security import verify_csrf
from apps.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, encode
from apps.api.views.chat import (
    CSRF_INVALIDO,
    LIMITE_ALCANZADO,
    MAX_QUESTION_CHARS,
    SERVICE_UNAVAILABLE,
    consume_quota,
    request_identity,
    set_identity_cookie,
)
from carvajal.adapters.storage.chat import ChatMessage, ConversationSummary, SqlChatRepository
from carvajal.adapters.storage.quotes import SqlQuoteRepository
from carvajal.adapters.storage.visitor_memory import SqlVisitorMemory
from carvajal.quotation import quote
from carvajal.quotation.data import DEFAULT_DIR, DemoDataError
from carvajal.quotation.memory import extract, forget_requested, merge

__all__ = ["CotizadorStore", "SqlCotizadorStore", "router"]

logger = structlog.get_logger(__name__)
router = APIRouter()

PREFIX = "cot:"
EMPTY = "Escribe qué necesitas cotizar."
HISTORY_QUESTIONS = 12
DEMO_OFF = {"0", "false", "no"}
FICHAS = DEFAULT_DIR / "fichas"
SLUG = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
QUOTE_DAYS = 7


class ThreadAccessError(PermissionError):
    """El hilo no existe o pertenece a otro dueño."""


@runtime_checkable
class CotizadorStore(Protocol):
    """Historial del Cotizador; la vista no conoce SQLAlchemy."""

    def history(
        self, owner_key: str, thread_id: str | None
    ) -> tuple[str | None, Sequence[ChatMessage]]:
        """Devuelve el hilo y sus mensajes."""

        ...

    def append(
        self,
        owner_key: str,
        *,
        question: str,
        answer: str,
        sources: Sequence[Mapping[str, Any]],
        thread_id: str | None,
    ) -> tuple[str, ChatMessage, ChatMessage]:
        """Guarda el turno y devuelve el hilo y los dos mensajes."""

        ...

    def new(self, owner_key: str) -> str:
        """Crea un hilo y devuelve su id."""

        ...

    def conversations(self, owner_key: str) -> Sequence[ConversationSummary]:
        """Lista los hilos del dueño."""

        ...

    def memory(self, owner_key: str) -> dict[str, Any]:
        """Perfil recordado, vacío si no hay o caducó."""

        ...

    def remember(self, owner_key: str, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Guarda el perfil y devuelve lo que quedó."""

        ...

    def forget(self, owner_key: str) -> None:
        """Borra lo recordado del visitante."""

        ...

    def issue_quote(
        self,
        owner_key: str,
        *,
        quote_id: str,
        payload: Mapping[str, Any],
        total: Decimal,
        currency: str,
        valid_until: Any,
        conversation_id: str | None,
    ) -> None:
        """Registra la cotización emitida para auditoría."""

        ...


class SqlCotizadorStore:
    """Implementación sobre PostgreSQL con el prefijo del Cotizador."""

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    def _owner(self, owner_key: str) -> str:
        return f"{PREFIX}{owner_key}"

    def history(
        self, owner_key: str, thread_id: str | None
    ) -> tuple[str | None, Sequence[ChatMessage]]:
        with self._factory() as session:
            repo = SqlChatRepository(session)
            owner = self._owner(owner_key)
            if thread_id:
                conversation = repo.get_owned(thread_id, owner)
                if conversation is None:
                    raise ThreadAccessError(thread_id)
            else:
                conversation = repo.active(owner)
                if conversation is None:
                    return None, []
            # Cierra la transacción de solo lectura antes de tocar el motor.
            session.rollback()
            return conversation.id, repo.history(conversation.id)

    def append(
        self,
        owner_key: str,
        *,
        question: str,
        answer: str,
        sources: Sequence[Mapping[str, Any]],
        thread_id: str | None,
    ) -> tuple[str, ChatMessage, ChatMessage]:
        with self._factory.begin() as session:
            repo = SqlChatRepository(session)
            owner = self._owner(owner_key)
            if thread_id:
                conversation = repo.get_owned(thread_id, owner)
                if conversation is None:
                    raise ThreadAccessError(thread_id)
            else:
                conversation = repo.get_or_create(owner)
            user, assistant = repo.append_turn(
                conversation.id, question=question, answer=answer, sources=sources
            )
            return conversation.id, user, assistant

    def new(self, owner_key: str) -> str:
        with self._factory.begin() as session:
            return SqlChatRepository(session).create(self._owner(owner_key)).id

    def conversations(self, owner_key: str) -> Sequence[ConversationSummary]:
        with self._factory() as session:
            items = SqlChatRepository(session).summarize(self._owner(owner_key))
            session.rollback()
            return items

    def memory(self, owner_key: str) -> dict[str, Any]:
        # La lectura puede borrar un perfil caducado: la transacción lo confirma.
        with self._factory.begin() as session:
            return SqlVisitorMemory(session).get(self._owner(owner_key))

    def remember(self, owner_key: str, profile: Mapping[str, Any]) -> dict[str, Any]:
        with self._factory.begin() as session:
            return SqlVisitorMemory(session).save(self._owner(owner_key), profile)

    def forget(self, owner_key: str) -> None:
        with self._factory.begin() as session:
            SqlVisitorMemory(session).forget(self._owner(owner_key))

    def issue_quote(
        self,
        owner_key: str,
        *,
        quote_id: str,
        payload: Mapping[str, Any],
        total: Decimal,
        currency: str,
        valid_until: Any,
        conversation_id: str | None,
    ) -> None:
        with self._factory.begin() as session:
            SqlQuoteRepository(session).record(
                quote_id,
                self._owner(owner_key),
                payload=payload,
                total=total,
                currency=currency,
                valid_until=valid_until,
                conversation_id=conversation_id,
            )


def cotizador_store(request: Request) -> CotizadorStore:
    """Doble inyectado en las pruebas o el almacén real sobre PostgreSQL."""

    state = getattr(request.app, "state", None)
    injected = getattr(state, "cotizador", None) if state is not None else None
    return injected if injected is not None else SqlCotizadorStore(runtime.session_factory())


def demo_enabled() -> bool:
    """``COTIZADOR_DEMO=0`` apaga lo simulado y deja solo lo publicado."""

    return os.environ.get("COTIZADOR_DEMO", "1").strip().lower() not in DEMO_OFF


DELETED = "Listo, borré lo que tenía guardado de ti."


def _issue_quote(
    store: CotizadorStore, owner_key: str, payload: dict[str, Any], conversation_id: str | None
) -> dict[str, Any]:
    """Identifica y registra la cotización; solo aplica a cotizaciones completas."""

    if payload.get("kind") != "cotizacion" or not payload.get("primary"):
        return payload
    primary = payload["primary"]
    quote_id = uuid4().hex[:12]
    valid = (datetime.now(UTC).date() + timedelta(days=QUOTE_DAYS)).isoformat()
    payload = {**payload, "quote": {"id": quote_id, "valid_until": valid, "days": QUOTE_DAYS}}
    store.issue_quote(
        owner_key,
        quote_id=quote_id,
        payload=payload,
        total=Decimal(str(payload.get("total") or primary["presentation"]["subtotal"])),
        currency=primary["currency"],
        valid_until=datetime.fromisoformat(valid).date(),
        conversation_id=conversation_id,
    )
    return payload


def _profile(store: CotizadorStore, owner_key: str, text: str) -> tuple[dict[str, Any], bool]:
    """Lee, actualiza o borra la memoria del visitante.

    Un fallo de la base no impide cotizar: se sigue con memoria vacía.
    """

    try:
        if forget_requested(text):
            store.forget(owner_key)
            return {}, True
        updates = extract(text)
        current = store.memory(owner_key)
        if not updates:
            return current, False
        return store.remember(owner_key, merge(current, updates)), False
    except SQLAlchemyError as error:
        logger.warning("cotizador.memory_failed", error=str(error))
        return {}, False


def _page(
    request: Request,
    messages: Sequence[ChatMessage],
    *,
    thread_id: str | None = None,
    conversations: Sequence[ConversationSummary] = (),
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    return runtime.templates.TemplateResponse(
        request,
        "cotizador.html",
        {
            "messages": messages,
            "thread_id": thread_id or "",
            "conversations": conversations,
            "error": error,
        },
        status_code=status_code,
    )


def _turno(
    request: Request,
    messages: Sequence[ChatMessage],
    *,
    thread_id: str,
    status_code: int = 200,
) -> HTMLResponse:
    return runtime.templates.TemplateResponse(
        request,
        "_cotizador_turno.html",
        {"messages": messages, "thread_id": thread_id},
        status_code=status_code,
        headers={"X-Thread": thread_id},
    )


def _error_turno(message: str, *, thread_id: str, status_code: int) -> HTMLResponse:
    html = (
        '<li class="turno turno-assistant"><div class="burbuja"><p class="aviso" '
        f'role="alert">{message}</p></div></li>'
    )
    return HTMLResponse(html, status_code=status_code, headers={"X-Thread": thread_id})


def _quota(request: Request, owner_key: str) -> Response | None:
    try:
        result = consume_quota(request, owner_key)
    except SQLAlchemyError as error:
        logger.warning("cotizador.quota_failed", error=str(error))
        return _error_turno(SERVICE_UNAVAILABLE, thread_id="", status_code=503)
    if result.allowed:
        return None
    response = _error_turno(LIMITE_ALCANZADO, thread_id="", status_code=429)
    response.headers["Retry-After"] = str(result.retry_after)
    return response


@router.get("/cotizador", response_class=HTMLResponse)
def page(
    request: Request,
    thread: str | None = None,
    panel: str | None = None,
) -> Response:
    """Muestra el hilo del Cotizador o su estado vacío."""

    identity = request_identity(request)
    owner_key = identity.read(request)
    new_owner = owner_key is None
    owner_key = owner_key or identity.new_owner_key()
    thread_id = (thread or "").strip() or None
    messages: Sequence[ChatMessage] = []
    conversations: Sequence[ConversationSummary] = []
    if not new_owner:
        try:
            store = cotizador_store(request)
            thread_id, messages = store.history(owner_key, thread_id)
            conversations = store.conversations(owner_key)
        except ThreadAccessError as error:
            raise HTTPException(status_code=404, detail="El hilo solicitado no existe.") from error
        except SQLAlchemyError as error:
            logger.warning("cotizador.history_failed", error=str(error))
            response = _page(
                request, [], error=SERVICE_UNAVAILABLE, status_code=503, thread_id=thread_id
            )
            if new_owner:
                set_identity_cookie(response, request, identity, owner_key)
            return response
    response = _page(
        request,
        messages,
        thread_id=thread_id,
        conversations=conversations if panel == "hilos" else (),
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


def _cuerpo_ficha(text: str) -> str:
    """Quita el front matter de la ficha; el cuerpo se pinta como Markdown saneado."""

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text.strip()


@router.get("/cotizador/ficha/{slug}", response_class=HTMLResponse)
def ficha(request: Request, slug: str) -> Response:
    """Ficha citable de una oferta, servida desde el conjunto de demostración."""

    if SLUG.fullmatch(slug) is None:
        raise HTTPException(status_code=404, detail="La ficha no existe.")
    path = FICHAS / f"{slug}.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="La ficha no existe.")
    return runtime.templates.TemplateResponse(
        request,
        "ficha.html",
        {"slug": slug, "contenido": _cuerpo_ficha(path.read_text(encoding="utf-8"))},
    )


@router.post("/cotizador", response_class=HTMLResponse)
def turn(
    request: Request,
    question: str = Form(default=""),
    csrf_token: str = Form(default=""),
    thread_id: str = Form(default=""),
) -> Response:
    """Cotiza el turno y devuelve los dos mensajes para añadirlos al hilo."""

    text = question.strip()
    hilo = thread_id.strip() or None
    identity = request_identity(request)
    stored_owner = identity.read(request)
    new_owner = stored_owner is None
    owner_key = stored_owner or identity.new_owner_key()
    structlog.contextvars.bind_contextvars(owner_key=owner_key)

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _error_turno(CSRF_INVALIDO, thread_id=hilo or "", status_code=403)
    if not text:
        return _error_turno(EMPTY, thread_id=hilo or "", status_code=422)
    if len(text) > MAX_QUESTION_CHARS:
        return _error_turno(
            f"El mensaje no puede superar los {MAX_QUESTION_CHARS} caracteres.",
            thread_id=hilo or "",
            status_code=422,
        )
    blocked = _quota(request, owner_key)
    if blocked is not None:
        return blocked

    store = cotizador_store(request)
    try:
        current_id, history = store.history(owner_key, hilo)
        questions = [item.content for item in history if item.role == "user"][-HISTORY_QUESTIONS:]
        profile, forgot = _profile(store, owner_key, text)
        result = quote(text, questions=questions, allow_demo=demo_enabled(), profile=profile)
        if forgot:
            result["reply"] = f"{DELETED} {result['reply']}"
        payload = {key: value for key, value in result.items() if key != "reply"}
        payload["kind"] = payload.get("kind", "cotizacion")
        payload = _issue_quote(store, owner_key, payload, current_id)
        conversation_id, user, assistant = store.append(
            owner_key,
            question=text,
            answer=result["reply"],
            sources=[payload],
            thread_id=current_id,
        )
    except ThreadAccessError as error:
        raise HTTPException(status_code=404, detail="El hilo solicitado no existe.") from error
    except DemoDataError as error:
        logger.warning("cotizador.demo_failed", error=str(error))
        return _error_turno(
            "El conjunto de demostración del Cotizador no está disponible.",
            thread_id=hilo or "",
            status_code=503,
        )
    except SQLAlchemyError as error:
        logger.warning("cotizador.failed", error=str(error))
        return _error_turno(SERVICE_UNAVAILABLE, thread_id=hilo or "", status_code=503)

    response = _turno(request, [user, assistant], thread_id=conversation_id)
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


@router.post("/cotizador/nuevo")
def new_thread(request: Request, csrf_token: str = Form(default="")) -> Response:
    """Crea un hilo nuevo y vuelve a la vista."""

    identity = request_identity(request)
    stored_owner = identity.read(request)
    new_owner = stored_owner is None
    owner_key = stored_owner or identity.new_owner_key()
    structlog.contextvars.bind_contextvars(owner_key=owner_key)
    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        response = _page(request, [], error=CSRF_INVALIDO, status_code=403, thread_id="")
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response
    try:
        conversation_id = cotizador_store(request).new(owner_key)
    except SQLAlchemyError as error:
        logger.warning("cotizador.new_failed", error=str(error))
        response = _page(request, [], error=SERVICE_UNAVAILABLE, status_code=503, thread_id="")
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response
    response = RedirectResponse(
        url=f"/cotizador?thread={conversation_id}&panel=hilos", status_code=303
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


def _cards(payload: Mapping[str, Any]) -> str:
    """Tarjetas de la cotización ya escapadas por Jinja; vacío si no hay oferta."""

    if payload.get("kind") != "cotizacion":
        return ""
    return runtime.templates.env.get_template("_cotizacion.html").render(cotizacion=payload).strip()


def _pieces(text: str, size: int = 4) -> Iterator[str]:
    """Trozos de texto para que el cliente pinte la respuesta mientras llega."""

    words = text.split()
    for start in range(0, len(words), size):
        chunk = " ".join(words[start : start + size])
        yield chunk if start + size >= len(words) else f"{chunk} "


def _stream(
    store: CotizadorStore,
    owner_key: str,
    question: str,
    *,
    questions: list[str],
    thread_id: str | None,
    profile: Mapping[str, Any] | None = None,
    forgot: bool = False,
) -> Iterator[str]:
    """Eventos del turno: apertura, texto, tarjetas, cierre y error controlado."""

    structlog.contextvars.bind_contextvars(owner_key=owner_key)
    sent = False
    try:
        result = quote(question, questions=questions, allow_demo=demo_enabled(), profile=profile)
        if forgot:
            result["reply"] = f"{DELETED} {result['reply']}"
        payload = {key: value for key, value in result.items() if key != "reply"}
        yield encode("open", {"stream": "cotizador"})
        for piece in _pieces(result["reply"]):
            sent = True
            yield encode("token", {"text": piece})
        payload = _issue_quote(store, owner_key, payload, thread_id)
        html = _cards(payload)
        if html:
            yield encode("products", {"html": html})
        conversation_id, user, assistant = store.append(
            owner_key,
            question=question,
            answer=result["reply"],
            sources=[payload],
            thread_id=thread_id,
        )
        yield encode(
            "done",
            {
                "conversation_id": conversation_id,
                "message_id": assistant.id,
                "user_message_id": user.id,
                "thread_id": conversation_id,
            },
        )
    except DemoDataError as error:
        logger.warning("cotizador.demo_failed", error=str(error), stream=True)
        yield encode(
            "error",
            {
                "message": "El conjunto de demostración del Cotizador no está disponible.",
                "incomplete": sent,
            },
        )
    except Exception as error:
        logger.warning("cotizador.stream_failed", error=str(error))
        yield encode(
            "error",
            {"message": SERVICE_UNAVAILABLE, "incomplete": sent},
        )


def _invalid_stream(
    message: str, *, status_code: int, headers: Mapping[str, str] | None = None
) -> Response:
    """Error en el mismo formato SSE que el stream correcto."""

    return Response(
        content=encode("error", {"message": message, "incomplete": False}),
        status_code=status_code,
        media_type=SSE_MEDIA_TYPE,
        headers={"X-Carvajal-Error": "1", **SSE_HEADERS, **(headers or {})},
    )


@router.post("/cotizador/stream")
def stream_turn(
    request: Request,
    question: str = Form(default=""),
    csrf_token: str = Form(default=""),
    thread_id: str = Form(default=""),
) -> Response:
    """Cotiza el turno como eventos SSE y persiste una sola vez al cerrar."""

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _invalid_stream(CSRF_INVALIDO, status_code=403)

    text = question.strip()
    hilo = thread_id.strip() or None
    if not text:
        return _invalid_stream(EMPTY, status_code=422)
    if len(text) > MAX_QUESTION_CHARS:
        return _invalid_stream(
            f"El mensaje no puede superar los {MAX_QUESTION_CHARS} caracteres.",
            status_code=422,
        )

    identity = request_identity(request)
    stored_owner = identity.read(request)
    new_owner = stored_owner is None
    owner_key = stored_owner or identity.new_owner_key()
    structlog.contextvars.bind_contextvars(owner_key=owner_key)

    try:
        quota = consume_quota(request, owner_key)
    except SQLAlchemyError as error:
        logger.warning("cotizador.quota_failed", error=str(error), stream=True)
        response = _invalid_stream(SERVICE_UNAVAILABLE, status_code=503)
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response
    if not quota.allowed:
        response = _invalid_stream(
            LIMITE_ALCANZADO,
            status_code=429,
            headers={"Retry-After": str(quota.retry_after)},
        )
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response

    store = cotizador_store(request)
    try:
        current_id, history = store.history(owner_key, hilo)
    except ThreadAccessError as error:
        raise HTTPException(status_code=404, detail="El hilo solicitado no existe.") from error
    except SQLAlchemyError as error:
        logger.warning("cotizador.history_failed", error=str(error), stream=True)
        return _invalid_stream(SERVICE_UNAVAILABLE, status_code=503)

    questions = [item.content for item in history if item.role == "user"][-HISTORY_QUESTIONS:]
    profile, forgot = _profile(store, owner_key, text)
    response = StreamingResponse(
        _stream(
            store,
            owner_key,
            text,
            questions=questions,
            thread_id=current_id,
            profile=profile,
            forgot=forgot,
        ),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response
