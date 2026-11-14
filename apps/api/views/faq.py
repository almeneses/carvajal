"""Vista de FAQ: genera lotes de hasta diez preguntas por SSE.

La generación tarda decenas de segundos y consume el corpus completo, así que
se emite como stream: el navegador ve el texto mientras llega y el cierre
``done`` trae el lote ya interpretado. El endpoint comparte identidad, CSRF y
cupo diario con el chat; no persiste la FAQ, porque cada clic pide preguntas
nuevas y el estado acumulado vive en la pestaña del navegador.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import asdict
from typing import cast

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from apps.api import runtime
from apps.api.chat.service import SERVICE_UNAVAILABLE
from apps.api.observability import capture_exception
from apps.api.runtime import templates
from apps.api.security import verify_csrf
from apps.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, encode
from apps.api.views.chat import (
    CSRF_INVALIDO,
    consume_quota,
    request_identity,
    set_identity_cookie,
)
from carvajal.adapters.storage.repositories import SqlDocumentRepository
from carvajal.application.errors import LLMError
from carvajal.application.faq import (
    CORE_LAYER,
    MAX_CORPUS_DOCUMENTS,
    MAX_PROMPT_CHARS,
    FaqBatch,
    FaqCorpus,
    FAQError,
    GenerateFaq,
    PreparedFaq,
)
from carvajal.application.ports import LLMProvider
from carvajal.domain.entities import Document
from carvajal.infrastructure.settings import Settings

__all__ = ["page_router", "router"]

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api")
page_router = APIRouter()

MAX_EXCLUDE = 100
MAX_EXCLUDE_CHARS = 200
MAX_EXCLUDE_TOTAL_CHARS = 50_000
LIMITE_ALCANZADO = "Has alcanzado el límite diario de generaciones. Vuelve a intentarlo mañana."
EXCLUDE_INVALIDO = "La lista de preguntas previas no es válida."


class SqlFaqCorpus:
    """Lee del corpus núcleo en PostgreSQL, sin recuperación vectorial."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def documents(self) -> Sequence[Document]:
        """Devuelve los documentos núcleo ordenados por slug."""

        with self._factory.begin() as session:
            repository = SqlDocumentRepository(session)
            return tuple(repository.list(layer=CORE_LAYER, limit=MAX_CORPUS_DOCUMENTS))


def faq_service(request: Request) -> GenerateFaq:
    """Usa el corpus y el LLM inyectados o construye los reales, sin cachear."""

    corpus = cast(
        FaqCorpus | None,
        getattr(request.app.state, "faq_corpus", None),
    )
    if corpus is None:
        corpus = SqlFaqCorpus(runtime.session_factory())
    llm = cast(LLMProvider | None, getattr(request.app.state, "faq_llm", None))
    if llm is None:
        llm = runtime.llm_provider()

    settings = Settings()
    max_prompt_chars = MAX_PROMPT_CHARS
    if settings.llm_provider.strip().lower() == "ollama":
        # Tres caracteres por token es conservador para el castellano; deja
        # margen para la respuesta dentro de la ventana configurada.
        max_prompt_chars = min(
            MAX_PROMPT_CHARS,
            max(20_000, settings.ollama_num_ctx * 3),
        )
    return GenerateFaq(corpus, llm, max_prompt_chars=max_prompt_chars)


@page_router.get("/faq", response_class=HTMLResponse)
def faq_page(request: Request) -> Response:
    """Renderiza la pestaña FAQ del shell Jinja2 con su token CSRF."""

    return templates.TemplateResponse(request, "faq.html", {})


