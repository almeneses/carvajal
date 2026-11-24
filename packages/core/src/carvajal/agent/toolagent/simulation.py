"""Simulador de conversaciones contra el chat real (spec 044, observación simulada).

Sustituye la observación de dos semanas con tráfico real por conversaciones simuladas de
varios turnos. Cada escenario abre su propio hilo (``owner_key`` de prueba ``sim-<id>`` y un
``thread_id`` nuevo) en el ``ChatService`` real: se ejercen la persistencia en Postgres, los
folios de cotización, el respaldo determinista y los eventos SSE tal como en producción.

El primer mensaje es el ``opening`` del escenario. Los siguientes los escribe un **cliente
simulado**: un LLM con un rol distinto del asistente (DeepSeek sin herramientas) que recibe
la persona, el objetivo, los temas de seguimiento y la conversación hasta ahora, y devuelve
su siguiente mensaje o la marca :data:`CLIENT_END` si el objetivo se cumplió o no tiene
sentido seguir. La conversación se corta también en ``max_turns`` o en el primer error.

Por turno se registran el mensaje, la respuesta, los eventos SSE (los tokens contiguos se
agrupan en uno), ``path`` y ``mode`` del ``trace``, fuentes, tarjetas, folios, latencias
(primera línea de estado, primer texto y total) y el uso de tokens del asistente, que el
servicio publica en los metadatos de su traza (:class:`RecordingTracer` los lee sin cambiar
el monitoreo configurado). Las métricas automáticas reutilizan
:mod:`carvajal.agent.toolagent.evaluation`:

- datos sin fuente: :func:`unsupported_figures` con el respaldo **visible** del turno
  (fuentes y tarjetas); los números que escribió el cliente no cuentan como afirmación;
- materiales no aptos: cada referencia de las tarjetas frente a :func:`check_material` para
  el uso que se infiere de los mensajes del cliente (o de las etiquetas ``uso:<uso>``,
  ``temperatura:<t>`` y ``microondas`` del escenario); sin uso inferible no se mide;
- abstención (:func:`is_abstention`), respaldo (``path == "fallback"``) y errores.

La utilidad de las respuestas no se juzga aquí: cada transcripción deja ``judge`` en
``None`` para el veredicto de los agentes revisores.
"""

import json
import math
import re
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol
from uuid import uuid4

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from carvajal.agent.commercial import CommercialRequest, check_material
from carvajal.agent.commercial_intent import mentions_microwave
from carvajal.agent.toolagent.evaluation import (
    ToolCall,
    TurnObservation,
    UsageRecorder,
    ensure_apps_importable,
    is_abstention,
    unsupported_figures,
)
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import Reference
from carvajal.infrastructure.monitoring import TOKEN_KEYS, NoopTracer, Turn, TurnTracer

__all__ = [
    "CLIENT_END",
    "ChatBackend",
    "ConversationSimulator",
    "RecordingTracer",
    "Scenario",
    "SimulatedClient",
    "Transcript",
    "TurnRecord",
    "build_real_simulator",
    "client_messages",
    "infer_request",
    "load_scenarios",
    "parse_client_reply",
    "render_summary",
    "run_simulation",
    "runtime_mode",
    "select_scenarios",
    "summarize",
    "unsuitable_materials",
]

logger = structlog.get_logger(__name__)

# Marca con la que el cliente simulado cierra la conversación.
CLIENT_END: Final = "FIN"
DEFAULT_MAX_TURNS: Final = 4
# Prefijo del dueño de prueba: los hilos simulados se distinguen de los reales.
OWNER_PREFIX: Final = "sim-"
# Largo máximo de cada respuesta del asistente en el prompt del cliente.
_ANSWER_CHARS: Final = 2000
_TEXT_EVENTS: Final = frozenset({"token", "fallback"})
_FIN: Final = re.compile(rf"(?:^|\W){CLIENT_END}\W*$")
_SPEAKER: Final = re.compile(r"^\s*(?:cliente|usuario|yo|persona)\s*:\s*", re.IGNORECASE)


# --- Escenarios ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """Una conversación simulada: quién escribe, qué busca y cómo abre."""

    id: str
    opening: str
    persona: str = ""
    goal: str = ""
    max_turns: int = DEFAULT_MAX_TURNS
    follow_ups: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    facts_to_check: tuple[str, ...] = ()

    @property
    def owner_key(self) -> str:
        return f"{OWNER_PREFIX}{self.id}"


def _strings(raw: Any, key: str, scenario_id: str) -> tuple[str, ...]:
    value = raw.get(key) or []
    if isinstance(value, str) or not isinstance(value, list):
        raise ValueError(f"Escenario {scenario_id}: «{key}» debe ser una lista de textos.")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _scenario(raw: Any) -> Scenario:
    if not isinstance(raw, Mapping):
        raise ValueError("Cada escenario debe ser un objeto JSON.")
    scenario_id = str(raw.get("id") or "").strip()
    opening = str(raw.get("opening") or "").strip()
    if not scenario_id:
        raise ValueError("Hay un escenario sin «id».")
    if not opening:
        raise ValueError(f"Escenario {scenario_id}: falta «opening».")
    max_turns = raw.get("max_turns", DEFAULT_MAX_TURNS)
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
        raise ValueError(f"Escenario {scenario_id}: «max_turns» debe ser un entero ≥ 1.")
    return Scenario(
        id=scenario_id,
        opening=opening,
        persona=str(raw.get("persona") or "").strip(),
        goal=str(raw.get("goal") or "").strip(),
        max_turns=max_turns,
        follow_ups=_strings(raw, "follow_ups", scenario_id),
        tags=_strings(raw, "tags", scenario_id),
        facts_to_check=_strings(raw, "facts_to_check", scenario_id),
    )


