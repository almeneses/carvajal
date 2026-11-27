"""Textos de un solo uso en memoria del proceso, por dueño e id de mensaje (spec 056).

``POST /chat`` (sin JavaScript) guarda el turno y redirige al hilo (PRG). En la confirmación
del pedido el texto visible lleva el nombre y el correo de quien compra, que no se guardan:
el POST lo deja aquí y el ``GET /chat`` siguiente lo muestra **una sola vez** (:meth:`pop`).
Recargar después muestra lo guardado, sin los datos, y nunca reenvía el formulario.

Solo en memoria, como mucho ``ttl_s`` segundos y ``max_entries`` textos (LRU): nunca va a la
base ni a los logs. Con varios workers, el GET puede caer en otro proceso: entonces se ve lo
guardado (la versión sin datos), que es lo seguro.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Final

__all__ = ["SHOWN_TTL_S", "EphemeralTexts", "shown_turns"]

# Lo que tarda el navegador en seguir el 303, con margen; después se ve lo guardado.
SHOWN_TTL_S: Final = 120.0
SHOWN_ENTRIES: Final = 256
_STATE_LOCK = threading.Lock()


class EphemeralTexts:
    """Textos por ``(dueño, id de mensaje)`` que caducan por tiempo y por tamaño."""

    def __init__(
        self,
        *,
        ttl_s: float = SHOWN_TTL_S,
        max_entries: int = SHOWN_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max(1, int(max_entries))
        self._clock = clock
        self._lock = threading.Lock()
        self._items: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def remember(self, owner_key: str, message_id: str, text: str) -> None:
        if not owner_key or not message_id or not text:
            return
        with self._lock:
            key = (owner_key, message_id)
            self._items[key] = (self._clock(), text)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def get(self, owner_key: str, message_id: str) -> str | None:
        """El texto si sigue vigente, sin consumirlo."""

        with self._lock:
            return self._fresh(owner_key, message_id, consume=False)

    def pop(self, owner_key: str, message_id: str) -> str | None:
        """El texto si sigue vigente, y se olvida: se muestra una sola vez."""

        with self._lock:
            return self._fresh(owner_key, message_id, consume=True)

    def _fresh(self, owner_key: str, message_id: str, *, consume: bool) -> str | None:
        key = (owner_key, message_id)
        found = self._items.get(key)
        if found is None:
            return None
        if self._clock() - found[0] >= self.ttl_s:
            del self._items[key]
            return None
        if consume:
            del self._items[key]
        return found[1]


def shown_turns(state: Any) -> EphemeralTexts:
    """El :class:`EphemeralTexts` de la app (``app.state.shown_turns``), creado al primer uso."""

    found = getattr(state, "shown_turns", None)
    if isinstance(found, EphemeralTexts):
        return found
    with _STATE_LOCK:
        found = getattr(state, "shown_turns", None)
        if not isinstance(found, EphemeralTexts):
            found = EphemeralTexts()
            state.shown_turns = found
    return found
