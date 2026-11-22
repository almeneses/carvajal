"""Memoria silenciosa del visitante en el Cotizador (spec 042, FR-022).

El Cotizador recuerda lo que la persona cuenta de sí misma —nombre, dónde trabaja,
dónde vive y qué prefiere— y lo aplica en el hilo siguiente sin preguntarlo. La
extracción es por reglas, no por LLM: solo se guarda lo que coincide con un patrón
conocido, con límites de tamaño y caducidad.

El perfil no se escribe en ``messages.sources`` ni en las trazas: vive en su propia
tabla, se asocia al dueño firmado y se borra cuando la persona lo pide.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = [
    "EMPTY",
    "MEMORY_DAYS",
    "extract",
    "forget_requested",
    "is_fresh",
    "merge",
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

_NAME = re.compile(
    r"\b(?:me llamo|mi nombre es)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,})?)",
    re.I,
)
_WORK = re.compile(r"\btrabajo (?:en|para)\s+([^,.;\n]{3,80})", re.I)
_BUSINESS = re.compile(
    r"\btengo (?:una|un)\s+"
    r"(cafeter[íi]a|restaurante|panader[íi]a|tienda|comida r[áa]pida|hotel|catering|negocio)"
    r"((?:\s+(?:en|llamad[ao]))?\s*[^,.;\n]{0,60})",
    re.I,
)
_HOME = re.compile(r"\b(?:vivo|resido)\s+en\s+([^,.;\n]{3,60})", re.I)
_PLACE = re.compile(
    r"\bmi (?:negocio|cafeter[íi]a|restaurante|panader[íi]a|tienda)\s+(?:est[áa]|queda)\s+en\s+"
    r"([^,.;\n]{3,60})",
    re.I,
)
_DELIVERY = re.compile(
    r"\b(?:siempre\s+(?:pido|quiero|necesito)\s+(?:a\s+)?domicilio|prefiero\s+(?:el\s+)?domicilio"
    r"|siempre a domicilio)\b",
    re.I,
)
_PREFERENCE = re.compile(
    r"\b(?:me gusta|me gustan|me encanta|me encantan|prefiero|siempre pido)\s+([^,.;\n]{3,60})",
    re.I,
)
_FORGET = re.compile(
    r"\b(?:olvida (?:mis datos|lo que sabes de m[íi])|olv[íi]dame|borra (?:mi memoria|mis datos)"
    r"|no recuerdes (?:mis datos|nada de m[íi]))\b",
    re.I,
)
_TRAILING = re.compile(
    r"\s+(?:por favor|gracias|y ya)\b.*$"
    r"|\s+y\s+(?:siempre|me|mi|trabajo|vivo|prefiero|pido|quiero|necesito)\b.*$",
    re.I,
)


_CITY_IN_PHRASE = re.compile(r"\ben\s+([^,.;\n]{3,60})\s*$", re.I)
_ARTICLE = re.compile(r"^(?:una|un|la|el|mi)\s+", re.I)


def _clean(value: str, limit: int) -> str:
    text = _TRAILING.sub("", value.strip(" .,;:"))
    return text[:limit].strip()


def extract(text: str) -> dict[str, Any]:
    """Detalles personales que la persona dijo en este mensaje.

    Solo devuelve las claves encontradas; ``{}`` si no dijo nada que se guarde.
    """

    found: dict[str, Any] = {}
    name = _NAME.search(text)
    if name:
        found["name"] = _clean(name.group(1), MAX_NAME).title()
    work = _WORK.search(text)
    if work:
        workplace = _ARTICLE.sub("", _clean(work.group(1), MAX_TEXT))
        found["workplace"] = workplace
        inside = _CITY_IN_PHRASE.search(workplace)
        if inside:
            found["city"] = _clean(inside.group(1), MAX_TEXT)
    business = _BUSINESS.search(text)
    if business:
        tail = _clean(business.group(2) or "", MAX_TEXT)
        phrase = _ARTICLE.sub("", f"{business.group(1).lower()} {tail}".strip())
        found.setdefault("workplace", phrase)
        inside = _CITY_IN_PHRASE.search(phrase)
        if inside and "city" not in found:
            found["city"] = _clean(inside.group(1), MAX_TEXT)
    home = _HOME.search(text) or _PLACE.search(text)
    if home:
        found["city"] = _clean(home.group(1), MAX_TEXT)
    if _DELIVERY.search(text):
        found["delivery"] = True
    preferences = [
        clean
        for item in _PREFERENCE.findall(text)
        if (clean := _clean(item, MAX_PREFERENCE))
        and clean.lower() not in {"a domicilio", "el domicilio", "domicilio"}
    ]
    if preferences:
        found["preferences"] = preferences
    return found


def forget_requested(text: str) -> bool:
    """La persona pidió borrar lo que el Cotizador recuerda de ella."""

    return _FORGET.search(text) is not None


def merge(profile: dict[str, Any] | None, updates: dict[str, Any]) -> dict[str, Any]:
    """Aplica lo nuevo sobre lo recordado sin perder preferencias anteriores."""

    current = normalize(profile or EMPTY)
    merged = {**current}
    for key, value in updates.items():
        if value in (None, "", []):
            continue
        if key == "preferences":
            merged["preferences"] = [*value, *current["preferences"]]
        else:
            merged[key] = value
    return normalize(merged)


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