def load_scenarios(path: Path) -> list[Scenario]:
    """Lee el JSON de escenarios: una lista o un objeto con la clave ``scenarios``."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("scenarios") if isinstance(raw, Mapping) else raw
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path} no trae escenarios (lista o clave «scenarios»).")
    scenarios = [_scenario(item) for item in items]
    repeated = sorted(key for key, n in Counter(s.id for s in scenarios).items() if n > 1)
    if repeated:
        raise ValueError(f"Identificadores repetidos: {repeated}")
    return scenarios


def select_scenarios(
    scenarios: Sequence[Scenario], *, ids: Sequence[str] = (), limit: int | None = None
) -> list[Scenario]:
    """Filtra por ``ids`` (en el orden del archivo) y luego corta en ``limit``."""

    chosen = list(scenarios)
    if ids:
        known = {scenario.id for scenario in scenarios}
        unknown = sorted(set(ids) - known)
        if unknown:
            raise ValueError(f"Escenarios desconocidos: {unknown}")
        wanted = set(ids)
        chosen = [scenario for scenario in chosen if scenario.id in wanted]
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit debe ser un entero ≥ 1.")
        chosen = chosen[:limit]
    return chosen


# --- Cliente simulado -----------------------------------------------------------------------

CLIENT_SYSTEM: Final = """\
Eres una persona que escribe al chat web de Carvajal (grupo empresarial colombiano: empaques, \
información corporativa e indicadores financieros). NO eres el asistente: eres quien le \
escribe.

Tu persona: {persona}
Tu objetivo en esta conversación: {goal}
Temas que puedes sacar si vienen al caso (sin forzarlos y sin repetir los ya resueltos):
{follow_ups}

