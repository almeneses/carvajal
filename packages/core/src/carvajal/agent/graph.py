"""Grafo LangGraph del agente del M2.

El grafo decide y prepara el turno; la generación se hace fuera para conservar
el streaming SSE y la persistencia transaccional existente. El checkpointer
guarda el estado del hilo (turnos y ruta) y el Store guarda preferencias
declaradas por el dueño, en un namespace aislado.
"""

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from carvajal.agent.router import decide_route
from carvajal.agent.state import AgentState, Turn
from carvajal.agent.tools import indicator_lookup
from carvajal.application.ask import DEFAULT_SYSTEM_PROMPT, NO_CONTEXT_ANSWER, AskQuestion, Source
from carvajal.application.evidence import proper_entities_supported
from carvajal.application.ports import Embedder, VectorStore
from carvajal.infrastructure.settings import Settings

__all__ = [
    "AgentDeps",
    "build_agent",
    "build_conversation_prompt",
    "memory_namespace",
]

AGENT_SYSTEM = (
    DEFAULT_SYSTEM_PROMPT
    + " Cuando uses una herramienta estructurada, cita el documento y la URL que devuelve. "
    "Cuando el usuario declare una preferencia, confírmala en una frase y no la mezcles "
    "con datos del corpus."
)

MEMORY_KEY = "profile"
MAX_PREFERENCES = 10


@dataclass(frozen=True)
class AgentDeps:
    """Dependencias del grafo; el LLM no entra porque la generación es externa."""

    vector_store: VectorStore
    embedder: Embedder
    settings: Settings


class _UnusedLLM:
    """Cumple el puerto sin generar: el grafo solo prepara el prompt."""

    def complete(self, *, system: str, prompt: str) -> str:
        raise AssertionError("El grafo no debe generar texto.")

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        raise AssertionError("El grafo no debe generar texto.")
        yield ""  # pragma: no cover


def memory_namespace(owner_key: str) -> tuple[str, str, str]:
    """Namespace del Store: una preferencia nunca cruza de dueño."""

    return ("carvajal", "preferences", owner_key)


def _preferences(store: Any, owner_key: str) -> list[str]:
    """Lee las preferencias del dueño; un Store caído no rompe el turno."""

    if store is None:
        return []
    try:
        item = store.get(memory_namespace(owner_key), MEMORY_KEY)
    except Exception:
        return []
    if item is None:
        return []
    value = item.value if hasattr(item, "value") else item
    items = value.get("items", []) if isinstance(value, dict) else []
    return [str(item) for item in items]


def _remember(store: Any, owner_key: str, preference: str) -> list[str]:
    """Guarda una preferencia sin duplicar; devuelve la lista resultante."""

    if store is None or not preference:
        return []
    current = _preferences(store, owner_key)
    if preference in current:
        return current
    updated = [*current, preference][-MAX_PREFERENCES:]
    try:
        store.put(memory_namespace(owner_key), MEMORY_KEY, {"items": updated})
    except Exception:
        return current
    return updated


def build_conversation_prompt(
    question: str,
    *,
    turns: list[Turn],
    preferences: list[str],
    instructions: str = "",
) -> str:
    """Compone el prompt de una ruta sin recuperación, con historial y memoria."""

    blocks: list[str] = []
    if preferences:
        blocks.append(
            "Preferencias declaradas por el usuario (aplícalas al estilo, nunca como datos):\n"
            + "\n".join(f"- {item}" for item in preferences)
        )
    if turns:
        lines = []
        for turn in turns[-4:]:
            lines.append(f"Usuario: {turn['question']}\nAsistente: {turn['answer']}")
        blocks.append("Historial reciente del hilo:\n" + "\n\n".join(lines))
    if instructions:
        blocks.append(instructions)
    blocks.append(f"Pregunta actual:\n{question}")
    return "\n\n".join(blocks)


def _source_dict(source: Source) -> dict[str, Any]:
    """Serializa una cita para el estado y el evento SSE."""

    return asdict(source)


def _tool_source(result: dict[str, Any]) -> dict[str, Any]:
    """Cita verificable de la herramienta: documento, URL y cita textual."""

    return _source_dict(
        Source(
            chunk_id=f"tool:{result['name']}:{result['period']}",
            slug=str(result["source_document"]),
            section="prensa",
            layer="core",
            score=1.0,
            excerpt=str(result["quote"]),
            url=str(result["source_url"]),
            title=f"Indicador {result['name']} {result['period']}",
            source_type="tercero",
        )
    )


