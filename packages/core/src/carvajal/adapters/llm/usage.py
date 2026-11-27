"""Uso de DeepSeek por proceso para la página de métricas (spec 055).

:class:`DeepSeekUsageCallback` es un *callback* de LangChain que se adjunta al ``ChatOpenAI``
de DeepSeek (:func:`carvajal.adapters.llm.deepseek.build_deepseek_chat`): cuenta cada llamada
al modelo con su estado, su latencia y los tokens de entrada y salida que informa el
proveedor (``usage_metadata``). No lee ni guarda mensajes. Un fallo al contar nunca rompe el
turno: LangChain registra el error del callback y sigue.
"""

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Final
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from carvajal.infrastructure.metrics import DEEPSEEK_CALLS, CallStats

__all__ = ["DEEPSEEK_USAGE", "MAX_PENDING", "DeepSeekUsageCallback", "usage_of"]

# Llamadas en curso que se recuerdan a la vez (inicio y modelo) para medir su latencia.
MAX_PENDING: Final = 512


def usage_of(response: LLMResult) -> tuple[int, int]:
    """Tokens de entrada y salida de una respuesta (``usage_metadata`` o ``token_usage``)."""

    prompt = completion = 0
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "usage_metadata", None)
            if isinstance(metadata, Mapping):
                prompt += int(metadata.get("input_tokens") or 0)
                completion += int(metadata.get("output_tokens") or 0)
    if prompt or completion:
        return prompt, completion
    usage = (response.llm_output or {}).get("token_usage") or {}
    if isinstance(usage, Mapping):
        return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    return 0, 0


class DeepSeekUsageCallback(BaseCallbackHandler):
    """Cuenta las llamadas al modelo en ``stats`` (por defecto, las del proceso)."""

    raise_error = False

    def __init__(
        self, stats: CallStats = DEEPSEEK_CALLS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._stats = stats
        self._clock = clock
        self._lock = threading.Lock()
        self._started: dict[UUID, tuple[float, str]] = {}

    def _start(self, run_id: UUID, serialized: Mapping[str, Any] | None, kwargs: Any) -> None:
        params = kwargs.get("invocation_params") or {}
        model = str(params.get("model") or params.get("model_name") or "")
        if not model and isinstance(serialized, Mapping):
            model = str((serialized.get("kwargs") or {}).get("model_name") or "")
        with self._lock:
            # Sin fin (p. ej. una llamada abandonada por el plazo del turno) no crece sin límite:
            # se descartan las más antiguas (el dict guarda el orden de inicio), no las que
            # siguen en curso.
            while len(self._started) >= MAX_PENDING:
                self._started.pop(next(iter(self._started)))
            self._started[run_id] = (self._clock(), model)

    def _finish(self, run_id: UUID) -> tuple[float | None, str]:
        with self._lock:
            started = self._started.pop(run_id, None)
        if started is None:
            return None, ""
        return max(0.0, (self._clock() - started[0]) * 1000), started[1]

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._start(run_id, serialized, kwargs)

    def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._start(run_id, serialized, kwargs)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        latency, model = self._finish(run_id)
        prompt, completion = usage_of(response)
        self._stats.record(
            kind=model,
            status="ok",
            latency_ms=latency,
            input_tokens=prompt,
            output_tokens=completion,
            # En streaming sin ``stream_usage`` (p. ej. ``DeepSeekProvider``) no llega el uso:
            # /metricas lo dice en vez de presentar un costo completo.
            usage_known=bool(prompt or completion),
        )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        latency, model = self._finish(run_id)
        status = "timeout" if "timeout" in type(error).__name__.lower() else "error"
        self._stats.record(kind=model, status=status, latency_ms=latency)


# Un solo callback por proceso: lo comparten todos los clientes de DeepSeek.
DEEPSEEK_USAGE = DeepSeekUsageCallback()
