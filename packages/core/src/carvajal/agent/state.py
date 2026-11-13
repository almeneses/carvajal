"""Estado del agente del M2 y contrato de decisión del router."""

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

__all__ = ["AgentState", "RouteDecision", "RouteName", "Turn"]

RouteName = Literal["direct", "rag", "tool"]


class Turn(TypedDict):
    """Turno ya respondido, conservado por el checkpointer."""

    question: str
    answer: str
    incomplete: bool


class AgentState(TypedDict, total=False):
    """Canales que persiste el checkpointer entre turnos de un mismo hilo."""

    question: str
    owner_key: str
    thread_id: str
    turns: list[Turn]
    seed_turns: list[Turn]
    preferences: list[str]
    route: RouteName
    intent: str
    query: str
    prompt: str | None
    system: str
    sources: list[dict[str, Any]]
    tool_result: dict[str, Any]
    tool_name: str
    tool_period: str
    reason: str
    answer: str
    incomplete: bool


@dataclass(frozen=True)
class RouteDecision:
    """Decisión explícita y auditable del router.

    ``query`` es la consulta de recuperación (puede incluir el turno anterior en
    un seguimiento elíptico). ``tool_name``/``tool_period`` solo existen en la
    ruta ``tool``. ``preference`` se guarda en el Store cuando el usuario declara
    una preferencia.
    """

    route: RouteName
    intent: str
    query: str
    tool_name: str = ""
    tool_period: str = ""
    preference: str = ""
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
