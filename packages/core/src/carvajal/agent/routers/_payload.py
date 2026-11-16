"""Convierte la salida estructurada de un modelo en un ``RouteDecision`` (spec 036, T037-T038).

El enrutador LLM y el de modelo de decisión devuelven el mismo esquema JSON. Este módulo lo
valida contra el contrato del router de reglas: rutas e intenciones permitidas, indicador
reconocido con período explícito y claves de ``CommercialRequest`` con tipos y valores del
catálogo. Lo que no cumple se rechaza con ``InvalidRouterOutput``; nunca se rellena un dato.
"""

import json
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any, get_args

from carvajal.agent.commercial_intent import empty_request
from carvajal.agent.router import decide_route
from carvajal.agent.state import RouteDecision, Turn
from carvajal.agent.tools import resolve_indicator_name
from carvajal.catalog.models import Family

__all__ = [
    "FALLBACK_PREFIX",
    "INTENTS",
    "OUTPUT_SCHEMA",
    "InvalidRouterOutput",
    "decision_from_payload",
    "fallback_decision",
    "parse_json",
]

INTENTS: dict[str, frozenset[str]] = {
    "direct": frozenset({"greeting", "memory_write", "missing_period", "commercial_clarify"}),
    "rag": frozenset({"documental", "follow_up"}),
    "tool": frozenset({"indicator"}),
    "commercial": frozenset({"commercial", "commercial_custom", "commercial_mixed"}),
}
_FAMILIES = frozenset(get_args(Family))
_TEMPERATURES = frozenset({"caliente", "fria", "ambiente"})
_COUNTRIES = frozenset({"MX", "CO"})
FALLBACK_PREFIX = "salida inválida del enrutador"
_PERIOD = re.compile(r"20\d{2}(?:-(?:0[1-9]|1[0-2]))?")

OUTPUT_SCHEMA = """{
  "route": "direct | rag | tool | commercial",
  "intent": "una de las intenciones permitidas para esa ruta",
  "tool_name": "indicador, solo en la ruta tool",
  "tool_period": "AAAA o AAAA-MM, solo en la ruta tool",
  "other_part": "parte no comercial, solo con intent commercial_mixed",
  "request": {
    "use": "bebida_caliente | bebida_fria | comida_caliente | comida_fria | sopas | frituras
            | ensaladas | sandwiches | salsas | panaderia | frutas | null",
    "temperature": "caliente | fria | ambiente | null",
    "microwave": "true | null",
    "capacity_oz": "número en onzas (convierte ml dividiendo por 29.5735) | null",
    "family": "vaso | tapa | contenedor | charola | cubierto | envase | plato | null",
    "quantity": "unidades como entero | null",
    "country": "MX | CO | null",
    "city": "ciudad tal como la escribió | null",
    "prefers_renewable": "true | false"
  },
  "reason": "una frase corta"
}"""


class InvalidRouterOutput(ValueError):
    """La salida del modelo no cumple el contrato del router."""


def parse_json(text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON del texto, aunque venga entre cercas de código."""

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match is None:
        raise InvalidRouterOutput("la salida no contiene un objeto JSON")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise InvalidRouterOutput(f"JSON inválido: {error.msg}") from error
    if not isinstance(data, dict):
        raise InvalidRouterOutput("la salida no es un objeto JSON")
    return data


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _request(raw: Any) -> dict[str, Any]:
    """Normaliza la solicitud: un valor fuera del contrato queda en ``None``."""

    data = raw if isinstance(raw, dict) else {}
    result = empty_request()
    use = data.get("use")
    result["use"] = use if isinstance(use, str) and use.strip() else None
    result["temperature"] = (
        data.get("temperature") if data.get("temperature") in _TEMPERATURES else None
    )
    result["microwave"] = True if data.get("microwave") is True else None
    capacity = _number(data.get("capacity_oz"))
    result["capacity_oz"] = round(capacity, 2) if capacity else None
    result["family"] = data.get("family") if data.get("family") in _FAMILIES else None
    quantity = _number(data.get("quantity"))
    result["quantity"] = int(quantity) if quantity else None
    result["country"] = data.get("country") if data.get("country") in _COUNTRIES else None
    city = data.get("city")
    result["city"] = city.strip() if isinstance(city, str) and city.strip() else None
    result["prefers_renewable"] = data.get("prefers_renewable") is True
    return result


def decision_from_payload(
    payload: dict[str, Any], question: str, turns: list[Turn], *, source: str
) -> RouteDecision:
    """Valida la salida del modelo y arma la decisión con el mismo contrato que las reglas."""

    route, intent = payload.get("route"), payload.get("intent")
    if route not in INTENTS or intent not in INTENTS[route]:
        raise InvalidRouterOutput(f"ruta o intención no permitida: {route!r}/{intent!r}")
    reason = f"{source}: {str(payload.get('reason') or '').strip()[:200]}"
    if route == "tool":
        name = resolve_indicator_name(str(payload.get("tool_name") or ""))
        period = str(payload.get("tool_period") or "")
        if name is None or not _PERIOD.fullmatch(period):
            raise InvalidRouterOutput("indicador o período no reconocido")
        return RouteDecision(
            route="tool",
            intent=intent,
            query=question,
            tool_name=name,
            tool_period=period,
            reason=reason,
        )
    if intent == "memory_write":
        preference = " ".join(question.strip().split())[:280]
        return RouteDecision(
            route="direct", intent=intent, query=question, preference=preference, reason=reason
        )
    if intent == "follow_up" and turns:
        query = f"{turns[-1]['question']} {question}"
        return RouteDecision(route="rag", intent=intent, query=query, reason=reason)
    if route == "commercial" or intent == "commercial_clarify":
        other = str(payload.get("other_part") or "").strip()
        query = other if intent == "commercial_mixed" and other else question
        return RouteDecision(
            route=route,
            intent=intent,
            query=query,
            reason=reason,
            extra=_request(payload.get("request")),
        )
    return RouteDecision(route=route, intent=intent, query=question, reason=reason)


def fallback_decision(
    error: Exception,
    question: str,
    turns: list[Turn],
    *,
    source: str,
    fallback: Callable[[str, list[Turn] | None], RouteDecision] = decide_route,
) -> RouteDecision:
    """Decisión del enrutador de reglas cuando el modelo falla; la razón lo deja visible."""

    decision = fallback(question, turns)
    detail = error if isinstance(error, InvalidRouterOutput) else type(error).__name__
    return replace(
        decision, reason=f"{FALLBACK_PREFIX} ({source}: {detail}); reglas: {decision.reason}"
    )
