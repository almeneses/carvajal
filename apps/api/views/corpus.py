"""Vista del corpus (W3.1): lista, filtros y paginación.

La pantalla pide una página de ``PAGE_SIZE`` documentos al proveedor inyectado
en ``app.state.corpus``; si no existe, se construye uno real sobre PostgreSQL al
momento de la petición. El proveedor traduce a consultas el filtro por sección
y por capa, y expone las secciones disponibles para el formulario.
"""

from collections.abc import Sequence
from typing import Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.runtime import session_factory, templates
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.repositories import SqlDocumentRepository
from carvajal.domain.entities import Document

router = APIRouter()

PAGE_SIZE = 50
LAYERS = ("core", "extended")


class CorpusProvider(Protocol):
    """Puerto de lectura que la pantalla del corpus necesita."""

    def list(
        self,
        *,
        layer: str | None = None,
        section: str | None = None,
        limit: int = PAGE_SIZE,
        offset: int = 0,
    ) -> Sequence[Document]:
        """Devuelve los documentos filtrados y ordenados por slug."""

        ...

    def sections(self) -> Sequence[str]:
        """Devuelve las secciones distintas que ofrece el corpus."""

        ...


class SqlCorpusProvider:
    """Proveedor real sobre PostgreSQL para la pantalla del corpus."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def list(
        self,
        *,
        layer: str | None = None,
        section: str | None = None,
        limit: int = PAGE_SIZE,
        offset: int = 0,
    ) -> Sequence[Document]:
        """Lista documentos con los filtros indicados, ordenados por slug."""

        with self._factory.begin() as session:
            return SqlDocumentRepository(session).list(
                layer=layer, section=section, limit=limit, offset=offset
            )

    def sections(self) -> Sequence[str]:
        """Consulta las secciones distintas, en orden alfabético."""

        query = select(tables.documents.c.section).distinct().order_by(tables.documents.c.section)
        with self._factory.begin() as session:
            return list(session.scalars(query))


def _parse_section(raw: str | None) -> str | None:
    """Recorta la sección y convierte el vacío en ``None``."""

    section = (raw or "").strip()
    return section or None


def _parse_layer(raw: str | None) -> str | None:
    """Acepta solo las capas conocidas; cualquier otro valor se ignora."""

    layer = (raw or "").strip()
    return layer if layer in LAYERS else None


def _parse_page(raw: str | None) -> int:
    """Convierte la página en un entero mayor o igual que uno.

    Un valor ausente, vacío, no numérico o menor que uno se normaliza a la
    primera página, de modo que un enlace mal formado nunca produzca un 500.
    """

    text = (raw or "").strip()
    if not text:
        return 1
    try:
        page = int(text)
    except ValueError:
        return 1
    return page if page >= 1 else 1


def _provider(request: Request) -> CorpusProvider:
    """Usa el proveedor inyectado en ``app.state.corpus`` o construye el real."""

    injected = cast(CorpusProvider | None, getattr(request.app.state, "corpus", None))
    if injected is not None:
        return injected
    return SqlCorpusProvider(session_factory())


def _page_url(
    request: Request,
    page: int,
    section: str | None,
    layer: str | None,
) -> str:
    """Construye un enlace de paginación con los filtros ya normalizados."""

    url = request.url.remove_query_params(["page", "section", "layer"])
    params: dict[str, str | int] = {"page": page}
    if section is not None:
        params["section"] = section
    if layer is not None:
        params["layer"] = layer
    return str(url.include_query_params(**params))


@router.get("/corpus", response_class=HTMLResponse)
def corpus_list(
    request: Request,
    section: str | None = None,
    layer: str | None = None,
    page: str | None = None,
) -> Response:
    """Muestra la lista paginada del corpus con filtros combinables."""

    clean_section = _parse_section(section)
    clean_layer = _parse_layer(layer)
    current_page = _parse_page(page)
    offset = (current_page - 1) * PAGE_SIZE
    provider = _provider(request)

    # Se pide un documento de más: si llega el 51, hay página siguiente.
    rows = list(
        provider.list(
            layer=clean_layer,
            section=clean_section,
            limit=PAGE_SIZE + 1,
            offset=offset,
        )
    )
    documents = rows[:PAGE_SIZE]
    has_next = len(rows) > PAGE_SIZE

    return templates.TemplateResponse(
        request,
        "corpus.html",
        {
            "documents": documents,
            "sections": provider.sections(),
            "section": clean_section,
            "layer": clean_layer,
            "page": current_page,
            "prev_url": (
                _page_url(request, current_page - 1, clean_section, clean_layer)
                if current_page > 1
                else None
            ),
            "next_url": (
                _page_url(request, current_page + 1, clean_section, clean_layer)
                if has_next
                else None
            ),
        },
    )
