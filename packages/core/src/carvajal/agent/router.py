"""Router determinista del agente: ``tool``, ``commercial``, ``rag`` o ``direct``.

Orden de decisión (documentado y probado):

0. **Olvido total (spec 045).** «Olvida todo lo que sabes de mí», «olvida mis datos»,
   «borra todo lo que sabes de mí» u «olvídate de mí» van a ``direct`` con la intención
   ``memory_forget``: el grafo borra la memoria sin decisión del proveedor.
1. **Memoria/preferencia declarada.** «Recuerda que…», «prefiero…», «mi nombre
   es…» se responde en ``direct`` y se guarda en el Store por categoría (en el grafo
   ``tools`` lo atiende el agente con ``recordar_preferencia``). No se busca.
2. **Saludo, agradecimiento o capacidad.** ``direct`` sin tocar Qdrant ni la
   herramienta.
3. **Solicitud comercial de empaques (spec 036).** Si nombra un producto del
   catálogo con una señal de compra o un dato de la necesidad, ``commercial``
   con la solicitud en ``extra``. Si falta la temperatura (ni dicha ni
   deducible del uso), ``direct`` pide aclararla: no se supone el material.
   Una necesidad a la medida va a ``commercial`` para derivarla a contacto
   comercial; si la pregunta trae además una parte no comercial, se marca
   ``commercial_mixed`` y esa parte viaja en ``query``. La extracción vive en
   ``commercial_intent``.
4. **Intención estructurada.** Si hay un indicador reconocible y un período
   explícito, ``tool``. Si hay indicador pero falta el período, ``direct`` pide
   aclararlo: no se inventa el período ni se mezcla con RAG.
5. **Seguimiento elíptico.** Si la pregunta es corta o usa «eso», «ese año»,
   «allí», se reescribe con el turno anterior y va a ``rag``.
6. **Documental.** Todo lo demás va a ``rag``; si no hay evidencia sobre el
   umbral, el caso de uso se abstiene.

La ambigüedad tool/RAG se resuelve por la presencia de un indicador **y** un
período; sin ambos, la pregunta es conversacional o documental, nunca una
consulta estructurada a medias.
"""

import re
import unicodedata
from typing import Any

from carvajal.agent.commercial_intent import (
    empty_request,
    extract_commercial,
    is_custom_need,
    non_commercial_part,
)
from carvajal.agent.memory.store import forget_all_requested
from carvajal.agent.state import RouteDecision, Turn
from carvajal.agent.tools import known_names, resolve_indicator_name

__all__ = ["decide_route", "extract_period", "looks_elliptical"]

_YEAR = re.compile(r"\b(20\d{2})\b")
_MONTHS = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}
_TRIMESTERS = {"primer": "03", "segundo": "06", "tercer": "09", "cuarto": "12"}
_GREETINGS = {
    "hola",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "gracias",
    "muchas gracias",
    "quien eres",
    "que puedes hacer",
    "que sabes",
    "ayuda",
    "adios",
}
_ELLIPTICAL = {
    "eso",
    "esa",
    "ese",
    "esos",
    "esas",
    "alli",
    "ahi",
    "el mismo",
    "la misma",
    "en que ano",
    "que ano",
}
_PREFERENCE_MARKERS = (
    "recuerda que",
    "ten en cuenta que",
    "prefiero",
    "mi nombre es",
    "llamame",
)


def _plain(value: str) -> str:
    """Minúsculas sin acentos, para comparar sin depender de la tilde."""

    decomposed = unicodedata.normalize("NFKD", value.lower().replace("_", " "))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


def extract_period(question: str) -> str:
    """Extrae un período explícito ``YYYY`` o ``YYYY-MM``; vacío si no hay."""

    text = _plain(question)
    iso = re.search(r"\b(20\d{2}-(?:0[1-9]|1[0-2]))\b", text)
    if iso:
        return iso.group(1)
    year_match = _YEAR.search(text)
    if year_match is None:
        return ""
    year = year_match.group(1)
    if re.search(r"\b(primer|segundo|tercer|cuarto)\s+trimestre\b", text):
        ordinal = re.search(r"\b(primer|segundo|tercer|cuarto)\s+trimestre\b", text)
        assert ordinal is not None
        return f"{year}-{_TRIMESTERS[ordinal.group(1)]}"
    for month, number in _MONTHS.items():
        if re.search(rf"\b{month}\b", text):
            return f"{year}-{number}"
    return year


def looks_elliptical(question: str, turns: list[Turn]) -> bool:
    """Indica si la pregunta necesita el contexto del turno anterior."""

    if not turns:
        return False
    normalized = _plain(question)
    # Solo los marcadores anafóricos convierten una pregunta en seguimiento.
    # Se comparan como palabras completas: «proceso» no contiene «eso».
    return any(re.search(rf"\b{re.escape(marker)}\b", normalized) for marker in _ELLIPTICAL)


def _expand(question: str) -> str:
    """Expansión determinista con términos verificados en el corpus.

    Los embeddings pequeños confunden «ciudad de fundación» con la portada.
    Añadir el año y la ciudad que el corpus sí declara (``quienes_somos``)
    alinea la consulta con el fragmento correcto sin inventar datos: el texto
    sigue saliendo del documento y la cita conserva su URL.
    """

    normalized = _plain(question)
    extras: list[str] = []
    if (
        "fundad" in normalized or "fundacion" in normalized or re.search(r"\bnacio\b", normalized)
    ) and "carvajal" in normalized:
        extras.append("1904 Cali Colombia")
    if "presencia" in normalized and (
        re.search(r"\bpais\b", normalized) or re.search(r"\bpaises\b", normalized)
    ):
        extras.append("10 países")
    if "empleado" in normalized or "nomina" in normalized:
        extras.append("13.000 empleados")
    if "liquidezya" in normalized:
        extras.append("LiquidezYa")
    return f"{question} {' '.join(extras)}" if extras else question