Reglas:
- Escribe solo tu siguiente mensaje al asistente, en español, breve (una a tres frases) y \
natural, como en un chat. Sin comillas ni prefijos como «Cliente:».
- Reacciona a lo último que respondió el asistente: si te pidió un dato, dalo de forma \
coherente con tu persona; si algo no quedó claro o no te sirve, dilo o pregunta.
- No inventes lo que dijo el asistente ni cambies de objetivo.
- Si tu objetivo ya se cumplió, o seguir no tiene sentido (el asistente no puede ayudarte o \
se repite), responde exactamente {end} y nada más."""


def _card_summary(products: Mapping[str, Any], quote_ids: Sequence[str]) -> str:
    """Resumen en texto de las tarjetas que vio la persona (nombre, material y folios)."""

    names: list[str] = []
    for option in products.get("options") or []:
        if isinstance(option, Mapping) and option.get("name"):
            names.append(f"{option['name']} ({option.get('material') or 's/m'})")
    ficha = products.get("ficha")
    if isinstance(ficha, Mapping) and ficha.get("name"):
        names.append(f"{ficha['name']} ({ficha.get('material') or 's/m'})")
    parts: list[str] = []
    if names:
        parts.append("tarjetas: " + "; ".join(dict.fromkeys(names)))
    for quote in _shown_quotes(products):
        total = quote.get("total")
        currency = quote.get("currency") or ""
        parts.append(f"cotización total {total} {currency}".strip() if total else "cotización")
    if quote_ids:
        parts.append("folios: " + ", ".join(quote_ids))
    return f"[{' · '.join(parts)}]" if parts else ""


def client_messages(scenario: Scenario, exchanges: Sequence["TurnRecord"]) -> list[BaseMessage]:
    """Prompt del cliente simulado: su rol y la conversación hasta ahora."""

    follow_ups = "\n".join(f"- {item}" for item in scenario.follow_ups) or "- (ninguno)"
    system = CLIENT_SYSTEM.format(
        persona=scenario.persona or "cliente de Carvajal",
        goal=scenario.goal or "resolver la consulta del primer mensaje",
        follow_ups=follow_ups,
        end=CLIENT_END,
    )
    lines: list[str] = []
    for record in exchanges:
        lines.append(f"Tú: {record.message}")
        answer = record.answer.strip() or "(sin respuesta)"
        if len(answer) > _ANSWER_CHARS:
            answer = answer[:_ANSWER_CHARS] + "…"
        cards = _card_summary(record.products, record.quote_ids)
        lines.append(f"Asistente: {answer}" + (f"\n{cards}" if cards else ""))
    transcript = "\n\n".join(lines)
    prompt = (
        f"Conversación hasta ahora:\n\n{transcript}\n\n"
        f"Escribe tu siguiente mensaje (o {CLIENT_END} si ya no hay nada más que pedir)."
    )
    return [SystemMessage(content=system), HumanMessage(content=prompt)]


def parse_client_reply(text: str) -> str | None:
    """Mensaje limpio del cliente o ``None`` si cerró con :data:`CLIENT_END` o vino vacío."""

    reply = text.strip()
    if not reply or _FIN.search(reply):
        return None
    reply = _SPEAKER.sub("", reply).strip()
    if len(reply) >= 2 and reply[0] == reply[-1] and reply[0] in "\"'«":
        reply = reply[1:-1].strip()
    if reply.startswith("«") and reply.endswith("»"):
        reply = reply[1:-1].strip()
    return reply or None


def _content_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) if isinstance(part, Mapping) else str(part)
            for part in content
        )
    return str(content)


class SimulatedClient:
    """Escribe el siguiente mensaje del cliente con un chat de LangChain (``invoke``)."""

    def __init__(self, chat: Any) -> None:
        self._chat = chat

    def next_message(
        self, scenario: Scenario, exchanges: Sequence["TurnRecord"]
    ) -> tuple[str | None, dict[str, int] | None]:
        """Devuelve ``(mensaje o None si cierra, uso de tokens de la llamada)``.

        El ``UsageRecorder`` es propio de cada llamada: el chat se comparte entre hilos y el
        uso de un escenario no se mezcla con el de otro.
        """

        recorder = UsageRecorder(self._chat)
        reply = recorder.invoke(client_messages(scenario, exchanges))
        return parse_client_reply(_content_text(reply)), recorder.take()


# --- Lectura de la traza del servicio --------------------------------------------------------


class _RecordingTurn:
    """Envuelve el turno del tracer real y copia sus metadatos (ruta, tokens, herramientas)."""

    def __init__(self, inner: Turn, sink: dict[str, Any], tools: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._sink = sink
        self._tools = tools

    def generation(self) -> AbstractContextManager[None]:
        return self._inner.generation()

    @contextmanager
    def _child(
        self, name: str, run_type: str, metadata: Mapping[str, Any] | None
    ) -> Generator[Turn]:
        if run_type == "tool":
            self._tools.append(dict(metadata or {}, tool=name))
        with self._inner.child(name, run_type=run_type, metadata=metadata) as child:
            yield child

    def child(
        self, name: str, *, run_type: str = "chain", metadata: Mapping[str, Any] | None = None
    ) -> AbstractContextManager[Turn]:
        return self._child(name, run_type, metadata)

    def add_metadata(self, **values: Any) -> None:
        self._sink.update(values)
        self._inner.add_metadata(**values)

    def add_outputs(self, **values: Any) -> None:
        self._inner.add_outputs(**values)

    def finish(self, *, outcome: str = "ok", error: BaseException | None = None) -> None:
        self._inner.finish(outcome=outcome, error=error)


class RecordingTracer:
    """Tracer de un escenario: delega en el configurado y guarda los metadatos del turno.

    El servicio publica en la raíz de la traza el camino, el motivo del respaldo, las
    herramientas y los tokens del turno (``token_metadata``); ningún evento SSE los trae.
    Hay un tracer por escenario y los turnos de un escenario son secuenciales, así que
    :meth:`take` siempre lee el turno recién terminado.
    """

    def __init__(self, inner: TurnTracer | None = None) -> None:
        self._inner: TurnTracer = inner if inner is not None else NoopTracer()
        self._metadata: dict[str, Any] = {}
        self._tools: list[dict[str, Any]] = []

    @contextmanager
    def turn(
        self,
        *,
        request_id: str,
        thread_hash: str,
        router: str,
        question: str = "",
    ) -> Generator[Turn]:
        self._metadata, self._tools = {}, []
        with self._inner.turn(
            request_id=request_id, thread_hash=thread_hash, router=router, question=question
        ) as inner:
            yield _RecordingTurn(inner, self._metadata, self._tools)

    def take(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Metadatos de la raíz y de cada herramienta del último turno; luego se vacían."""

        taken = (self._metadata, self._tools)
        self._metadata, self._tools = {}, []
        return taken


# --- Contrato mínimo del servicio ------------------------------------------------------------


class ChatEventLike(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def data(self) -> Mapping[str, Any]: ...


class ConversationLike(Protocol):
    @property
    def id(self) -> str: ...


class MessageLike(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def role(self) -> str: ...

    @property
    def content(self) -> str: ...

    @property
    def sources(self) -> list[dict[str, Any]] | None: ...

    @property
    def status(self) -> str: ...


class ChatBackend(Protocol):
    """Lo que el simulador usa del ``ChatService`` (el real lo cumple)."""

    def new_conversation(self, owner_key: str) -> ConversationLike: ...

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEventLike]: ...

    def history(self, owner_key: str, thread_id: str | None = None) -> Sequence[MessageLike]: ...


class ClientLike(Protocol):
    def next_message(
        self, scenario: Scenario, exchanges: Sequence["TurnRecord"]
    ) -> tuple[str | None, dict[str, int] | None]: ...


# --- Uso pedido y materiales -----------------------------------------------------------------


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# Uso de la guía de materiales que se infiere del texto del cliente (texto plegado).
_USE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("sopas", re.compile(r"\bsopas?\b|\bcaldos?\b|\bcremas? de\b|\bsancocho")),
    (
        "bebida_caliente",
        re.compile(
            r"\bcafes?\b|\btintos?\b|\bcapuchinos?\b|\blattes?\b|\bchocolate caliente\b"
            r"|\baromaticas?\b|\bte caliente\b|\bbebidas? calientes?\b"
        ),
    ),
    (
        "bebida_fria",
        re.compile(
            r"\bjugos?\b|\bgaseosas?\b|\blimonadas?\b|\bgranizados?\b|\bfrappes?\b"
            r"|\bmalteadas?\b|\bsmoothies?\b|\bbebidas? frias?\b|\bcafe frio\b"
        ),
    ),
    (
        "comida_caliente",
        re.compile(
            r"\bcomidas? calientes?\b|\balmuerzos?\b|\bcorrientazos?\b|\bplatos? calientes?\b"
            r"|\bcomida (?:a|para) domicilio\b"
        ),
    ),
    ("frituras", re.compile(r"\bfrituras?\b|\bfritos?\b|\bpapas fritas\b|\bempanadas?\b")),
    ("ensaladas", re.compile(r"\bensaladas?\b")),
    ("comida_fria", re.compile(r"\bcomidas? frias?\b")),
)
_HOT_USES: Final = frozenset({"bebida_caliente", "comida_caliente", "sopas"})
_COLD_USES: Final = frozenset({"bebida_fria", "comida_fria"})
_TEMPERATURES: Final[dict[str, Literal["caliente", "fria", "ambiente"]]] = {
    "caliente": "caliente",
    "fria": "fria",
    "ambiente": "ambiente",
}


