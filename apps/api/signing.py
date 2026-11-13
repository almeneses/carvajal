"""Cookies firmadas: valor + HMAC-SHA256 con ``SESSION_SECRET``.

Se usa para la identidad anónima y para el estado del flujo OAuth. El valor no
debe contener puntos: el formato es ``valor.firma`` y la firma se separa por el
último punto.
"""

import hmac
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from functools import cache
from hashlib import sha256
from typing import Any

from carvajal.infrastructure.settings import Settings

__all__ = ["dumps", "loads", "sign", "verify"]


@cache
def _secret() -> str:
    """Clave de firma; en producción debe venir de ``SESSION_SECRET``."""

    return Settings().session_secret


def _signature(value: str) -> str:
    return hmac.new(_secret().encode(), value.encode(), sha256).hexdigest()


def sign(value: str) -> str:
    """Devuelve ``valor.firma``."""

    return f"{value}.{_signature(value)}"


def verify(raw: str) -> str | None:
    """Devuelve el valor si la firma cuadra; ``None`` si falta o está manipulado."""

    value, _, firma = raw.rpartition(".")
    if value and hmac.compare_digest(_signature(value), firma):
        return value
    return None


def dumps(payload: dict[str, Any]) -> str:
    """Serializa y firma un diccionario (sin puntos en el valor)."""

    crudo = json.dumps(payload, separators=(",", ":")).encode()
    return sign(urlsafe_b64encode(crudo).decode())


def loads(raw: str) -> dict[str, Any] | None:
    """Verifica la firma y recupera el diccionario; ``None`` si no es válido."""

    value = verify(raw)
    if value is None:
        return None
    try:
        datos = json.loads(urlsafe_b64decode(value.encode()))
    except (ValueError, TypeError):
        return None
    return datos if isinstance(datos, dict) else None
