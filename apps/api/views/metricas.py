"""Página «Métricas» (spec 055): Qdrant, Jev, LangSmith, DeepSeek y WhatsApp (OpenClaw).

Pública salvo el saldo de DeepSeek, que solo ve el operador (``OPERATOR_TOKEN`` por cookie,
cabecera o Basic, como en Configuración). Solo agregados: sin texto de las personas, sin
``owner_key``, sin teléfonos y sin secretos. Si una fuente falla, su tarjeta lo dice y la
página sigue. Los contadores de proceso se reinician con cada despliegue.

«Reintentar» (``POST /metricas/reintentar``) da una fuente por caducada para que la carga
siguiente la consulte: exige el token CSRF, solo acepta las fuentes de
:data:`~apps.api.metrics.RETRYABLE_SOURCES` y tiene un enfriamiento global por fuente, así que
nadie puede martillar LangSmith ni Qdrant desde la página pública.
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from apps.api.metrics import RETRY_COOLDOWN, RETRYABLE_SOURCES, MetricsBoard
from apps.api.observability import claims_block, jev_block
from apps.api.runtime import templates
from apps.api.security import operator_authorized, verify_csrf
from carvajal.adapters.judge import food_photo_health
from carvajal.infrastructure.settings import Settings

router = APIRouter()
logger = structlog.get_logger(__name__)

CSRF_INVALIDO = "La sesión del formulario expiró. Recarga la página e inténtalo de nuevo."
FUENTE_DESCONOCIDA = "Esa fuente no se puede reintentar desde aquí."
SOLO_OPERADOR = "Solo el operador puede reintentar la consulta del saldo."
# Fuente → nombre que ve la persona y tarjeta donde vive su botón.
RETRY_LABELS = {
    "qdrant": ("Qdrant", "qdrant"),
    "langsmith": ("LangSmith", "langsmith"),
    "whatsapp": ("WhatsApp (OpenClaw)", "whatsapp"),
    "deepseek_balance": ("el saldo de DeepSeek", "deepseek"),
}


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


def _in(moment: datetime | None) -> str:
    """«en 25 s» o «en 3 min» hasta la hora indicada; «en un momento» si ya pasó."""

    if moment is None:
        return ""
    seconds = int((moment - datetime.now(UTC)).total_seconds() + 0.999)
    if seconds <= 0:
        return "en un momento"
    if seconds < 120:
        return f"en {seconds} s"
    return f"en {seconds // 60} min"


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
templates.env.filters["metrica_en"] = _in


def _notice(request: Request, board: MetricsBoard | None) -> dict[str, Any] | None:
    """Aviso del último reintento (``?reintento=`` o ``?espera=``), solo de fuentes conocidas."""

    for kind in ("reintento", "espera"):
        source = request.query_params.get(kind, "")
        if source in RETRY_LABELS:
            wait = board.retry_wait(source) if board is not None and kind == "espera" else 0
            label, card = RETRY_LABELS[source]
            return {"kind": kind, "source": source, "label": label, "card": card, "wait": wait}
    return None


def _render(
    request: Request, *, status_code: int = 200, error: str = "", settings: Settings | None = None
) -> Response:
    settings = settings or _settings(request)
    operator = operator_authorized(request, settings=settings)
    board: MetricsBoard | None = None
    try:
        board = _board(request)
        cards = board.cards(
            operator=operator,
            jev=jev_block(settings),
            claims=claims_block(settings),
            photo=_photo(request, settings),
        )
    except Exception as error_:  # la página nunca cae: se muestra sin tarjetas
        logger.warning("metrics.page_failed", error_type=type(error_).__name__)
        cards = []
    waits = {
        source: board.retry_wait(source) if board is not None else 0 for source in RETRYABLE_SOURCES
    }
    # SecurityMiddleware marca el HTML con ``Cache-Control: no-store``: varía con la sesión de
    # operador y con cada consulta.
    return templates.TemplateResponse(
        request,
        "metricas.html",
        {
            "cards": {card.key: card for card in cards},
            "operator": operator,
            "retry_waits": waits,
            "retry_cooldown": RETRY_COOLDOWN,
            "notice": None if error else _notice(request, board),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/metricas", response_class=HTMLResponse)
def metricas(request: Request) -> Response:
    """Tarjetas de métricas; el saldo de DeepSeek solo con sesión de operador."""

    return _render(request)


@router.post("/metricas/reintentar")
def reintentar(
    request: Request,
    fuente: str = Form(default=""),
    csrf_token: str = Form(default=""),
) -> Response:
    """Pide volver a consultar una fuente y vuelve a la página (303), sin JS.

    No consulta la fuente aquí: la da por caducada y la carga de la página la consulta. Dentro
    del enfriamiento no cambia nada y la página dice cuánto falta.
    """

    settings = _settings(request)
    if not verify_csrf(request, csrf_token):
        logger.warning("csrf.rejected", path=request.url.path)
        return _render(request, status_code=403, error=CSRF_INVALIDO, settings=settings)
    operator = operator_authorized(request, settings=settings)
    outcome = _board(request).retry(fuente, operator=operator)
    if outcome.status == "unknown":
        return _render(request, status_code=400, error=FUENTE_DESCONOCIDA, settings=settings)
    if outcome.status == "forbidden":
        return _render(request, status_code=403, error=SOLO_OPERADOR, settings=settings)
    kind = "reintento" if outcome.status == "done" else "espera"
    return RedirectResponse(url=f"/metricas?{kind}={fuente}#reintento-{fuente}", status_code=303)
