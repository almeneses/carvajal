"""Embeddings servidos por Ollama a través de LangChain."""

from langchain_ollama import OllamaEmbeddings

DEFAULT_MODEL = "nomic-embed-text"
"""Modelo de embeddings por defecto del corpus."""


class OllamaEmbedder:
    """Implementación de ``Embedder`` sobre ``OllamaEmbeddings``.

    El modelo y la URL del servidor se inyectan por parámetro; la configuración
    global sigue en ``Settings`` y no se amplía aquí.
    """

    def __init__(self, *, model: str = DEFAULT_MODEL, base_url: str | None = None) -> None:
        self._embeddings = OllamaEmbeddings(model=model, base_url=base_url)

    def embed(self, text: str) -> list[float]:
        """Devuelve el vector del texto calculado por Ollama."""

        return [float(value) for value in self._embeddings.embed_query(text)]
