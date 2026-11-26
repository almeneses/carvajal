"""Página «Métricas» (spec 055): Qdrant, Jev, LangSmith, DeepSeek y WhatsApp (OpenClaw).

Pública salvo el saldo de DeepSeek, que solo ve el operador (``OPERATOR_TOKEN`` por cookie,
cabecera o Basic, como en Configuración). Solo agregados: sin texto de las personas, sin
``owner_key``, sin teléfonos y sin secretos. Si una fuente falla, su tarjeta lo dice y la
página sigue. Los contadores de proceso se reinician con cada despliegue.
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from apps.api.metrics import MetricsBoard
from apps.api.observability import claims_block, jev_block
from apps.api.runtime import templates
from apps.api.security import operator_authorized
from carvajal.adapters.judge import food_photo_health
from carvajal.infrastructure.settings import Settings

router = APIRouter()
logger = structlog.get_logger(__name__)


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or Settings()


def _board(request: Request) -> MetricsBoard:
    """Tablero de la app (inyectable en ``app.state.metrics``); se crea al primer uso."""

    board = getattr(request.app.state, "metrics", None)
    if isinstance(board, MetricsBoard):
        return board
    from apps.api import runtime

    board = MetricsBoard(settings=_settings(request), session_factory=runtime.session_factory)
    request.app.state.metrics = board
    return board


def _photo(request: Request, settings: Settings) -> dict[str, Any]:
    classifier = getattr(request.app.state, "food_classifier", None)
    if classifier is None and hasattr(request.app.state, "food_classifier"):
        return {"status": "off", "calls_today": 0}
    return food_photo_health(settings, classifier)


def _age(moment: datetime | None) -> str:
    """«hace 3 min» a partir de la hora de la tarjeta."""

    if moment is None:
        return ""
    seconds = max(0, int((datetime.now(UTC) - moment).total_seconds()))
    if seconds < 60:
        return "hace un momento"
    if seconds < 3600:
        return f"hace {seconds // 60} min"
    return f"hace {seconds // 3600} h"


def _number(value: Any, digits: int = 0) -> str:
    """Número con miles a la colombiana («12.345») y coma decimal; «—» si falta."""

    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:,.{digits}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def _percent(value: Any) -> str:
    if value is None:
        return "—"
    return f"{_number(float(value) * 100, 1)} %"


def _bytes(value: Any) -> str:
    if not value:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{_number(size, 0 if unit == 'B' else 1)} {unit}"
        size /= 1024
    return "—"


def _when(value: Any) -> str:
    """Fecha y hora de Bogotá de un ISO o ``datetime``; «—» si falta."""

    from zoneinfo import ZoneInfo

    if not value:
        return "—"
    try:
        moment = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except ValueError:
        return "—"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo("America/Bogota")).strftime("%d/%m/%Y %H:%M")


templates.env.filters["metrica_numero"] = _number
templates.env.filters["metrica_porcentaje"] = _percent
templates.env.filters["metrica_bytes"] = _bytes
templates.env.filters["metrica_cuando"] = _when
templates.env.filters["metrica_edad"] = _age


@router.get("/metricas", response_class=HTMLResponse)
def metricas(request: Request) -> Response:
    """Tarjetas de métricas; el saldo de DeepSeek solo con sesión de operador."""

    settings = _settings(request)
    operator = operator_authorized(request, settings=settings)
    try:
        cards = _board(request).cards(
            operator=operator,
            jev=jev_block(settings),
            claims=claims_block(settings),
            photo=_photo(request, settings),
        )
    except Exception as error:  # la página nunca cae: se muestra sin tarjetas
        logger.warning("metrics.page_failed", error_type=type(error).__name__)
        cards = []
    # SecurityMiddleware marca el HTML con ``Cache-Control: no-store``: varía con la sesión de
    # operador y con cada consulta.
    return templates.TemplateResponse(
        request,
        "metricas.html",
        {"cards": {card.key: card for card in cards}, "operator": operator},
    )
