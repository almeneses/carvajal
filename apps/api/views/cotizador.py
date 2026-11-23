"""Acceso heredado al Cotizador (spec 043): historial autorizado y redirecciones.

El compositor y la escritura pública del Cotizador se retiraron: la conversación
continúa en el chat. Los enlaces antiguos se redirigen, salvo un hilo histórico,
que se abre en una vista de solo lectura y únicamente si pertenece al dueño
firmado y su contenido es publicable (real, no simulado ni ambiguo).
"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.api import runtime
from apps.api.views.chat import request_identity
from carvajal.adapters.storage.chat import ChatMessage, ConversationSummary, SqlChatRepository
from carvajal.adapters.storage.quotes import (
    QuoteRecord,
    SqlQuoteRepository,
    classify_quote_payload,
)
from carvajal.adapters.storage.visitor_memory import SqlVisitorMemory

__all__ = ["CotizadorStore", "SqlCotizadorStore", "router"]

logger = structlog.get_logger(__name__)
router = APIRouter()

PREFIX = "cot:"


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
        quote: Mapping[str, Any] | None = None,
    ) -> tuple[str, ChatMessage, ChatMessage]:
        """Guarda el turno, y la cotización si la hay, en una sola transacción."""

        ...

    def new(self, owner_key: str) -> str:
        """Crea un hilo y devuelve su id."""

        ...

    def conversations(self, owner_key: str) -> Sequence[ConversationSummary]:
        """Lista los hilos del dueño."""

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
            # Cierra la transacción de solo lectura antes de devolver el historial.
            session.rollback()
            return conversation.id, repo.history(conversation.id)

    def quotes(self, owner_key: str, thread_id: str) -> list[QuoteRecord]:
        """Cotizaciones versionadas del hilo, solo para su dueño."""

        with self._factory() as session:
            owner = self._owner(owner_key)
            conversation = SqlChatRepository(session).get_owned(thread_id, owner)
            if conversation is None:
                raise ThreadAccessError(thread_id)
            items = SqlQuoteRepository(session).for_conversation(conversation.id, owner)
            session.rollback()
            return items

    def append(
        self,
        owner_key: str,
        *,
        question: str,
        answer: str,
        sources: Sequence[Mapping[str, Any]],
        thread_id: str | None,
        quote: Mapping[str, Any] | None = None,
    ) -> tuple[str, ChatMessage, ChatMessage]:
        # Se conserva para la lectura y las pruebas del almacén histórico; la
        # escritura pública ya no se expone desde la vista.
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
            if quote is not None:
                self._record_quote(session, owner, quote, conversation.id)
            return conversation.id, user, assistant

    @staticmethod
    def _record_quote(
        session: Session,
        owner: str,
        quote: Mapping[str, Any],
        conversation_id: str,
    ) -> None:
        """Registra la cotización versionada dentro de la transacción del turno."""

        total = quote.get("total")
        subtotal = quote.get("subtotal")
        tax_rate = quote.get("tax_rate")
        shipping_cost = quote.get("shipping_cost")
        commercial_valid_until = quote.get("commercial_valid_until")
        SqlQuoteRepository(session).record(
            str(quote["id"]),
            owner,
            payload=quote["payload"],
            total=None if total is None else Decimal(str(total)),
            currency=str(quote["currency"]),
            valid_until=date.fromisoformat(str(quote["valid_until"])),
            version=int(quote.get("version", 1)),
            issued_at=quote.get("issued_at"),
            subtotal=None if subtotal is None else Decimal(str(subtotal)),
            tax_status=quote.get("tax_status"),
            tax_rate=None if tax_rate is None else Decimal(str(tax_rate)),
            shipping_cost=None if shipping_cost is None else Decimal(str(shipping_cost)),
            commercial_valid_until=(
                None
                if commercial_valid_until is None
                else date.fromisoformat(str(commercial_valid_until))
            ),
            conversation_id=conversation_id,
        )

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


def cotizador_store(request: Request) -> CotizadorStore:
    """Doble inyectado en las pruebas o el almacén real sobre PostgreSQL."""

    state = getattr(request.app, "state", None)
    injected = getattr(state, "cotizador", None) if state is not None else None
    return injected if injected is not None else SqlCotizadorStore(runtime.session_factory())


def _payload(message: ChatMessage) -> Mapping[str, Any] | None:
    """Primer payload comercial del mensaje, si lo hay."""

    for item in message.sources or []:
        if isinstance(item, Mapping) and "kind" in item:
            return item
    return None


def _readable(
    messages: Sequence[ChatMessage],
    owner_key: str,
    quotes: Sequence[QuoteRecord] = (),
) -> list[ChatMessage]:
    """Turnos que se pueden publicar: solo la rama real de la matriz de procedencia.

    Un turno simulado o ambiguo se excluye completo (texto, tarjeta y contexto);
    el usuario no ve contenido contaminado ni siquiera como cita.
    """

    by_message = {row.get("message_id"): row for row in quotes if row.get("message_id")}
    by_folio = {row.folio: row for row in quotes if row.folio}
    users_by_turn: dict[str, ChatMessage] = {}
    pending_user: ChatMessage | None = None
    emitted: set[str] = set()
    result: list[ChatMessage] = []
    for message in messages:
        if message.role == "user":
            pending_user = message
            if message.client_turn_id:
                users_by_turn[message.client_turn_id] = message
            continue
        if message.role != "assistant":
            continue
        user = users_by_turn.get(message.client_turn_id) if message.client_turn_id else None
        if user is None:
            user = pending_user
        if user is None:
            continue
        payload = _payload(message)
        quote = by_message.get(message.id)
        if quote is None and payload is not None:
            folio = (payload.get("quote") or {}).get("id")
            quote = by_folio.get(folio or "")
        if quote is not None:
            provenance = quote.provenance
        else:
            provenance = classify_quote_payload(
                payload,
                owner_key=owner_key,
                folio=((payload or {}).get("quote") or {}).get("id"),
            )
        if provenance == "real" and user.id not in emitted:
            result.extend([user, message])
            emitted.add(user.id)
        if pending_user is user:
            pending_user = None
    return result


def _history(
    request: Request, owner_key: str, thread_id: str
) -> tuple[str | None, list[ChatMessage]]:
    """Historial autorizado del hilo, ya filtrado por procedencia."""

    store = cotizador_store(request)
    current, messages = store.history(owner_key, thread_id)
    loader: Any = getattr(store, "quotes", None)
    quotes: Sequence[QuoteRecord] = loader(owner_key, thread_id) if callable(loader) else ()
    return current, _readable(messages, owner_key, quotes)


@router.get("/cotizador", response_class=HTMLResponse)
def page(request: Request, thread: str | None = None, panel: str | None = None) -> Response:
    """Redirige el acceso general o abre un histórico autorizado de solo lectura."""

    owner_key = request_identity(request).read(request)
    thread_id = (thread or "").strip()
    if owner_key is None or not thread_id:
        destino = "/chat?panel=hilos" if panel == "hilos" else "/chat"
        return RedirectResponse(url=destino, status_code=303)
    try:
        current, messages = _history(request, owner_key, thread_id)
    except ThreadAccessError as error:
        raise HTTPException(status_code=404, detail="El hilo solicitado no existe.") from error
    except SQLAlchemyError as error:
        logger.warning("cotizador.history_failed", error=str(error))
        return HTMLResponse(
            "El histórico del Cotizador no está disponible en este momento.",
            status_code=503,
        )
    return runtime.templates.TemplateResponse(
        request,
        "historial.html",
        {"messages": messages, "thread_id": current or thread_id},
    )


@router.post("/cotizador")
def turn() -> Response:
    """El compositor anterior se retiró: la conversación sigue en el chat."""

    return RedirectResponse(url="/chat", status_code=303)


@router.post("/cotizador/stream")
def stream_turn() -> Response:
    """El SSE del compositor anterior se redirige al chat unificado."""

    return RedirectResponse(url="/chat", status_code=303)


@router.post("/cotizador/nuevo")
def new_thread() -> Response:
    """Los hilos nuevos se abren desde el chat."""

    return RedirectResponse(url="/chat", status_code=303)


@router.get("/cotizador/ficha/{slug}")
def ficha(slug: str) -> Response:
    """Las fichas generadas por la demo ya no se sirven."""

    raise HTTPException(status_code=404, detail="La ficha no existe.")
