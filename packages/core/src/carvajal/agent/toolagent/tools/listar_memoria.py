"""``listar_memoria``: lo que el asistente recuerda de la persona (spec 045, T048).

Contrato *Memoria* de ``contracts/agent-tools-045.md``. Lee la memoria del dueño del turno
(nunca la de otro) y la devuelve por categorías; no registra fuentes ni escribe.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from carvajal.agent.memory.store import load
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Draft, _Sources
from carvajal.agent.toolagent.tools._memory import owner_error

DESCRIPTION = (
    "Lista lo que el asistente recuerda de la persona, por categorías. Úsala para: «¿qué "
    "sabes de mí?», «¿qué recuerdas?». No la uses para: guardar ni borrar datos (usa "
    "recordar_preferencia u olvidar_preferencia)."
)
CITY_NOTICE = "la ciudad habitual solo se usa si la confirmas"


class ListarMemoriaArgs(BaseModel):
    """Sin argumentos: el dueño sale del servidor."""


def status(_args: Mapping[str, Any]) -> str:
    return "Estoy revisando lo que recuerdo de ti…"


def run(args: ListarMemoriaArgs, deps: ToolDeps, context: ToolContext, sources: _Sources) -> _Draft:
    failure = owner_error(deps, context)
    if failure is not None:
        return failure
    profile = load(
        deps.memory, context.owner_key, days=deps.settings.agent_memory_days, now=deps.now
    )
    if profile.empty:
        return _Draft("empty", "No hay nada guardado de esta persona.", data={"memoria": {}})
    data: dict[str, Any] = {"memoria": profile.listing()}
    if "ciudad_habitual" in profile.values:
        data["aviso"] = CITY_NOTICE
    return _Draft(
        "ok",
        "Esto es lo que hay guardado; preséntalo como datos de la persona.",
        data=data,
    )
