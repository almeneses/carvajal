"""Adaptador entre el grafo LangGraph y el servicio de chat.

El servicio no conoce LangGraph: recibe un ``AgentRunner`` con dos operaciones
—preparar el turno y registrar el resultado—. Las pruebas inyectan un doble y
la app real usa el grafo con PostgresSaver/Store y Qdrant.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import structlog

from carvajal.agent.state import Turn
from carvajal.application.ask import Source
from carvajal.application.ports import LLMProvider

__all__ = ["AgentRunner", "AgentTurn", "GraphAgentRunner", "default_agent_runner"]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AgentTurn:
    """Resultado de preparar un turno: ruta, prompt y citas."""

    route: str
    intent: str
    prompt: str | None
    system: str
    sources: tuple[Source, ...]
    turns: tuple[Turn, ...]
    preferences: tuple[str, ...] = ()
    tool: str = ""
    reason: str = ""
    query: str = ""


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

    def record(
        self,
        thread_id: str,
        *,
        question: str,
        answer: str,
        incomplete: bool,
        turns: Sequence[Turn],
    ) -> None:
        """Persiste el turno en el checkpointer del hilo."""

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
        turns = tuple(state.get("turns", []))
        return AgentTurn(
            route=str(state.get("route", "direct")),
            intent=str(state.get("intent", "")),
            prompt=state.get("prompt"),
            system=str(state.get("system", "")),
            sources=sources,
            turns=turns,
            preferences=tuple(state.get("preferences", [])),
            tool=str(state.get("tool_name", "")),
            reason=str(state.get("reason", "")),
            query=str(state.get("query", "")),
        )

    def record(
        self,
        thread_id: str,
        *,
        question: str,
        answer: str,
        incomplete: bool,
        turns: Sequence[Turn],
    ) -> None:
        """Añade el turno al checkpoint; un fallo aquí no rompe la respuesta."""

        updated = [*turns]
        if not (
            updated and updated[-1]["question"] == question and updated[-1]["answer"] == answer
        ):
            updated.append({"question": question, "answer": answer, "incomplete": incomplete})
        try:
            self._graph.update_state(
                {"configurable": {"thread_id": thread_id}},
                {"turns": updated, "answer": answer, "incomplete": incomplete},
            )
        except Exception as error:  # el checkpointer no debe tumbar la UI
            logger.warning("agent.checkpoint_failed", error=str(error), thread_id=thread_id)


def default_agent_runner() -> GraphAgentRunner:
    """Runner real; importa el runtime de forma diferida para no abrir conexiones."""

    from apps.api.agent_runtime import agent_runtime
    from carvajal.infrastructure.settings import Settings

    runtime = agent_runtime()
    return GraphAgentRunner(runtime.graph, Settings())


# ``LLMProvider`` se reexporta en el contrato para documentar que la generación
# sigue fuera del grafo y la consume el servicio.
_ = LLMProvider
