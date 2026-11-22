"""Festivos de Colombia y ventanas de entrega en días hábiles.

La demo calcula plazos con el calendario colombiano: fines de semana y los 18 festivos
del año no cuentan. Los festivos se derivan por algoritmo (Pascua incluida), sin tablas
que se queden viejas.
"""

from datetime import date, timedelta
from functools import cache

__all__ = [
    "business_days_after",
    "colombian_holidays",
    "easter_sunday",
    "is_business_day",
    "next_business_day",
    "window",
]


def easter_sunday(year: int) -> date:
    """Domingo de Pascua (algoritmo gregoriano anónimo)."""

    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _next_monday(moment: date) -> date:
    """Ley Emiliani: el festivo se traslada al lunes siguiente."""

    return moment + timedelta(days=(7 - moment.weekday()) % 7)


def colombian_holidays(year: int) -> frozenset[date]:
    """Los 18 festivos nacionales de Colombia para ``year``."""

    return _holidays(year)


@cache
def _holidays(year: int) -> frozenset[date]:

    easter = easter_sunday(year)
    fixed = {
        date(year, 1, 1),
        date(year, 5, 1),
        date(year, 7, 20),
        date(year, 8, 7),
        date(year, 12, 8),
        date(year, 12, 25),
    }
    movable = {
        date(year, 1, 6),
        date(year, 3, 19),
        date(year, 6, 29),
        date(year, 8, 15),
        date(year, 10, 12),
        date(year, 11, 1),
        date(year, 11, 11),
    }
    easter_related = {
        easter - timedelta(days=3),  # Jueves Santo
        easter - timedelta(days=2),  # Viernes Santo
        easter + timedelta(days=43),  # Ascensión
        easter + timedelta(days=64),  # Corpus Christi
        easter + timedelta(days=71),  # Sagrado Corazón
    }
    easter_related = {
        _next_monday(easter + timedelta(days=43)),  # Ascensión
        _next_monday(easter + timedelta(days=64)),  # Corpus Christi
        _next_monday(easter + timedelta(days=71)),  # Sagrado Corazón
    }
    holy_week = {easter - timedelta(days=3), easter - timedelta(days=2)}
    moved = {_next_monday(day) for day in movable}
    return frozenset(fixed | moved | easter_related | holy_week)


def is_business_day(day: date) -> bool:
    """Día con atención: ni fin de semana ni festivo."""

    return day.weekday() < 5 and day not in colombian_holidays(day.year)


def next_business_day(day: date) -> date:
    """El primer día hábil estrictamente posterior a ``day``."""

    candidate = day + timedelta(days=1)
    while not is_business_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def business_days_after(day: date, count: int) -> date:
    """Suma ``count`` días hábiles, sin contar ``day``."""

    if count < 1:
        raise ValueError("Los días hábiles deben ser al menos 1.")
    candidate = day
    for _ in range(count):
        candidate = next_business_day(candidate)
    return candidate


def window(start: date, days_min: int, days_max: int) -> tuple[date, date]:
    """Ventana estimada: del primer al último día hábil pedido."""

    if days_min < 1 or days_max < days_min:
        raise ValueError("Ventana inválida: revisa los días mínimo y máximo.")
    return business_days_after(start, days_min), business_days_after(start, days_max)
