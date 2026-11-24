"""Juez externo del modo ``tools`` (spec 046): Jev de TypeSafe detrás de dos puertos.

:func:`build_judge` construye el juez solo si Jev está encendido de forma explícita
(``JEV_ENABLED=true``) y hay clave (``Settings.jev_active``); si no, devuelve ``None`` y el
gatekeeper y la compuerta siguen sin Jev (FR-006). :func:`jev_health` arma el bloque ``jev``
de ``/health`` sin llamar a TypeSafe (FR-024).
"""

from pathlib import Path
from typing import Any

import httpx

from carvajal.adapters.judge.jev import JevClient, JevJudge
from carvajal.infrastructure.settings import Settings

__all__ = ["JevClient", "JevJudge", "build_judge", "jev_health"]


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

    key = settings.typesafe_api_key.get_secret_value().strip()
    if not key or not (settings.jev_active or force):
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


def jev_health(settings: Settings, judge: Any = None) -> dict[str, Any]:
    """Bloque ``jev`` de ``/health``: configuración y estado del cliente, sin red.

    Sin runtime construido (``judge`` ``None``), ``available`` es la configuración
    (configurado y encendido) y ``calls_today`` es 0. No cambia el ``status`` global: Jev es
    opcional para atender.
    """

    configured = bool(settings.typesafe_api_key.get_secret_value().strip())
    enabled = settings.jev_enabled
    client = getattr(judge, "client", None)
    if isinstance(client, JevClient):
        available = client.available()
        calls_today = client.calls_today
        last_error = client.last_error
        model = client.last_model or settings.jev_model
    else:
        available = configured and enabled
        calls_today = 0
        last_error = None
        model = settings.jev_model
    return {
        "configured": configured,
        "enabled": enabled,
        "available": available,
        "model": model,
        "router": settings.jev_router_mode,
        "claims": settings.jev_claims_mode,
        "calls_today": calls_today,
        "last_error": last_error,
    }
