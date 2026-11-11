"""Embeddings servidos por Ollama a través de LangChain."""

from langchain_ollama import OllamaEmbeddings

DEFAULT_MODEL = "nomic-embed-text"
"""Modelo de embeddings por defecto del corpus."""

DEFAULT_TIMEOUT = 30.0
"""Segundos de espera antes de abandonar una petición a Ollama."""


class OllamaEmbedder:
    """Implementación de ``Embedder`` sobre ``OllamaEmbeddings``.

    El modelo, la URL del servidor y el ``timeout`` se inyectan por parámetro;
    la configuración global sigue en ``Settings`` y no se amplía aquí. Construir
    el adaptador no verifica la conectividad: el primer fallo aparece al llamar
    a ``embed``.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._embeddings = OllamaEmbeddings(
            model=model,
            base_url=base_url,
            client_kwargs={"timeout": timeout},
        )

    def embed(self, text: str) -> list[float]:
        """Devuelve el vector del texto calculado por Ollama.

        La dimensión la valida ``PgVectorStore``, que conoce el tamaño del
        índice; aquí solo se rechaza un vector vacío.
        """

        vector = [float(value) for value in self._embeddings.embed_query(text)]
        if not vector:
            raise ValueError("Ollama devolvió un vector vacío.")
        return vector
