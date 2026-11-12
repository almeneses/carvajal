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


class EmbeddingError(RuntimeError):
    """Ollama no pudo calcular el vector (conexión, timeout o respuesta inválida)."""


class OllamaEmbedder:
    """Implementación de ``Embedder`` sobre ``OllamaEmbeddings``.

    El modelo, la URL del servidor y el ``timeout`` se inyectan por parámetro;
    la configuración global sigue en ``Settings`` y no se amplía aquí. Construir
    el adaptador no verifica la conectividad: el primer fallo aparece al llamar
    a ``embed`` y se traduce a ``EmbeddingError`` con la causa encadenada.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url or DEFAULT_BASE_URL
        self._embeddings = OllamaEmbeddings(
            model=model,
            base_url=self._base_url,
            client_kwargs={"timeout": timeout},
        )

    def embed(self, text: str) -> list[float]:
        """Devuelve el vector del texto calculado por Ollama.

        La dimensión la valida ``PgVectorStore``, que conoce el tamaño del
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