@router.post("/faq")
def faq_stream(
    request: Request,
    csrf_token: str = Form(default=""),
    exclude: str = Form(default="[]"),
) -> Response:
    """Genera un lote de hasta diez preguntas nuevas y lo emite por SSE."""

    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _invalid(CSRF_INVALIDO, status_code=403)

    try:
        previous = parse_exclude(exclude)
    except ValueError as error:
        return _invalid(str(error), status_code=422)

    identity = request_identity(request)
    stored_owner = identity.read(request)
    new_owner = stored_owner is None
    owner_key = stored_owner or identity.new_owner_key()
    # Los tokens del LLM quedan asociados a este visitante en los logs.
    structlog.contextvars.bind_contextvars(owner_key=owner_key)

    try:
        quota = consume_quota(request, owner_key)
    except SQLAlchemyError as error:
        logger.warning("faq.quota_failed", error=str(error))
        response = _invalid(SERVICE_UNAVAILABLE, status_code=503)
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response
    if not quota.allowed:
        response = _invalid(
            LIMITE_ALCANZADO,
            status_code=429,
            headers={"Retry-After": str(quota.retry_after)},
        )
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response

    try:
        service = faq_service(request)
        prepared = service.prepare(previous)
    except FAQError as error:
        logger.warning("faq.prepare_failed", error=str(error))
        response = _invalid(str(error), status_code=422)
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response
    except (SQLAlchemyError, OSError, ValueError) as error:
        logger.warning("faq.corpus_failed", error=str(error))
        response = _invalid(SERVICE_UNAVAILABLE, status_code=503)
        if new_owner:
            set_identity_cookie(response, request, identity, owner_key)
        return response

    response = StreamingResponse(
        _sse(service, prepared, previous, owner_key),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
    if new_owner:
        set_identity_cookie(response, request, identity, owner_key)
    return response


def parse_exclude(raw: str) -> list[str]:
    """Interpreta la lista JSON de preguntas ya mostradas.

    Raises:
        ValueError: si el JSON no es una lista de textos o supera los límites.
    """

    value = raw.strip()
    if not value:
        return []
    if len(value) > MAX_EXCLUDE_TOTAL_CHARS:
        raise ValueError(EXCLUDE_INVALIDO)
    try:
        data = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(EXCLUDE_INVALIDO) from error
    if not isinstance(data, list):
        raise ValueError(EXCLUDE_INVALIDO)

    questions: list[str] = []
    for item in data[:MAX_EXCLUDE]:
        if not isinstance(item, str):
            raise ValueError(EXCLUDE_INVALIDO)
        clean = " ".join(item.split())[:MAX_EXCLUDE_CHARS]
        if clean:
            questions.append(clean)
    return questions


def _sse(
    service: GenerateFaq,
    prepared: PreparedFaq,
    previous: Sequence[str],
    owner_key: str,
) -> Iterator[str]:
    """Traduce la generación a eventos SSE y siempre cierra con ``done`` o ``error``."""

    structlog.contextvars.bind_contextvars(owner_key=owner_key)
    buffer: list[str] = []
    yield encode("open", {"stream": "faq"})
    try:
        for chunk in service.stream(prepared):
            if not chunk:
                continue
            buffer.append(chunk)
            yield encode("token", {"text": chunk})
        batch = service.finish(prepared, "".join(buffer), previous)
    except LLMError as error:
        logger.warning("faq.llm_failed", error=str(error))
        capture_exception(error)
        yield encode(
            "error",
            {"message": SERVICE_UNAVAILABLE, "incomplete": bool(buffer)},
        )
        return
    except FAQError as error:
        yield encode("error", {"message": str(error), "incomplete": False})
        return
    except Exception as error:
        logger.warning("faq.stream_failed", error=str(error))
        capture_exception(error)
        yield encode(
            "error",
            {"message": SERVICE_UNAVAILABLE, "incomplete": bool(buffer)},
        )
        return

    yield encode("done", _payload(batch))


def _payload(batch: FaqBatch) -> dict[str, object]:
    """Contrato JSON del lote que consume la SPA."""

    return {
        "entries": [asdict(entry) for entry in batch.entries],
        "missing_topics": list(batch.missing_topics),
        "truncated": batch.truncated,
        "exhausted": batch.exhausted,
        "warnings": list(batch.warnings),
    }


def _invalid(
    message: str,
    *,
    status_code: int = 422,
    headers: dict[str, str] | None = None,
) -> Response:
    """Respuesta SSE controlada para CSRF, cupo o corpus no disponible."""

    return Response(
        content=encode("error", {"message": message, "incomplete": False}),
        status_code=status_code,
        media_type=SSE_MEDIA_TYPE,
        headers={"X-Carvajal-Error": "1", **SSE_HEADERS, **(headers or {})},
    )
