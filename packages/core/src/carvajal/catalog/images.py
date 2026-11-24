"""Fotos del catálogo: ``imagenes.csv`` + ``imagenes/{sku}.jpg`` (``scripts/curar_imagenes_co.py``).

Las fotos son opcionales: un SKU sin foto se cotiza igual y el PDF no deja un hueco. La
búsqueda mira primero el catálogo vivo (``data/catalog/empaques``, el volumen en
producción) y después la copia de aprovisionamiento de la imagen Docker
(``seed-data/catalog/empaques``), para que una foto nueva sirva aunque el volumen aún no
se haya resembrado. Solo se aceptan nombres de archivo simples del manifiesto: nunca una
ruta que salga de la carpeta.
"""

import csv
from dataclasses import dataclass
from functools import cache
from pathlib import Path

__all__ = ["IMAGE_DIRS", "CatalogImage", "catalog_image", "clear_image_cache"]

IMAGE_DIRS: tuple[Path, ...] = (
    Path("data") / "catalog" / "empaques",
    Path("seed-data") / "catalog" / "empaques",
)


@dataclass(frozen=True)
class CatalogImage:
    """Foto de un SKU con su procedencia."""

    sku: str
    path: Path
    source_url: str
    source_type: str
    captured_at: str


@cache
def _manifest(base: Path) -> dict[str, CatalogImage]:
    manifest = base / "imagenes.csv"
    if not manifest.is_file():
        return {}
    images: dict[str, CatalogImage] = {}
    with manifest.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            sku = (row.get("sku") or "").strip()
            name = (row.get("archivo") or "").strip()
            if not sku or not name or Path(name).name != name:
                continue
            path = base / "imagenes" / name
            if path.suffix.lower() in {".jpg", ".jpeg", ".png"} and path.is_file():
                images[sku] = CatalogImage(
                    sku=sku,
                    path=path,
                    source_url=(row.get("fuente_url") or "").strip(),
                    source_type=(row.get("tipo_fuente") or "oficial").strip(),
                    captured_at=(row.get("capturada_el") or "").strip(),
                )
    return images


def catalog_image(sku: str, dirs: tuple[Path, ...] = IMAGE_DIRS) -> CatalogImage | None:
    """Foto del SKU o ``None``; el primer directorio que la tenga gana."""

    for base in dirs:
        image = _manifest(base).get(sku)
        if image is not None:
            return image
    return None


def clear_image_cache() -> None:
    """Olvida los manifiestos leídos (tras una resiembra o en pruebas)."""

    _manifest.cache_clear()
