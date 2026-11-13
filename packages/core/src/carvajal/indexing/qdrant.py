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
    gold_dir: Path,
    store: QdrantVectorStore,
    embedder: Embedder,
    recreate: bool = False,
    prune: bool = True,
    batch_size: int = 32,
    cards_path: Path | None = None,
) -> IndexReport:
    """Indexa el corpus Gold y devuelve los conteos de la corrida.

    Raises:
        FileNotFoundError: si ``gold_dir`` no existe.
        ValueError: si no hay documentos o si un vector no tiene la dimensión
            configurada.
    """

    if not gold_dir.is_dir():
        raise FileNotFoundError(f"{gold_dir}: el directorio Gold no existe.")
    started = perf_counter()
    if recreate and store.client.collection_exists(store.collection):
        store.client.delete_collection(store.collection)
        logger.info("qdrant.collection_recreated", collection=store.collection)
    store.ensure_collection()

    cards = _load_cards(cards_path or gold_dir.parent / "retrieval_cards.json")
    documents = []
    for path in sorted(gold_dir.glob("*.md")):
        documents.append(load_document(path, layer="core"))
    if not documents:
        raise ValueError(f"{gold_dir}: no hay documentos Gold para indexar.")

    valid_ids: list[str] = []
    indexed = 0
    total_chunks = 0
    for document in documents:
        chunks = split_document(document)
        total_chunks += len(chunks)
        metadata = {
            "url": document.url,
            "title": document.title,
            "source_type": document.source_type,
            "content_hash": document.content_hash,
            "abstract": cards.get(document.id, ""),
        }
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            # El título y la sección no viven en el cuerpo Gold (están en el
            # front matter); se anteponen al texto que se vectoriza para que
            # una pregunta por el nombre del documento lo recupere. El payload
            # conserva el texto original para la cita.
            card = cards.get(document.id, "")
            embeddings = [
                embedder.embed(f"{document.title} · {document.section}. {card}\n{chunk.text}")
                for chunk in batch
            ]
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

    deleted = store.prune(valid_ids) if prune else 0
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
