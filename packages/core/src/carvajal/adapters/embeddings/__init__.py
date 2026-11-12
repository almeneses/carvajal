"""Generadores de embeddings que implementan el puerto ``Embedder``."""

from carvajal.adapters.embeddings.ollama import EmbeddingError, OllamaEmbedder

__all__ = ["EmbeddingError", "OllamaEmbedder"]
