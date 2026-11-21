"""Importador del corpus Gold al almacén relacional (CLI: ``migrate-gold``).

Migra los Markdown de ``data/gold`` a Postgres de forma idempotente: cada
documento se identifica por su slug y cada fragmento por ``documento:posición``.
La lectura y la fragmentación viven en :mod:`carvajal.gold_documents`, que
comparten este adaptador y el indexado vectorial. La función solo hace
``flush()``; la transacción la abre quien la invoca (la CLI).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from carvajal.adapters.storage.repositories import SqlChunkRepository, SqlDocumentRepository
from carvajal.domain.entities import Document
from carvajal.gold_documents import (
    GoldImportError,
    load_document,
    load_documents,
    split_document,
)

__all__ = [
    "GoldImportError",
    "GoldImportReport",
    "import_documents",
    "load_document",
    "load_documents",
    "migrate_gold",
    "prune_layer",
    "split_document",
]

PAGE_SIZE = 200


@dataclass(frozen=True)
class GoldImportReport:
    """Totales de una corrida de importación.

    ``removed`` cuenta los documentos de la capa que ya no existen en el
    directorio y se podaron de la base.
    """

    documents: int
    chunks: int
    removed: int = 0


def import_documents(
    session: Session,
    documents: Sequence[Document],
    *,
    layer: str,
    prune: bool = True,
    only: Collection[str] | None = None,
) -> GoldImportReport:
    """Importa documentos Gold ya leídos, sin volver a tocar el disco.

    La sincronización lee Gold una vez y entrega el lote a los dos almacenes;
    esta función es el lado relacional de ese lote. Con ``only`` solo escribe
    los slugs pedidos y nunca poda; sin él, ``prune`` compara la capa con los
    documentos recibidos (que son la capa completa) y borra el resto.

    Reimportar no duplica documentos ni fragmentos; solo el cambio de cuerpo
    archiva una versión.
    """

    seleccion: set[str] | None = None
    loaded = list(documents)
    if only is not None:
        seleccion = set(only)
        loaded = [document for document in loaded if document.id in seleccion]
        prune = False

    documents_repo = SqlDocumentRepository(session)
    chunks_repo = SqlChunkRepository(session)

    total_chunks = 0
    for document in loaded:
        document_chunks = split_document(document)
        documents_repo.upsert(document)
        chunks_repo.replace(document.id, document_chunks)
        total_chunks += len(document_chunks)

    keep = {document.id for document in documents}
    removed = _prune(documents_repo, layer=layer, keep=keep) if prune else 0
    return GoldImportReport(documents=len(loaded), chunks=total_chunks, removed=removed)


def migrate_gold(
    session: Session,
    *,
    gold_dir: Path,
    layer: str = "core",
    allow_empty: bool = False,
    prune: bool = True,
    only: Collection[str] | None = None,
) -> GoldImportReport:
    """Importa los ``*.md`` de ``gold_dir`` y devuelve los totales.

    El directorio es autoritativo para la capa: tras importar se podan los
    documentos de esa capa cuyo slug ya no existe en disco (p. ej. si se importa
    con ``--gold`` un subconjunto, el resto se elimina). La FK en cascada limpia
    fragmentos y versiones. Reimportar no duplica documentos ni fragmentos; solo
    el cambio de cuerpo archiva una versión. Un slug repetido aborta la corrida
    antes de escribir para no sobrescribir en silencio.

    Un directorio existente sin Markdown se rechaza con ``GoldImportError``
    salvo ``allow_empty=True``, que interpreta el vacío como una poda total de
    la capa. Un directorio inexistente se rechaza siempre.

    Con ``prune=False`` solo se hace la importación incremental: útil para
    cargas de documentos sobre un corpus cuya copia Gold no está en el mismo
    contenedor, donde podar borraría documentos válidos. ``only`` limita la
    importación a esos slugs y nunca poda el resto; si ninguno de los slugs
    pedidos está en este directorio, la llamada no hace nada (una capa puede
    repartir sus documentos en varias fuentes).
    """

    loaded = load_documents(gold_dir, layer=layer)
    seleccion: set[str] | None = None
    if only is not None:
        seleccion = set(only)
        loaded = [document for document in loaded if document.id in seleccion]
        prune = False
    if not loaded and not allow_empty and seleccion is None:
        # Una selección incremental puede no incluir este directorio; no es un error.
        raise GoldImportError(
            f"{gold_dir}: no hay documentos Gold; usa --allow-empty para vaciar la capa."
        )
    return import_documents(session, loaded, layer=layer, prune=prune)


def prune_layer(session: Session, *, layer: str, keep: Collection[str]) -> int:
    """Borra de la capa los documentos cuyo slug no está en ``keep``.

    La poda de una capa repartida en varias fuentes se hace con la unión de sus
    slugs: ningún directorio es autoritativo por separado, así que borrar con la
    lista de uno solo eliminaría los documentos de los demás.
    """

    return _prune(SqlDocumentRepository(session), layer=layer, keep=set(keep))


def _prune(documents: SqlDocumentRepository, *, layer: str, keep: set[str]) -> int:
    """Borra los documentos de la capa cuyo id no está en ``keep``.

    Pagina primero y borra después: borrar mientras se pagina con ``offset``
    desplazaría el resultado y dejaría huérfanos.
    """

    orphan_ids: list[str] = []
    offset = 0
    while True:
        page = documents.list(layer=layer, limit=PAGE_SIZE, offset=offset)
        orphan_ids.extend(document.id for document in page if document.id not in keep)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    for document_id in orphan_ids:
        documents.delete(document_id)
    return len(orphan_ids)
