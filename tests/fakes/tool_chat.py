"""Doble de chat con tool calling guionizado para probar grafos sin red (research R10).

``GenericFakeChatModel`` de LangChain no soporta ``bind_tools``; este doble sí. Cada
invocación consume el siguiente paso del guion y responde con su texto y sus llamadas a
herramientas. En streaming emite primero el texto en trozos (el «pensamiento» previo a
las llamadas, o la respuesta final) y después los ``tool_call_chunks``, de modo que el
mensaje agregado tenga ``tool_calls`` correctos. Al subclasear ``BaseChatModel``, dentro
de un nodo de LangGraph los trozos llegan a ``graph.stream(stream_mode=["messages"])``.
"""

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, field_validator


class ScriptedToolChatError(AssertionError):
    """El guion no prevé la llamada recibida (por ejemplo, se agotó)."""


@dataclass(frozen=True)
class ScriptStep:
    """Un paso del guion: lo que el modelo responde en una invocación.

    - ``text``: contenido del mensaje. Con ``tool_calls`` se emite antes de las llamadas
      (sirve para probar el descarte de «pensamientos»); sin ellas, es la respuesta final.
    - ``tool_calls``: lista de ``{"name", "args", "id"}``; el ``id`` es opcional.
    - ``usage_metadata``: tokens del paso, como lo reportaría el proveedor.
    - ``error``: si se indica, la invocación lanza esta excepción y no responde.
    """

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage_metadata: dict[str, int] | None = None
    error: BaseException | None = None


class ScriptedToolChat(BaseChatModel):
    """Chat determinista que recorre un guion de ``ScriptStep`` y registra lo recibido."""

    steps: list[ScriptStep] = Field(default_factory=list)
    chunk_size: int = 8
    """Caracteres por trozo de texto en streaming."""
    position: int = 0
    """Índice del próximo paso a consumir."""
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    """Mensajes recibidos en cada invocación, en orden."""
    call_kwargs: list[dict[str, Any]] = Field(default_factory=list)
    """Kwargs recibidos en cada invocación (``tools``, ``tool_choice``…); no incluye ``stop``."""
    bindings: list[dict[str, Any]] = Field(default_factory=list)
    """Cada ``bind_tools``: nombres de herramientas y kwargs extra (p. ej. ``tool_choice``)."""

    @field_validator("steps", mode="before")
    @classmethod
    def _coerce_steps(cls, value: Sequence[ScriptStep | Mapping[str, Any]]) -> list[ScriptStep]:
        """Acepta pasos como ``ScriptStep`` o como diccionarios con los mismos campos."""

        return [s if isinstance(s, ScriptStep) else ScriptStep(**s) for s in value]

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-chat"

    @property
    def remaining_steps(self) -> int:
        """Pasos del guion aún sin consumir."""

        return len(self.steps) - self.position

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """Registra las herramientas y los kwargs, y los ata como haría un proveedor real."""

        formatted = [convert_to_openai_tool(t) for t in tools]
        self.bindings.append(
            {"tools": [t["function"]["name"] for t in formatted], "kwargs": dict(kwargs)}
        )
        return self.bind(tools=formatted, **kwargs)

    def _next_step(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> ScriptStep:
        """Registra la invocación, consume el paso siguiente y lanza su error si lo tiene."""

        self.calls.append(list(messages))
        self.call_kwargs.append(dict(kwargs))
        if self.position >= len(self.steps):
            raise ScriptedToolChatError(
                f"guion agotado: invocación {len(self.calls)} con {len(self.steps)} pasos"
            )
        step = self.steps[self.position]
        self.position += 1
        if step.error is not None:
            raise step.error
        return step

    def _call_id(self, call: Mapping[str, Any], index: int) -> str:
        # El id generado incluye el paso para que dos pasos del guion no repitan
        # ``tool_call_id``, como ocurre con un proveedor real.
        return call.get("id") or f"call_{self.position}_{index}"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Responde el paso completo en un solo ``AIMessage``."""

        step = self._next_step(messages, kwargs)
        message = AIMessage(
            content=step.text,
            tool_calls=[
                {"name": c["name"], "args": dict(c.get("args", {})), "id": self._call_id(c, i)}
                for i, c in enumerate(step.tool_calls)
            ],
            usage_metadata=step.usage_metadata,  # type: ignore[arg-type]
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Emite el texto en trozos, luego cada llamada en dos trozos y el uso al final."""

        step = self._next_step(messages, kwargs)
        size = max(1, self.chunk_size)
        for start in range(0, len(step.text), size):
            piece = step.text[start : start + size]
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if run_manager is not None:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk
        for index, call in enumerate(step.tool_calls):
            # Los argumentos se parten en dos trozos para ejercitar la agregación.
            args = json.dumps(call.get("args", {}), ensure_ascii=False)
            middle = len(args) // 2
            parts = [
                {"name": call["name"], "args": args[:middle], "id": self._call_id(call, index)},
                {"name": None, "args": args[middle:], "id": None},
            ]
            for part in parts:
                chunk = ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[{**part, "index": index, "type": "tool_call_chunk"}],
                    )
                )
                if run_manager is not None:
                    run_manager.on_llm_new_token("", chunk=chunk)
                yield chunk
        last = ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                usage_metadata=step.usage_metadata,  # type: ignore[arg-type]
                chunk_position="last",
            )
        )
        if run_manager is not None:
            run_manager.on_llm_new_token("", chunk=last)
        yield last
