"""``fecha_actual``: la fecha de hoy en Colombia, si es día hábil y el próximo día hábil.

Spec 045 (contrato 17, T079; research R11). Usa el reloj único ``_now_bogota`` (el mismo de
``_today`` de ``cotizar`` y de ``consultar_cotizacion``, así que nunca se contradicen cerca
de la medianoche) y el calendario de festivos de Colombia de ``quotation/calendar.py``
(``is_business_day``, ``next_business_day``, ``colombian_holidays``). Es determinista, no
consulta la red ni el índice y no tiene argumentos: la zona es siempre ``America/Bogota``
y nada que escriba el modelo la cambia.

Registra una sola fuente ``reloj:{fecha}`` con ``source_type="sistema"`` (ni oficial ni de
terceros: reloj del servidor y calendario de festivos, sin URL) cuyo excerpt contiene
literalmente cada valor que devuelve, con la fecha también en letras (día de la semana y
mes), porque la compuerta trata fechas y meses como datos y sin fuente abstendría.

No importa el registro: ``registry.py`` la incorpora como ``ToolSpec``.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _Draft,
    _now_bogota,
    _Sources,
)
from carvajal.quotation.calendar import (
    colombian_holidays,
    is_business_day,
    next_business_day,
)

__all__ = [
    "DESCRIPTION",
    "SOURCE_TYPE",
    "TIME_ZONE",
    "FechaActualArgs",
    "run",
    "status",
]

DESCRIPTION = (
    "Da la fecha de hoy en Colombia, el día de la semana, si es día hábil o festivo y el "
    "próximo día hábil. Úsala para: preguntas sobre qué día es hoy o si hoy o mañana se "
    "atiende. No la uses para: plazos de entrega o vigencia de una cotización (usa cotizar)."
)

SOURCE_TYPE = "sistema"
TIME_ZONE = "America/Bogota"
_WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
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


class FechaActualArgs(BaseModel):
    """Sin argumentos: la fecha sale siempre del reloj del servidor, en la hora de Colombia."""

    model_config = ConfigDict(extra="ignore")


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado fija: nunca interpola argumentos ni valores (FR-018)."""

    return "Estoy revisando la fecha de hoy…"


def _weekday(day: date) -> str:
    return _WEEKDAYS[day.weekday()]


def _in_words(day: date) -> str:
    """«martes 24 de noviembre de 2026»: la forma en que el modelo la escribirá."""

    return f"{_weekday(day)} {day.day} de {_MONTHS[day.month - 1]} de {day.year}"


def _yes_no(value: bool) -> str:
    return "sí" if value else "no"


def _excerpt(today: date, business: bool, holiday: bool, following: date) -> str:
    return (
        f"Fecha de hoy en Colombia (zona horaria {TIME_ZONE}): {_in_words(today)} "
        f"({today.isoformat()}). Es día hábil: {_yes_no(business)}. "
        f"Es festivo en Colombia: {_yes_no(holiday)}. "
        f"Próximo día hábil: {_in_words(following)} ({following.isoformat()}). "
        "Origen: reloj del servidor y calendario de festivos de Colombia; no cuentan como "
        "hábiles los sábados, los domingos ni los festivos."
    )


def run(args: FechaActualArgs, deps: ToolDeps, context: ToolContext, sources: _Sources) -> _Draft:
    """Fecha de hoy en Bogotá con su condición de hábil o festivo y su fuente ``sistema``."""

    try:
        today = _now_bogota(deps).date()
        business = is_business_day(today)
        holiday = today in colombian_holidays(today.year)
        following = next_business_day(today)
    except (OverflowError, ValueError):
        # Solo con un reloj fuera de rango (año 9999): se declara, nunca se estima.
        return _Draft("error", "No pude leer la fecha del reloj del servidor.")
    fecha = today.isoformat()
    [number] = sources.add(
        [
            {
                "chunk_id": f"reloj:{fecha}",
                "slug": "reloj",
                "section": "sistema",
                "layer": CORE_LAYER,
                "score": 1.0,
                "excerpt": _excerpt(today, business, holiday, following),
                "url": "",
                "title": "Reloj y calendario de Colombia",
                "source_type": SOURCE_TYPE,
            }
        ]
    )
    return _Draft(
        "ok",
        f"Hoy es {_in_words(today)} ({fecha}) en Colombia; "
        f"{'es' if business else 'no es'} día hábil"
        f"{' (festivo)' if holiday else ''}. "
        f"Próximo día hábil: {_in_words(following)} ({following.isoformat()}).",
        data={
            "n": number,
            "fecha": fecha,
            "dia_semana": _weekday(today),
            "es_habil": business,
            "es_festivo": holiday,
            "proximo_dia_habil": following.isoformat(),
            "zona": TIME_ZONE,
        },
    )
