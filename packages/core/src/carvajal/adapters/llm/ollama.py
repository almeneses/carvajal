"""Adaptador de LLM para Ollama (modelo local)."""

from langchain_ollama import ChatOllama

from carvajal.adapters.llm._chat import _LangChainChat
from carvajal.infrastructure.settings import Settings


class OllamaProvider(_LangChainChat):
    """Generación de texto con el modelo local servido por Ollama."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or Settings()
        super().__init__(ChatOllama(model=settings.ollama_model, temperature=0))
