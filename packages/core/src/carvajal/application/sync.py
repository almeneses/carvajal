"""Sincronización del corpus: Gold → PostgreSQL → Qdrant.

Un único camino reconcilia lo que está en disco con los dos almacenes que
consultan la web y el chat. El servicio es incremental: compara la huella de
cada documento —texto y metadatos persistidos— con la que guardan PostgreSQL y
el índice, y solo importa e indexa los nuevos o cambiados. Con ``verify`` no
escribe: informa las diferencias y devuelve un reporte que la CLI traduce a
código de salida distinto de cero.

Las escrituras se serializan con :func:`carvajal.gold.corpus_lock` sobre la raíz
del corpus. El orden de bloqueo es siempre ``corpus_lock`` y después el bloqueo
interno del índice JSONL (``_index_lock``); nunca al revés.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from carvajal.config import LAYER_CORE, LAYER_EXTENDED
from carvajal.gold import corpus_lock

__all__ = [
    "CorpusLayer",
    "SyncDatabase",
    "SyncIndex",
    "SyncReport",
    "corpus_layers",
    "gold_hashes",
    "sync_corpus",
    "sync_fingerprint",
]


def sync_fingerprint(*, content_hash: str, title: str, section: str, source_type: str) -> str:
    """Huella de sincronización: el cuerpo y los metadatos que se persisten.

    El título, la sección y el tipo de fuente no viven en el cuerpo Gold, pero se
    guardan en PostgreSQL, viajan en el payload de Qdrant y se vectorizan con
    cada fragmento. Comparar solo la huella del texto dejaría sin reparar para
    siempre una corrección editorial de esos campos.
    """

    raw = "\x1f".join((content_hash, title, section, source_type))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CorpusLayer:
    """Capa lógica del corpus y los directorios Gold que la componen."""

    name: str
    directories: tuple[Path, ...]


@dataclass(frozen=True)
class SyncReport:
    """Resultado verificable de una sincronización o de una verificación."""

    new: int = 0
    updated: int = 0
    unchanged: int = 0
    pruned: int = 0
    indexed: int = 0
    documents: int = 0
    chunks: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
    differences: tuple[str, ...] = field(default_factory=tuple)
    verified: bool = False

    @property
    def changed(self) -> int:
        """Documentos nuevos o actualizados que se procesaron."""

        return self.new + self.updated

    @property
    def ok(self) -> bool:
        """La corrida no dejó errores ni diferencias pendientes."""

        return not self.errors and not self.differences


@runtime_checkable
class SyncDatabase(Protocol):
    """Lado PostgreSQL de la sincronización."""

    def hashes(self, layer: str) -> Mapping[str, str]:
        """Huella de sincronización por documento de la capa (texto y metadatos)."""

        ...

    def import_gold(
        self,
        layer: CorpusLayer,
        *,
        only: set[str] | None,
        prune: bool,
    ) -> tuple[int, int, int]:
        """Importa Gold y devuelve ``(documentos, fragmentos, podados)``."""

        ...

    def count(self, layer: str) -> int:
        """Cuenta los documentos de la capa en PostgreSQL."""

        ...


@runtime_checkable
class SyncIndex(Protocol):
    """Lado Qdrant de la sincronización."""

    def hashes(self, layer: str) -> Mapping[str, str]:
        """Huella de sincronización por documento de la capa en el índice."""

        ...

    def index_gold(
        self,
        layer: CorpusLayer,
        *,
        only: set[str] | None,
        prune: bool,
    ) -> tuple[int, int]:
        """Indexa Gold y devuelve ``(puntos indexados, documentos podados)``."""

        ...

    def count(self, layer: str) -> int:
        """Cuenta los documentos de la capa en el índice vectorial."""

        ...


def corpus_layers(
    gold_dir: Path,
    external_dir: Path,
    *,
    which: str = "core",
) -> tuple[CorpusLayer, ...]:
    """Resuelve las capas pedidas por la CLI a sus directorios Gold.

    ``which`` acepta ``core``, ``extended`` o ``all``. La capa extendida agrupa
    todas las fuentes importadas bajo la misma capa lógica.

    La capa extendida se devuelve aunque no queden fuentes: es una capa vacía,
    no una capa inexistente. Si se omitiera, ``sync --layer extended --prune``
    no podría borrar sus documentos de los almacenes y ``--verify`` no vería los
    residuos de una fuente eliminada.
    """

    if which not in {"core", "extended", "all"}:
        raise ValueError(f"Capa desconocida: {which!r}; usa core, extended o all.")
    layers: list[CorpusLayer] = []
    if which in {"core", "all"}:
        layers.append(CorpusLayer(LAYER_CORE, (gold_dir,)))
    if which in {"extended", "all"}:
        directories: tuple[Path, ...] = ()
        if external_dir.is_dir():
            directories = tuple(
                sorted(
                    child / "gold"
                    for child in external_dir.iterdir()
                    if child.is_dir()
                    and not child.name.endswith(".tmp")
                    and (child / "gold").is_dir()
                )
            )
        layers.append(CorpusLayer(LAYER_EXTENDED, directories))
    return tuple(layers)


def gold_hashes(layer: CorpusLayer) -> dict[str, str]:
    """Lee las huellas de sincronización de los Markdown Gold de una capa.

    Cada huella cubre el texto y los metadatos que los almacenes persisten: un
    cambio solo de título, sección o tipo de fuente también cuenta como cambio.
    Una capa sin directorios es una capa vacía, no un error: su corpus está
    vacío y la poda debe poder vaciar los almacenes.

    Raises:
        FileNotFoundError: si la capa declara directorios y ninguno existe.
        ValueError: si dos archivos de la capa publican el mismo slug.
    """

    from carvajal.adapters.storage.import_corpus import load_document

    if not layer.directories:
        return {}
    hashes: dict[str, str] = {}
    found = False
    for directory in layer.directories:
        if not directory.is_dir():
            continue
        found = True
        for path in sorted(directory.glob("*.md")):
            document = load_document(path, layer=layer.name)
            if document.id in hashes:
                raise ValueError(f"{path}: el slug {document.id!r} ya existe en la capa.")
            hashes[document.id] = sync_fingerprint(
                content_hash=document.content_hash,
                title=document.title,
                section=document.section,
                source_type=document.source_type,
            )
    if not found:
        raise FileNotFoundError(f"{layer.name}: no existe ningún directorio Gold de la capa.")
    return hashes


def sync_corpus(
    layers: Sequence[CorpusLayer],
    *,
    database: SyncDatabase,
    index: SyncIndex,
    only: Collection[str] | None = None,
    prune: bool = False,
    verify: bool = False,
    lock_root: Path | None = None,
) -> SyncReport:
    """Reconcilia Gold con PostgreSQL y Qdrant, de forma incremental.

    Args:
        layers: Capas a sincronizar y sus directorios Gold.
        database: Adaptador de PostgreSQL.
        index: Adaptador de Qdrant.
        only: Limita la corrida a esos slugs; desactiva la poda.
        prune: Elimina de ambos almacenes lo que ya no está en Gold.
        verify: No escribe; compara huellas y conteos.
        lock_root: Raíz donde se toma ``.corpus.lock``. Sin él no se bloquea
            (pruebas); la CLI y la web siempre lo pasan.

    Returns:
        Reporte con nuevos, actualizados, sin cambios, podados y errores.

    Raises:
        ValueError: si ``prune`` y ``only`` se piden a la vez.
    """

    if prune and only is not None:
        raise ValueError("La poda no se puede combinar con un conjunto de slugs.")
    if lock_root is None:
        return _sync(layers, database, index, only, prune, verify)
    with corpus_lock(lock_root):
        return _sync(layers, database, index, only, prune, verify)


def _sync(
    layers: Sequence[CorpusLayer],
    database: SyncDatabase,
    index: SyncIndex,
    only: Collection[str] | None,
    prune: bool,
    verify: bool,
) -> SyncReport:
    new = updated = unchanged = pruned = indexed = documents = chunks = 0
    errors: list[str] = []
    differences: list[str] = []
    selected = set(only) if only is not None else None

    for layer in layers:
        try:
            hashes = gold_hashes(layer)
            if selected is not None:
                hashes = {slug: value for slug, value in hashes.items() if slug in selected}
            store_hashes = dict(database.hashes(layer.name))
            new_slugs = {slug for slug in hashes if slug not in store_hashes}
            updated_slugs = {
                slug
                for slug, value in hashes.items()
                if slug in store_hashes and store_hashes[slug] != value
            }
            changed = new_slugs | updated_slugs
            new += len(new_slugs)
            updated += len(updated_slugs)
            unchanged += len(hashes) - len(changed)

            if verify:
                differences.extend(
                    _differences(
                        layer,
                        hashes,
                        store_hashes,
                        index,
                        database,
                    )
                )
                continue

            # PostgreSQL y Qdrant se reparan por separado: si uno falló en la
            # corrida anterior, el siguiente sync vuelve a verlo pendiente.
            try:
                # Con poda la base necesita ver el directorio completo para
                # saber qué falta; sin poda basta con los cambiados.
                if prune:
                    layer_documents, layer_chunks, removed = database.import_gold(
                        layer, only=None, prune=True
                    )
                elif changed:
                    layer_documents, layer_chunks, removed = database.import_gold(
                        layer, only=changed, prune=False
                    )
                else:
                    layer_documents = layer_chunks = removed = 0
                documents += layer_documents
                chunks += layer_chunks
                pruned += removed
            except Exception as error:  # un almacén caído no debe perder Gold
                errors.append(f"{layer.name} (PostgreSQL): {error}")

            try:
                index_hashes = dict(index.hashes(layer.name))
                index_changed = {
                    slug for slug, value in hashes.items() if index_hashes.get(slug) != value
                }
                if index_changed or prune:
                    layer_indexed, deleted = index.index_gold(
                        layer, only=index_changed, prune=prune
                    )
                else:
                    layer_indexed = deleted = 0
                indexed += layer_indexed
                pruned += deleted
            except Exception as error:  # Qdrant puede estar caído; Gold queda
                errors.append(f"{layer.name} (Qdrant): {error}")
        except Exception as error:  # Gold ilegible: no se toca ningún almacén
            errors.append(f"{layer.name}: {error}")

    return SyncReport(
        new=new,
        updated=updated,
        unchanged=unchanged,
        pruned=pruned,
        indexed=indexed,
        documents=documents,
        chunks=chunks,
        errors=tuple(errors),
        differences=tuple(differences),
        verified=verify,
    )


def _differences(
    layer: CorpusLayer,
    gold: Mapping[str, str],
    database: Mapping[str, str],
    index: SyncIndex,
    db: SyncDatabase,
) -> list[str]:
    """Compara Gold con PostgreSQL y Qdrant sin escribir nada."""

    differences: list[str] = []
    index_hashes = dict(index.hashes(layer.name))

    for slug in sorted(set(gold) | set(database)):
        if slug not in database:
            differences.append(f"{slug}: falta en PostgreSQL.")
        elif slug not in gold:
            differences.append(f"{slug}: sobra en PostgreSQL.")
        elif gold[slug] != database[slug]:
            differences.append(f"{slug}: PostgreSQL tiene otra huella de sincronización.")

    for slug in sorted(set(gold) | set(index_hashes)):
        if slug not in index_hashes:
            differences.append(f"{slug}: falta en Qdrant.")
        elif slug not in gold:
            differences.append(f"{slug}: sobra en Qdrant.")
        elif gold[slug] != index_hashes[slug]:
            differences.append(f"{slug}: Qdrant tiene otra huella de sincronización.")

    expected = len(gold)
    db_count = db.count(layer.name)
    index_count = len(index_hashes)
    if db_count != expected:
        differences.append(
            f"{layer.name}: PostgreSQL tiene {db_count} documentos y Gold {expected}."
        )
    if index_count != expected:
        differences.append(
            f"{layer.name}: Qdrant tiene {index_count} documentos y Gold {expected}."
        )
    return differences
