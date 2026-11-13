"""Herramienta estructurada y determinista sobre indicadores del corpus.

``data/indicators.json`` es un extracto versionado de ``data/gold``: cada valor
conserva la cita textual y la URL del documento del que salió. La herramienta
no consulta la red ni el LLM; devuelve el registro o declara su ausencia.
"""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

__all__ = [
    "IndicatorQuery",
    "IndicatorResult",
    "find_indicator",
    "indicator_lookup",
    "known_names",
    "resolve_indicator_name",
]

DEFAULT_PATH = Path("data") / "indicators.json"
_PERIOD = re.compile(r"^\d{4}(-(?:0[1-9]|1[0-2]))?$")


class IndicatorQuery(BaseModel):
    """Argumentos tipados: el nombre se resuelve contra alias conocidos."""

    name: str = Field(description="Indicador: ingresos, ebitda, utilidad_neta, activos, empleados…")
    period: str = Field(description="Año (2024) o año-mes (2025-09)")

    @field_validator("name")
    @classmethod
    def _nombre_valido(cls, value: str) -> str:
        resolved = _resolve_name(value)
        if resolved is None:
            raise ValueError(
                f"Indicador desconocido: {value!r}. Válidos: {', '.join(sorted(known_names()))}."
            )
        return resolved

    @field_validator("period")
    @classmethod
    def _periodo_valido(cls, value: str) -> str:
        normalized = value.strip()
        if not _PERIOD.match(normalized):
            raise ValueError(f"Período inválido: {value!r}; usa 2024 o 2025-09.")
        return normalized


class IndicatorResult(BaseModel):
    """Resultado estable: siempre incluye ``found`` y el par consultado."""

    found: bool
    name: str
    period: str
    value: float | None = None
    unit: str = ""
    display: str = ""
    source_document: str = ""
    source_url: str = ""
    quote: str = ""
    message: str = ""


@cache
def _load(path: str = str(DEFAULT_PATH)) -> tuple[dict[str, Any], ...]:
    """Carga y valida el JSON una sola vez por proceso."""

    file = Path(path)
    payload = json.loads(file.read_text(encoding="utf-8"))
    records = tuple(payload.get("indicators", ()))
    if not records:
        raise ValueError(f"{file}: no contiene indicadores.")
    return records


def known_names() -> set[str]:
    """Nombres canónicos que la herramienta reconoce."""

    return {str(record["name"]) for record in _load()}


def resolve_indicator_name(value: str) -> str | None:
    """Nombre canónico del indicador o ``None`` si no se reconoce."""

    return _resolve_name(value)


def _resolve_name(value: str) -> str | None:
    """Resuelve nombre canónico o alias sin distinguir mayúsculas/acentos."""

    normalized = _normalize(value)
    for record in _load():
        names = [str(record["name"]), *(str(alias) for alias in record.get("aliases", ()))]
        if any(_normalize(name) == normalized for name in names):
            return str(record["name"])
    return None


def _normalize(value: str) -> str:
    normalized = value.strip().lower().replace("_", " ")
    for source, target in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        normalized = normalized.replace(source, target)
    return " ".join(normalized.split())


def find_indicator(name: str, period: str) -> IndicatorResult:
    """Busca un registro exacto; la ausencia no es un error."""

    query = IndicatorQuery(name=name, period=period)
    for record in _load():
        if record["name"] == query.name and record["period"] == query.period:
            return IndicatorResult(
                found=True,
                name=query.name,
                period=query.period,
                value=float(record["value"]),
                unit=str(record["unit"]),
                display=str(record["display"]),
                source_document=str(record["source_document"]),
                source_url=str(record["source_url"]),
                quote=str(record["quote"]),
            )
    return IndicatorResult(
        found=False,
        name=query.name,
        period=query.period,
        message=(
            f"No hay registro de {query.name} para {query.period} en el corpus. "
            "No se estima ni se interpola."
        ),
    )


@tool("indicator_lookup", args_schema=IndicatorQuery)
def indicator_lookup(name: str, period: str) -> dict[str, Any]:
    """Consulta un indicador de Carvajal por nombre y período.

    Devuelve el valor con su unidad, la cita textual y la URL del documento.
    Si la combinación no existe, devuelve ``found=False`` con un mensaje.
    """

    return find_indicator(name, period).model_dump()
