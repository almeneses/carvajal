"""Enrutador LLM con salida estructurada (spec 036, T037).

Cumple el mismo contrato que ``decide_route``: recibe la pregunta y los turnos y devuelve un
``RouteDecision``. El LLM solo clasifica y extrae; la salida se valida con
``decision_from_payload``. Si el LLM falla o su salida no cumple el contrato, se usa el
enrutador de reglas y la razón empieza por ``FALLBACK_PREFIX``, para que la evaluación lo
cuente como salida inválida en vez de ocultarlo.
"""

from collections.abc import Callable

from carvajal.agent.router import decide_route
from carvajal.agent.routers._payload import (
    FALLBACK_PREFIX,
    OUTPUT_SCHEMA,
    decision_from_payload,
    fallback_decision,
    parse_json,
)
from carvajal.agent.state import RouteDecision, Turn
from carvajal.agent.tools import known_names
from carvajal.application.ports import LLMProvider

__all__ = ["FALLBACK_PREFIX", "LLMRouter", "system_prompt"]

MAX_CONTEXT_TURNS = 3
_INDICATORS = "<<indicadores>>"

_SYSTEM_TEMPLATE = f"""Eres el enrutador de un asistente sobre Carvajal. Clasifica la \
pregunta en una ruta y extrae datos. Responde solo con un objeto JSON con este esquema:

{OUTPUT_SCHEMA}

Rutas e intenciones:
- direct/greeting: saludo, agradecimiento o pregunta sobre lo que puede hacer el asistente.
- direct/memory_write: el usuario declara una preferencia («recuerda que…», «prefiero…»).
- tool/indicator: indicador financiero o de empleados de Carvajal con un período explícito.
  tool_name debe ser uno de: {_INDICATORS}.
- direct/missing_period: pide un indicador pero sin período.
- commercial/commercial: quiere comprar o cotizar empaques (vasos, tapas, contenedores,
  charolas, cubiertos, envases, platos) y se conoce la temperatura del contenido.
- direct/commercial_clarify: quiere comprar empaques pero no se sabe si el contenido es
  caliente, frío o ambiente, ni un uso que lo indique.
- commercial/commercial_custom: empaque a la medida (industrial, agro, exportación, con logo).
- commercial/commercial_mixed: compra de empaques más otra pregunta no comercial; pon la otra
  en other_part.
- rag/follow_up: sigue la pregunta anterior con «eso», «ese año», «allí».
- rag/documental: cualquier otra pregunta sobre Carvajal, incluidas las que solo preguntan
  qué productos fabrica, sin intención de compra.

Si la respuesta corta completa una solicitud comercial anterior, combina sus datos con los de
los turnos previos. Deduce la temperatura del uso («café» → caliente, «microondas» → caliente).
No inventes datos: lo que no se dijo va en null."""


def system_prompt() -> str:
    """Prompt del sistema con los indicadores del registro, leídos en cada turno."""

    return _SYSTEM_TEMPLATE.replace(_INDICATORS, ", ".join(sorted(known_names())))


Router = Callable[[str, list[Turn] | None], RouteDecision]


class LLMRouter:
    """Enrutador que delega la clasificación en un ``LLMProvider``."""

    name = "llm"

    def __init__(self, llm: LLMProvider, *, fallback: Router = decide_route) -> None:
        self._llm = llm
        self._fallback = fallback
        self.calls = 0
        self.invalid = 0

    def _prompt(self, question: str, turns: list[Turn]) -> str:
        previous = [turn["question"] for turn in turns[-MAX_CONTEXT_TURNS:]]
        context = "\n".join(f"- {item}" for item in previous) or "(ninguno)"
        return f"Preguntas anteriores del hilo:\n{context}\n\nPregunta actual:\n{question}"

    def __call__(self, question: str, turns: list[Turn] | None = None) -> RouteDecision:
        turns = turns or []
        self.calls += 1
        try:
            text = self._llm.complete(system=system_prompt(), prompt=self._prompt(question, turns))
            return decision_from_payload(parse_json(text), question, turns, source=self.name)
        except Exception as error:  # el turno nunca se cae por el enrutador
            self.invalid += 1
            return fallback_decision(
                error, question, turns, source=self.name, fallback=self._fallback
            )
