"""Adaptador entre el grafo LangGraph y el servicio de chat.

El servicio no conoce LangGraph: recibe un ``AgentRunner`` con una sola
operación —preparar el turno—. El historial visible lo persiste PostgreSQL y el
servicio lo siembra en cada pregunta; el checkpointer solo conserva estado
técnico de la ejecución (spec 046). Las pruebas inyectan un doble y la app real
usa el grafo con PostgresSaver/Store y Qdrant.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from carvajal.agent.state import Turn
from carvajal.application.ask import Source
from carvajal.application.ports import LLMProvider

__all__ = ["AgentRunner", "AgentTurn", "GraphAgentRunner", "default_agent_runner"]


@dataclass(frozen=True)
class AgentTurn:
    """Resultado de preparar un turno: ruta, prompt y citas."""

    route: str
    intent: str
    prompt: str | None
    system: str
    sources: tuple[Source, ...]
    preferences: tuple[str, ...] = ()
    tool: str = ""
    tool_period: str = ""
    reason: str = ""
    query: str = ""
    # Resultado estructurado de la ruta comercial (spec 036): opciones, canal y contactos.
    products: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentRunner(Protocol):
    """Contrato que consume ``ChatService``."""

    def prepare(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
    ) -> AgentTurn:
        """Decide la ruta y prepara el prompt sin generar texto."""

        ...


def _source(payload: dict[str, Any]) -> Source:
    """Reconstruye una cita desde el estado serializado del grafo."""

    return Source(
        chunk_id=str(payload.get("chunk_id", "")),
        slug=str(payload.get("slug", "")),
        section=str(payload.get("section", "")),
        layer=str(payload.get("layer", "")),
        score=float(payload.get("score", 0.0)),
        excerpt=str(payload.get("excerpt", "")),
        url=str(payload.get("url", "")),
        title=str(payload.get("title", "")),
        source_type=str(payload.get("source_type", "oficial")),
        origin=str(payload.get("origin", "")),
        category=str(payload.get("category", "")),
        entity=str(payload.get("entity", "")),
    )


class GraphAgentRunner:
    """Runner real sobre el grafo compilado."""

    def __init__(self, graph: Any, settings: Any) -> None:
        self._graph = graph
        self._settings = settings

    def prepare(
        self,
        owner_key: str,
        thread_id: str,
        question: str,
        seed_turns: Sequence[Turn],
    ) -> AgentTurn:
        """Invoca el grafo con el hilo como ``thread_id``."""

        state = self._graph.invoke(
            {
                "question": question,
                "owner_key": owner_key,
                "thread_id": thread_id,
                "seed_turns": list(seed_turns),
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        sources = tuple(_source(payload) for payload in state.get("sources", []))
        route = str(state.get("route", "direct"))
        return AgentTurn(
            route=route,
            intent=str(state.get("intent", "")),
            prompt=state.get("prompt"),
            system=str(state.get("system", "")),
            sources=sources,
            preferences=tuple(state.get("preferences", [])),
            tool=str(state.get("tool_name", "")),
            tool_period=str(state.get("tool_period", "")),
            reason=str(state.get("reason", "")),
            query=str(state.get("query", "")),
            products=dict(state.get("tool_result") or {}) if route == "commercial" else {},
        )


def default_agent_runner() -> GraphAgentRunner:
    """Runner real; importa el runtime de forma diferida para no abrir conexiones."""

    from apps.api.agent_runtime import agent_runtime
    from carvajal.infrastructure.settings import Settings

    runtime = agent_runtime()
    return GraphAgentRunner(runtime.graph, Settings())


# ``LLMProvider`` se reexporta en el contrato para documentar que la generación
# sigue fuera del grafo y la consume el servicio.
_ = LLMProvider
