"""Eventos del stream del chat (W3.4).

El servicio emite estos eventos y la vista los traduce a SSE; así el dominio y
la persistencia no dependen del formato de cable. ``data`` es un mapeo JSON.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["ChatEvent", "EventName"]

EventName = Literal["open", "trace", "meta", "token", "fallback", "done", "error"]


@dataclass(frozen=True)
class ChatEvent:
    """Evento nombrado del protocolo SSE con su carga JSON."""

    name: EventName
    data: Mapping[str, Any]
