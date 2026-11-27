"""Para qué vasos sirve una tapa, leído de su nombre (ensayo del 2026-11-27).

El catálogo no trae la capacidad de las tapas en ``capacity_oz``: la declara el nombre
(«Tapa traslúcida para vaso rígido 3,5 a 7 oz Wau», «Tapa viajera 8 y 12 oz para vaso
Bioform», «Tapa viajera 16 oz para vaso Bioform»). Sin esto el motor no encontraba ninguna tapa
al pedir una capacidad y el flujo agregaba al carrito una tapa que no le servía al vaso.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

__all__ = ["lid_brand", "lid_capacities", "lid_fits", "lid_fits_cup"]

_NUMBER: Final = r"(\d+(?:[.,]\d+)?)"
_RANGE: Final = re.compile(_NUMBER + r"\s*a\s*" + _NUMBER + r"\s*oz\b", re.IGNORECASE)
_PAIR: Final = re.compile(_NUMBER + r"\s*y\s*" + _NUMBER + r"\s*oz\b", re.IGNORECASE)
_ONE: Final = re.compile(_NUMBER + r"\s*oz\b", re.IGNORECASE)
# «para vaso Bioform», «para vasos Gold»: la línea de vasos va con mayúscula; «para vaso
# rígido» describe el vaso, no lo nombra.
_FOR_LINE: Final = re.compile(r"\bpara\s+vasos?\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)")
# Tolerancia al comparar una capacidad exacta («16 oz») con la del vaso.
TOLERANCE_OZ: Final = 0.5


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def lid_capacities(name: str) -> tuple[float, float] | frozenset[float] | None:
    """Capacidades que cubre la tapa: un rango ``(mín, máx)``, un conjunto o ``None``.

    «3,5 a 7 oz» → ``(3.5, 7.0)``; «8 y 12 oz» → ``{8.0, 12.0}``; «16 oz» → ``{16.0}``. Sin
    capacidad en el nombre devuelve ``None``: no se sabe y no se descarta.
    """

    found = _RANGE.search(name)
    if found is not None:
        low, high = sorted((_number(found.group(1)), _number(found.group(2))))
        return (low, high)
    found = _PAIR.search(name)
    if found is not None:
        return frozenset({_number(found.group(1)), _number(found.group(2))})
    values = frozenset(_number(match) for match in _ONE.findall(name))
    return values or None


def lid_fits(name: str, capacity_oz: float | None) -> bool | None:
    """La tapa sirve para un vaso de ``capacity_oz``; ``None`` si no se puede saber."""

    if capacity_oz is None:
        return None
    covered = lid_capacities(name)
    if covered is None:
        return None
    if isinstance(covered, tuple):
        low, high = covered
        return low - TOLERANCE_OZ <= capacity_oz <= high + TOLERANCE_OZ
    return any(abs(value - capacity_oz) <= TOLERANCE_OZ for value in covered)


def lid_brand(name: str) -> str | None:
    """Línea de vasos que nombra la tapa («para vaso Bioform» → «Bioform»), o ``None``."""

    found = _FOR_LINE.search(name)
    return found.group(1) if found is not None else None


def lid_fits_cup(lid_name: str, cups: Iterable[tuple[str, float | None]]) -> bool:
    """La tapa le sirve a alguno de los vasos ``(nombre, capacidad)``; sin vasos, sí.

    No sirve si declara una capacidad que ningún vaso tiene o si nombra una línea de vasos
    («para vaso Bioform») que no está en el nombre de ningún vaso. Lo que no se puede saber
    (sin capacidad en el nombre o en el vaso) no la descarta.
    """

    known = list(cups)
    if not known:
        return True
    line = lid_brand(lid_name)
    for cup_name, capacity in known:
        if line is not None and line.lower() not in cup_name.lower():
            continue
        if lid_fits(lid_name, capacity) is not False:
            return True
    return False
