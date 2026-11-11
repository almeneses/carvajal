"""Base común de los adaptadores de LLM sobre objetos de chat de LangChain."""

from collections.abc import Iterator, Sequence
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


class ChatLike(Protocol):
    """Contrato mínimo del chat inyectado: ``invoke`` y ``stream`` sobre mensajes."""

    def invoke(self, input: Sequence[BaseMessage]) -> AIMessage:
        """Devuelve la respuesta completa a los mensajes."""

        ...

    def stream(self, input: Sequence[BaseMessage]) -> Iterator[AIMessage]:
        """Emite la respuesta por fragmentos."""

        ...


class _LangChainChat:
    """Implementa ``LLMProvider`` sobre un chat de LangChain ya construido.

    El chat se inyecta para que las pruebas usen un doble sin red y para que cada
    proveedor solo decida cómo se construye su modelo.
    """

    def __init__(self, chat: ChatLike) -> None:
        self._chat = chat

    def complete(self, *, system: str, prompt: str) -> str:
        """Devuelve la respuesta completa del modelo como texto."""

        response = self._chat.invoke(self._messages(system, prompt))
        return str(response.content)

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        """Emite los fragmentos del modelo en el orden en que llegan."""

        for chunk in self._chat.stream(self._messages(system, prompt)):
            yield str(chunk.content)

    @staticmethod
    def _messages(system: str, prompt: str) -> list[BaseMessage]:
        """Compone el par de mensajes que recibe el modelo."""

        return [SystemMessage(content=system), HumanMessage(content=prompt)]
