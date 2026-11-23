"""Eventos del stream del chat (W3.4).

El servicio emite estos eventos y la vista los traduce a SSE; así el dominio y
la persistencia no dependen del formato de cable. ``data`` es un mapeo JSON.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["ChatEvent", "EventName"]

# ``products`` (spec 036) lleva las tarjetas de la ruta comercial, ya renderizadas y escapadas.
# ``status`` (spec 044) es la línea de progreso de una herramienta del modo ``tools``.
EventName = Literal[
    "open", "trace", "status", "meta", "products", "token", "fallback", "done", "error"
]


@dataclass(frozen=True)
class ChatEvent:
    """Evento nombrado del protocolo SSE con su carga JSON."""

    name: EventName
    data: Mapping[str, Any]
