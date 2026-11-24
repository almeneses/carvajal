"""Adaptador de LLM para DeepSeek (API compatible con OpenAI)."""

from typing import Any

from langchain_openai import ChatOpenAI

from carvajal.adapters.llm._chat import _LangChainChat
from carvajal.infrastructure.settings import Settings

__all__ = ["DeepSeekProvider", "build_deepseek_chat"]


def build_deepseek_chat(
    settings: Settings | None = None,
    *,
    stream_usage: bool = False,
    for_tools: bool = False,
    timeout_s: float | None = None,
    max_retries: int | None = None,
) -> ChatOpenAI:
    """Construye el ``ChatOpenAI`` de DeepSeek con temperatura 0.

    Es el mismo cliente que usa :class:`DeepSeekProvider`; el agente con herramientas
    (spec 044) lo usa directamente porque necesita ``bind_tools``. ``stream_usage`` pide al
    proveedor el uso de tokens también en streaming (``usage_metadata`` del último trozo).

    ``for_tools`` apaga el modo de razonamiento (``thinking``): los modelos recientes de
    DeepSeek (p. ej. ``deepseek-flash``) lo traen activo por defecto y en ese modo la API
    rechaza ``tool_choice="required"`` («Thinking mode does not support this tool_choice»),
    que el agente usa en el primer paso. Además, sin razonamiento la temperatura 0 aplica.

    ``timeout_s`` y ``max_retries`` acotan la petición (por defecto, ``llm_timeout_seconds``
    y los reintentos del cliente de ``openai``); el agente los usa para no esperar más que el
    plazo del turno.

    Construirlo no comprueba que la API esté accesible ni que el modelo exista: eso se
    descubre en la primera llamada.

    Raises:
        ValueError: si falta la clave, el modelo o la URL base.
    """

    if settings is None:
        settings = Settings()
    api_key = settings.deepseek_api_key.strip()
    model = settings.deepseek_model.strip()
    base_url = settings.deepseek_base_url.strip()
    if not api_key:
        raise ValueError("Falta DEEPSEEK_API_KEY: define la clave para usar DeepSeekProvider.")
    if not model:
        raise ValueError("DEEPSEEK_MODEL está vacío: define un modelo para DeepSeek.")
    if not base_url:
        raise ValueError("DEEPSEEK_BASE_URL está vacía: define la URL de la API de DeepSeek.")
    # Solo se pasa si se pide: el proveedor de texto conserva su configuración de siempre.
    extra: dict[str, Any] = {"stream_usage": True} if stream_usage else {}
    if for_tools:
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
    if max_retries is not None:
        extra["max_retries"] = max_retries
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        request_timeout=settings.llm_timeout_seconds if timeout_s is None else timeout_s,
        **extra,
    )


class DeepSeekProvider(_LangChainChat):
    """Generación de texto con DeepSeek a través de ``ChatOpenAI``.

    Construir el adaptador no comprueba que la API esté accesible ni que el
    modelo exista: eso se descubre en la primera llamada.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(build_deepseek_chat(settings))
