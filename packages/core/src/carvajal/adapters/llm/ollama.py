"""Adaptador de LLM para Ollama (modelo local)."""

from langchain_ollama import ChatOllama

from carvajal.adapters.llm._chat import _LangChainChat
from carvajal.infrastructure.settings import Settings


class OllamaProvider(_LangChainChat):
    """Generación de texto con el modelo local servido por Ollama.

    Construir el adaptador no comprueba que el servicio esté accesible ni que el
    modelo exista: eso se descubre en la primera llamada.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        if settings is None:
            settings = Settings()
        if not settings.ollama_model.strip():
            raise ValueError("OLLAMA_MODEL está vacío: define un modelo para Ollama.")
        super().__init__(
            ChatOllama(
                model=settings.ollama_model,
                temperature=0,
                num_ctx=settings.ollama_num_ctx,
            )
        )
