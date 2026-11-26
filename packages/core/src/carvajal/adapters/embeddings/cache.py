"""Caché LRU acotada de embeddings de consultas y calentamiento del modelo.

``buscar_corpus`` embebe la consulta en cada llamada; las preguntas frecuentes («historia de
Carvajal», «dónde comprar») se repiten entre turnos y entre personas. :class:`CachedEmbedder`
envuelve cualquier ``Embedder`` y guarda los últimos ``maxsize`` vectores en memoria del
proceso, con un candado para los hilos del servidor. No es para indexar: los fragmentos del
corpus no se repiten y llenarían la caché.

:func:`warm_up` lanza una petición de embedding en un hilo aparte al arrancar la app o el
MCP, para que el modelo ya esté cargado en Ollama cuando llegue la primera pregunta. No
bloquea el arranque y no falla si Ollama no responde: solo lo registra.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable
from time import perf_counter

import structlog

from carvajal.application.ports import Embedder

__all__ = ["DEFAULT_CACHE_SIZE", "WARM_UP_TEXT", "CachedEmbedder", "warm_up"]

logger = structlog.get_logger(__name__)

DEFAULT_CACHE_SIZE = 512
"""Consultas distintas que se conservan (≈ 25 KB por vector de 768 dimensiones)."""

WARM_UP_TEXT = "Carvajal empaques"
"""Texto de la petición de calentamiento: corto y sin datos de nadie."""


class CachedEmbedder:
    """``Embedder`` con caché LRU acotada por texto exacto de la consulta.

    Solo guarda vectores calculados con éxito; un fallo se propaga sin cachear. Devuelve una
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
        with self._lock:
            cached = self._vectors.get(text)
            if cached is not None:
                self._vectors.move_to_end(text)
                self.hits += 1
                return list(cached)
            self.misses += 1
        vector = self._inner.embed(text)
        with self._lock:
            self._vectors[text] = tuple(vector)
            self._vectors.move_to_end(text)
            while len(self._vectors) > self._maxsize:
                self._vectors.popitem(last=False)
        return list(vector)

    def __len__(self) -> int:
        return len(self._vectors)


def warm_up(
    embedder: Callable[[], Embedder], *, name: str = "embeddings.warm_up"
) -> threading.Thread:
    """Calienta el modelo en segundo plano: construye el embedder y embebe un texto corto.

    ``embedder`` es una fábrica para que también la construcción (importar LangChain tarda
    segundos) salga del arranque. El hilo es *daemon*: no retiene el cierre del proceso.
    Cualquier error queda en el log ``embeddings.warm_up_failed`` (sin el mensaje, que puede
    traer la URL) y el arranque sigue igual.
    """

    def run() -> None:
        started = perf_counter()
        try:
            embedder().embed(WARM_UP_TEXT)
        except Exception as error:  # Ollama caído, sin el modelo o mal configurado
            logger.warning("embeddings.warm_up_failed", error=type(error).__name__)
            return
        logger.info("embeddings.warmed_up", elapsed_ms=round((perf_counter() - started) * 1000))

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread
