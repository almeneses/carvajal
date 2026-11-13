"""Base común de los adaptadores de LLM sobre objetos de chat de LangChain."""

from collections.abc import Iterator, Sequence
from typing import Any, Protocol

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


def _usage(message: BaseMessage) -> dict[str, int]:
    """Extrae el uso publicado por el proveedor, si lo hay."""

    uso: Any = getattr(message, "usage_metadata", None)
    if not isinstance(uso, dict):
        return {}
    return {
        clave: valor
        for clave in ("input_tokens", "output_tokens", "total_tokens")
        if isinstance(valor := uso.get(clave), int)
    }


def _merge_usage(acumulado: dict[str, int], message: BaseMessage) -> None:
    """Acumula el uso de varios chunks sin perder los campos ya vistos.

    Algunos proveedores publican ``usage_metadata`` solo en el primer chunk, en
    uno intermedio o repartido; quedarse con el último perdería el resto.
    """

    acumulado.update(_usage(message))


def _log_usage(chat: ChatLike, uso: dict[str, int]) -> None:
    """Registra el consumo de tokens si el proveedor publica su uso.

    La línea sale por structlog, que ya lleva ``request_id`` y ``owner_key`` del
    contexto de la petición: esa es la métrica de tokens por visitante.
    """

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
        _log_usage(self._chat, _usage(respuesta))
        return texto

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        """Emite los fragmentos del modelo en el orden en que llegan.

        Los fragmentos vacíos se omiten para que el consumidor no reciba ruido.

        Raises:
            LLMError: si el proveedor falla o devuelve contenido no textual.
        """

        acumulado: dict[str, int] = {}
        try:
            for chunk in self._chat.stream(self._messages(system, prompt)):
                _merge_usage(acumulado, chunk)
                fragment = _text(chunk)
                if fragment:
                    yield fragment
        except LLMError:
            raise
        except Exception as error:
            raise LLMError("El proveedor de LLM no devolvió una respuesta utilizable.") from error
        finally:
            # También se registra si el cliente corta el stream: el consumo
            # acumulado hasta ese punto no debe perderse.
            _log_usage(self._chat, acumulado)

    @staticmethod
    def _messages(system: str, prompt: str) -> list[BaseMessage]:
        """Compone el par de mensajes que recibe el modelo."""

        return [SystemMessage(content=system), HumanMessage(content=prompt)]
