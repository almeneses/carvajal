"""Adaptadores de generación de texto que implementan ``LLMProvider``."""

from carvajal.adapters.llm.deepseek import DeepSeekProvider
from carvajal.adapters.llm.ollama import OllamaProvider
from carvajal.application.ports import LLMProvider
from carvajal.infrastructure.settings import Settings

__all__ = ["DeepSeekProvider", "OllamaProvider", "build_llm_provider"]


def build_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Construye el proveedor configurado en ``llm_provider``.

    Args:
        settings: Configuración a usar; por defecto, la del entorno y ``.env``.

    Returns:
        ``DeepSeekProvider`` con ``LLM_PROVIDER=deepseek`` u ``OllamaProvider``
        con ``LLM_PROVIDER=ollama``.

    Raises:
        ValueError: si el proveedor no está soportado en esta ruta. ``claude``
            sigue disponible solo en la interfaz Streamlit (``rag.build_llm``).
    """

    if settings is None:
        settings = Settings()
    provider = settings.llm_provider.strip().lower()
    if provider == "deepseek":
        return DeepSeekProvider(settings)
    if provider == "ollama":
        return OllamaProvider(settings)
    if provider == "claude":
        raise ValueError(
            "El proveedor 'claude' solo está disponible en la interfaz Streamlit (rag.build_llm)."
        )
    raise ValueError(f"Proveedor de LLM no soportado: {provider!r}. Usa 'deepseek' u 'ollama'.")
