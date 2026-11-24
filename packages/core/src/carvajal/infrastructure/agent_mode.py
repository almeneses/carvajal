"""Modo del agente que permite la configuración (spec 044, FR-015).

``AGENT_MODE=tools`` es el valor por defecto, pero el modo ``tools`` solo arranca con
``LLM_PROVIDER=deepseek`` y DeepSeek completo (clave, modelo y URL base). Con otro
proveedor (por ejemplo ``ollama``, el de ``Settings`` y de los compose) el chat sigue en
``routes``: una clave de DeepSeek presente para el enrutador, ``eval-agent`` o la revisión
automática no debe desviar los turnos a DeepSeek sin que nadie lo decida.

La política se calcula solo con ``Settings``: no construye clientes ni importa LangChain,
de modo que ``/health`` la publica sin coste y el runtime la aplica al construir los
grafos. El modo efectivo lo confirma el log ``agent.runtime_ready`` en la primera
pregunta.
"""

from dataclasses import dataclass
from typing import Final, Literal

from carvajal.infrastructure.settings import Settings

__all__ = ["TOOLS_PROVIDER", "AgentModeStatus", "ToolsBlocker", "agent_mode_status"]

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
