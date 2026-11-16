"""Carga y validación del catálogo curado de empaques.

Los tres CSV de ``data/catalog/empaques/`` se leen una sola vez por proceso, como
``data/indicators.json``. Una fila inválida detiene la carga con el archivo y la
línea exactos: un catálogo a medias recomendaría con datos incompletos.
"""

import csv
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ValidationError

from carvajal.catalog.models import Distributor, MaterialRule, Reference

__all__ = ["DEFAULT_DIR", "Catalog", "CatalogError", "load_catalog"]

DEFAULT_DIR = Path("data") / "catalog" / "empaques"
REFERENCES = "referencias.csv"
MATERIALS = "materiales.csv"
DISTRIBUTORS = "distribuidores.csv"


class CatalogError(ValueError):
    """El catálogo no se puede cargar; el mensaje nombra archivo y línea."""


@dataclass(frozen=True)
class Catalog:
    """Catálogo validado y listo para las herramientas comerciales."""

    references: tuple[Reference, ...]
    materials: dict[str, MaterialRule]
    distributors: tuple[Distributor, ...]


def _read[T: BaseModel](path: Path, model: type[T]) -> tuple[T, ...]:
    """Valida cada fila; la línea reportada es la del archivo (cabecera = 1)."""

    if not path.is_file():
        raise CatalogError(f"{path}: no existe el archivo del catálogo.")
    rows: list[T] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for line, raw in enumerate(csv.DictReader(handle), start=2):
            try:
                rows.append(model.model_validate(raw))
            except ValidationError as error:
                first = error.errors()[0]
                field = ".".join(str(part) for part in first["loc"]) or "fila"
                raise CatalogError(f"{path.name}:{line}: {field}: {first['msg']}") from error
    if not rows:
        raise CatalogError(f"{path}: el archivo no tiene filas.")
    return tuple(rows)


@cache
def load_catalog(directory: str = str(DEFAULT_DIR)) -> Catalog:
    """Carga los tres CSV y verifica claves únicas."""

    base = Path(directory)
    references = _read(base / REFERENCES, Reference)
    materials = _read(base / MATERIALS, MaterialRule)
    distributors = _read(base / DISTRIBUTORS, Distributor)

    seen: set[tuple[str, str]] = set()
    for reference in references:
        key = (reference.sku, reference.country)
        if key in seen:
            raise CatalogError(
                f"{REFERENCES}: SKU repetido {reference.sku} en {reference.country}."
            )
        seen.add(key)

    by_material: dict[str, MaterialRule] = {}
    for rule in materials:
        if rule.material in by_material:
            raise CatalogError(f"{MATERIALS}: material repetido {rule.material}.")
        by_material[rule.material] = rule

    return Catalog(references=references, materials=by_material, distributors=distributors)
