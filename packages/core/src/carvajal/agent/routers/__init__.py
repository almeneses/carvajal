"""Enrutadores intercambiables del agente (spec 036, US5, FR-013).

Todos cumplen ``Router``: reciben la pregunta y los turnos del hilo y devuelven un
``RouteDecision``. ``rules`` (``decide_route``) es el valor por defecto; ``llm`` y
``decision`` sirven para comparar con ``carvajal eval-router``.
"""

from collections.abc import Callable

from carvajal.agent.router import decide_route
from carvajal.agent.state import RouteDecision, Turn
from carvajal.infrastructure.settings import Settings

__all__ = ["ROUTER_NAMES", "Router", "build_router", "router_name"]

Router = Callable[[str, list[Turn] | None], RouteDecision]
ROUTER_NAMES = ("rules", "llm", "decision")


def router_name(router: Router) -> str:
    """Nombre del enrutador para los logs y los informes."""

    if router is decide_route:
        return "rules"
    return str(getattr(router, "name", getattr(router, "__name__", "custom")))


def build_router(name: str, settings: Settings | None = None) -> Router:
    """Construye el enrutador por nombre.

    Raises:
        ValueError: si el nombre no existe.
        DecisionModelUnavailable: si se pide ``decision`` sin un modelo configurado.
    """

    if name == "rules":
        return decide_route
    settings = settings or Settings()
    if name == "llm":
        from carvajal.adapters.llm import build_llm_provider
        from carvajal.agent.routers.llm import LLMRouter

        return LLMRouter(build_llm_provider(settings))
    if name == "decision":
        from carvajal.agent.routers.decision import DecisionRouter, load_decision_model

        return DecisionRouter(load_decision_model(settings.decision_model))
    raise ValueError(f"Enrutador desconocido: {name!r}. Usa uno de {', '.join(ROUTER_NAMES)}.")
