"""Caché LRU acotada de embeddings de consultas y calentamiento del modelo.

``buscar_corpus`` embebe la consulta en cada llamada; las preguntas frecuentes («historia de
Carvajal», «dónde comprar») se repiten entre turnos y entre personas. :class:`CachedEmbedder`
envuelve cualquier ``Embedder`` y guarda los últimos ``maxsize`` vectores en memoria del
proceso, con un candado para los hilos del servidor. No es para indexar: los fragmentos del
corpus no se repiten y llenarían la caché.

:func:`warm_up` lanza una petición de embedding en un hilo aparte al arrancar la app o el
MCP, para que el modelo ya esté cargado en Ollama cuando llegue la primera pregunta. No
bloquea el arranque y no falla si Ollama no responde: reintenta durante unos dos minutos
(en ``compose.prod.yml`` la app arranca con Ollama apenas iniciado, y su healthcheck da 90 s
de ``start_period``) y, si sigue sin responder, solo lo registra.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from time import perf_counter

import structlog

from carvajal.application.ports import Embedder

__all__ = [
    "DEFAULT_CACHE_SIZE",
    "WARM_UP_RETRY_DELAYS_S",
    "WARM_UP_TEXT",
    "CachedEmbedder",
    "warm_up",
]

logger = structlog.get_logger(__name__)

DEFAULT_CACHE_SIZE = 512
"""Consultas distintas que se conservan (≈ 25 KB por vector de 768 dimensiones)."""

WARM_UP_TEXT = "Carvajal empaques"
"""Texto de la petición de calentamiento: corto y sin datos de nadie."""

WARM_UP_RETRY_DELAYS_S: tuple[float, ...] = (5, 10, 15, 30, 30, 30)
"""Esperas entre intentos: 7 intentos en unos 120 s, más que el ``start_period`` de Ollama."""


class CachedEmbedder:
    """``Embedder`` con caché LRU acotada por texto exacto de la consulta.

    Solo guarda vectores calculados con éxito; un fallo se propaga sin cachear. Con la misma
    consulta en varios hilos, solo uno la calcula y los demás esperan su vector; consultas
    distintas se calculan en paralelo. ``misses`` cuenta los cálculos iniciados. Devuelve una
    copia de la lista para que quien la reciba no altere la guardada. Con ``maxsize=0`` no
    guarda nada y delega siempre.
    """

    def __init__(self, inner: Embedder, *, maxsize: int = DEFAULT_CACHE_SIZE) -> None:
        if maxsize < 0:
            raise ValueError(f"maxsize debe ser no negativo; recibido {maxsize!r}.")
        self._inner = inner
        self._maxsize = maxsize
        self._vectors: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._lock = threading.Lock()
        # Un cálculo en curso por texto: dos hilos con la misma consulta no van los dos a
        # Ollama; el segundo espera el vector del primero (sin retener el candado global).
        self._pending: dict[str, threading.Event] = {}
        self.hits = 0
        self.misses = 0

    @property
    def model(self) -> str:
        """El modelo del embedder envuelto (el índice lo compara con el de sus puntos)."""

        return str(getattr(self._inner, "model", ""))

    @property
    def inner(self) -> Embedder:
        return self._inner

    def embed(self, text: str) -> list[float]:
        if self._maxsize == 0:
            return list(self._inner.embed(text))
        while True:
            with self._lock:
                cached = self._vectors.get(text)
                if cached is not None:
                    self._vectors.move_to_end(text)
                    self.hits += 1
                    return list(cached)
                pending = self._pending.get(text)
                if pending is None:
                    # Este hilo calcula el texto; los demás con el mismo texto esperan.
                    pending = self._pending[text] = threading.Event()
                    self.misses += 1
                    break
            # Otro hilo ya lo pide a Ollama: se espera y se relee la caché. Si aquel falló,
            # la entrada no existe y este hilo lo intenta por su cuenta en la vuelta.
            pending.wait()
        try:
            vector = self._inner.embed(text)
            with self._lock:
                self._vectors[text] = tuple(vector)
                self._vectors.move_to_end(text)
                while len(self._vectors) > self._maxsize:
                    self._vectors.popitem(last=False)
        finally:
            with self._lock:
                del self._pending[text]
            pending.set()
        return list(vector)

    def __len__(self) -> int:
        return len(self._vectors)


def warm_up(
    embedder: Callable[[], Embedder],
    *,
    name: str = "embeddings.warm_up",
    retry_delays: Sequence[float] = WARM_UP_RETRY_DELAYS_S,
    sleep: Callable[[float], None] = time.sleep,
) -> threading.Thread:
    """Calienta el modelo en segundo plano: construye el embedder y embebe un texto corto.

    ``embedder`` es una fábrica para que también la construcción (importar LangChain tarda
    segundos) salga del arranque. El hilo es *daemon*: no retiene el cierre del proceso.
    Si falla, reintenta tras cada espera de ``retry_delays`` (Ollama puede estar arrancando
    todavía); cada fallo intermedio queda en ``embeddings.warm_up_retry`` y el último en
    ``embeddings.warm_up_failed``, sin el mensaje (puede traer la URL). El arranque sigue
    igual en todos los casos.
    """

    def run() -> None:
        started = perf_counter()
        attempts = len(retry_delays) + 1
        for attempt in range(1, attempts + 1):
            try:
                embedder().embed(WARM_UP_TEXT)
            except Exception as error:  # Ollama caído, arrancando, sin el modelo…
                if attempt == attempts:
                    logger.warning(
                        "embeddings.warm_up_failed", error=type(error).__name__, attempts=attempt
                    )
                    return
                logger.info("embeddings.warm_up_retry", error=type(error).__name__, attempt=attempt)
                sleep(retry_delays[attempt - 1])
                continue
            logger.info(
                "embeddings.warmed_up",
                elapsed_ms=round((perf_counter() - started) * 1000),
                attempts=attempt,
            )
            return

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread
