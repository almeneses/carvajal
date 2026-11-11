"""Adaptador de LLM para DeepSeek (API compatible con OpenAI)."""

from langchain_openai import ChatOpenAI

from carvajal.adapters.llm._chat import _LangChainChat
from carvajal.infrastructure.settings import Settings


class DeepSeekProvider(_LangChainChat):
    """Generación de texto con DeepSeek a través de ``ChatOpenAI``."""

    def __init__(self, settings: Settings | None = None) -> None:
        if settings is None:
            settings = Settings()
        if not settings.deepseek_api_key:
            raise ValueError("Falta DEEPSEEK_API_KEY: define la clave para usar DeepSeekProvider.")
        super().__init__(
            ChatOpenAI(
                model=settings.deepseek_model,
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                temperature=0,
            )
        )
