"""``olvidar_preferencia``: borra una categoría, una nota o todo (spec 045, T049).

Contrato *Memoria* de ``contracts/agent-tools-045.md`` y research R16. Solo actúa si la
pregunta del turno pide olvidar. Con ``todo`` borra la memoria, el formato anterior y todos
los borradores de resumen del dueño, deja la lápida y marca el canal del resumen del hilo
actual (el grafo completa el marcador con los turnos del hilo).
"""

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from carvajal.agent.memory.store import forget
from carvajal.agent.memory.summary import forgotten_marker
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Draft, _Sources
from carvajal.agent.toolagent.tools._memory import forget_requested, memory_update, owner_error

DESCRIPTION = (
    "Borra lo que el asistente recuerda de la persona: una categoría, una nota o todo. "
    "Úsala para: «olvida mi nombre», «borra la nota de…», «olvida todo lo que sabes de "
    "mí». No la uses para: consultar la memoria (usa listar_memoria)."
)


class OlvidarPreferenciaArgs(BaseModel):
    """Qué olvidar (sin identidad: el dueño sale del servidor)."""

    categoria: Literal[
        "nombre", "empresa", "ciudad_habitual", "formato_preferido", "nota", "todo"
    ] = Field(description="Categoría a olvidar, o todo.")
    valor: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = Field(
        default=None, description="Con nota: texto o fragmento de la nota; sin él, todas."
    )

    @field_validator("valor", mode="before")
    @classmethod
    def _collapse(cls, value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(value.split()) or None
        return value


def status(_args: Mapping[str, Any]) -> str:
    return "Estoy borrando lo que me pediste olvidar…"


def run(
    args: OlvidarPreferenciaArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    failure = owner_error(deps, context)
    if failure is not None:
        return failure
    if not forget_requested(context):
        return _Draft(
            "empty",
            "No se borró nada: la persona no pidió olvidar en su mensaje.",
            data={"olvidado": [], "motivo": "no_pedido"},
        )
    operation = forget(
        deps.memory,
        context.owner_key,
        args.categoria,
        args.valor if args.categoria == "nota" else None,
        thread_id=context.thread_id or None,
        days=deps.settings.agent_memory_days,
        now=deps.now,
    )
    if operation.outcome == "rejected":
        return _Draft(
            "error",
            "La memoria no está disponible en este momento.",
            data={"motivo": operation.reason or "store_no_disponible"},
        )
    if operation.outcome == "nothing":
        return _Draft(
            "empty",
            "No había nada guardado de eso.",
            data={"olvidado": [], "motivo": "nada_que_olvidar"},
        )
    update = memory_update(operation.profile)
    if args.categoria == "todo":
        # El grafo completa ``covered_turns``/``forgotten_through`` con los turnos del hilo.
        update["conversation_summary"] = forgotten_marker(0, now=deps.now).to_value()
    return _Draft(
        "ok",
        "Borrado. Confírmalo en una frase.",
        data={"olvidado": [args.categoria]},
        state_update=update,
    )
