"""Reserva de entregas del webhook: cada ``message_id`` se atiende una sola vez.

El relé de mensajería reintenta cuando el turno tarda más que su timeout, y una petición
firmada se puede reenviar tal cual dentro de la ventana del timestamp. Antes de abrir un
hilo, cobrar cupo o llamar al LLM, el puente reserva ``(owner_key, delivery_id)`` de forma
atómica:

- ``acquired``: este intento atiende el mensaje.
- ``pending``: otro intento lo está atendiendo; el webhook responde 409 y el relé reintenta.
- ``done``: ya se atendió. Si dejó un turno guardado, el puente lo reemite; si no (nueva
  conversación sin texto, cupo agotado), devuelve la respuesta corta guardada.

Un fallo libera la reserva para que el reintento vuelva a intentarlo. Si el intento ya
cobró el cupo o abrió el hilo nuevo, la liberación lo anota (``quota_charged``,
``thread_opened``) y el reintento recibe esas marcas en su :class:`DeliveryClaim` para no
repetir esos efectos. Una reserva ``pending`` más vieja que :data:`PENDING_TTL` (proceso
caído a mitad de turno) la toma el siguiente intento. Cada toma lleva un ``token`` propio:
``finish`` y ``release`` solo actúan sobre la reserva del intento que la tiene, así un
intento viejo que termina tarde no borra la reserva que tomó otro. Las filas se purgan
después de :data:`RETENTION`, muy por encima de la ventana de ±5 minutos del timestamp
firmado.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

__all__ = [
    "PENDING_TTL",
    "RETENTION",
    "DeliveryClaim",
    "DeliveryStatus",
    "DeliveryStore",
    "InMemoryDeliveryStore",
    "new_token",
    "utc_now",
]

# Por encima del máximo de ``AGENT_TURN_TIMEOUT_S`` (300 s): un turno lento no se pisa.
PENDING_TTL = timedelta(minutes=10)
RETENTION = timedelta(days=1)

DeliveryStatus = Literal["acquired", "pending", "done"]


@dataclass(frozen=True)
class DeliveryClaim:
    """Resultado de reservar una entrega; ``reply`` solo existe en ``done`` sin turno.

    En ``acquired``, ``token`` identifica esta toma (se pasa a ``finish`` y ``release``) y
    ``quota_charged``/``thread_opened`` dicen si un intento anterior fallido ya cobró el
    cupo o abrió el hilo nuevo de este mensaje.
    """

    status: DeliveryStatus
    reply: str | None = None
    token: str | None = field(default=None, compare=False)
    quota_charged: bool = False
    thread_opened: bool = False


class DeliveryStore(Protocol):
    """Reserva atómica de entregas por dueño e identificador del mensaje."""

    def claim(self, owner_key: str, delivery_id: str) -> DeliveryClaim:
        """Reserva la entrega o informa en qué estado está."""

        ...

    def finish(
        self, owner_key: str, delivery_id: str, reply: str | None, token: str | None = None
    ) -> None:
        """Marca la entrega como atendida; ``reply`` si no quedó un turno guardado.

        Con ``token``, solo si la reserva sigue siendo de ese intento.
        """

        ...

    def release(
        self,
        owner_key: str,
        delivery_id: str,
        token: str | None = None,
        *,
        quota_charged: bool = False,
        thread_opened: bool = False,
    ) -> None:
        """Suelta una reserva pendiente para que un reintento la vuelva a tomar.

        Con ``token``, solo si la reserva sigue siendo de ese intento. Con
        ``quota_charged`` o ``thread_opened``, la fila queda libre con esas marcas.
        """

        ...


def utc_now() -> datetime:
    """Hora actual en UTC (inyectable en las pruebas)."""

    return datetime.now(UTC)


def new_token() -> str:
    """Identificador de una toma de la reserva."""

    return uuid.uuid4().hex


@dataclass
class _Entry:
    status: Literal["pending", "done"]
    reply: str | None
    updated_at: datetime
    # ``None`` en una reserva pendiente liberada con marcas: la toma el siguiente intento.
    token: str | None = None
    quota_charged: bool = False
    thread_opened: bool = False


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
            if entry is None:
                token = new_token()
                self._entries[key] = _Entry("pending", None, now, token)
                return DeliveryClaim("acquired", token=token)
            free = entry.status == "pending" and (
                entry.token is None or entry.updated_at < now - self._pending_ttl
            )
            if free:
                entry.token = new_token()
                entry.updated_at = now
                return DeliveryClaim(
                    "acquired",
                    token=entry.token,
                    quota_charged=entry.quota_charged,
                    thread_opened=entry.thread_opened,
                )
            return DeliveryClaim(entry.status, entry.reply)

    def finish(
        self, owner_key: str, delivery_id: str, reply: str | None, token: str | None = None
    ) -> None:
        key = (owner_key, delivery_id)
        with self._lock:
            entry = self._entries.get(key)
            if token is not None and (entry is None or entry.token != token):
                return
            self._entries[key] = _Entry("done", reply, self._now())

    def release(
        self,
        owner_key: str,
        delivery_id: str,
        token: str | None = None,
        *,
        quota_charged: bool = False,
        thread_opened: bool = False,
    ) -> None:
        key = (owner_key, delivery_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.status != "pending":
                return
            if token is not None and entry.token != token:
                return
            if quota_charged or thread_opened:
                entry.token = None
                entry.updated_at = self._now()
                entry.quota_charged = entry.quota_charged or quota_charged
                entry.thread_opened = entry.thread_opened or thread_opened
                return
            del self._entries[key]

    def _purge(self, now: datetime) -> None:
        cutoff = now - self._retention
        expired = [key for key, entry in self._entries.items() if entry.updated_at < cutoff]
        for key in expired:
            del self._entries[key]
