"""Pruebas de los adaptadores de LLM con un chat doble, sin red."""

from collections.abc import Iterator, Sequence
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from ollama import ResponseError

from carvajal.adapters.llm import DeepSeekProvider, OllamaProvider, build_llm_provider
from carvajal.adapters.llm._chat import _LangChainChat
from carvajal.application.errors import LLMError
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


class BlockChat:
    """Chat que devuelve bloques estructurados en lugar de texto."""

    def invoke(self, input: Sequence[BaseMessage]) -> AIMessage:
        return AIMessage(content=[{"type": "text", "text": "hola"}])

    def stream(self, input: Sequence[BaseMessage]) -> Iterator[AIMessage]:
        yield AIMessage(content=[{"type": "text", "text": "hola"}])


class FailingChat:
    """Chat que simula un fallo del proveedor (red caída, modelo ausente...)."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def invoke(self, input: Sequence[BaseMessage]) -> AIMessage:
        raise self._error

    def stream(self, input: Sequence[BaseMessage]) -> Iterator[AIMessage]:
        raise self._error


def _limpia_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deja fuera del entorno las variables del LLM para no depender del host."""

    for name in ("LLM_PROVIDER", "DEEPSEEK_API_KEY", "OLLAMA_MODEL"):
        monkeypatch.delenv(name, raising=False)


