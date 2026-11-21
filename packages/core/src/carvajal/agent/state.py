"""Estado del agente del M2 y contrato de decisión del router."""

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

__all__ = ["AgentState", "RouteDecision", "RouteName", "Turn"]

RouteName = Literal["direct", "rag", "tool", "commercial"]


class Turn(TypedDict):
    """Turno ya respondido; PostgreSQL lo persiste y el servicio lo siembra."""

    question: str
    answer: str
    incomplete: bool


class AgentState(TypedDict, total=False):
    """Canales del turno; el checkpointer conserva el estado técnico de la ejecución."""

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
    commercial: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class RouteDecision:
    """Decisión explícita y auditable del router.

    ``query`` es la consulta de recuperación (puede incluir el turno anterior en
    un seguimiento elíptico). ``tool_name``/``tool_period`` solo existen en la
    ruta ``tool``. ``preference`` se guarda en el Store cuando el usuario declara
    una preferencia. ``extra`` lleva la solicitud comercial, con las claves de
    ``CommercialRequest``, en la ruta ``commercial`` y en su aclaración.
    """

    route: RouteName
    intent: str
    query: str
    tool_name: str = ""
    tool_period: str = ""
    preference: str = ""
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
