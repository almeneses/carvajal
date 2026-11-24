"""Memoria de largo plazo del dueño, por categorías (spec 045, US2; research R5 a R7 y R16).

Vive en el Store de LangGraph (``PostgresStore`` en producción, ``InMemoryStore`` en
pruebas), namespace ``("carvajal", "memory", owner_key)``, con un elemento por categoría:

- ``nombre``, ``empresa``, ``ciudad_habitual`` y ``formato_preferido``:
  ``{"v": 2, "value", "origin", "updated_at"}``;
- ``notas``: ``{"v": 2, "items": [{"value", "origin", "updated_at"}], "updated_at"}``, hasta
  cinco notas de 120 caracteres.

Reglas: nunca se guarda un dato sensible (:mod:`carvajal.agent.memory.sensitive`); un dueño
vacío o con forma inválida no lee ni escribe (``sin_dueno``); un Store caído no rompe el
turno (perfil vacío o ``store_no_disponible``); lo que no se actualiza en
``AGENT_MEMORY_DAYS`` (180) días se ignora y se borra al leerlo. El formato anterior
(``("carvajal", "preferences", owner_key)`` / ``"profile"``) se migra una sola vez, en la
primera lectura (R6), y «olvidar todo» lo borra con todo lo demás, incluidos los borradores
de resumen del dueño, y deja una lápida (R16).

La memoria del Cotizador (``carvajal.quotation.memory``) es otra y no se toca.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import structlog

from carvajal.agent.memory.sensitive import find_sensitive
from carvajal.agent.memory.summary import TOMBSTONE_KEY, parse_time, summary_namespace

__all__ = [
    "CATEGORIES",
    "CATEGORY_LABELS",
    "FORMATS",
    "LEGACY_KEY",
    "MAX_NOTES",
    "MEMORY_DAYS",
    "NOTES_KEY",
    "NOTE_CHARS",
    "Category",
    "ForgetCategory",
    "MemoryOperation",
    "MemoryProfile",
    "classify",
    "forget",
    "forget_all_requested",
    "legacy_namespace",
    "load",
    "memory_namespace",
    "normalize_format",
    "remember",
    "valid_owner",
]

logger = structlog.get_logger(__name__)

MEMORY_VERSION: Final = 2
MEMORY_DAYS: Final = 180
MAX_NOTES: Final = 5
NOTE_CHARS: Final = 120
NOTES_KEY: Final = "notas"
LEGACY_KEY: Final = "profile"
# Marca de la migración del formato anterior: evita repetirla si nada se pudo migrar.
MIGRATED_KEY: Final = "_migrated"
CATEGORIES: Final[tuple[str, ...]] = (
    "nombre",
    "empresa",
    "ciudad_habitual",
    "formato_preferido",
)
FORMATS: Final[tuple[str, ...]] = ("corto", "detallado", "tablas", "viñetas", "tuteo", "usted")
# Largo máximo de cada categoría (data-model *MemoryProfile*).
_LIMITS: Final[Mapping[str, int]] = {
    "nombre": 60,
    "empresa": 80,
    "ciudad_habitual": 60,
    "formato_preferido": 40,
}
CATEGORY_LABELS: Final[Mapping[str, str]] = {
    "nombre": "nombre",
    "empresa": "empresa",
    "ciudad_habitual": "ciudad habitual (solo sugerir, confirmar antes de usar)",
    "formato_preferido": "formato preferido",
}
_OWNER_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SEARCH_LIMIT: Final = 100

Category = Literal["nombre", "empresa", "ciudad_habitual", "formato_preferido", "nota"]
ForgetCategory = Literal[
    "nombre", "empresa", "ciudad_habitual", "formato_preferido", "nota", "todo"
]
Origin = Literal["tool", "shortcut", "migration"]
Outcome = Literal["saved", "replaced", "rejected", "listed", "forgotten", "nothing"]
Reason = Literal[
    "dato_sensible",
    "vacio",
    "limite_notas",
    "demasiado_largo",
    "no_dicho",
    "no_pedido",
    "store_no_disponible",
    "sin_dueno",
]


def memory_namespace(owner_key: str) -> tuple[str, str, str]:
    """Namespace de la memoria por categorías del dueño."""

    return ("carvajal", "memory", owner_key)


def legacy_namespace(owner_key: str) -> tuple[str, str, str]:
    """Namespace del formato anterior (solo lectura para migrar y borrado al olvidar)."""

    return ("carvajal", "preferences", owner_key)


def valid_owner(owner_key: str) -> bool:
    """Dueño firmado por el servidor: no vacío, sin espacios ni separadores de ruta.

    Las identidades del chat son hexadecimales (cookie web) o ``wa_`` + hexadecimal
    (WhatsApp); el ejecutor de evaluación y las pruebas usan claves ASCII como
    ``eval-tools-m-001``. Un dueño vacío nunca comparte un namespace ``("…", "")``.
    """

    return isinstance(owner_key, str) and _OWNER_RE.fullmatch(owner_key) is not None


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text).casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def _clean(value: str) -> str:
    return " ".join(str(value).split())


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


# --- Perfil ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryProfile:
    """Memoria vigente del dueño: un valor por categoría y hasta cinco notas."""

    values: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        """Categorías con valor (las notas cuentan como una): ``trace.memory``."""

        return len(self.values) + (1 if self.notes else 0)

    @property
    def empty(self) -> bool:
        return not self.values and not self.notes

    def lines(self) -> list[str]:
        """Vista para el bloque ``<preferencias>``; la ciudad va rotulada como sugerencia."""

        lines = [
            f"{CATEGORY_LABELS[category]}: {self.values[category]}"
            for category in CATEGORIES
            if self.values.get(category)
        ]
        lines.extend(f"nota: {note}" for note in self.notes)
        return lines

    def listing(self) -> dict[str, Any]:
        """Forma de ``listar_memoria``: categorías con valor y la lista de notas."""

        data: dict[str, Any] = {
            category: self.values[category] for category in CATEGORIES if category in self.values
        }
        data["notas"] = list(self.notes)
        return data

    def snapshot(self) -> dict[str, Any]:
        """Instantánea JSON para el canal ``memory`` del grafo (sin fechas ni orígenes)."""

        data: dict[str, Any] = {
            category: {"v": MEMORY_VERSION, "value": self.values[category]}
            for category in CATEGORIES
            if category in self.values
        }
        if self.notes:
            data[NOTES_KEY] = {
                "v": MEMORY_VERSION,
                "items": [{"value": note} for note in self.notes],
            }
        return data

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "MemoryProfile":
        """Perfil desde la instantánea del canal; lo que no valida se ignora."""

        if not isinstance(snapshot, Mapping):
            return cls()
        values: dict[str, str] = {}
        for category in CATEGORIES:
            item = snapshot.get(category)
            if isinstance(item, Mapping) and isinstance(item.get("value"), str):
                values[category] = item["value"]
        notes: list[str] = []
        block = snapshot.get(NOTES_KEY)
        if isinstance(block, Mapping) and isinstance(block.get("items"), list):
            notes = [
                str(entry["value"])
                for entry in block["items"]
                if isinstance(entry, Mapping) and isinstance(entry.get("value"), str)
            ]
        return cls(values=values, notes=tuple(notes[:MAX_NOTES]))


@dataclass(frozen=True)
class MemoryOperation:
    """Resultado interno de una operación de memoria (data-model *MemoryOperation*).

    ``profile`` es la memoria tras la operación (para el ``state_update`` del turno);
    ``None`` si no se pudo leer. Al monitoreo solo van ``action``, ``category``,
    ``outcome`` y ``reason``: nunca el valor.
    """

    action: Literal["remember", "list", "forget"]
    category: str | None
    outcome: Outcome
    reason: Reason | None = None
    replaced: bool = False
    profile: MemoryProfile | None = None
    kinds: tuple[str, ...] = ()


# --- Lectura, caducidad y migración ------------------------------------------------------------


def _expired(updated_at: Any, now: datetime, days: int) -> bool:
    moment = parse_time(updated_at)
    return moment is None or now - moment > timedelta(days=days)


def _item_value(item: Any) -> Any:
    return item.value if hasattr(item, "value") else item


def load(
    store: Any, owner_key: str, *, days: int = MEMORY_DAYS, now: datetime | None = None
) -> MemoryProfile:
    """Memoria vigente del dueño; vacía sin Store, sin dueño válido o con el Store caído."""

    if store is None or not valid_owner(owner_key):
        return MemoryProfile()
    moment = _now(now)
    try:
        return _read(store, owner_key, days, moment)
    except Exception as error:  # un Store caído no rompe el turno (FR-009)
        logger.warning("agent.memory_store_failed", operation="load", error=type(error).__name__)
        return MemoryProfile()


def _read(store: Any, owner_key: str, days: int, now: datetime) -> MemoryProfile:
    namespace = memory_namespace(owner_key)
    items = list(store.search(namespace, limit=_SEARCH_LIMIT))
    if not items:
        return _migrate(store, owner_key, now)
    values: dict[str, str] = {}
    notes: list[str] = []
    for item in items:
        key = str(getattr(item, "key", ""))
        value = _item_value(item)
        if key.startswith("_") or not isinstance(value, Mapping):
            continue
        if key == NOTES_KEY:
            live = [
                entry
                for entry in value.get("items") or []
                if isinstance(entry, Mapping)
                and isinstance(entry.get("value"), str)
                and not _expired(entry.get("updated_at"), now, days)
            ]
            if not live:
                store.delete(namespace, key)
            elif len(live) != len(value.get("items") or []):
                store.put(namespace, key, {**value, "items": live})
            notes = [str(entry["value"]) for entry in live][-MAX_NOTES:]
            continue
        if key not in CATEGORIES or not isinstance(value.get("value"), str):
            continue
        if _expired(value.get("updated_at"), now, days):
            store.delete(namespace, key)
            continue
        values[key] = value["value"]
    return MemoryProfile(values=values, notes=tuple(notes))


def _cut_note(text: str) -> str:
    text = _clean(text)
    return text if len(text) <= NOTE_CHARS else text[: NOTE_CHARS - 1] + "…"


def _migrate(store: Any, owner_key: str, now: datetime) -> MemoryProfile:
    """Migra una sola vez el formato anterior ``{"items": [str]}`` (R6)."""

    namespace = memory_namespace(owner_key)
    legacy = store.get(legacy_namespace(owner_key), LEGACY_KEY)
    value = _item_value(legacy) if legacy is not None else None
    phrases = value.get("items") if isinstance(value, Mapping) else None
    if not isinstance(phrases, list) or not phrases:
        return MemoryProfile()
    values: dict[str, str] = {}
    candidates: list[str] = []
    dropped = 0
    for phrase in (str(item) for item in phrases):
        if find_sensitive(phrase):
            dropped += 1
            continue
        found = classify(phrase)
        if found:
            values.update(found)
        else:
            candidates.append(phrase)
    notes = [_cut_note(note) for note in candidates[-MAX_NOTES:]]
    dropped += max(0, len(candidates) - MAX_NOTES)
    stamp = now.isoformat()
    for category, text in values.items():
        store.put(namespace, category, _entry(text, "migration", stamp))
    if notes:
        store.put(
            namespace,
            NOTES_KEY,
            {
                "v": MEMORY_VERSION,
                "items": [
                    {"value": note, "origin": "migration", "updated_at": stamp} for note in notes
                ],
                "updated_at": stamp,
            },
        )
    store.put(namespace, MIGRATED_KEY, {"v": MEMORY_VERSION, "at": stamp})
    logger.info("agent.memory_migrated", categories=len(values), notes=len(notes))
    if dropped:
        logger.info("agent.memory_migration_dropped", count=dropped)
    return MemoryProfile(values=values, notes=tuple(notes))


def _entry(value: str, origin: str, stamp: str) -> dict[str, Any]:
    return {"v": MEMORY_VERSION, "value": value, "origin": origin, "updated_at": stamp}


# --- Escritura -----------------------------------------------------------------------------------


_FORMAT_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("corto", re.compile(r"\b(?:cort[oa]s?|breves?|resumid[oa]s?|concis[oa]s?)\b")),
    ("detallado", re.compile(r"\b(?:detallad[oa]s?|en detalle|con detalle)\b")),
    ("tablas", re.compile(r"\btablas?\b")),
    ("viñetas", re.compile(r"\b(?:vinetas?|listas? de puntos|bullets?)\b")),
    ("tuteo", re.compile(r"\b(?:tuteo|tutea\w*|tuteame|de tu)\b")),
    ("usted", re.compile(r"\b(?:de usted|usted)\b")),
)


def normalize_format(value: str) -> str | None:
    """Estilo de la lista cerrada que nombra ``value`` (con sinónimos); ``None`` si ninguno."""

    text = _plain(value)
    for name, pattern in _FORMAT_PATTERNS:
        if pattern.search(text):
            return name
    return None


_NAME_CHARS: Final = re.compile(r"[^\w '\-]|[\d_]")


def _normalize_city(value: str, cities: Iterable[str]) -> str:
    target = _plain(value)
    for city in cities:
        if _plain(city) == target:
            return city
    return value


def remember(
    store: Any,
    owner_key: str,
    category: str,
    value: str,
    *,
    origin: Origin = "tool",
    cities: Sequence[str] = (),
    days: int = MEMORY_DAYS,
    now: datetime | None = None,
) -> MemoryOperation:
    """Guarda ``value`` en la categoría (reemplaza; las notas se deduplican y tienen tope).

    ``formato_preferido`` se normaliza a la lista cerrada; si no encaja, se guarda como
    nota (que nunca se presenta como instrucción). ``cities`` (las del directorio) permite
    normalizar la ciudad habitual.
    """

    def rejected(reason: Reason, kinds: Sequence[str] = ()) -> MemoryOperation:
        if reason not in ("store_no_disponible", "sin_dueno"):
            logger.info("agent.memory_rejected", reason=reason, category=category)
        return MemoryOperation("remember", category, "rejected", reason=reason, kinds=tuple(kinds))

    if not valid_owner(owner_key):
        return rejected("sin_dueno")
    if store is None:
        return rejected("store_no_disponible")
    text = _clean(value)
    if category == "formato_preferido":
        normalized = normalize_format(text)
        if normalized is None:
            category = "nota"
        else:
            text = normalized
    if category == "nombre":
        text = _clean(_NAME_CHARS.sub(" ", text))
    if not text:
        return rejected("vacio")
    kinds = find_sensitive(text)
    if kinds:
        return rejected("dato_sensible", kinds)
    if category == "nota":
        if len(text) > NOTE_CHARS:
            return rejected("demasiado_largo")
    elif category in CATEGORIES:
        if len(text) > _LIMITS[category]:
            return rejected("demasiado_largo")
        if category == "ciudad_habitual":
            text = _normalize_city(text, cities)
    else:
        return rejected("vacio")
    moment = _now(now)
    stamp = moment.isoformat()
    namespace = memory_namespace(owner_key)
    try:
        # La primera lectura migra el formato anterior antes de escribir el nuevo (R6).
        _read(store, owner_key, days, moment)
        if category == "nota":
            outcome, reason = _add_note(store, namespace, text, origin, stamp)
            replaced = False
        else:
            current = store.get(namespace, category)
            previous = _item_value(current) if current is not None else None
            old = previous.get("value") if isinstance(previous, Mapping) else None
            store.put(namespace, category, _entry(text, origin, stamp))
            replaced = bool(old) and old != text
            outcome, reason = ("replaced" if replaced else "saved"), None
        profile = _read(store, owner_key, days, moment)
    except Exception as error:
        logger.warning(
            "agent.memory_store_failed", operation="remember", error=type(error).__name__
        )
        return rejected("store_no_disponible")
    if outcome == "rejected":
        logger.info("agent.memory_rejected", reason=reason, category=category)
    return MemoryOperation(
        "remember", category, outcome, reason=reason, replaced=replaced, profile=profile
    )


def _add_note(
    store: Any, namespace: tuple[str, ...], text: str, origin: str, stamp: str
) -> tuple[Outcome, Reason | None]:
    current = store.get(namespace, NOTES_KEY)
    value = _item_value(current) if current is not None else None
    items = [
        dict(entry)
        for entry in (value.get("items") if isinstance(value, Mapping) else None) or []
        if isinstance(entry, Mapping) and isinstance(entry.get("value"), str)
    ]
    if any(_plain(entry["value"]) == _plain(text) for entry in items):
        return "saved", None
    if len(items) >= MAX_NOTES:
        return "rejected", "limite_notas"
    items.append({"value": text, "origin": origin, "updated_at": stamp})
    store.put(namespace, NOTES_KEY, {"v": MEMORY_VERSION, "items": items, "updated_at": stamp})
    return "saved", None


def forget(
    store: Any,
    owner_key: str,
    category: str,
    value: str | None = None,
    thread_id: str | None = None,
    *,
    days: int = MEMORY_DAYS,
    now: datetime | None = None,
) -> MemoryOperation:
    """Olvida una categoría, una nota (o todas) o ``todo`` (R16).

    ``todo`` borra las categorías, el formato anterior y todos los borradores de resumen
    del dueño, y escribe la lápida ``_forgotten`` con la hora: el worker y ``recall``
    descartan lo que sea anterior. ``thread_id`` identifica el hilo que lo pidió (su canal
    queda como marcador por el ``state_update`` del turno).
    """

    if not valid_owner(owner_key):
        return MemoryOperation("forget", category, "rejected", reason="sin_dueno")
    if store is None:
        return MemoryOperation("forget", category, "rejected", reason="store_no_disponible")
    moment = _now(now)
    namespace = memory_namespace(owner_key)
    try:
        if category == "todo":
            _forget_all(store, owner_key, moment)
            outcome: Outcome = "forgotten"
        elif category == "nota":
            outcome = _forget_note(store, namespace, value, moment)
        elif category in CATEGORIES:
            current = store.get(namespace, category)
            outcome = "nothing"
            if current is not None:
                store.delete(namespace, category)
                outcome = "forgotten"
        else:
            outcome = "nothing"
        profile = _read(store, owner_key, days, moment)
    except Exception as error:
        logger.warning("agent.memory_store_failed", operation="forget", error=type(error).__name__)
        return MemoryOperation("forget", category, "rejected", reason="store_no_disponible")
    logger.info(
        "agent.memory_forgotten", category=category, outcome=outcome, thread=bool(thread_id)
    )
    return MemoryOperation("forget", category, outcome, profile=profile)


def _forget_note(
    store: Any, namespace: tuple[str, ...], value: str | None, now: datetime
) -> Outcome:
    current = store.get(namespace, NOTES_KEY)
    raw = _item_value(current) if current is not None else None
    data: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}
    items = list(data.get("items") or [])
    if not items:
        return "nothing"
    if not value or not _plain(value):
        store.delete(namespace, NOTES_KEY)
        return "forgotten"
    fragment = _plain(value)
    kept = [
        entry
        for entry in items
        if not (isinstance(entry, Mapping) and fragment in _plain(str(entry.get("value") or "")))
    ]
    if len(kept) == len(items):
        return "nothing"
    if kept:
        store.put(namespace, NOTES_KEY, {**data, "items": kept, "updated_at": now.isoformat()})
    else:
        store.delete(namespace, NOTES_KEY)
    return "forgotten"


def _delete_all(store: Any, namespace: tuple[str, ...], *, keep: str | None = None) -> None:
    """Borra todos los elementos del namespace (salvo ``keep``), página a página.

    ``store.search`` devuelve como mucho :data:`_SEARCH_LIMIT` elementos: se repite la
    búsqueda hasta que no quede nada que borrar. Si una página no borra nada nuevo (un
    Store que ignora ``delete``), se corta para no girar sin fin.
    """

    seen: set[str] = set()
    while True:
        batch = [
            str(item.key)
            for item in store.search(namespace, limit=_SEARCH_LIMIT)
            if str(item.key) != keep
        ]
        if not batch or seen.issuperset(batch):
            return
        seen.update(batch)
        for key in batch:
            store.delete(namespace, key)


def _forget_all(store: Any, owner_key: str, now: datetime) -> None:
    """Borra toda la memoria y los borradores del dueño y deja la lápida (R16).

    La lápida se escribe **antes** de borrar los borradores: un ``SummaryWorker`` que
    escriba su borrador después del borrado relee la lápida tras su ``put`` y lo borra él
    mismo (ver ``summary_worker``); con la lápida al final quedaría una ventana en la que
    ninguno de los dos lo vería.
    """

    drafts = summary_namespace(owner_key)
    store.put(drafts, TOMBSTONE_KEY, {"v": 1, "at": now.isoformat()})
    _delete_all(store, memory_namespace(owner_key))
    store.delete(legacy_namespace(owner_key), LEGACY_KEY)
    _delete_all(store, drafts, keep=TOMBSTONE_KEY)


# --- Patrones propios (R6) -----------------------------------------------------------------------

_FORGET_ALL: Final = re.compile(
    r"\b(?:"
    r"olvida(?:te)?\s+(?:todo\s+)?(?:lo\s+que\s+sabes\s+de\s+mi|mis\s+datos)"
    r"|borra\s+(?:todo\s+lo\s+que\s+sabes\s+de\s+mi|mi\s+memoria|mis\s+datos)"
    r"|olvidate\s+de\s+mi"
    r")\b"
)


def forget_all_requested(text: str) -> bool:
    """La persona pide olvidar todo («olvida todo lo que sabes de mí», «olvídate de mí»…)."""

    return _FORGET_ALL.search(_plain(text)) is not None


_CUT: Final = re.compile(r"\s+(?:y|e)\s+|[,;.!?¿¡\n]")
_WORD: Final = re.compile(r"[^\W\d_]+(?:['\-][^\W\d_]+)?")
_NAME_STOP: Final = frozenset(
    {"y", "e", "o", "u", "pero", "porque", "que", "por", "para", "con", "en", "de", "del"}
    | {"la", "el", "los", "las", "un", "una", "mejor", "no", "si", "sí", "ya", "muy", "tu", "su"}
)
_NAME_MARKER: Final = re.compile(
    r"\b(?:me\s+llamo|mi\s+nombre\s+es|ll[aá]mame|me\s+dicen)\s+", re.IGNORECASE
)
_SOY: Final = re.compile(r"\b[Ss]oy\s+")
_COMPANY_MARKER: Final = re.compile(
    r"\b(?:trabajo\s+en|trabajo\s+para|mi\s+empresa\s+es|tengo\s+una?)\s+", re.IGNORECASE
)
_CITY_MARKER: Final = re.compile(
    r"\b(?:suelo\s+comprar\s+en|normalmente\s+compro\s+en|compro\s+en|normalmente\s+en)\s+",
    re.IGNORECASE,
)


def _name_after(text: str, start: int, *, capitalized: bool) -> str:
    words: list[str] = []
    position = start
    while len(words) < 3:
        match = _WORD.match(text, position)
        if match is None:
            break
        word = match.group()
        if word.casefold() in _NAME_STOP or (capitalized and not word[0].isupper()):
            break
        words.append(word)
        position = match.end()
        gap = re.match(r"[ \t]+", text[position:])
        if gap is None:
            break
        position += gap.end()
    return " ".join(words)


def _phrase_after(text: str, start: int) -> str:
    return _clean(_CUT.split(text[start:], maxsplit=1)[0])


def classify(text: str) -> dict[str, str]:
    """Categorías que declara una frase: nombre, empresa, ciudad habitual y formato (R6).

    «llámame Ana, trabajo en Panadería La Espiga y suelo comprar en Cali» → nombre «Ana»,
    empresa «Panadería La Espiga» y ciudad «Cali». «Soy Ana» es un nombre; «soy de Cali» no.
    Lo que no se clasifica queda fuera (el llamador decide si es una nota).
    """

    found: dict[str, str] = {}
    marker = _NAME_MARKER.search(text)
    name = _name_after(text, marker.end(), capitalized=False) if marker else ""
    if not name:
        soy = _SOY.search(text)
        name = _name_after(text, soy.end(), capitalized=True) if soy else ""
    if name:
        found["nombre"] = name
    company = _COMPANY_MARKER.search(text)
    if company:
        value = _phrase_after(text, company.end())
        if value:
            found["empresa"] = value
    city = _CITY_MARKER.search(text)
    if city:
        value = _phrase_after(text, city.end())
        if value:
            found["ciudad_habitual"] = value
    style = normalize_format(text)
    if style is not None:
        found["formato_preferido"] = style
    return found
