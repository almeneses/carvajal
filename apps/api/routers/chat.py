"""Portada y respuesta: el flujo de preguntar al corpus."""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError

from apps.api.deps import SERVICE_UNAVAILABLE, Handlers, get_handlers, message, templates
from apps.api.session import owner_key
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.application.ask import LLMError

__all__ = ["router"]

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["chat"])


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> Response:
    """Sirve la portada con el formulario de preguntas."""

    return templates.TemplateResponse(request, "index.html", {})


@router.post("/ask", response_class=HTMLResponse)
def ask(
    request: Request,
    handlers: Annotated[Handlers, Depends(get_handlers)],
    owner: Annotated[str, Depends(owner_key)],
    question: str = Form(default=""),
) -> Response:
    """Responde a la pregunta y devuelve el parcial con la respuesta y sus fuentes."""

    # A partir de aquí, todo log de la petición (incluido el consumo de tokens
    # del adaptador) queda asociado al visitante anónimo.
    structlog.contextvars.bind_contextvars(owner_key=owner)

    text = question.strip()
    if not text:
        return message(request, 422, "La pregunta no puede estar vacía.")
    try:
        answer = handlers.ask(text)
    except (EmbeddingError, LLMError, SQLAlchemyError) as error:
        logger.warning("ask.failed", error=str(error))
        return message(request, 503, SERVICE_UNAVAILABLE)
    return templates.TemplateResponse(request, "_answer.html", {"answer": answer})