def _use_from(text: str) -> str | None:
    """El uso que aparece primero en el texto, o ``None``."""

    found = [
        (match.start(), use)
        for use, pattern in _USE_PATTERNS
        if (match := pattern.search(text)) is not None
    ]
    return min(found)[1] if found else None


def infer_request(messages: Sequence[str], tags: Iterable[str] = ()) -> CommercialRequest | None:
    """Uso pedido (y temperatura y microondas) para validar materiales, si se puede inferir.

    Las etiquetas del escenario mandan (``uso:<uso>``, ``temperatura:caliente|fria``,
    ``microondas``); si no, el mensaje más reciente del cliente que nombra un uso. Sin uso
    ni temperatura ni microondas devuelve ``None``: la verificación no se mide.
    """

    folded_tags = [_fold(tag).strip() for tag in tags]
    use = next((tag.split(":", 1)[1] for tag in folded_tags if tag.startswith("uso:")), None)
    tagged = next(
        (tag.split(":", 1)[1] for tag in folded_tags if tag.startswith("temperatura:")), ""
    )
    folded = [_fold(message) for message in messages]
    if use is None:
        use = next((u for text in reversed(folded) if (u := _use_from(text))), None)
    # El mismo detector que el producto: medir con otro daría falsos «materiales aptos».
    microwave = "microondas" in folded_tags or any(mentions_microwave(m) for m in messages)
    temperature = _TEMPERATURES.get(tagged)
    if temperature is None:
        temperature = _TEMPERATURES.get(
            "caliente" if use in _HOT_USES else "fria" if use in _COLD_USES else ""
        )
    if use is None and temperature is None and not microwave:
        return None
    return CommercialRequest(
        use=use,
        temperature=temperature,
        microwave=True if microwave else None,
        capacity_oz=None,
        family=None,
        quantity=None,
        country=None,
        city=None,
    )


def _shown_quotes(products: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Cotizaciones mostradas como tarjeta (``quotes`` o, si no, ``quote``)."""

    quotes = products.get("quotes")
    candidates = list(quotes) if isinstance(quotes, list) and quotes else [products.get("quote")]
    return [
        quote
        for quote in candidates
        if isinstance(quote, Mapping) and quote.get("kind", "cotizacion") == "cotizacion"
    ]


def _card_entries(products: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Referencias recomendadas en las tarjetas (opciones, ficha y cotizaciones)."""

    entries: list[Mapping[str, Any]] = [
        option for option in products.get("options") or [] if isinstance(option, Mapping)
    ]
    ficha = products.get("ficha")
    if isinstance(ficha, Mapping):
        entries.append(ficha)
    for quote in _shown_quotes(products):
        for entry in [quote.get("primary"), *(quote.get("alternatives") or [])]:
            if isinstance(entry, Mapping):
                entries.append(entry)
    return [entry for entry in entries if entry.get("sku")]


def unsuitable_materials(
    products: Mapping[str, Any], request: CommercialRequest | None, catalog: Catalog
) -> list[dict[str, str]]:
    """Referencias de las tarjetas que :func:`check_material` descarta para ``request``.

    Una referencia que no está en el catálogo no se puede validar y no se marca.
    """

    if request is None or not products:
        return []
    by_key: dict[tuple[str, str], Reference] = {}
    by_sku: dict[str, Reference] = {}
    for reference in catalog.references:
        by_key.setdefault((reference.sku, reference.country), reference)
        by_sku.setdefault(reference.sku, reference)
    flagged: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in _card_entries(products):
        sku = str(entry["sku"])
        if sku in seen:
            continue
        seen.add(sku)
        reference = by_key.get((sku, str(entry.get("country") or ""))) or by_sku.get(sku)
        if reference is None:
            continue
        _, discarded = check_material([reference], request, catalog=catalog)
        if discarded:
            flagged.append(
                {"sku": sku, "material": reference.material, "reason": discarded[0].reason}
            )
    return flagged


# --- Registro de turnos y transcripciones ---------------------------------------------------


@dataclass
class TurnRecord:
    """Un turno simulado: lo que se envió, lo que llegó por SSE y sus métricas."""

    index: int
    message: str
    answer: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    mode: str = ""
    path: str = ""
    route: str = ""
    gate: str = ""
    fallback_reason: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    cards_html: list[str] = field(default_factory=list)
    products: dict[str, Any] = field(default_factory=dict)
    quote_ids: list[str] = field(default_factory=list)
    message_id: str = ""
    persisted: bool | None = None
    first_status_s: float | None = None
    first_text_s: float | None = None
    total_s: float = 0.0
    usage: dict[str, int] | None = None
    client_usage: dict[str, int] | None = None
    unsupported_figures: list[str] = field(default_factory=list)
    inferred_use: dict[str, Any] | None = None
    unsuitable_materials: list[dict[str, str]] = field(default_factory=list)
    abstained: bool = False
    error: str = ""

    @property
    def fallback(self) -> bool:
        return self.path == "fallback"

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "fallback": self.fallback}