class ChatSpy:
    """Cliente espía: registra los *kwargs* de construcción y responde texto fijo."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def invoke(self, input: Sequence[BaseMessage]) -> AIMessage:
        return AIMessage(content="respuesta")

    def stream(self, input: Sequence[BaseMessage]) -> Iterator[AIMessage]:
        yield AIMessage(content="respuesta")


def _espia(creados: list[ChatSpy]) -> Any:
    """Devuelve una fábrica de ``ChatSpy`` que acumula los clientes construidos."""

    def fabrica(**kwargs: Any) -> ChatSpy:
        creados.append(ChatSpy(**kwargs))
        return creados[-1]

    return fabrica


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


def test_stream_omite_los_fragmentos_vacios() -> None:
    # Arrange
    chat = FakeChat(("", "Car", "", "vajal", ""))
    provider = _LangChainChat(chat)

    # Act
    fragments = list(provider.stream(system="Responde en español.", prompt="Historia"))

    # Assert
    assert fragments == ["Car", "vajal"]


def test_complete_rechaza_el_contenido_no_textual() -> None:
    # Arrange
    provider = _LangChainChat(BlockChat())

    # Act / Assert
    with pytest.raises(LLMError) as captured:
        provider.complete(system="Responde en español.", prompt="¿Qué es Carvajal?")
    assert "contenido no textual" in str(captured.value.__cause__)


def test_stream_rechaza_el_contenido_no_textual() -> None:
    # Arrange
    provider = _LangChainChat(BlockChat())

    # Act / Assert
    with pytest.raises(LLMError) as captured:
        list(provider.stream(system="Responde en español.", prompt="¿Qué es Carvajal?"))
    assert "contenido no textual" in str(captured.value.__cause__)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("Ollama caído"),
        ResponseError("model 'llama3.2' not found", 404),
        RuntimeError("fallo inesperado del SDK"),
    ],
)
def test_complete_traduce_los_fallos_del_proveedor(error: Exception) -> None:
    # Arrange
    provider = _LangChainChat(FailingChat(error))

    # Act / Assert
    with pytest.raises(LLMError) as captured:
        provider.complete(system="Responde en español.", prompt="¿Qué es Carvajal?")
    assert captured.value.__cause__ is error


def test_stream_traduce_los_fallos_del_proveedor() -> None:
    # Arrange
    failure = httpx.ConnectError("Ollama caído")
    provider = _LangChainChat(FailingChat(failure))

    # Act / Assert
    with pytest.raises(LLMError) as captured:
        list(provider.stream(system="Responde en español.", prompt="¿Qué es Carvajal?"))
    assert captured.value.__cause__ is failure


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


def test_fabrica_sin_argumentos_lee_el_entorno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    _limpia_entorno(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    # Act
    provider = build_llm_provider()

    # Assert
    assert isinstance(provider, OllamaProvider)


def test_fabrica_sin_argumentos_usa_deepseek_con_clave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    _limpia_entorno(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")

    # Act
    provider = build_llm_provider()

    # Assert
    assert isinstance(provider, DeepSeekProvider)


def test_fabrica_normaliza_el_nombre_del_proveedor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    _limpia_entorno(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "DeepSeek ")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")

    # Act
    provider = build_llm_provider()

    # Assert
    assert isinstance(provider, DeepSeekProvider)


def test_fabrica_rechaza_un_proveedor_desconocido(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _limpia_entorno(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    # Act / Assert
    with pytest.raises(ValueError, match="no soportado"):
        build_llm_provider()


def test_fabrica_remite_claude_a_streamlit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _limpia_entorno(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "claude")

    # Act / Assert
    with pytest.raises(ValueError, match="Streamlit"):
        build_llm_provider()


def test_deepseek_configura_el_cliente(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    creados: list[ChatSpy] = []
    monkeypatch.setattr("carvajal.adapters.llm.deepseek.ChatOpenAI", _espia(creados))
    settings = Settings(
        _env_file=None, llm_provider="deepseek", deepseek_api_key="  clave-de-prueba  "
    )

    # Act
    provider = DeepSeekProvider(settings)
    answer = provider.complete(system="sistema", prompt="pregunta")

    # Assert
    assert creados[0].kwargs == {
        "model": settings.deepseek_model,
        "api_key": "clave-de-prueba",
        "base_url": settings.deepseek_base_url,
        "temperature": 0,
    }
    assert answer == "respuesta"


def test_ollama_configura_el_cliente(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    creados: list[ChatSpy] = []
    monkeypatch.setattr("carvajal.adapters.llm.ollama.ChatOllama", _espia(creados))
    settings = Settings(_env_file=None, llm_provider="ollama", ollama_model="llama3.2")

    # Act
    provider = OllamaProvider(settings)
    fragments = list(provider.stream(system="sistema", prompt="pregunta"))

    # Assert
    assert creados[0].kwargs == {
        "model": "llama3.2",
        "temperature": 0,
        "num_ctx": 32768,
    }
    assert fragments == ["respuesta"]


def test_deepseek_con_clave_en_blanco_falla() -> None:
    # Arrange
    settings = Settings(_env_file=None, llm_provider="deepseek", deepseek_api_key="   ")

    # Act / Assert
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        build_llm_provider(settings)


def test_deepseek_con_modelo_vacio_falla() -> None:
    # Arrange
    settings = Settings(
        _env_file=None, llm_provider="deepseek", deepseek_api_key="test", deepseek_model="  "
    )

    # Act / Assert
    with pytest.raises(ValueError, match="DEEPSEEK_MODEL"):
        build_llm_provider(settings)


def test_deepseek_con_base_url_vacia_falla() -> None:
    # Arrange
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        deepseek_api_key="test",
        deepseek_base_url="  ",
    )

    # Act / Assert
    with pytest.raises(ValueError, match="DEEPSEEK_BASE_URL"):
        build_llm_provider(settings)


def test_ollama_con_modelo_vacio_falla() -> None:
    # Arrange
    settings = Settings(_env_file=None, llm_provider="ollama", ollama_model="  ")

    # Act / Assert
    with pytest.raises(ValueError, match="OLLAMA_MODEL"):
        build_llm_provider(settings)


def test_deepseek_sin_clave_falla_al_construir() -> None:
    # Arrange
    settings = Settings(_env_file=None, llm_provider="deepseek", deepseek_api_key="")

    # Act / Assert
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY") as error:
        build_llm_provider(settings)
    assert "LLM_PROVIDER" not in str(error.value)
