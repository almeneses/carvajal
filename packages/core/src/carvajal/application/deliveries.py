"""Reserva de entregas del webhook: cada ``message_id`` se atiende una sola vez.

El relé de mensajería reintenta cuando el turno tarda más que su timeout, y una petición
firmada se puede reenviar tal cual dentro de la ventana del timestamp. Antes de abrir un
hilo, cobrar cupo o llamar al LLM, el puente reserva ``(owner_key, delivery_id)`` de forma
atómica:

- ``acquired``: este intento atiende el mensaje.
- ``pending``: otro intento lo está atendiendo; el webhook responde 409 y el relé reintenta.
- ``done``: ya se atendió. Si dejó un turno guardado, el puente lo reemite; si no (nueva
  conversación sin texto, cupo agotado), devuelve la respuesta corta guardada.

Un fallo libera la reserva para que el reintento vuelva a intentarlo. Una reserva
``pending`` más vieja que :data:`PENDING_TTL` (proceso caído a mitad de turno) la toma el
siguiente intento. Las filas se purgan después de :data:`RETENTION`, muy por encima de la
ventana de ±5 minutos del timestamp firmado.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

__all__ = [
    "PENDING_TTL",
    "RETENTION",
    "DeliveryClaim",
    "DeliveryStatus",
    "DeliveryStore",
    "InMemoryDeliveryStore",
    "utc_now",
]

# Por encima del máximo de ``AGENT_TURN_TIMEOUT_S`` (300 s): un turno lento no se pisa.
PENDING_TTL = timedelta(minutes=10)
RETENTION = timedelta(days=1)

DeliveryStatus = Literal["acquired", "pending", "done"]


@dataclass(frozen=True)
class DeliveryClaim:
    """Resultado de reservar una entrega; ``reply`` solo existe en ``done`` sin turno."""

    status: DeliveryStatus
    reply: str | None = None


class DeliveryStore(Protocol):
    """Reserva atómica de entregas por dueño e identificador del mensaje."""

    def claim(self, owner_key: str, delivery_id: str) -> DeliveryClaim:
        """Reserva la entrega o informa en qué estado está."""

        ...

    def finish(self, owner_key: str, delivery_id: str, reply: str | None) -> None:
        """Marca la entrega como atendida; ``reply`` si no quedó un turno guardado."""

        ...

    def release(self, owner_key: str, delivery_id: str) -> None:
        """Suelta una reserva pendiente para que un reintento la vuelva a tomar."""

        ...


def utc_now() -> datetime:
    """Hora actual en UTC (inyectable en las pruebas)."""

    return datetime.now(UTC)


@dataclass
class _Entry:
    status: Literal["pending", "done"]
    reply: str | None
    updated_at: datetime


class InMemoryDeliveryStore:
    """Reserva en memoria del proceso, con un candado.

    Sirve para un solo proceso (pruebas, desarrollo). En producción el puente usa
    :class:`carvajal.adapters.storage.deliveries.SqlDeliveryStore`, que ven todos los
    workers.
    """

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = utc_now,
        pending_ttl: timedelta = PENDING_TTL,
        retention: timedelta = RETENTION,
    ) -> None:
        self._now = now
        self._pending_ttl = pending_ttl
        self._retention = retention
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _Entry] = {}

    def claim(self, owner_key: str, delivery_id: str) -> DeliveryClaim:
        now = self._now()
        key = (owner_key, delivery_id)
        with self._lock:
            self._purge(now)
            entry = self._entries.get(key)
            stale = (
                entry is not None
                and entry.status == "pending"
                and entry.updated_at < now - self._pending_ttl
            )
            if entry is None or stale:
                self._entries[key] = _Entry("pending", None, now)
                return DeliveryClaim("acquired")
            return DeliveryClaim(entry.status, entry.reply)

    def finish(self, owner_key: str, delivery_id: str, reply: str | None) -> None:
        with self._lock:
            self._entries[(owner_key, delivery_id)] = _Entry("done", reply, self._now())

    def release(self, owner_key: str, delivery_id: str) -> None:
        key = (owner_key, delivery_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.status == "pending":
                del self._entries[key]

    def _purge(self, now: datetime) -> None:
        cutoff = now - self._retention
        expired = [key for key, entry in self._entries.items() if entry.updated_at < cutoff]
        for key in expired:
            del self._entries[key]
