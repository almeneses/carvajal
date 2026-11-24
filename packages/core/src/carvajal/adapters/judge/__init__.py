"""Juez externo del modo ``tools`` (spec 046): Jev de TypeSafe detrás de dos puertos.

:func:`build_judge` construye el juez solo si Jev está encendido de forma explícita
(``JEV_ENABLED=true``) y hay clave (``Settings.jev_active``); si no, devuelve ``None`` y el
gatekeeper y la compuerta siguen sin Jev (FR-006). :func:`jev_health` arma el bloque ``jev``
de ``/health`` sin llamar a TypeSafe (FR-024).
"""

from pathlib import Path
from typing import Any

import httpx
import structlog

from carvajal.adapters.judge.jev import JevClient, JevJudge
from carvajal.infrastructure.settings import Settings

__all__ = ["JevClient", "JevJudge", "build_judge", "jev_health", "usable_key"]

logger = structlog.get_logger(__name__)


def usable_key(settings: Settings) -> str:
    """Clave de TypeSafe sin espacios, o ``""`` si falta o no sirve como cabecera HTTP.

    Una clave con caracteres no ASCII o no imprimibles (un espacio de ancho cero o una
    comilla tipográfica pegados en el ``.env``) no se puede enviar en ``Authorization``.
    """

    key = settings.typesafe_api_key.get_secret_value().strip()
    return key if key.isascii() and key.isprintable() else ""


def build_judge(
    settings: Settings,
    *,
    ledger: str | Path | None = None,
    transport: httpx.BaseTransport | None = None,
    force: bool = False,
) -> JevJudge | None:
    """Juez de Jev con la configuración dada; ``None`` si Jev no está activo.

    ``ledger`` sustituye a ``JEV_LEDGER`` (los comandos de evaluación pasan el suyo).
    ``force`` construye el juez con la clave aunque ``JEV_ENABLED`` sea ``false``: lo usan
    solo los comandos de medición, que exigen un libro explícito (FR-031).
    """

    if not (settings.jev_active or force):
        return None
    key = usable_key(settings)
    if not key:
        if settings.typesafe_api_key.get_secret_value().strip():
            # Nunca la clave: solo que no sirve.
            logger.warning("jev.invalid_key", reason="caracteres_no_ascii")
        return None
    client = JevClient(
        api_key=key,
        base_url=settings.jev_base_url,
        model=settings.jev_model,
        timeout_s=settings.jev_timeout_s,
        max_calls_per_day=settings.jev_max_calls_per_day,
        ledger=ledger if ledger is not None else (settings.jev_ledger or None),
        transport=transport,
    )
    return JevJudge(client, claims_threshold=settings.jev_claims_threshold)


def jev_health(settings: Settings, judge: Any = None, *, mode: str | None = None) -> dict[str, Any]:
    """Bloque ``jev`` de ``/health``: configuración y estado del cliente, sin red.

    ``mode`` es el modo que atiende el runtime ya construido (``tools`` o ``routes``);
    ``None`` si aún no se construyó. Sin runtime, ``available`` es la configuración
    (``AGENT_MODE=tools``, configurado con una clave válida y encendido) y ``calls_today`` es
    0. Con runtime y sin juez (modo ``routes``, que nunca llama a Jev, FR-009, o un juez
    que no se pudo construir), Jev no está disponible. Fuera del modo ``tools``, ``router`` y
    ``claims`` son ``off``. No cambia el ``status`` global: Jev es opcional para atender.
    """

    configured = bool(settings.typesafe_api_key.get_secret_value().strip())
    enabled = settings.jev_enabled
    serving = mode if mode is not None else settings.agent_mode
    client = getattr(judge, "client", None)
    if isinstance(client, JevClient):
        available = client.available()
        calls_today = client.calls_today
        last_error = client.last_error
        model = client.last_model or settings.jev_model
    else:
        available = mode is None and serving == "tools" and enabled and bool(usable_key(settings))
        calls_today = 0
        last_error = None
        model = settings.jev_model
    tools = serving == "tools"
    return {
        "configured": configured,
        "enabled": enabled,
        "available": available,
        "model": model,
        "router": settings.jev_router_mode if tools else "off",
        "claims": settings.jev_claims_mode if tools else "off",
        "calls_today": calls_today,
        "last_error": last_error,
    }
