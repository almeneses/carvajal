"""``indicadores_disponibles``: qué cifras financieras publicadas hay y de qué períodos.

Spec 045 (contrato 13, T078; research R10 y R11). Lee el mismo extracto versionado que
``indicador_financiero`` (``data/indicators.json`` vía ``carvajal.agent.tools._load``) y
devuelve, por indicador, su nombre canónico, sus alias, su unidad y sus períodos, **sin
valores**: el valor de cada uno lo da ``indicador_financiero`` con su cita textual. Es
determinista, no consulta la red ni el índice y no estima nada.

Registra una sola fuente ``tool:indicadores:catalogo`` cuyo excerpt enumera literalmente los
nombres, alias, unidades y períodos que devuelve (con el mes en letras para los períodos
año-mes, que la compuerta vigila) y los sitios de los documentos de origen. Es ``tercero``
salvo que todos los documentos sean de un dominio oficial de Carvajal.

No importa el registro: ``registry.py`` la incorpora como ``ToolSpec``.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _Draft,
    _is_official,
    _Sources,
)
from carvajal.agent.tools import _load

__all__ = [
    "CATALOG_CHUNK_ID",
    "DESCRIPTION",
    "IndicadoresDisponiblesArgs",
    "run",
    "status",
]

DESCRIPTION = (
    "Lista los indicadores financieros publicados de Carvajal con sus períodos, sin valores. "
    "Úsala para: saber qué cifras financieras puedes dar o de qué períodos hay datos. "
    "No la uses para: dar el valor de un indicador (usa indicador_financiero)."
)

CATALOG_CHUNK_ID = "tool:indicadores:catalogo"
_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


class IndicadoresDisponiblesArgs(BaseModel):
    """Sin argumentos: la herramienta siempre devuelve el catálogo completo."""

    model_config = ConfigDict(extra="ignore")


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado fija: nunca interpola argumentos ni valores (FR-018)."""

    return "Estoy revisando qué cifras financieras tengo…"


def _fold(value: str) -> str:
    return " ".join(value.replace("_", " ").lower().split())


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _period_label(period: str) -> str:
    """``2024`` queda igual; ``2024-09`` se lee también «septiembre de 2024»."""

    year, _, month = period.partition("-")
    if month.isdigit() and 1 <= int(month) <= len(_MONTHS):
        return f"{period} ({_MONTHS[int(month) - 1]} de {year})"
    return period


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def _catalog(records: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Indicadores agrupados (orden de aparición en el JSON) y URLs de origen sin repetir.

    Lanza ``KeyError`` si un registro no trae ``name``, ``period`` o ``unit``: un catálogo
    malformado se declara como error, nunca se completa.
    """

    grouped: dict[str, dict[str, list[str]]] = {}
    urls: list[str] = []
    for record in records:
        name = str(record["name"]).strip()
        period = str(record["period"]).strip()
        unit = str(record["unit"]).strip()
        if not name or not period:
            raise KeyError("name/period")
        entry = grouped.setdefault(name, {"alias": [], "unidad": [], "periodos": []})
        entry["alias"].extend(
            str(alias).strip()
            for alias in record.get("aliases", ())
            if _fold(str(alias)) != _fold(name)
        )
        entry["unidad"].append(unit)
        entry["periodos"].append(period)
        url = str(record.get("source_url") or "").strip()
        if url:
            urls.append(url)
    indicators = [
        {
            "nombre": name,
            "alias": _unique(entry["alias"]),
            "unidad": ", ".join(_unique(entry["unidad"])),
            "periodos": sorted(set(entry["periodos"])),
        }
        for name, entry in grouped.items()
    ]
    return indicators, _unique(urls)


def _excerpt(indicators: Sequence[Mapping[str, Any]], hosts: Sequence[str], kind: str) -> str:
    lines = []
    for item in indicators:
        name = str(item["nombre"])
        readable = name.replace("_", " ")
        # El nombre legible y el canónico, para que ambos tengan respaldo literal.
        details = [name] if readable != name else []
        if item["alias"]:
            details.append(f"alias: {', '.join(item['alias'])}")
        details.append(f"unidad: {item['unidad']}")
        periods = ", ".join(_period_label(period) for period in item["periodos"])
        lines.append(f"{readable} ({'; '.join(details)}): períodos {periods}.")
    origin = "oficiales" if kind == "oficial" else "de terceros"
    documents = f" Documentos de origen ({origin}): {', '.join(hosts)}." if hosts else ""
    return (
        "Indicadores financieros publicados de Carvajal con registro en el corpus "
        "(catálogo sin valores): " + " ".join(lines) + documents
    )


def run(
    args: IndicadoresDisponiblesArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    """Catálogo de indicadores y períodos con su fuente; nunca devuelve valores."""

    try:
        indicators, urls = _catalog(_load())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return _Draft(
            "error",
            "No tengo disponible el catálogo de indicadores financieros; no se consultó nada.",
        )
    if not indicators:
        return _Draft("empty", "No hay indicadores financieros publicados en el corpus.")
    kind = "oficial" if urls and all(_is_official(url) for url in urls) else "tercero"
    hosts = _unique([_host(url) for url in urls])
    [number] = sources.add(
        [
            {
                "chunk_id": CATALOG_CHUNK_ID,
                "slug": "indicators",
                "section": "prensa",
                "layer": CORE_LAYER,
                "score": 1.0,
                "excerpt": _excerpt(indicators, hosts, kind),
                "url": "",
                "title": "Indicadores financieros disponibles",
                "source_type": kind,
            }
        ]
    )
    periods = sum(len(item["periodos"]) for item in indicators)
    return _Draft(
        "ok",
        f"{len(indicators)} indicador(es) publicados con {periods} período(s), sin valores. "
        "Para el valor de uno, consulta indicador_financiero con su nombre y su período.",
        data={"n": number, "indicadores": indicators, "tipo_fuente": kind},
    )
