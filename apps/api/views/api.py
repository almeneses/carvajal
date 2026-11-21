"""Contrato HTTP/SSE en JSON para clientes automatizados.

El shell Jinja2/HTMX es la interfaz única; estos endpoints conservan el mismo
contrato en JSON/SSE para pruebas y clientes propios. El backend controla
credenciales, modelo, ruta, memoria y herramientas; el cliente solo recibe
tokens y citas. La cookie firmada y el CSRF son los mismos del shell, de modo
que ambas entradas comparten identidad y cuota.
"""

from collections.abc import Sequence
from typing import cast

import structlog
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.service import SERVICE_UNAVAILABLE
from apps.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from apps.api.views import corpus as corpus_views
from apps.api.views import documents as documents_views
from apps.api.views.chat_rules import (
    CSRF_INVALIDO,
    LIMITE_ALCANZADO,
    chat_provider,
    consume_quota,
    invalid_response,
    request_identity,
    require_csrf,
    resolve_visitor,
    set_identity_cookie,
    stream_events,
    validate_question,
)
from carvajal.domain.entities import Document, DocumentVersion

__all__ = ["router"]

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api")

DB_UNAVAILABLE = "La base de datos no responde."


@router.get("/csrf")
def csrf(request: Request) -> JSONResponse:
    """Entrega el token que el cliente debe repetir en el formulario."""

    return JSONResponse({"token": getattr(request.state, "csrf_token", "")})


@router.get("/health")
def health() -> JSONResponse:
    """Resumen JSON: base, documentos y estado de Qdrant."""

    from sqlalchemy import func, select

    from apps.api.runtime import qdrant_store, session_factory
    from carvajal.adapters.storage import tables

    try:
        with session_factory().begin() as session:
            documents = session.scalar(select(func.count()).select_from(tables.documents))
            chunks = session.scalar(select(func.count()).select_from(tables.chunks))
    except (SQLAlchemyError, OSError) as error:
        logger.warning("api.health_failed", error=str(error))
        return JSONResponse({"status": "degraded", "detail": "base de datos no disponible"}, 503)
    index = qdrant_store().health()
    healthy = index.get("status") in {"ok", "not_configured"}
    return JSONResponse(
        {
            "status": "ok" if healthy else "degraded",
            "documents": documents or 0,
            "chunks": chunks or 0,
            "index": index,
        },
        status_code=200 if healthy else 503,
    )


@router.get("/corpus")
def corpus_read(
    request: Request,
    q: str | None = None,
    section: str | None = None,
    source_type: str | None = None,
    layer: str | None = None,
    page: str | None = None,
    page_size: str | None = None,
) -> JSONResponse:
    """Lista paginada del corpus en JSON.

    Los filtros y la búsqueda se aplican al conjunto completo antes de paginar:
    ``total`` y ``has_next`` describen el resultado filtrado, no la página.
    """

    clean_q = corpus_views.parse_query(q)
    clean_layer = corpus_views.parse_layer(layer)
    current_page = corpus_views.parse_page(page)
    size = corpus_views.parse_page_size(page_size)
    provider = corpus_views.corpus_provider(request)
    try:
        sections = list(provider.sections())
        source_types = list(provider.source_types())
        clean_section = corpus_views.parse_section(section)
        if clean_section not in sections:
            clean_section = None
        clean_source = corpus_views.parse_section(source_type)
        if clean_source not in source_types:
            clean_source = None
        total = provider.count(
            layer=clean_layer,
            section=clean_section,
            source_type=clean_source,
            q=clean_q,
        )
        rows = provider.list(
            layer=clean_layer,
            section=clean_section,
            source_type=clean_source,
            q=clean_q,
            limit=size,
            offset=(current_page - 1) * size,
        )
    except (SQLAlchemyError, OSError) as error:
        logger.warning("api.corpus_failed", error=str(error))
        return JSONResponse({"detail": DB_UNAVAILABLE}, status_code=503)

    return JSONResponse(
        {
            "documents": [_document_row(document) for document in rows],
            "sections": sections,
            "source_types": source_types,
            "total": total,
            "page": current_page,
            "page_size": size,
            "has_next": (current_page - 1) * size + size < total,
        }
    )


@router.get("/documents/by-slug/{slug}")
def document_by_slug(request: Request, slug: str, version: str | None = None) -> JSONResponse:
    """Resuelve una cita del chat, que solo trae el slug del corpus."""

    return _document_response(request, slug, (version or "").strip() or None, by_slug=True)


@router.get("/documents/{document_id}")
def document_read(request: Request, document_id: str, version: str | None = None) -> JSONResponse:
    """Documento con HTML saneado, ficha y versiones archivadas."""

    return _document_response(request, document_id, (version or "").strip() or None, by_slug=False)


def _document_row(document: Document) -> dict[str, object]:
    """Metadatos públicos de un documento, sin Markdown ni campos internos."""

    return {
        "id": document.id,
        "url": documents_views.safe_url(document.url) or "",
        "title": document.title,
        "slug": document.slug,
        "section": document.section,
        "layer": document.layer,
        "source_type": document.source_type,
        "captured_at": documents_views.as_utc(document.captured_at).isoformat(),
    }


