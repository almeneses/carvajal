"""Carga y validación del catálogo curado de empaques.

Los tres CSV de ``data/catalog/empaques/`` se leen una sola vez por proceso, como
``data/indicators.json``. Una fila inválida detiene la carga con el archivo y la
línea exactos: un catálogo a medias recomendaría con datos incompletos.
"""

import csv
from collections.abc import Sequence
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


def _check_header[T: BaseModel](path: Path, model: type[T], header: Sequence[str]) -> None:
    """La cabecera debe listar exactamente los campos del modelo, en cualquier orden."""

    missing = [name for name in model.model_fields if name not in header]
    extra = [name for name in header if name not in model.model_fields]
    if not missing and not extra:
        return
    details: list[str] = []
    if missing:
        details.append(f"faltan {', '.join(missing)}")
    if extra:
        details.append(f"sobran {', '.join(extra)}")
    raise CatalogError(f"{path.name}: cabecera inválida: {'; '.join(details)}.")


def _read[T: BaseModel](path: Path, model: type[T]) -> tuple[tuple[int, T], ...]:
    """Valida la cabecera y cada fila y devuelve su línea física en el archivo.

    Se usa ``reader.line_num`` en vez de contar filas lógicas: una celda entrecomillada
    con saltos de línea ocupa varias líneas del archivo y el mensaje debe apuntar a una
    línea real (la cabecera es la línea 1).
    """

    if not path.is_file():
        raise CatalogError(f"{path}: no existe el archivo del catálogo.")
    rows: list[tuple[int, T]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _check_header(path, model, reader.fieldnames or [])
        for raw in reader:
            line = reader.line_num
            try:
                rows.append((line, model.model_validate(raw)))
            except ValidationError as error:
                first = error.errors()[0]
                field = ".".join(str(part) for part in first["loc"]) or "fila"
                raise CatalogError(f"{path.name}:{line}: {field}: {first['msg']}") from error
    if not rows:
        raise CatalogError(f"{path}: el archivo no tiene filas.")
    return tuple(rows)


@cache
def load_catalog(directory: str = str(DEFAULT_DIR)) -> Catalog:
    """Carga los tres CSV y verifica claves únicas.

    El resultado se cachea por directorio durante la vida del proceso; las pruebas
    limpian la caché con ``load_catalog.cache_clear()``.
    """

    base = Path(directory)
    references = _read(base / REFERENCES, Reference)
    materials = _read(base / MATERIALS, MaterialRule)
    distributors = _read(base / DISTRIBUTORS, Distributor)

    seen: dict[tuple[str, str], int] = {}
    for line, reference in references:
        key = (reference.sku, reference.country)
        if key in seen:
            raise CatalogError(
                f"{REFERENCES}:{line}: SKU repetido {reference.sku} en {reference.country} "
                f"(ya en la línea {seen[key]})."
            )
        seen[key] = line

    by_material: dict[str, MaterialRule] = {}
    first_line: dict[str, int] = {}
    for line, rule in materials:
        if rule.material in by_material:
            raise CatalogError(
                f"{MATERIALS}:{line}: material repetido {rule.material} "
                f"(ya en la línea {first_line[rule.material]})."
            )
        by_material[rule.material] = rule
        first_line[rule.material] = line

    return Catalog(
        references=tuple(reference for _, reference in references),
        materials=by_material,
        distributors=tuple(distributor for _, distributor in distributors),
    )
