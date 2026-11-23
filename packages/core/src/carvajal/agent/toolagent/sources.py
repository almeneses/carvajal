"""Registro de fuentes del turno con números de cita ``[n]`` (spec 044, R6).

El estado del grafo guarda las fuentes como ``list[dict]`` porque el checkpointer
de Postgres solo admite datos JSON-serializables. ``SourceRegistry`` no tiene
estado propio: sus métodos son funciones puras que reciben esa lista y devuelven
una nueva, sin mutar la entrada.

Cada entrada es ``dataclasses.asdict(Source)`` más el campo ``n``. Los números son
consecutivos (1, 2, 3…) en el orden en que aparece cada procedencia nueva dentro
del turno, de modo que el texto del modelo y la API comparten la misma numeración.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import MISSING, asdict, dataclass, fields
from typing import Any

from carvajal.application.ask import Source

SourceInput = Source | Mapping[str, Any]
"""Fuente recibida de una herramienta: el ``Source`` del caso de uso o su forma serializada."""

_FIELDS = tuple(fields(Source))


@dataclass(frozen=True)
class RegisteredSources:
    """Resultado de registrar un lote de fuentes.

    ``numbers[i]`` es el número de cita de la fuente ``i`` del lote (una repetida
    reutiliza el número ya asignado) y ``sources`` es la lista completa nueva.
    """

    numbers: list[int]
    sources: list[dict[str, Any]]


class SourceRegistry:
    """Numeración y deduplicación de fuentes sobre ``ToolAgentState.sources``."""

    @staticmethod
    def register(
        sources: Sequence[Mapping[str, Any]], batch: Iterable[SourceInput]
    ) -> RegisteredSources:
        """Añade ``batch`` a ``sources`` y devuelve los números asignados.

        Deduplica por ``chunk_id``; si falta, por URL y título (y, si tampoco hay
        URL ni título, por slug, sección y extracto). Ante una repetición conserva
        la primera entrada tal cual. ``sources`` no se modifica: se devuelve una
        lista nueva con copias de sus entradas.
        """

        registered = [dict(entry) for entry in sources]
        index = {_key(entry): int(entry["n"]) for entry in registered}
        next_n = max(index.values(), default=0) + 1
        numbers: list[int] = []
        for item in batch:
            entry = _serialize(item)
            key = _key(entry)
            if key not in index:
                index[key] = next_n
                registered.append({**entry, "n": next_n})
                next_n += 1
            numbers.append(index[key])
        return RegisteredSources(numbers=numbers, sources=registered)

    @staticmethod
    def to_sources(sources: Iterable[Mapping[str, Any]]) -> tuple[Source, ...]:
        """Convierte la lista del estado en ``Source`` ordenados por ``n``.

        Es la forma que persiste el servicio hoy (``asdict`` sin ``n``): el orden de
        la tupla reproduce la numeración, así que la cita ``[n]`` es ``sources[n-1]``.
        Los campos ausentes toman su valor por defecto y los desconocidos se ignoran.
        """

        ordered = sorted(sources, key=lambda entry: int(entry.get("n", 0)))
        return tuple(_source(entry) for entry in ordered)


def _serialize(item: SourceInput) -> dict[str, Any]:
    """Forma JSON de una fuente, normalizada a los campos de ``Source``."""

    source = item if isinstance(item, Source) else _source(item)
    return asdict(source)


def _source(payload: Mapping[str, Any]) -> Source:
    """Reconstruye un ``Source`` tolerando campos ausentes, extra o mal tipados."""

    values: dict[str, Any] = {}
    for field in _FIELDS:
        default = "" if field.default is MISSING else field.default
        raw = payload.get(field.name, default)
        if field.name == "score":
            values[field.name] = float(raw if raw is not None else 0.0)
        else:
            values[field.name] = str(raw if raw is not None else default)
    return Source(**values)


def _key(entry: Mapping[str, Any]) -> tuple[str, ...]:
    """Huella de procedencia para deduplicar."""

    chunk_id = str(entry.get("chunk_id") or "")
    if chunk_id:
        return ("chunk", chunk_id)
    url = str(entry.get("url") or "")
    title = str(entry.get("title") or "")
    if url or title:
        return ("url", url, title)
    return (
        "content",
        str(entry.get("slug") or ""),
        str(entry.get("section") or ""),
        str(entry.get("excerpt") or ""),
    )