@dataclass
class Transcript:
    """Una conversación simulada completa; ``judge`` lo llenan los revisores aparte."""

    scenario: Scenario
    owner_key: str
    thread_id: str = ""
    turns: list[TurnRecord] = field(default_factory=list)
    finished_by: str = ""
    error: str = ""
    started_at: str = ""
    total_s: float = 0.0
    judge: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.scenario.id,
            "scenario": asdict(self.scenario),
            "owner_key": self.owner_key,
            "thread_id": self.thread_id,
            "started_at": self.started_at,
            "total_s": self.total_s,
            "finished_by": self.finished_by,
            "error": self.error,
            "turns": [turn.as_dict() for turn in self.turns],
            "judge": self.judge,
        }


def _source_texts(sources: Iterable[Mapping[str, Any]]) -> list[str]:
    """Textos citables de las fuentes (sin la URL: sus dígitos no respaldan cifras)."""

    texts: list[str] = []
    for source in sources:
        texts.extend(str(source.get(key) or "") for key in ("excerpt", "title", "section", "slug"))
    return [text for text in texts if text]


def _commercial_result(payload: Any) -> dict[str, Any]:
    """Resultado comercial guardado junto a las citas del mensaje (tarjetas al recargar)."""

    if not isinstance(payload, list):
        return {}
    for item in payload:
        if isinstance(item, Mapping) and item.get("result_type") == "commercial":
            result = item.get("result")
            return dict(result) if isinstance(result, Mapping) else {}
    return {}


def _usage(metadata: Mapping[str, Any]) -> dict[str, int] | None:
    usage = {
        key: value
        for key in TOKEN_KEYS
        if isinstance(value := metadata.get(key), int) and not isinstance(value, bool)
    }
    return usage or None


