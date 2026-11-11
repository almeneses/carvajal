"""Pruebas de los adaptadores de LLM con un chat doble, sin red."""

from collections.abc import Iterator, Sequence

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from carvajal.adapters.llm import DeepSeekProvider, OllamaProvider, build_llm_provider
from carvajal.adapters.llm._chat import _LangChainChat
from carvajal.application.ports import LLMProvider
from carvajal.infrastructure.settings import Settings


class FakeChat:
    """Chat de prueba: registra los mensajes recibidos y responde un guion fijo."""

    def __init__(self, chunks: Sequence[str] = ("Car", "vajal")) -> None:
        self._chunks = list(chunks)
        self.calls: list[list[BaseMessage]] = []

    def invoke(self, input: Sequence[BaseMessage]) -> AIMessage:
        self.calls.append(list(input))
        return AIMessage(content="".join(self._chunks))

    def stream(self, input: Sequence[BaseMessage]) -> Iterator[AIMessage]:
        self.calls.append(list(input))
        for chunk in self._chunks:
            yield AIMessage(content=chunk)


def test_complete_devuelve_texto_y_envia_sistema_y_prompt() -> None:
    # Arrange
    chat = FakeChat(("Carvajal es un grupo empresarial.",))
    provider = _LangChainChat(chat)

    # Act
    answer = provider.complete(system="Responde en español.", prompt="¿Qué es Carvajal?")

    # Assert
    assert answer == "Carvajal es un grupo empresarial."
    assert len(chat.calls) == 1
    assert [message.type for message in chat.calls[0]] == ["system", "human"]
    assert [message.content for message in chat.calls[0]] == [
        "Responde en español.",
        "¿Qué es Carvajal?",
    ]


def test_stream_emite_los_fragmentos_en_orden() -> None:
    # Arrange
    chat = FakeChat(("Car", "vajal", " 1958"))
    provider = _LangChainChat(chat)

    # Act
    fragments = list(provider.stream(system="Responde en español.", prompt="Historia"))

    # Assert
    assert fragments == ["Car", "vajal", " 1958"]
    assert "".join(fragments) == "Carvajal 1958"
    assert [message.type for message in chat.calls[0]] == ["system", "human"]


def test_la_base_implementa_el_puerto() -> None:
    # Arrange
    provider = _LangChainChat(FakeChat())

    # Act / Assert
    assert isinstance(provider, LLMProvider)


def test_settings_trae_el_modelo_de_ollama_por_defecto() -> None:
    # Arrange / Act
    settings = Settings(_env_file=None)

    # Assert
    assert settings.ollama_model == "llama3.2"


def test_fabrica_elige_deepseek() -> None:
    # Arrange
    settings = Settings(_env_file=None, llm_provider="deepseek", deepseek_api_key="clave-de-prueba")

    # Act
    provider = build_llm_provider(settings)

    # Assert
    assert isinstance(provider, DeepSeekProvider)
    assert isinstance(provider, LLMProvider)


def test_fabrica_elige_ollama() -> None:
    # Arrange
    settings = Settings(_env_file=None, llm_provider="ollama")

    # Act
    provider = build_llm_provider(settings)

    # Assert
    assert isinstance(provider, OllamaProvider)
    assert isinstance(provider, LLMProvider)


def test_fabrica_rechaza_un_proveedor_desconocido() -> None:
    # Arrange
    settings = Settings(_env_file=None, llm_provider="claude")

    # Act / Assert
    with pytest.raises(ValueError, match="claude"):
        build_llm_provider(settings)


def test_deepseek_sin_clave_falla_al_construir() -> None:
    # Arrange
    settings = Settings(_env_file=None, llm_provider="deepseek", deepseek_api_key="")

    # Act / Assert
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        build_llm_provider(settings)
