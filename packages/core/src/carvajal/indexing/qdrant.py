"""Indexación reproducible del corpus Gold en Qdrant.

El comando ``carvajal index-qdrant`` es la única vía de escritura del índice.
Lee ``data/gold/*.md``, reutiliza el mismo chunking del importador y calcula los
embeddings con el proveedor configurado. Al terminar, poda los puntos que ya no
existen y escribe un reporte con conteos y huellas.

Estrategia de actualización: reejecutar el comando. Cada fragmento tiene un ID
estable derivado de documento, posición y hash del texto; los que no cambian se
sobrescriben con el mismo ID y los obsoletos se eliminan. Si el texto no cambió
y solo cambiaron metadatos del payload, se reescribe el payload sin llamar al
embedder. ``--recreate`` reconstruye la colección desde cero para una demo o un
cambio de modelo.
"""

import json
import tempfile
from array import array
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import IO

import structlog

from carvajal.adapters.storage.import_corpus import load_document, split_document
from carvajal.adapters.vector.qdrant_store import (
    ExistingPoints,
    QdrantVectorStore,
    metadata_signature,
)
from carvajal.application.ports import Embedder, ModelAwareEmbedder
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
    # Puntos cuyo payload se reescribió sin volver a embeberlos: el texto
    # vectorizado no cambió, solo sus metadatos de procedencia.
    updated: int = 0


def _embedding_model(embedder: Embedder) -> str:
    """Modelo declarado por el embedder, si lo declara.

    El puerto ``Embedder`` solo exige ``embed``; los dobles de prueba no
    declaran modelo y el índice omite la validación. El adaptador de Ollama
    implementa ``ModelAwareEmbedder``.
    """

    if isinstance(embedder, ModelAwareEmbedder):
        return str(embedder.model)
    return ""


