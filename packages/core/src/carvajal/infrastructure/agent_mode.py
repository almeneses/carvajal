"""Modo del agente que permite la configuración (spec 044, FR-015).

``AGENT_MODE=tools`` es el valor por defecto, pero el modo ``tools`` solo arranca con
``LLM_PROVIDER=deepseek`` (el valor por defecto de ``Settings`` y de los compose) y DeepSeek
completo (clave, modelo y URL base). Con otro proveedor (por ejemplo ``ollama``, elegido a
mano) el chat sigue en ``routes``: una clave de DeepSeek presente para el enrutador,
``eval-agent`` o la revisión automática no debe desviar los turnos a DeepSeek sin que nadie
lo decida.

Sin ``DEEPSEEK_API_KEY`` y con DeepSeek como proveedor, el chat degrada: ``tools`` no arranca
(``missing_deepseek_api_key``) y ``routes`` atiende saludos, indicadores y cotizaciones, pero
las respuestas que necesitan redactar con el LLM fallan con el aviso genérico del servicio.
``chat_llm_blocker`` da ese motivo para que el arranque lo registre una vez.

La política se calcula solo con ``Settings``: no construye clientes ni importa LangChain,
de modo que ``/health`` la publica sin coste y el runtime la aplica al construir los
grafos. El modo efectivo lo confirma el log ``agent.runtime_ready`` en la primera
pregunta.
"""

from dataclasses import dataclass
from typing import Final, Literal

from carvajal.infrastructure.settings import Settings

__all__ = [
    "TOOLS_PROVIDER",
    "AgentModeStatus",
    "ToolsBlocker",
    "agent_mode_status",
    "chat_llm_blocker",
]

# Proveedor que exige el modo ``tools``: el grafo usa ``bind_tools`` sobre DeepSeek.
TOOLS_PROVIDER: Final = "deepseek"

ToolsBlocker = Literal[
    "llm_provider",
    "missing_deepseek_api_key",
    "missing_deepseek_model",
    "missing_deepseek_base_url",
]


@dataclass(frozen=True)
class AgentModeStatus:
    """Modo pedido, modo que atenderá los turnos y, si difieren, el motivo."""

    configured: Literal["routes", "tools"]
    expected: Literal["routes", "tools"]
    reason: ToolsBlocker | None = None

    def as_health(self) -> dict[str, object]:
        """Bloque de ``/health``: sin claves, modelos ni URLs del proveedor."""

        return {
            "mode_configured": self.configured,
            "mode_expected": self.expected,
            "tools_unavailable_reason": self.reason,
        }


def _tools_blocker(settings: Settings) -> ToolsBlocker | None:
    """Primer motivo por el que el modo ``tools`` no puede arrancar, o ``None``."""

    if settings.llm_provider.strip().lower() != TOOLS_PROVIDER:
        return "llm_provider"
    if not settings.deepseek_api_key.strip():
        return "missing_deepseek_api_key"
    if not settings.deepseek_model.strip():
        return "missing_deepseek_model"
    if not settings.deepseek_base_url.strip():
        return "missing_deepseek_base_url"
    return None


def agent_mode_status(settings: Settings) -> AgentModeStatus:
    """Resume qué modo atenderá los turnos según la configuración, sin construir nada."""

    if settings.agent_mode != "tools":
        return AgentModeStatus(configured="routes", expected="routes")
    reason = _tools_blocker(settings)
    if reason is not None:
        return AgentModeStatus(configured="tools", expected="routes", reason=reason)
    return AgentModeStatus(configured="tools", expected="tools")


def chat_llm_blocker(settings: Settings) -> ToolsBlocker | None:
    """Motivo por el que el LLM del chat no puede construirse, o ``None``.

    Solo aplica con ``LLM_PROVIDER=deepseek``: es la misma comprobación que bloquea el modo
    ``tools`` (clave, modelo y URL base), porque ambos usan el mismo cliente. Con otro
    proveedor no hay nada que comprobar sin red y devuelve ``None``.
    """

    if settings.llm_provider.strip().lower() != TOOLS_PROVIDER:
        return None
    return _tools_blocker(settings)
