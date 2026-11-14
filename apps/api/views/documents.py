"""Vista de documento (W3.2): Markdown, metadatos y versiones.

El proveedor de documentos es inyectable en ``app.state.documents``; si no se
inyecta, la vista abre una sesión con la fábrica real al atender la petición.

``document_context`` resuelve el documento por ID o por slug y prepara un
contexto común para la plantilla HTML y para los endpoints JSON del M2.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from apps.api import runtime
from apps.api.rendering import render_markdown
from carvajal.adapters.storage.repositories import SqlDocumentRepository
from carvajal.domain.entities import Document, DocumentVersion

router = APIRouter()

DOCUMENT_NOT_FOUND = "El documento solicitado no existe."
VERSION_NOT_FOUND = "La versión solicitada no pertenece a este documento."


class DocumentProvider(Protocol):
    """Contrato mínimo del proveedor de documentos inyectable."""

    def get(self, document_id: str) -> Document | None:
        """Devuelve el documento por ID, o ``None`` si no existe."""

    def get_by_slug(self, slug: str) -> Document | None:
        """Devuelve el documento por slug, o ``None`` si no existe."""

    def list_versions(self, document_id: str) -> Sequence[DocumentVersion]:
        """Devuelve las versiones archivadas del documento."""


def as_utc(value: datetime) -> datetime:
    """Normaliza una fecha a UTC; las fechas sin zona se asumen UTC."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def safe_url(value: str) -> str | None:
    """Devuelve la URL solo si usa ``http`` o ``https``; si no, ``None``."""

    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return None
    return value if scheme in {"http", "https"} else None


def document_context(
    provider: DocumentProvider,
    document_id: str,
    version_id: str | None,
    *,
    by_slug: bool = False,
) -> dict[str, object]:
    """Resuelve documento y versión, y prepara el contexto de la plantilla.

    Con ``by_slug=True`` el identificador recibido se interpreta como el slug
    real del corpus, que es lo que traen las citas del chat.
    """

    document = provider.get_by_slug(document_id) if by_slug else provider.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail=DOCUMENT_NOT_FOUND)

    versions = list(provider.list_versions(document.id))
    selected: DocumentVersion | None = None
    if version_id is not None:
        selected = next((item for item in versions if item.id == version_id), None)
        if selected is None or selected.document_id != document.id:
            raise HTTPException(status_code=404, detail=VERSION_NOT_FOUND)

    markdown = selected.markdown if selected is not None else document.markdown
    content_hash = selected.content_hash if selected is not None else document.content_hash
    captured_at = as_utc(selected.captured_at if selected is not None else document.captured_at)

    return {
        "document": document,
        "document_url": safe_url(document.url),
        "version": selected,
        "versions": versions,
        "content": render_markdown(markdown),
        "content_hash": content_hash[:12],
        "captured_display": captured_at.strftime("%Y-%m-%d %H:%M UTC"),
        "captured_iso": captured_at.isoformat(),
    }


def load_document_context(
    request: Request,
    document_id: str,
    version_id: str | None,
    *,
    by_slug: bool = False,
) -> dict[str, object]:
    """Carga el contexto usando el proveedor inyectado o una sesión real."""

    provider = cast(DocumentProvider | None, getattr(request.app.state, "documents", None))
    if provider is not None:
        return document_context(provider, document_id, version_id, by_slug=by_slug)
    with runtime.session_factory()() as session:
        return document_context(
            SqlDocumentRepository(session), document_id, version_id, by_slug=by_slug
        )


@router.get("/documents/{document_id}", response_class=HTMLResponse)
def show_document(request: Request, document_id: str, version: str | None = None) -> Response:
    """Renderiza el documento actual o una de sus versiones archivadas."""

    version_id = (version or "").strip() or None
    context = load_document_context(request, document_id, version_id)
    return runtime.templates.TemplateResponse(request, "document.html", context)
