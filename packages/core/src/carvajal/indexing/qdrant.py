"""Indexación reproducible del corpus Gold en Qdrant.

El comando ``carvajal index-qdrant`` es la única vía de escritura del índice.
Lee ``data/gold/*.md``, reutiliza el mismo chunking del importador y calcula los
embeddings con el proveedor configurado. Al terminar, poda los puntos que ya no
existen y escribe un reporte con conteos y huellas.

Estrategia de actualización: reejecutar el comando. Cada fragmento tiene un ID
estable derivado de documento, posición y hash del texto; los que no cambian se
sobrescriben con el mismo ID y los obsoletos se eliminan. ``--recreate``
reconstruye la colección desde cero para una demo o un cambio de modelo.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import structlog

from carvajal.adapters.storage.import_corpus import load_document, split_document
from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.application.ports import Embedder
from carvajal.domain.entities import Chunk

__all__ = ["IndexReport", "index_gold"]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class IndexReport:
    """Resultado verificable de una indexación."""

    collection: str
    documents: int
    chunks: int
    indexed: int
    deleted: int
    points: int
    dimensions: int
    elapsed_ms: int
    recreated: bool = False


def index_gold(
    *,
    gold_dir: Path | None = None,
    gold_dirs: Sequence[tuple[Path, str]] | None = None,
    store: QdrantVectorStore,
    embedder: Embedder,
    recreate: bool = False,
    prune: bool = True,
    layer: str = "core",
    batch_size: int = 32,
    cards_path: Path | None = None,
    only: set[str] | None = None,
) -> IndexReport:
    """Indexa una o varias áreas Gold y devuelve los conteos de la corrida.

    ``gold_dirs`` permite indexar el núcleo y las capas extendidas en la misma
    corrida, cada directorio con su capa. ``gold_dir`` se conserva para los
    llamadores de una sola área. Antes de insertar cada documento se borran sus
    puntos anteriores: un documento acortado no deja fragmentos de la versión
    vieja.

    Raises:
        FileNotFoundError: si algún directorio Gold no existe.
        ValueError: si no hay documentos, si un slug se repite entre áreas o si
            un vector no tiene la dimensión configurada.
    """

    areas = list(gold_dirs) if gold_dirs is not None else []
    if gold_dir is not None:
        if areas:
            raise ValueError("Usa gold_dir o gold_dirs, no ambos.")
        areas = [(gold_dir, layer)]
    if not areas:
        raise ValueError("index_gold necesita gold_dir o gold_dirs.")
    # Las rutas se validan antes de tocar la colección: un directorio mal escrito no
    # debe borrar el índice que ya funciona, ni siquiera con ``recreate``.
    for directory, _ in areas:
        if not directory.is_dir():
            raise FileNotFoundError(f"{directory}: el directorio Gold no existe.")

    started = perf_counter()
    if recreate and store.client.collection_exists(store.collection):
        store.client.delete_collection(store.collection)
        logger.info("qdrant.collection_recreated", collection=store.collection)
    store.ensure_collection()

    cards: dict[str, str] = {}
    documents = []
    for directory, area_layer in areas:
        for slug, card in _load_cards(
            cards_path or directory.parent / "retrieval_cards.json"
        ).items():
            cards.setdefault(slug, card)
        for path in sorted(directory.glob("*.md")):
            document = load_document(path, layer=area_layer)
            if only is None or document.id in only:
                documents.append(document)
    if not documents:
        if only is not None:
            # La sincronización incremental puede no tocar este directorio: no es un error.
            return IndexReport(
                collection=store.collection,
                documents=0,
                chunks=0,
                indexed=0,
                deleted=0,
                points=store.count(),
                dimensions=store.dimensions,
                elapsed_ms=round((perf_counter() - started) * 1000),
                recreated=recreate,
            )
        raise ValueError("No hay documentos Gold para indexar.")
    seen: set[str] = set()
    duplicates: set[str] = set()
    for document in documents:
        if document.id in seen:
            duplicates.add(document.id)
        seen.add(document.id)
    if duplicates:
        raise ValueError(f"Slug repetido entre áreas Gold: {sorted(duplicates)[0]!r}.")
    if only is not None:
        prune = False

    valid_ids: list[str] = []
    indexed = 0
    total_chunks = 0
    for document in documents:
        chunks = split_document(document)
        total_chunks += len(chunks)
        card = cards.get(document.id, "")
        metadata = {
            "url": document.url,
            "title": document.title,
            "source_type": document.source_type,
            "content_hash": document.content_hash,
            "abstract": card,
            "chunks_total": str(len(chunks)),
        }
        # Los vectores del documento se preparan antes de tocar el índice: si el
        # proveedor falla o devuelve un vector inválido, la versión anterior sigue en
        # Qdrant y el documento no desaparece de la recuperación.
        prepared: list[tuple[list[Chunk], list[list[float]]]] = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            # El título y la sección no viven en el cuerpo Gold (están en el
            # front matter); se anteponen al texto que se vectoriza para que
            # una pregunta por el nombre del documento lo recupere. El payload
            # conserva el texto original para la cita.
            # ``_vector`` valida la dimensión al preparar, no en ``upsert``: la
            # versión anterior se borra antes de escribir, así que un proveedor
            # mal configurado dejaría el documento sin ningún punto.
            prepared.append(
                (
                    batch,
                    [
                        _vector(
                            embedder,
                            f"{document.title} · {document.section}. {card}\n{chunk.text}",
                            store.dimensions,
                        )
                        for chunk in batch
                    ],
                )
            )
        if not recreate:
            # Un documento acortado o reescrito no debe dejar fragmentos de la versión
            # vieja: sus puntos se reemplazan en cada indexación.
            store.delete_by_document(document.id)
        for batch, embeddings in prepared:
            index_batch(store, batch, embeddings, metadata)
            indexed += len(batch)
            valid_ids.extend(store.point_ids(batch, [metadata for _ in batch]))
            logger.info(
                "qdrant.document_progress",
                document=document.id,
                chunks=indexed,
                total=total_chunks,
            )
        logger.info(
            "qdrant.document_indexed",
            document=document.id,
            chunks=len(chunks),
            url=document.url,
        )

    deleted = store.prune(valid_ids, layer=layer) if prune else 0
    points = store.count()
    elapsed_ms = round((perf_counter() - started) * 1000)
    report = IndexReport(
        collection=store.collection,
        documents=len(documents),
        chunks=total_chunks,
        indexed=indexed,
        deleted=deleted,
        points=points,
        dimensions=store.dimensions,
        elapsed_ms=elapsed_ms,
        recreated=recreate,
    )
    logger.info(
        "qdrant.index_completed",
        collection=report.collection,
        documents=report.documents,
        chunks=report.chunks,
        indexed=report.indexed,
        deleted=report.deleted,
        points=report.points,
        dimensions=report.dimensions,
        elapsed_ms=report.elapsed_ms,
        recreated=report.recreated,
    )
    return report


def _load_cards(path: Path) -> dict[str, str]:
    """Carga las fichas de recuperación; sin archivo, el índice sigue válido."""

    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    cards = payload.get("cards", payload)
    if not isinstance(cards, dict):
        raise ValueError(f"{path}: el formato de fichas no es un objeto.")
    return {str(key): str(value) for key, value in cards.items()}


def index_batch(
    store: QdrantVectorStore,
    chunks: Sequence[Chunk],
    embeddings: Sequence[Sequence[float]],
    metadata: dict[str, str],
) -> None:
    """Escribe un lote con la metadata del documento que comparten sus chunks."""

    store.upsert(
        chunks,
        embeddings,
        metadata=[metadata for _ in chunks],
    )


def _vector(embedder: Embedder, text: str, dimensions: int) -> list[float]:
    """Vector del texto; valida la dimensión antes de que se borre nada."""

    vector = [float(value) for value in embedder.embed(text)]
    if len(vector) != dimensions:
        raise ValueError(f"El vector debe tener {dimensions} dimensiones; recibidas {len(vector)}.")
    return vector