def _compact(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa los tokens contiguos en un solo evento con su conteo."""

    compacted: list[dict[str, Any]] = []
    for event in events:
        last = compacted[-1] if compacted else None
        if event["name"] == "token" and last is not None and last["name"] == "token":
            last["data"]["text"] += str(event["data"].get("text", ""))
            last["count"] += 1
            continue
        entry = {"name": event["name"], "t": event["t"], "data": dict(event["data"])}
        if event["name"] == "token":
            entry["data"]["text"] = str(entry["data"].get("text", ""))
            entry["count"] = 1
        compacted.append(entry)
    return compacted


class ConversationSimulator:
    """Corre escenarios contra el servicio de chat con un cliente simulado.

    ``service_factory`` recibe el :class:`RecordingTracer` del escenario y devuelve el
    servicio (el real lo construye :func:`build_real_simulator`). ``run`` nunca lanza: un
    fallo queda en la transcripción.
    """

    def __init__(
        self,
        service_factory: Callable[[RecordingTracer], ChatBackend],
        client: ClientLike,
        *,
        inner_tracer: TurnTracer | None = None,
        catalog: Catalog | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._factory = service_factory
        self._client = client
        self._inner_tracer = inner_tracer
        self._catalog = catalog
        self._clock = clock

    def _catalog_or_default(self) -> Catalog:
        if self._catalog is None:
            self._catalog = load_catalog()
        return self._catalog

    def run(self, scenario: Scenario) -> Transcript:
        transcript = Transcript(
            scenario=scenario,
            owner_key=scenario.owner_key,
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        started = self._clock()
        try:
            self._run(scenario, transcript)
        except Exception as error:  # el escenario falla, la corrida sigue
            logger.warning("simulate_chat.scenario_failed", scenario=scenario.id, error=str(error))
            transcript.error = f"{type(error).__name__}: {error}"
            transcript.finished_by = transcript.finished_by or "error"
        transcript.total_s = round(self._clock() - started, 3)
        return transcript

    def _run(self, scenario: Scenario, transcript: Transcript) -> None:
        tracer = RecordingTracer(self._inner_tracer)
        service = self._factory(tracer)
        transcript.thread_id = str(service.new_conversation(scenario.owner_key).id)
        message: str | None = scenario.opening
        client_usage: dict[str, int] | None = None
        for index in range(scenario.max_turns):
            if index > 0:
                try:
                    message, client_usage = self._client.next_message(scenario, transcript.turns)
                except Exception as error:
                    transcript.finished_by = "client_error"
                    transcript.error = f"{type(error).__name__}: {error}"
                    return
                if message is None:
                    transcript.finished_by = "fin"
                    return
            record = self._turn(service, tracer, scenario, transcript, index, message or "")
            record.client_usage = client_usage
            transcript.turns.append(record)
            if record.error:
                transcript.finished_by = "error"
                return
        transcript.finished_by = "max_turns"

    def _turn(
        self,
        service: ChatBackend,
        tracer: RecordingTracer,
        scenario: Scenario,
        transcript: Transcript,
        index: int,
        message: str,
    ) -> TurnRecord:
        record = TurnRecord(index=index, message=message)
        raw_events: list[dict[str, Any]] = []
        tokens: list[str] = []
        fallback_text: str | None = None
        trace: dict[str, Any] = {}
        started = self._clock()
        try:
            for event in service.stream(
                scenario.owner_key,
                message,
                client_turn_id=f"sim-{uuid4().hex[:24]}",
                thread_id=transcript.thread_id,
            ):
                elapsed = round(self._clock() - started, 3)
                name, data = str(event.name), dict(event.data)
                raw_events.append({"name": name, "t": elapsed, "data": data})
                text = str(data.get("text") or "")
                if name == "status" and record.first_status_s is None:
                    record.first_status_s = elapsed
                if name in _TEXT_EVENTS and text and record.first_text_s is None:
                    record.first_text_s = elapsed
                if name == "token":
                    tokens.append(text)
                elif name == "fallback":
                    fallback_text = text
                elif name == "meta":
                    record.sources = [dict(s) for s in data.get("sources") or []]
                elif name == "products" and data.get("html"):
                    record.cards_html.append(str(data["html"]))
                elif name == "trace":
                    trace = data
                elif name == "done":
                    record.quote_ids = [str(q) for q in data.get("quote_ids") or []]
                    record.message_id = str(data.get("message_id") or "")
                elif name == "error":
                    record.error = str(data.get("message") or "error")
        except Exception as error:
            record.error = f"{type(error).__name__}: {error}"
        record.total_s = round(self._clock() - started, 3)
        record.events = _compact(raw_events)
        # En ``tools`` el respaldo se suma a lo emitido; en ``routes`` lo sustituye (no hubo
        # tokens). En ambos casos el texto del respaldo es la respuesta.
        record.answer = fallback_text if fallback_text is not None else "".join(tokens)
        metadata, tools = tracer.take()
        record.mode = str(trace.get("mode") or ("routes" if trace else ""))
        record.route = str(trace.get("route") or "")
        record.path = str(trace.get("path") or record.route)
        record.gate = str(trace.get("gate") or "")
        record.fallback_reason = str(metadata.get("fallback_reason") or "")
        if record.fallback and not record.fallback_reason:
            record.fallback_reason = str(trace.get("reason") or "")
        record.tools = tools
        record.usage = _usage(metadata)
        self._read_persisted(service, scenario, transcript, record)
        self._score(record, scenario, transcript, metadata)
        return record

    def _read_persisted(
        self, service: ChatBackend, scenario: Scenario, transcript: Transcript, record: TurnRecord
    ) -> None:
        """Comprueba el turno guardado y recupera el resultado comercial estructurado."""

        if record.error and not record.message_id:
            return
        try:
            history = service.history(scenario.owner_key, transcript.thread_id)
        except Exception as error:
            logger.warning("simulate_chat.history_failed", error=str(error))
            record.persisted = False
            return
        assistant = next((m for m in reversed(history) if m.role == "assistant"), None)
        if assistant is None:
            record.persisted = False
            return
        record.persisted = (
            (not record.message_id or assistant.id == record.message_id)
            and assistant.content == record.answer
            and assistant.status == "complete"
        )
        record.products = _commercial_result(assistant.sources)

    def _score(
        self,
        record: TurnRecord,
        scenario: Scenario,
        transcript: Transcript,
        metadata: Mapping[str, Any],
    ) -> None:
        """Métricas automáticas del turno con los criterios de ``evaluation``."""

        said = [turn.message for turn in transcript.turns] + [record.message]
        backing = _source_texts(record.sources)
        if record.products:
            backing.append(json.dumps(record.products, ensure_ascii=False, default=str))
        record.unsupported_figures = unsupported_figures(record.answer, backing, ignore=said)
        calls = [ToolCall(str(t.get("tool")), str(t.get("status") or "ok")) for t in record.tools]
        if metadata.get("no_query"):
            calls.append(ToolCall("sin_consulta"))
        observation = TurnObservation(
            answer=record.answer,
            tools=tuple(calls),
            sources=tuple(record.sources),
            products=record.products,
            quotes=tuple(_shown_quotes(record.products)),
            backing=tuple(backing),
            abstained=bool(metadata.get("abstained")),
            path=record.path or "agent",
        )
        record.abstained = is_abstention(observation, ignore=said)
        request = infer_request(said, scenario.tags)
        if request is not None:
            record.inferred_use = {
                "use": request.use,
                "temperature": request.temperature,
                "microwave": request.microwave,
            }
            if record.products:
                record.unsuitable_materials = unsuitable_materials(
                    record.products, request, self._catalog_or_default()
                )


# --- Corrida ---------------------------------------------------------------------------------


def run_simulation(
    simulator: ConversationSimulator,
    scenarios: Sequence[Scenario],
    *,
    concurrency: int = 3,
    on_transcript: Callable[[Transcript], None] | None = None,
) -> list[Transcript]:
    """Corre los escenarios con ``concurrency`` hilos; devuelve en el orden del archivo.

    ``on_transcript`` recibe cada transcripción al terminar (para escribirla enseguida: una
    interrupción conserva lo ya corrido). Con Ctrl+C se cancelan los escenarios pendientes.
    """

    if concurrency < 1:
        raise ValueError("--concurrency debe ser un entero ≥ 1.")
    done: dict[str, Transcript] = {}
    lock = threading.Lock()

    def finished(transcript: Transcript) -> None:
        with lock:
            done[transcript.scenario.id] = transcript
            if on_transcript is not None:
                on_transcript(transcript)

    pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="simulate-chat")
    try:
        futures: list[Future[Transcript]] = [pool.submit(simulator.run, s) for s in scenarios]
        for future in as_completed(futures):
            finished(future.result())
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return [done[scenario.id] for scenario in scenarios if scenario.id in done]


# --- Resumen ---------------------------------------------------------------------------------


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    """Percentil por rango más cercano, como el informe de ``eval-agent``."""

    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))]


def _add_usage(total: dict[str, int], usage: Mapping[str, int] | None) -> None:
    for key, value in (usage or {}).items():
        total[key] = total.get(key, 0) + value


def _cost(
    usage: Mapping[str, int], price_in: float | None, price_out: float | None
) -> float | None:
    if price_in is None or price_out is None:
        return None
    return (
        usage.get("input_tokens", 0) * price_in + usage.get("output_tokens", 0) * price_out
    ) / 1_000_000


def summarize(
    transcripts: Sequence[Transcript],
    *,
    price_in: float | None = None,
    price_out: float | None = None,
) -> dict[str, Any]:
    """Agregados de la corrida (lo que muestra :func:`render_summary`)."""

    turns = [turn for transcript in transcripts for turn in transcript.turns]
    assistant: dict[str, int] = {}
    client: dict[str, int] = {}
    for turn in turns:
        _add_usage(assistant, turn.usage)
        _add_usage(client, turn.client_usage)
    latencies = {
        "first_status_s": [t.first_status_s for t in turns if t.first_status_s is not None],
        "first_text_s": [t.first_text_s for t in turns if t.first_text_s is not None],
        "total_s": [t.total_s for t in turns if not t.error],
    }
    count = len(turns)
    assistant_cost = _cost(assistant, price_in, price_out)
    client_cost = _cost(client, price_in, price_out)
    return {
        "scenarios": len(transcripts),
        "turns": count,
        "finished_by": dict(Counter(t.finished_by for t in transcripts)),
        "fallback_turns": sum(t.fallback for t in turns),
        "fallback_pct": (100 * sum(t.fallback for t in turns) / count) if count else None,
        "fallback_reasons": dict(Counter(t.fallback_reason for t in turns if t.fallback)),
        "paths": dict(Counter(t.path or "sin_trace" for t in turns)),
        "unsupported_turns": sum(bool(t.unsupported_figures) for t in turns),
        "unsupported_figures": sum(len(t.unsupported_figures) for t in turns),
        "unsuitable_turns": sum(bool(t.unsuitable_materials) for t in turns),
        "material_checked_turns": sum(bool(t.inferred_use and t.products) for t in turns),
        "abstentions": sum(t.abstained for t in turns),
        "turn_errors": sum(bool(t.error) for t in turns),
        "scenario_errors": sum(bool(t.error) for t in transcripts),
        "not_persisted": sum(t.persisted is False for t in turns),
        "quotes": sum(len(t.quote_ids) for t in turns),
        "latency": {
            name: {"p50": _percentile(values, 0.5), "p95": _percentile(values, 0.95)}
            for name, values in latencies.items()
        },
        "assistant_usage": assistant,
        "assistant_usage_unknown": sum(t.usage is None and not t.error for t in turns),
        "client_usage": client,
        "cost_usd": {
            "assistant": assistant_cost,
            "client": client_cost,
            "total": None
            if assistant_cost is None or client_cost is None
            else assistant_cost + client_cost,
        },
        "price_input": price_in,
        "price_output": price_out,
    }


def _pct(part: int, total: int) -> str:
    return f"{100 * part / total:.1f} %" if total else "—"


def _secs(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} s"


def _usd(value: float | None) -> str:
    return "sin precio" if value is None else f"{value:.4f} USD"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_summary(
    transcripts: Sequence[Transcript],
    *,
    scenarios_path: str = "",
    price_in: float | None = None,
    price_out: float | None = None,
    header: Mapping[str, Any] | None = None,
    interrupted: bool = False,
) -> str:
    """Resumen Markdown: agregados, latencias, costo y hallazgos por turno."""

    data = summarize(transcripts, price_in=price_in, price_out=price_out)
    turns = data["turns"]
    lines = ["# Simulación de conversaciones (spec 044)", ""]
    if interrupted:
        lines += ["> ⚠️ Corrida interrumpida: el resumen cubre solo lo ya corrido.", ""]
    meta = {"Escenarios": scenarios_path, **(header or {})}
    lines += [f"- **{key}:** {value}" for key, value in meta.items() if value not in ("", None)]
    finished = ", ".join(f"{k or 'sin dato'}: {v}" for k, v in sorted(data["finished_by"].items()))
    reasons = ", ".join(f"{k or 'sin motivo'}: {v}" for k, v in data["fallback_reasons"].items())
    paths = ", ".join(f"{k}: {v}" for k, v in sorted(data["paths"].items()))
    lines += [
        "",
        "## Agregados",
        "",
        "| Métrica | Valor |",
        "| --- | --- |",
        f"| Conversaciones | {data['scenarios']} ({finished or '—'}) |",
        f"| Turnos | {turns} |",
        f"| Caminos | {paths or '—'} |",
        f"| Respaldo (path=fallback) | {data['fallback_turns']} "
        f"({_pct(data['fallback_turns'], turns)}){f' — {reasons}' if reasons else ''} |",
        f"| Turnos con datos sin fuente | {data['unsupported_turns']} "
        f"({_pct(data['unsupported_turns'], turns)}; {data['unsupported_figures']} cifras) |",
        f"| Turnos con materiales no aptos | {data['unsuitable_turns']} "
        f"(de {data['material_checked_turns']} con tarjetas y uso inferido) |",
        f"| Abstenciones | {data['abstentions']} ({_pct(data['abstentions'], turns)}) |",
        f"| Errores | {data['turn_errors']} turnos; {data['scenario_errors']} conversaciones |",
        f"| Turnos no persistidos como completos | {data['not_persisted']} |",
        f"| Folios emitidos | {data['quotes']} |",
        "",
        "## Latencias",
        "",
        "| Medida | p50 | p95 |",
        "| --- | --- | --- |",
    ]
    labels = {
        "first_status_s": "Primera línea de estado",
        "first_text_s": "Primer texto",
        "total_s": "Total del turno",
    }
    for key, label in labels.items():
        values = data["latency"][key]
        lines.append(f"| {label} | {_secs(values['p50'])} | {_secs(values['p95'])} |")
    assistant, client = data["assistant_usage"], data["client_usage"]
    cost = data["cost_usd"]
    lines += [
        "",
        "## Tokens y costo estimado",
        "",
        "| Rol | Entrada | Salida | Costo |",
        "| --- | --- | --- | --- |",
        f"| Asistente | {assistant.get('input_tokens', 0)} | {assistant.get('output_tokens', 0)} "
        f"| {_usd(cost['assistant'])} |",
        f"| Cliente simulado | {client.get('input_tokens', 0)} | {client.get('output_tokens', 0)} "
        f"| {_usd(cost['client'])} |",
        f"| **Total** | | | {_usd(cost['total'])} |",
        "",
    ]
    if data["assistant_usage_unknown"]:
        lines += [
            f"> {data['assistant_usage_unknown']} turnos sin uso de tokens publicado (respaldo "
            "sin modelo o modo `routes`): el costo del asistente es un mínimo.",
            "",
        ]
    lines += [
        f"Precios: {price_in if price_in is not None else '—'} USD/M de entrada, "
        f"{price_out if price_out is not None else '—'} USD/M de salida.",
        "",
        "## Conversaciones",
        "",
        "| Escenario | Turnos | Cierre | Respaldo | Sin fuente | No aptos | Errores | Juez |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t in transcripts:
        judge = "pendiente" if t.judge is None else _cell(str(t.judge))[:40]
        lines.append(
            f"| {t.scenario.id} | {len(t.turns)} | {t.finished_by} "
            f"| {sum(x.fallback for x in t.turns)} "
            f"| {sum(bool(x.unsupported_figures) for x in t.turns)} "
            f"| {sum(bool(x.unsuitable_materials) for x in t.turns)} "
            f"| {sum(bool(x.error) for x in t.turns) + bool(t.error)} | {judge} |"
        )
    findings: list[str] = []
    for t in transcripts:
        if t.error:
            findings.append(f"- `{t.scenario.id}`: {_cell(t.error)}")
        for turn in t.turns:
            where = f"`{t.scenario.id}` turno {turn.index + 1}"
            if turn.error:
                findings.append(f"- {where}: error — {_cell(turn.error)}")
            if turn.fallback:
                findings.append(f"- {where}: respaldo ({turn.fallback_reason or 'sin motivo'})")
            if turn.unsupported_figures:
                findings.append(f"- {where}: sin fuente {turn.unsupported_figures}")
            for item in turn.unsuitable_materials:
                findings.append(f"- {where}: no apto {item['sku']} ({item['material']})")
            if turn.persisted is False:
                findings.append(f"- {where}: el turno no quedó guardado como completo")
    lines += ["", "## Hallazgos por turno", ""]
    lines += findings or ["Sin hallazgos automáticos."]
    lines += [
        "",
        "> La utilidad de las respuestas no se mide aquí: el campo `judge` de cada "
        "transcripción queda para el veredicto de los revisores.",
    ]
    return "\n".join(lines)


# --- Runtime real ----------------------------------------------------------------------------


def build_real_simulator(settings: Any, *, client_chat: Any = None) -> ConversationSimulator:
    """Simulador contra el ``ChatService`` real y el cliente simulado de DeepSeek.

    Se construye como ``default_chat_service`` (misma fábrica de sesiones, embedder y LLM
    del runtime de la app), con un :class:`RecordingTracer` por escenario sobre el tracer
    configurado. Precalienta el runtime del agente (Postgres, Qdrant y grafos) para fallar
    antes de gastar tokens si algo no responde. El cliente simulado usa DeepSeek sin
    herramientas (``for_tools=False``), un rol distinto del asistente.
    """

    ensure_apps_importable()
    from apps.api import runtime
    from apps.api.agent_runtime import agent_runtime
    from apps.api.chat.service import ChatService
    from carvajal.adapters.llm.deepseek import build_deepseek_chat
    from carvajal.infrastructure.monitoring import build_tracer

    sessions = runtime.session_factory()
    agent_runtime()
    embedder, llm = runtime.ask_components()
    inner = build_tracer(settings)
    chat = client_chat or build_deepseek_chat(settings, for_tools=False, stream_usage=True)

    def factory(tracer: RecordingTracer) -> ChatBackend:
        return ChatService(sessions, embedder, llm, tracer=tracer, settings=settings)

    return ConversationSimulator(factory, SimulatedClient(chat), inner_tracer=inner)


def runtime_mode() -> str:
    """Modo que construyó el runtime del agente (``tools`` o ``routes``)."""

    from apps.api.agent_runtime import agent_runtime

    return str(agent_runtime().mode)
