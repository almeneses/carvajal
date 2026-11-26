"""Embeddings servidos por Ollama a través de LangChain."""

import httpx
from langchain_ollama import OllamaEmbeddings
from ollama import ResponseError

__all__ = ["EmbeddingError", "OllamaEmbedder"]

DEFAULT_MODEL = "nomic-embed-text"
"""Modelo de embeddings por defecto del corpus."""

DEFAULT_TIMEOUT = 30.0
"""Segundos de espera antes de abandonar una petición a Ollama."""

DEFAULT_BASE_URL = "http://localhost:11434"
"""URL por defecto del servidor de Ollama (la misma que usa LangChain)."""

DEFAULT_KEEP_ALIVE_S = 86_400
"""Segundos que Ollama mantiene el modelo cargado tras cada petición (24 h).

Con los 5 minutos por defecto de Ollama, el tráfico espaciado recargaba el modelo en cada
consulta (ensayo del 2026-11-26: ``buscar_corpus`` con p50 de 6 s en
producción frente a 1,3 s en local).
"""


class EmbeddingError(RuntimeError):
    """Ollama no pudo calcular el vector (conexión, timeout o respuesta inválida)."""


class OllamaEmbedder:
    """Implementación de ``Embedder`` sobre ``OllamaEmbeddings``.

    El modelo, la URL del servidor, el ``timeout`` y el ``keep_alive`` se inyectan por
    parámetro; la configuración global sigue en ``Settings``. Construir
    el adaptador no verifica la conectividad: el primer fallo aparece al llamar
    a ``embed`` y se traduce a ``EmbeddingError`` con la causa encadenada.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        keep_alive: int | None = DEFAULT_KEEP_ALIVE_S,
    ) -> None:
        self._model = model
        self._base_url = base_url or DEFAULT_BASE_URL
        # ``keep_alive`` viaja en cada petición; ``None`` deja el valor del servidor.
        self._embeddings = OllamaEmbeddings(
            model=model,
            base_url=self._base_url,
            client_kwargs={"timeout": timeout},
            keep_alive=keep_alive,
        )

    @property
    def model(self) -> str:
        """Modelo que produce los vectores; el índice lo registra en cada punto."""

        return self._model

    def embed(self, text: str) -> list[float]:
        """Devuelve el vector del texto calculado por Ollama.

        La dimensión la valida el índice vectorial, que conoce el tamaño del
        índice; aquí solo se rechaza un vector vacío.

        Raises:
            EmbeddingError: si no se puede contactar con Ollama o la respuesta
                no se puede procesar.
            ValueError: si el vector devuelto está vacío.
        """

        try:
            embedding = self._embeddings.embed_query(text)
        except (httpx.HTTPError, OSError, ResponseError) as error:
            raise EmbeddingError(
                f"No se pudo contactar con Ollama en {self._base_url}. Verifica que el servidor "
                "esté en marcha y que el modelo esté descargado."
            ) from error
        except ValueError as error:
            raise EmbeddingError(
                "Ollama devolvió una respuesta que no se pudo interpretar."
            ) from error
        vector = [float(value) for value in embedding]
        if not vector:
            raise ValueError("Ollama devolvió un vector vacío.")
        return vector
