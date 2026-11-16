"""Registro del corpus: los ``index.jsonl`` de todas sus áreas.

El registro no añade un artefacto nuevo: se proyecta desde los índices que ya
publica cada área —el núcleo en ``<datos>/gold`` y cada capa extendida en
``<datos>/external/*/gold``— para deduplicar por huella y decidir si un
documento cambió. La escritura sigue siendo única, atómica y bloqueada en
:func:`carvajal.gold.merge_index`.

La prioridad entre áreas es el orden de carga: ante el mismo ``slug`` o la misma
huella en dos áreas, gana la primera (el núcleo) y la sombreada se conserva en
``shadowed`` para poder informarla.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from carvajal.gold import read_index

INDEX_NAME = "index.jsonl"
"""Nombre del índice que publica cada área del corpus."""

CORE_AREA = Path("gold")
"""Ruta del área núcleo dentro del directorio de datos."""

EXTERNAL_AREA = Path("external")
"""Ruta de las capas extendidas dentro del directorio de datos."""


@dataclass(frozen=True)
class IndexedDocument:
    """Documento del registro: su entrada del índice y el área que lo publica."""

    slug: str
    area: Path
    entry: Mapping[str, str | int]

    @property
    def source_key(self) -> str:
        """Fuente canónica del documento; vacía en entradas anteriores a la 037."""

        return str(self.entry.get("source_key") or "")

    @property
    def raw_hash(self) -> str:
        """Huella SHA-256 de los bytes originales; vacía si no se conoce."""

        return str(self.entry.get("raw_hash") or "")

    @property
    def content_hash(self) -> str:
        """Huella SHA-256 del texto normalizado, que decide la versión vigente."""

        return str(self.entry.get("hash") or "")

    @property
    def layer(self) -> str:
        """Capa del corpus a la que pertenece el documento."""

        return str(self.entry.get("layer") or "core")


class Registry:
    """Vista de solo lectura del corpus, con búsqueda por identidad y huellas."""

    def __init__(
        self,
        documents: Sequence[IndexedDocument],
        shadowed: Sequence[IndexedDocument] = (),
    ) -> None:
        self._documents = tuple(documents)
        self._shadowed = tuple(shadowed)
        self._by_slug = {document.slug: document for document in self._documents}
        self._by_source_key: dict[str, IndexedDocument] = {}
        self._by_raw_hash: dict[str, IndexedDocument] = {}
        self._by_content_hash: dict[str, IndexedDocument] = {}
        for document in self._documents:
            if document.source_key:
                self._by_source_key.setdefault(document.source_key, document)
            if document.raw_hash:
                self._by_raw_hash.setdefault(document.raw_hash, document)
            if document.content_hash:
                self._by_content_hash.setdefault(document.content_hash, document)

    def __iter__(self) -> Iterator[IndexedDocument]:
        return iter(self._documents)

    def __len__(self) -> int:
        return len(self._documents)

    @property
    def documents(self) -> tuple[IndexedDocument, ...]:
        """Documentos vigentes, en el orden de carga de las áreas."""

        return self._documents

    @property
    def shadowed(self) -> tuple[IndexedDocument, ...]:
        """Entradas ignoradas por repetir el ``slug`` de un área anterior."""

        return self._shadowed

    @property
    def slugs(self) -> frozenset[str]:
        """Todos los slugs publicados."""

        return frozenset(self._by_slug)

    def by_slug(self, slug: str) -> IndexedDocument | None:
        """Documento con ese ``slug``, si existe."""

        return self._by_slug.get(slug)

    def by_source_key(self, source_key: str) -> IndexedDocument | None:
        """Documento cuya fuente canónica es ``source_key``."""

        return self._by_source_key.get(source_key)

    def by_raw_hash(self, raw_hash: str) -> IndexedDocument | None:
        """Documento cuyos bytes originales coinciden con ``raw_hash``."""

        return self._by_raw_hash.get(raw_hash)

    def by_content_hash(self, content_hash: str) -> IndexedDocument | None:
        """Documento cuyo texto vigente coincide con ``content_hash``."""

        return self._by_content_hash.get(content_hash)

    def slug_owners(self) -> dict[str, str]:
        """Mapa ``slug → source_key`` para asignar slugs sin renombrar existentes."""

        return {
            document.slug: document.source_key
            for document in self._documents
            if document.source_key
        }


def corpus_areas(data_dir: str | Path) -> tuple[Path, ...]:
    """Áreas del corpus en orden de prioridad: el núcleo y luego las extendidas.

    ``data_dir`` es el directorio que contiene ``gold/`` y ``external/``. Solo se
    devuelven las áreas que publican un ``index.jsonl``.
    """

    root = Path(data_dir)
    candidates = [root / CORE_AREA]
    external = root / EXTERNAL_AREA
    if external.is_dir():
        candidates.extend(
            sorted(child / "gold" for child in external.iterdir() if (child / "gold").is_dir())
        )
    return tuple(area for area in candidates if (area / INDEX_NAME).is_file())


def read_area(area: str | Path) -> tuple[IndexedDocument, ...]:
    """Lee el ``index.jsonl`` de un área; si no existe, devuelve una tupla vacía."""

    area_path = Path(area)
    entries = read_index(area_path / INDEX_NAME)
    return tuple(
        IndexedDocument(slug=slug, area=area_path, entry=entry)
        for slug, entry in sorted(entries.items())
    )


def load_registry(areas: Sequence[str | Path]) -> Registry:
    """Carga y fusiona las áreas indicadas, con prioridad de la primera a la última."""

    documents: list[IndexedDocument] = []
    shadowed: list[IndexedDocument] = []
    seen: set[str] = set()
    for area in areas:
        for document in read_area(area):
            if document.slug in seen:
                shadowed.append(document)
                continue
            seen.add(document.slug)
            documents.append(document)
    return Registry(documents, shadowed)
