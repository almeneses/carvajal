"""Perfil del visitante del Cotizador (spec 042, FR-022): forma, límites y caducidad.

Solo queda lo que usa ``SqlVisitorMemory``: el perfil vacío, su normalización (tamaños y
preferencias sin duplicar) y la caducidad de ``MEMORY_DAYS``. La extracción por reglas
del Cotizador anterior (``extract``, ``merge`` y ``forget_requested``) se retiró en la spec
054: el compositor del Cotizador ya no existe y la memoria del agente vive en
``carvajal.agent.memory`` (spec 045).

El perfil no se escribe en ``messages.sources`` ni en las trazas: vive en su propia
tabla, se asocia al dueño firmado y se borra cuando la persona lo pide.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = [
    "EMPTY",
    "MEMORY_DAYS",
    "is_fresh",
    "normalize",
]

MEMORY_DAYS = 180
MAX_NAME = 60
MAX_TEXT = 80
MAX_PREFERENCES = 5
MAX_PREFERENCE = 60

EMPTY: dict[str, Any] = {
    "name": None,
    "workplace": None,
    "city": None,
    "delivery": False,
    "preferences": [],
}

_TRAILING = re.compile(
    r"\s+(?:por favor|gracias|y ya)\b.*$"
    r"|\s+y\s+(?:siempre|me|mi|trabajo|vivo|prefiero|pido|quiero|necesito)\b.*$",
    re.I,
)


def _clean(value: str, limit: int) -> str:
    text = _TRAILING.sub("", value.strip(" .,;:"))
    return text[:limit].strip()


def normalize(profile: dict[str, Any]) -> dict[str, Any]:
    """Recorta tamaños y deduplica preferencias para que la tabla no crezca sin control."""

    preferences: list[str] = []
    for item in profile.get("preferences") or []:
        clean = _clean(str(item), MAX_PREFERENCE)
        if clean and clean.lower() not in {known.lower() for known in preferences}:
            preferences.append(clean)
    return {
        "name": _clean(str(profile["name"]), MAX_NAME) if profile.get("name") else None,
        "workplace": (
            _clean(str(profile["workplace"]), MAX_TEXT) if profile.get("workplace") else None
        ),
        "city": _clean(str(profile["city"]), MAX_TEXT) if profile.get("city") else None,
        "delivery": bool(profile.get("delivery")),
        "preferences": preferences[:MAX_PREFERENCES],
    }


def is_fresh(updated_at: datetime | None, *, now: datetime | None = None) -> bool:
    """El perfil caduca: lo guardado hace más de ``MEMORY_DAYS`` deja de usarse."""

    if updated_at is None:
        return False
    reference = now or datetime.now(UTC)
    return reference - updated_at <= timedelta(days=MEMORY_DAYS)