def _check_embedding_model(store: QdrantVectorStore, model: str) -> None:
    """Detiene la corrida si el índice tiene vectores de otro modelo.

    Un índice creado antes del registro no dice qué modelo lo construyó: se
    adopta el configurado ahora con una sola reescritura de payload, sin
    reembeber. Si conviven puntos de un modelo anterior, reconstruir con
    ``recreate`` es la única salida segura.
    """

    points = store.count()
    if not points:
        return
    with_model = store.count_embedding_model(model)
    if with_model == points:
        return
    if with_model < store.count_with_embedding_model():
        raise ValueError(
            "El índice tiene puntos de otro modelo de embeddings; mezclar modelos "
            f"recupera mal aunque las dimensiones coincidan. Reconstruye con --recreate "
            f"para usar {model!r}."
        )
    store.adopt_embedding_model(model)
    logger.info("qdrant.embedding_model_adopted", model=model, points=points - with_model)


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
    prune_layers: Sequence[str] | None = None,
) -> IndexReport:
    """Indexa una o varias áreas Gold y devuelve los conteos de la corrida.

    ``gold_dirs`` permite indexar el núcleo y las capas extendidas en la misma
    corrida, cada directorio con su capa. ``gold_dir`` se conserva para los
    llamadores de una sola área. Antes de insertar cada documento se borran sus
    puntos anteriores: un documento acortado no deja fragmentos de la versión
    vieja. La poda se limita a las capas de las áreas indexadas, comparando cada
    una solo con sus puntos: el resto de la colección queda intacto.

    Un documento cuyos puntos ya existen con exactamente los mismos IDs no
    necesita vectores nuevos: el ID estable cubre título, sección, ficha y
    texto, que es todo lo que se vectoriza. Si el texto no cambió y solo se
    movieron metadatos del payload (URL, origen, categoría, entidad, tipo de
    fuente), se reescribe el payload con ``update_payload`` y no se llama al
    embedder. Sin esta distinción, corregir la procedencia de un corpus de
    decenas de miles de fragmentos disparaba un re-embedding completo.

    El atajo no consulta al embedder y por eso no valida su dimensión: no
    escribe vectores y el guard de ``_vector`` protege la escritura, no el
    reposo. Lo que sí se valida es el modelo: el payload de cada fragmento lo
    registra y un índice con puntos de otro modelo se detiene antes de escribir
    nada, porque mezclar modelos produce una recuperación silenciosamente mala
    aunque las dimensiones coincidan. Reconstruir con ``recreate`` es la vía
    para cambiar de modelo. Un índice anterior al registro adopta el modelo
    configurado una sola vez, sin reembeber.

    ``prune_layers`` permite podar también una capa seleccionada sin directorios
    (una capa vacía): sin ella, retirar la última fuente extendida dejaría sus
    puntos en el índice.

    Raises:
        FileNotFoundError: si algún directorio Gold no existe.
        ValueError: si no hay documentos, si un slug se repite entre áreas, si
            un vector no tiene la dimensión configurada o si el índice se
            construyó con otro modelo de embeddings.
    """

    areas = list(gold_dirs) if gold_dirs is not None else []
    if gold_dir is not None:
        if areas:
            raise ValueError("Usa gold_dir o gold_dirs, no ambos.")
        areas = [(gold_dir, layer)]
    if not areas:
        raise ValueError("index_gold necesita gold_dir o gold_dirs.")

    started = perf_counter()
    # Rutas, fichas, documentos y slugs se validan antes de tocar la colección: una
    # entrada inválida no debe borrar el índice que ya funciona, ni con ``recreate``.
    for directory, _ in areas:
        if not directory.is_dir():
            raise FileNotFoundError(f"{directory}: el directorio Gold no existe.")
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

    if recreate and store.client.collection_exists(store.collection):
        store.client.delete_collection(store.collection)
        logger.info("qdrant.collection_recreated", collection=store.collection)
    store.ensure_collection()

    # El modelo se valida antes de tocar puntos: un índice que mezcla vectores de
    # modelos distintos recupera mal aunque las dimensiones coincidan.
    model = _embedding_model(embedder)
    if model and not recreate:
        _check_embedding_model(store, model)

    # Los IDs válidos se agrupan por capa: cada capa se poda comparando solo sus
    # puntos, y las capas que esta corrida no tocó quedan intactas.
    valid_ids: dict[str, list[str]] = {}
    indexed = 0
    updated = 0
    total_chunks = 0
    # IDs que hoy hay en el índice: la comparación con los del documento decide
    # entre reembeder (el texto cambió) y reescribir el payload (solo metadatos).
    # La firma del payload evita repetir un ``set_payload`` idéntico.
    existing: dict[str, ExistingPoints] = (
        {} if recreate else store.existing_points([document.id for document in documents])
    )
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
            "origin": document.origin,
            "category": document.category,
            "entity": document.entity,
            "embedding_model": model,
            "layer": document.layer,
        }
        ids = store.point_ids(chunks, [metadata for _ in chunks])
        current = existing.get(document.id)
        if current is not None and current.ids == set(ids):
            if current.payloads != {metadata_signature(metadata)}:
                store.update_payload(ids, metadata)
                updated += len(ids)
                logger.info(
                    "qdrant.document_payload_updated",
                    document=document.id,
                    chunks=len(ids),
                )
            valid_ids.setdefault(document.layer, []).extend(ids)
            continue
        # Los vectores del documento se preparan antes de tocar el índice: si el
        # proveedor falla o devuelve un vector inválido, la versión anterior sigue en
        # Qdrant y el documento no desaparece de la recuperación. Se preparan en un
        # archivo temporal: un documento grande produce decenas de miles de vectores
        # y retenerlos todos en memoria supera el límite del contenedor.
        with _staged_vectors(store.dimensions) as prepared:
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start : start + batch_size]
                # El título y la sección no viven en el cuerpo Gold (están en el
                # front matter); se anteponen al texto que se vectoriza para que
                # una pregunta por el nombre del documento lo recupere. El payload
                # conserva el texto original para la cita.
                for chunk in batch:
                    prepared.add(
                        _vector(
                            embedder,
                            f"{document.title} · {document.section}. {card}\n{chunk.text}",
                            store.dimensions,
                        )
                    )
            if not recreate:
                # Un documento acortado o reescrito no debe dejar fragmentos de la versión
                # vieja: sus puntos se reemplazan en cada indexación.
                store.delete_by_document(document.id)
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start : start + batch_size]
                index_batch(store, batch, prepared.take(len(batch)), metadata)
                indexed += len(batch)
                valid_ids.setdefault(document.layer, []).extend(
                    store.point_ids(batch, [metadata for _ in batch])
                )
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

    deleted = 0
    if prune:
        layers_to_prune = set(prune_layers or ())
        layers_to_prune.update(area_layer for _, area_layer in areas)
        for name in sorted(layers_to_prune):
            deleted += store.prune(valid_ids.get(name, []), layer=name)
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
        updated=updated,
    )
    logger.info(
        "qdrant.index_completed",
        collection=report.collection,
        documents=report.documents,
        chunks=report.chunks,
        indexed=report.indexed,
        updated=report.updated,
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


@contextmanager
def _staged_vectors(dimensions: int) -> Generator["_StagedVectors"]:
    """Prepara los vectores de un documento en un archivo temporal.

    El documento completo se embebe antes de borrar su versión anterior para que un
    fallo del proveedor no lo saque de la recuperación. Guardar todos los vectores en
    una lista rompe esa garantía de memoria: con 768 dimensiones y el chunking del
    corpus, un documento grande acumula decenas de miles de vectores. El archivo
    temporal deja un solo lote en memoria y conserva la versión anterior si la
    preparación falla.
    """

    with tempfile.TemporaryFile() as file:
        yield _StagedVectors(file, dimensions)


class _StagedVectors:
    """Vectores preparados en un archivo, en orden de preparación.

    Se guardan como ``float64`` para devolver exactamente los valores del proveedor.
    """

    def __init__(self, file: IO[bytes], dimensions: int) -> None:
        self._file = file
        self._dimensions = dimensions
        self._added = 0
        self._taken = 0

    def add(self, vector: Sequence[float]) -> None:
        """Añade un vector ya validado al final del archivo."""

        array("d", vector).tofile(self._file)
        self._added += 1

    def take(self, size: int) -> list[list[float]]:
        """Devuelve los siguientes ``size`` vectores, en el orden en que se prepararon."""

        count = min(size, self._added - self._taken)
        if count <= 0:
            return []
        if self._taken == 0:
            self._file.seek(0)
        values = array("d")
        values.fromfile(self._file, count * self._dimensions)
        self._taken += count
        return [
            values[start : start + self._dimensions].tolist()
            for start in range(0, len(values), self._dimensions)
        ]
