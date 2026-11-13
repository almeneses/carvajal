"""Base común de los adaptadores de LLM sobre objetos de chat de LangChain."""

from collections.abc import Iterator, Sequence
from typing import Protocol

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from carvajal.application.errors import LLMError

logger = structlog.get_logger(__name__)


class ChatLike(Protocol):
    """Contrato mínimo del chat inyectado: ``invoke`` y ``stream`` sobre mensajes.

    Solo se invoca con el mensaje como argumento posicional: otros *kwargs* del
    cliente (``config``, ``stop``…) no forman parte de este contrato.
    """

    def invoke(self, input: Sequence[BaseMessage]) -> BaseMessage:
        """Devuelve la respuesta completa a los mensajes."""

        ...

    def stream(self, input: Sequence[BaseMessage]) -> Iterator[BaseMessage]:
        """Emite la respuesta por fragmentos."""

        ...


def _text(message: BaseMessage) -> str:
    """Devuelve el contenido textual; falla si el modelo devuelve bloques."""

    content = message.content
    if not isinstance(content, str):
        raise ValueError(
            "El modelo devolvió contenido no textual; este adaptador solo admite texto."
        )
    return content


def _log_usage(chat: ChatLike, message: BaseMessage) -> None:
    """Registra el consumo de tokens si el proveedor publica su uso.

    La línea sale por structlog, que ya lleva ``request_id`` y ``owner_key`` del
    contexto de la petición: esa es la métrica de tokens por visitante.
    """

    uso = getattr(message, "usage_metadata", None)
    if not uso:
        return
    modelo = getattr(chat, "model_name", None) or getattr(chat, "model", None)
    logger.info(
        "llm.completed",
        model=modelo,
        tokens_in=uso.get("input_tokens"),
        tokens_out=uso.get("output_tokens"),
    )


class _LangChainChat:
    """Implementa ``LLMProvider`` sobre un chat de LangChain ya construido.

    El chat se inyecta para que las pruebas usen un doble sin red y para que cada
    proveedor solo decida cómo se construye su modelo.
    """

    def __init__(self, chat: ChatLike) -> None:
        self._chat = chat

    def complete(self, *, system: str, prompt: str) -> str:
        """Devuelve la respuesta completa del modelo como texto.

        Raises:
            LLMError: si el proveedor falla o devuelve contenido no textual.
        """

        try:
            respuesta = self._chat.invoke(self._messages(system, prompt))
            texto = _text(respuesta)
        except LLMError:
            raise
        except Exception as error:
            raise LLMError("El proveedor de LLM no devolvió una respuesta utilizable.") from error
        _log_usage(self._chat, respuesta)
        return texto

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        """Emite los fragmentos del modelo en el orden en que llegan.

        Los fragmentos vacíos se omiten para que el consumidor no reciba ruido.

        Raises:
            LLMError: si el proveedor falla o devuelve contenido no textual.
        """

        ultimo: BaseMessage | None = None
        try:
            for chunk in self._chat.stream(self._messages(system, prompt)):
                ultimo = chunk
                fragment = _text(chunk)
                if fragment:
                    yield fragment
        except LLMError:
            raise
        except Exception as error:
            raise LLMError("El proveedor de LLM no devolvió una respuesta utilizable.") from error
        if ultimo is not None:
            _log_usage(self._chat, ultimo)

    @staticmethod
    def _messages(system: str, prompt: str) -> list[BaseMessage]:
        """Compone el par de mensajes que recibe el modelo."""

        return [SystemMessage(content=system), HumanMessage(content=prompt)]
