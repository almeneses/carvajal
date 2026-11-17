"""Adaptador de un modelo de decisión externo (Jev u otro «System One»), spec 036 T038.

El repositorio no fija un proveedor: el modelo se carga desde ``DECISION_MODEL``, con la forma
``paquete.modulo:fabrica``. La fábrica devuelve un objeto con el método ``decide`` del
protocolo ``DecisionModel``, que recibe la pregunta, los turnos y el esquema de salida, y
devuelve un ``dict`` con ese esquema (el mismo del enrutador LLM). Así se puede probar Jev,
GLiNER2.5-Decide o cualquier otro sin añadir dependencias a la instalación por defecto.

Si el modelo no está configurado o no se puede importar, se lanza
``DecisionModelUnavailable`` con un mensaje claro: el error es controlado y el enrutador por
reglas sigue siendo el valor por defecto.
"""

import importlib
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import structlog

from carvajal.agent.routers._payload import (
    OUTPUT_SCHEMA,
    decision_from_payload,
    fallback_decision,
)
from carvajal.agent.state import RouteDecision, Turn

__all__ = ["DecisionModel", "DecisionModelUnavailable", "DecisionRouter", "load_decision_model"]

logger = structlog.get_logger(__name__)


class DecisionModelUnavailable(RuntimeError):
    """No hay un modelo de decisión configurado o no se puede cargar."""


class DecisionModel(Protocol):
    """Contrato mínimo que debe cumplir el modelo de decisión."""

    def decide(self, question: str, *, turns: Sequence[str], schema: str) -> Mapping[str, Any]: ...


def load_decision_model(spec: str) -> DecisionModel:
    """Importa la fábrica ``paquete.modulo:fabrica`` y construye el modelo."""

    if not spec.strip():
        raise DecisionModelUnavailable(
            "No hay modelo de decisión configurado. Define DECISION_MODEL=paquete.modulo:fabrica "
            "e instala su paquete; mientras tanto usa --router rules o --router llm."
        )
    module_name, _, factory_name = spec.partition(":")
    if not module_name or not factory_name:
        raise DecisionModelUnavailable(
            f"DECISION_MODEL debe tener la forma paquete.modulo:fabrica, no {spec!r}."
        )
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        model = factory()
    except Exception as error:  # la fábrica puede fallar al construir el modelo
        raise DecisionModelUnavailable(
            f"No se pudo cargar el modelo de decisión {spec!r}: {error}"
        ) from error
    if not callable(getattr(model, "decide", None)):
        raise DecisionModelUnavailable(f"{spec!r} no devuelve un objeto con el método decide().")
    return model


class DecisionRouter:
    """Enrutador sobre un ``DecisionModel``; misma validación y respaldo que el LLM."""

    name = "decision"

    def __init__(self, model: DecisionModel) -> None:
        self._model = model
        self.calls = 0
        self.invalid = 0

    def __call__(self, question: str, turns: list[Turn] | None = None) -> RouteDecision:
        turns = turns or []
        self.calls += 1
        try:
            payload = self._model.decide(
                question, turns=[turn["question"] for turn in turns], schema=OUTPUT_SCHEMA
            )
            return decision_from_payload(dict(payload), question, turns, source=self.name)
        except Exception as error:  # misma política que el enrutador LLM
            self.invalid += 1
            logger.warning(
                "agent.router_fallback",
                router=self.name,
                error=f"{type(error).__name__}: {error}",
            )
            return fallback_decision(error, question, turns, source=self.name)
