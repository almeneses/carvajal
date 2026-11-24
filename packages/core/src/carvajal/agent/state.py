"""Estado del agente del M2 y contrato de decisión del router."""

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

__all__ = ["AgentState", "RouteDecision", "RouteName", "Turn"]

RouteName = Literal["direct", "rag", "tool", "commercial"]


class Turn(TypedDict):
    """Turno ya respondido, conservado por el checkpointer."""

    question: str
    answer: str
    incomplete: bool


class AgentState(TypedDict, total=False):
    """Canales que persiste el checkpointer entre turnos de un mismo hilo.

    Spec 045: ``conversation_summary`` es el mismo canal del grafo ``tools`` (resumen
    acumulado de los turnos fuera de la ventana), declarado aquí para que el respaldo
    ``routes`` lo lea y lo adopte; ``turn_count`` es una entrada del turno (turnos
    persistidos del hilo antes del actual).
    """

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
    commercial_context: dict[str, Any]
    reason: str
    answer: str
    incomplete: bool
    conversation_summary: dict[str, Any]
    turn_count: int


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
