"""Capa institucional curada para las herramientas de la spec 045 (research R12, T022).

Lee, una sola vez por ruta, dos fuentes que ya existen en el repositorio:

- ``data/institucional/empresas.json``: unidades y marcas del grupo, cada una con su cita
  literal de una página del núcleo y la URL de ese slug en ``index.jsonl`` (lo verifica
  ``tests/test_institutional_data.py``);
- ``data/gold/historia.md``: los hitos de la historia oficial, partidos por los
  encabezados ``## AAAA``, con la URL de ``historia`` en ``index.jsonl``.

Nunca lanza: si falta un archivo o no se puede leer, el estado de esa parte es
``no_disponible`` y las herramientas (``empresas_del_grupo``, ``historia_carvajal``) lo
convierten en un ``error`` declarado. No añade hechos: todo texto que devuelve es literal.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

import structlog

from carvajal.domain.frontmatter import strip_front_matter

__all__ = [
    "AVAILABLE",
    "DEFAULT_COMPANIES",
    "DEFAULT_GOLD",
    "HISTORY_SLUG",
    "UNAVAILABLE",
    "Alias",
    "Company",
    "InstitutionalData",
    "Milestone",
    "load_institutional",
    "parse_milestones",
]

logger = structlog.get_logger(__name__)

DEFAULT_COMPANIES: Final = Path("data") / "institucional" / "empresas.json"
DEFAULT_GOLD: Final = Path("data") / "gold"
HISTORY_SLUG: Final = "historia"
AVAILABLE: Final = "ok"
UNAVAILABLE: Final = "no_disponible"

Status = Literal["ok", "no_disponible"]
_YEAR_HEADING = re.compile(r"^##\s+(\d{4})\s*$")
_HEADING = re.compile(r"^#{1,6}\s")


@dataclass(frozen=True)
class Alias:
    """Otro nombre de una empresa (por ejemplo, PROPAL), con su cita literal."""

    name: str
    quote: str


@dataclass(frozen=True)
class Company:
    """Unidad o marca del grupo, con la cita literal que la respalda."""

    name: str
    kind: Literal["unidad", "marca"]
    slug: str
    url: str
    quote: str
    aliases: tuple[Alias, ...] = ()


@dataclass(frozen=True)
class Milestone:
    """Hito de la historia oficial: el año del encabezado y su texto literal."""

    year: int
    text: str


@dataclass(frozen=True)
class InstitutionalData:
    """Empresas e hitos cargados, con el estado de cada parte.

    ``companies_status`` y ``history_status`` valen ``ok`` o ``no_disponible``; con
    ``no_disponible`` la tupla correspondiente está vacía.
    """

    companies: tuple[Company, ...] = ()
    companies_status: Status = UNAVAILABLE
    companies_version: str = ""
    milestones: tuple[Milestone, ...] = ()
    history_status: Status = UNAVAILABLE
    history_url: str = ""


def parse_milestones(markdown: str) -> tuple[Milestone, ...]:
    """Hitos de ``historia.md``: el texto entre un ``## AAAA`` y el siguiente encabezado.

    Los subtítulos decorativos que siguen al año antes de su texto («## Nuestra»,
    «## Historia» en 1904) se saltan; un encabezado que no es un año y llega después del
    texto («## EL LEGADO») cierra el hito, así que lo que sigue no se pega al último año.
    Un año sin texto (1962 en la captura actual) no se devuelve: no hay nada literal que
    citar y la herramienta ofrece los años cercanos.
    """

    milestones: list[Milestone] = []
    year: int | None = None
    lines: list[str] = []

    def flush() -> None:
        text = "\n".join(lines).strip()
        if year is not None and text:
            milestones.append(Milestone(year=year, text=text))

    for raw in strip_front_matter(markdown).splitlines():
        line = raw.strip()
        heading = _YEAR_HEADING.match(line)
        if heading:
            flush()
            year = int(heading.group(1))
            lines = []
            continue
        if not line:
            continue
        if _HEADING.match(line):
            if lines:
                flush()
                year = None
                lines = []
            continue
        if year is not None:
            lines.append(line)
    flush()
    return tuple(milestones)


def _index_url(gold: Path, slug: str) -> str:
    """URL de ``slug`` en ``index.jsonl`` (vacía si no está)."""

    for line in (gold / "index.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("slug") == slug:
            return str(row.get("url") or "")
    return ""


def _company(row: dict[str, Any]) -> Company:
    kind = str(row["tipo"])
    if kind not in ("unidad", "marca"):
        raise ValueError(f"tipo desconocido: {kind!r}")
    return Company(
        name=str(row["nombre"]),
        kind="unidad" if kind == "unidad" else "marca",
        slug=str(row["slug"]),
        url=str(row["url"]),
        quote=str(row["cita"]),
        aliases=tuple(
            Alias(name=str(alias["nombre"]), quote=str(alias["cita"]))
            for alias in row.get("alias") or []
        ),
    )


def _load_companies(path: Path) -> tuple[tuple[Company, ...], Status, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        companies = tuple(_company(row) for row in data["items"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.warning("institutional.companies_unavailable", path=str(path), error=repr(error))
        return (), UNAVAILABLE, ""
    return companies, AVAILABLE, str(data.get("version") or "")


def _load_history(gold: Path) -> tuple[tuple[Milestone, ...], Status, str]:
    try:
        markdown = (gold / f"{HISTORY_SLUG}.md").read_text(encoding="utf-8")
        url = _index_url(gold, HISTORY_SLUG)
    except (OSError, ValueError) as error:
        logger.warning("institutional.history_unavailable", path=str(gold), error=repr(error))
        return (), UNAVAILABLE, ""
    milestones = parse_milestones(markdown)
    if not milestones or not url:
        logger.warning("institutional.history_unavailable", path=str(gold), error="vacío")
        return (), UNAVAILABLE, ""
    return milestones, AVAILABLE, url


@lru_cache(maxsize=4)
def _cached(companies: str, gold: str) -> InstitutionalData:
    rows, companies_status, version = _load_companies(Path(companies))
    milestones, history_status, url = _load_history(Path(gold))
    return InstitutionalData(
        companies=rows,
        companies_status=companies_status,
        companies_version=version,
        milestones=milestones,
        history_status=history_status,
        history_url=url,
    )


def load_institutional(
    companies: Path = DEFAULT_COMPANIES, gold: Path = DEFAULT_GOLD
) -> InstitutionalData:
    """Empresas e hitos, cargados una vez por par de rutas; nunca lanza."""

    return _cached(str(companies), str(gold))