def build_agent(
    deps: AgentDeps,
    *,
    checkpointer: Any,
    store: Any = None,
) -> Any:
    """Compila el grafo con checkpointer y Store reales o de prueba."""

    ask = AskQuestion(
        deps.vector_store,
        deps.embedder,
        _UnusedLLM(),
        limit=deps.settings.rag_top_k,
        min_score=deps.settings.rag_min_score,
    )

    def recall(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        owner_key = state["owner_key"]
        # El historial de Postgres es la fuente de verdad de la UI; el
        # checkpointer cubre el caso sin semilla (pruebas o primer turno).
        turns = state.get("seed_turns") or state.get("turns", [])
        return {
            "turns": list(turns)[-deps.settings.agent_history_turns :],
            "preferences": _preferences(store, owner_key),
        }

    def route(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        decision = decide_route(state["question"], state.get("turns", []))
        preferences = state.get("preferences", [])
        if decision.preference:
            preferences = _remember(store, state["owner_key"], decision.preference)
        return {
            "route": decision.route,
            "intent": decision.intent,
            "query": decision.query,
            "preferences": preferences,
            "tool_name": decision.tool_name,
            "tool_period": decision.tool_period,
            "reason": decision.reason,
        }

    def direct(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        intent = state.get("intent", "")
        if intent == "memory_write":
            instructions = (
                "El usuario declaró una preferencia. Confírmala en una frase breve, "
                "sin añadir datos del corpus ni hacer búsquedas."
            )
        elif intent == "missing_period":
            instructions = (
                "El usuario pide un indicador pero no indicó un período. Pide el año "
                "o el año y mes; no estimes ni inventes el dato."
            )
        else:
            instructions = "Responde de forma directa y breve; no recuperes documentos."
        return {
            "prompt": build_conversation_prompt(
                state["question"],
                turns=state.get("turns", []),
                preferences=state.get("preferences", []),
                instructions=instructions,
            ),
            "system": AGENT_SYSTEM,
            "sources": [],
            "tool_result": {},
        }

    def rag(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        prepared = ask.prepare(state["query"])
        if prepared.prompt is not None and not proper_entities_supported(
            state["question"], [source.excerpt for source in prepared.sources]
        ):
            return {
                "prompt": None,
                "system": ask.system_prompt,
                "sources": [],
                "tool_result": {},
                "reason": "sin evidencia léxica para un nombre propio de la pregunta",
            }
        prompt = prepared.prompt
        if prompt is not None and state.get("turns"):
            previous = state["turns"][-1]
            prompt = f"Pregunta anterior del hilo: {previous['question']}\n\n{prompt}"
        return {
            "prompt": prompt,
            "system": ask.system_prompt,
            "sources": [_source_dict(source) for source in prepared.sources],
            "tool_result": {},
        }

    def tool(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        try:
            result = indicator_lookup.invoke(
                {"name": state.get("tool_name", ""), "period": state.get("tool_period", "")},
                config=config,
            )
        except Exception:
            # Un alias o período que no pase la validación Pydantic se trata
            # como ausencia de registro, no como un 503 del turno.
            result = {
                "found": False,
                "name": state.get("tool_name", ""),
                "period": state.get("tool_period", ""),
                "message": "La consulta estructurada no es válida; no se estima el dato.",
            }
        if result["found"]:
            instructions = (
                "Responde usando exclusivamente el resultado de la herramienta. "
                f"El usuario preguntó: {state['question']}\n"
                f"Resultado: {result['display']} ({result['unit']}), período {result['period']}.\n"
                f"Cita textual: {result['quote']}\n"
                f"Documento: {result['source_document']} — {result['source_url']}\n"
                "Incluye la cifra y la fuente; no estimes otros períodos."
            )
            sources = [_tool_source(result)]
        else:
            instructions = (
                "La herramienta no encontró el registro. Declara la ausencia sin "
f"estimar ni interpolar valores. Mensaje: {result.get('message', 'No hay registro para esa consulta.')}"
            )
            sources = []
        return {
            "prompt": build_conversation_prompt(
                state["question"],
                turns=state.get("turns", []),
                preferences=state.get("preferences", []),
                instructions=instructions,
            ),
            "system": AGENT_SYSTEM,
            "sources": sources,
            "tool_result": result,
        }

    workflow = StateGraph(cast(Any, AgentState))
    workflow.add_node("recall", recall)
    workflow.add_node("route", route)
    workflow.add_node("direct", direct)
    workflow.add_node("rag", rag)
    workflow.add_node("tool", tool)
    workflow.set_entry_point("recall")
    workflow.add_edge("recall", "route")
    workflow.add_conditional_edges(
        "route",
        lambda state: state["route"],
        {"direct": "direct", "rag": "rag", "tool": "tool"},
    )
    workflow.add_edge("direct", END)
    workflow.add_edge("rag", END)
    workflow.add_edge("tool", END)
    return workflow.compile(checkpointer=checkpointer, store=store)


# La herramienta está importada para que los tests de contrato puedan sustituirla
# desde este módulo sin reconstruir el grafo.
__all__ += ["NO_CONTEXT_ANSWER", "indicator_lookup"]