def _document_payload(context: dict[str, object]) -> dict[str, object]:
    """Traduce el contexto compartido con la vista HTML al contrato JSON."""

    document = cast(Document, context["document"])
    version = cast(DocumentVersion | None, context["version"])
    versions = cast(Sequence[DocumentVersion], context["versions"])
    return {
        "document": _document_row(document),
        "document_url": context["document_url"],
        "content_html": context["content"],
        "content_hash": context["content_hash"],
        "captured_display": context["captured_display"],
        "captured_iso": context["captured_iso"],
        "version": version.id if version is not None else None,
        "versions": [
            {
                "id": item.id,
                "captured_at": documents_views.as_utc(item.captured_at).isoformat(),
                "created_at": documents_views.as_utc(item.created_at).isoformat(),
                "content_hash": item.content_hash[:12],
            }
            for item in versions
        ],
    }


def _document_response(
    request: Request,
    identifier: str,
    version_id: str | None,
    *,
    by_slug: bool,
) -> JSONResponse:
    """Carga el contexto y responde JSON; la base caída no filtra detalles."""

    try:
        context = documents_views.load_document_context(
            request, identifier, version_id, by_slug=by_slug
        )
    except HTTPException as error:
        # La vista HTML deja que FastAPI renderice el 404; el contrato JSON lo
        # devuelve explícitamente para no depender del orden de los handlers.
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)
    except (SQLAlchemyError, OSError) as error:
        logger.warning("api.document_failed", error=str(error))
        return JSONResponse({"detail": DB_UNAVAILABLE}, status_code=503)
    return JSONResponse(_document_payload(context))


@router.get("/chat/history")
def history(request: Request) -> JSONResponse:
    """Historial del hilo activo, suficiente para recargar la página."""

    identity = request_identity(request)
    owner_key = identity.read(request)
    if owner_key is None:
        return JSONResponse({"thread_id": None, "messages": []})
    try:
        messages = chat_provider(request).history(owner_key)
    except SQLAlchemyError as error:
        logger.warning("api.history_failed", error=str(error))
        return JSONResponse({"status": "degraded", "detail": SERVICE_UNAVAILABLE}, 503)
    return JSONResponse(
        {
            "thread_id": messages[0].conversation_id if messages else None,
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "sources": message.sources or [],
                    "status": message.status,
                    "created_at": (message.created_at.isoformat() if message.created_at else None),
                }
                for message in messages
            ],
        }
    )


@router.post("/chat/new")
def new_thread(
    request: Request,
    csrf_token: str = Form(default=""),
) -> JSONResponse:
    """Crea un hilo nuevo del mismo dueño para demostrar la memoria del Store."""

    if not require_csrf(request, csrf_token):
        return JSONResponse({"detail": CSRF_INVALIDO}, 403)
    visitor = resolve_visitor(request)
    try:
        conversation = chat_provider(request).new_conversation(visitor.owner_key)
    except SQLAlchemyError as error:
        logger.warning("api.new_thread_failed", error=str(error))
        return JSONResponse({"detail": SERVICE_UNAVAILABLE}, 503)
    response = JSONResponse({"thread_id": conversation.id})
    if visitor.new_owner:
        set_identity_cookie(response, request, visitor.identity, visitor.owner_key)
    return response


@router.post("/chat/stream")
def chat_stream(
    request: Request,
    question: str = Form(default=""),
    csrf_token: str = Form(default=""),
    client_turn_id: str = Form(default=""),
    thread_id: str = Form(default=""),
) -> Response:
    """SSE con el mismo contrato que el shell Jinja2."""

    if not require_csrf(request, csrf_token):
        return invalid_response(CSRF_INVALIDO, status_code=403)

    text, invalida = validate_question(question)
    if invalida is not None:
        return invalid_response(invalida)

    visitor = resolve_visitor(request)
    structlog.contextvars.bind_contextvars(owner_key=visitor.owner_key)

    try:
        quota = consume_quota(request, visitor.owner_key)
    except SQLAlchemyError as error:
        logger.warning("api.quota_failed", error=str(error))
        response = invalid_response(SERVICE_UNAVAILABLE, status_code=503)
        if visitor.new_owner:
            set_identity_cookie(response, request, visitor.identity, visitor.owner_key)
        return response
    if not quota.allowed:
        response = invalid_response(
            LIMITE_ALCANZADO,
            status_code=429,
            headers={"Retry-After": str(quota.retry_after)},
        )
        if visitor.new_owner:
            set_identity_cookie(response, request, visitor.identity, visitor.owner_key)
        return response

    response = StreamingResponse(
        stream_events(
            chat_provider(request),
            visitor.owner_key,
            text,
            client_turn_id=client_turn_id.strip() or None,
            thread_id=thread_id.strip() or None,
            log_event="api.stream_failed",
        ),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
    if visitor.new_owner:
        set_identity_cookie(response, request, visitor.identity, visitor.owner_key)
    return response