def _preference(question: str) -> str:
    """Devuelve la declaración que se guardará, o vacío."""

    normalized = _plain(question)
    if not any(marker in normalized for marker in _PREFERENCE_MARKERS):
        return ""
    # Se guarda la frase del usuario, sin la pregunta ni datos de otros turnos.
    return " ".join(question.strip().split())[:280]


def _tool_intent(question: str) -> tuple[str, str]:
    """Nombre canónico del indicador y período; ambos vacíos si no aplica."""

    normalized = _plain(question)
    for candidate in sorted(known_names() | _ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(_plain(candidate))}\b", normalized):
            canonical = resolve_indicator_name(candidate)
            if canonical is not None:
                return canonical, extract_period(question)
    return "", ""


_ALIASES = {
    "ingresos",
    "ventas",
    "facturacion",
    "ebitda",
    "utilidad",
    "utilidad neta",
    "utilidad bruta",
    "ganancia neta",
    "perdida neta",
    "activos",
    "activos totales",
    "empleados",
    "nomina",
    "colaboradores",
    "trabajadores",
}


def _commercial_decision(question: str, request: dict[str, Any]) -> RouteDecision:
    """Ruta comercial, o aclaración en ``direct`` si falta la temperatura (FR-002)."""

    said = ", ".join(f"{key}={value}" for key, value in request.items() if value)
    if request["temperature"] is None:
        return RouteDecision(
            route="direct",
            intent="commercial_clarify",
            query=question,
            reason=f"solicitud comercial sin temperatura ni uso; se pide aclaración ({said})",
            extra=request,
        )
    # La parte mixta solo se busca en el texto de este turno: una respuesta corta
    # («caliente», «en Cali») hereda la solicitud y no debe perderla porque una palabra
    # suelta suene a otro tema.
    own = extract_commercial(question) is not None
    other = non_commercial_part(question) if own else None
    tool_name, period = _tool_intent(question)
    if own and tool_name and period:
        # El indicador se nombra en su forma canónica legible y con su período.
        other = f"el indicador {tool_name.replace('_', ' ')} de {period}"
    if other is not None:
        return RouteDecision(
            route="commercial",
            intent="commercial_mixed",
            query=other,
            reason=f"pregunta mixta: se atiende la parte comercial ({said}) y se avisa la otra",
            extra=request,
        )
    return RouteDecision(
        route="commercial",
        intent="commercial",
        query=question,
        reason=f"solicitud comercial de empaques ({said})",
        extra=request,
    )


def decide_route(question: str, turns: list[Turn] | None = None) -> RouteDecision:
    """Aplica el orden de decisión y devuelve una decisión auditable."""

    turns = turns or []
    normalized = _plain(question)
    if forget_all_requested(question):
        # «Olvida todo lo que sabes de mí» (spec 045, FR-015): lo atiende el atajo sin
        # decisión del proveedor; el grafo borra la memoria antes de responder.
        return RouteDecision(
            route="direct",
            intent="memory_forget",
            query=question,
            reason="la persona pidió olvidar todo lo que se sabe de ella",
        )
    preference = _preference(question)
    if preference:
        return RouteDecision(
            route="direct",
            intent="memory_write",
            query=question,
            preference=preference,
            reason="el usuario declaró una preferencia",
        )

    if is_custom_need(question):
        return RouteDecision(
            route="commercial",
            intent="commercial_custom",
            query=question,
            reason="necesidad a la medida fuera del catálogo; se deriva a contacto comercial",
            extra=extract_commercial(question, turns) or empty_request(),
        )
    commercial = extract_commercial(question, turns)
    if commercial is not None:
        return _commercial_decision(question, commercial)

    tool_name, period = _tool_intent(question)
    if tool_name and period:
        return RouteDecision(
            route="tool",
            intent="indicator",
            query=question,
            tool_name=tool_name,
            tool_period=period,
            reason=f"indicador {tool_name} con período {period}",
        )

    greeting = normalized.replace("¿", "").replace("?", "").replace("!", "").strip()
    greeting_pattern = re.compile(
        r"^(hola|buenos dias|buenas tardes|buenas noches|gracias|muchas gracias)"
        r"([, ]+(que puedes hacer|que sabes|ayuda))?$"
    )
    if greeting in _GREETINGS or greeting_pattern.fullmatch(greeting):
        return RouteDecision(
            route="direct",
            intent="greeting",
            query=question,
            reason="saludo o capacidad, sin recuperación",
        )
    if tool_name and not period:
        return RouteDecision(
            route="direct",
            intent="missing_period",
            query=question,
            reason="hay indicador pero falta el período; se pide aclaración",
        )

    if looks_elliptical(question, turns):
        previous = turns[-1]
        # Se reescribe con la pregunta anterior, no con su respuesta: la
        # respuesta puede divagar y arrastrar el embedding a otro documento.
        query = f"{previous['question']} {question}"
        return RouteDecision(
            route="rag",
            intent="follow_up",
            query=_expand(query),
            reason="seguimiento elíptico: se recupera con el turno anterior",
        )

    return RouteDecision(
        route="rag",
        intent="documental",
        query=_expand(question),
        reason="consulta documental por defecto",
    )
