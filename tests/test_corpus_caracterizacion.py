"""Caracterización de invariantes del corpus antes de la fase 045.

Fija valores que la refactorización de la fase 045 no puede cambiar: los
documentos que se construyen desde un Markdown Gold, sus fragmentos, sus
huellas de sincronización, los IDs de punto y el payload que alimenta la cita
del chat. Cualquier diferencia en estos valores es una regresión de formato,
no una mejora de rendimiento.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from carvajal.adapters.storage.import_corpus import load_document, split_document
from carvajal.adapters.vector.qdrant_store import (
    QdrantVectorStore,
    fingerprint_for,
    payload_for,
    point_id,
)
from carvajal.application.sync import CorpusLayer, gold_hashes, gold_index_hashes
from carvajal.domain.chunking import CHUNK_SIZE

LARGO = "Carvajal Empaques produce soluciones de empaque sostenible. " * 40

GOLD = {
    "historia": (
        "---\n"
        "slug: historia\n"
        "url: https://www.carvajal.com/historia/\n"
        "title: Historia\n"
        "hash: hash-historia\n"
        "captured_at: 2026-10-31T13:43:21+00:00\n"
        "source_type: oficial\n"
        "---\n\n"
        "## 1904\n\n"
        "Carvajal nace en Cali.\n"
    ),
    "informe": (
        "---\n"
        "slug: informe\n"
        "url: https://ejemplo.com/informe\n"
        "title: Informe anual\n"
        "hash: hash-informe\n"
        "captured_at: 2026-10-31T13:43:21+00:00\n"
        "source_type: tercero\n"
        "origin: Cámara de Comercio\n"
        "category: informe\n"
        "entity: Carvajal Empaques\n"
        "---\n\n" + LARGO + "\n"
    ),
}

BASE = {
    "historia": "22b34647afa48c8ff863b6fd8bcd40e3afa17f572a580ae993815f638405d9e3",
    "informe": "bae0b323c8ba6e9b9483c56b8b7706cadbda859fb8e0c3f765f2407d219ac9bf",
}
INDEX = {
    "historia": "22b34647afa48c8ff863b6fd8bcd40e3afa17f572a580ae993815f638405d9e3",
    "informe": "0018cbcb594af1765f51d25db5db0f7bc0e905c2e8ab4a7e71ebf985b701cc71",
}
CHUNK_SHA256 = {
    "historia": ["2b03119499832f1bee6f1402ae332a5b8c0cda5b8a503e45e6c6fed92d0c0270"],
    "informe": [
        "d61f73e32368090b076381df80fee3954d6cecc39e2cf55a9a5de23c0148ec68",
        "f1d4629766c80eb03576146ad029fc582ac905fecbfbb2a99bb715a6592a895d",
        "7c4f7554859933489492081287d0ae13e8ce43933b92110db90d321b3e5057fa",
    ],
}
POINT_IDS = {
    "historia": ["ea309aa7-3797-5d7c-ab76-4228269ebed5"],
    "informe": [
        "5b7e8549-e8e1-5eb2-90ac-9ba9fea0eef7",
        "ea8876bd-bb01-54a4-9256-a799052cbf2b",
        "be7bf262-c08c-55a7-ba2c-8d0e2fa885e3",
    ],
}


def _write_gold(directory: Path) -> None:
    """Escribe los dos Markdown de la caracterización."""

    directory.mkdir(parents=True, exist_ok=True)
    for slug, content in GOLD.items():
        (directory / f"{slug}.md").write_text(content, encoding="utf-8")


def _metadata(document: object, chunks: int) -> dict[str, str]:
    """Metadatos que el índice guarda en el payload de cada fragmento."""

    return {
        "url": document.url,  # type: ignore[attr-defined]
        "title": document.title,  # type: ignore[attr-defined]
        "source_type": document.source_type,  # type: ignore[attr-defined]
        "content_hash": document.content_hash,  # type: ignore[attr-defined]
        "abstract": "",
        "chunks_total": str(chunks),
        "origin": document.origin,  # type: ignore[attr-defined]
        "category": document.category,  # type: ignore[attr-defined]
        "entity": document.entity,  # type: ignore[attr-defined]
        "layer": document.layer,  # type: ignore[attr-defined]
    }


class EmbedderDeterminista:
    """Vector fijo de 768 dimensiones; no declara modelo."""

    def embed(self, text: str) -> list[float]:
        """Devuelve siempre el mismo vector unitario."""

        return [1.0, *([0.0] * 767)]


class Snapshot:
    """Instantánea mínima del índice para los dobles de la verificación."""

    def __init__(self, hashes: Mapping[str, str], document_ids: set[str]) -> None:
        self.hashes = hashes
        self.document_ids = document_ids


def test_load_document_caracteriza_los_campos_del_corpus(tmp_path: Path) -> None:
    """El documento Gold conserva id, título, sección, huella y procedencia."""

    # Arrange
    _write_gold(tmp_path)

    # Act
    document = load_document(tmp_path / "informe.md", layer="core")

    # Assert
    assert document.id == "informe"
    assert document.slug == "informe"
    assert document.url == "https://ejemplo.com/informe"
    assert document.title == "Informe anual"
    assert document.section == "prensa"
    assert document.layer == "core"
    assert document.content_hash == "hash-informe"
    assert document.source_type == "tercero"
    assert document.origin == "Cámara de Comercio"
    assert document.category == "informe"
    assert document.entity == "Carvajal Empaques"
    assert document.captured_at == datetime(2026, 10, 31, 13, 43, 21, tzinfo=UTC)
    assert document.markdown == LARGO.rstrip()


@pytest.mark.parametrize("slug", sorted(GOLD))
def test_split_document_caracteriza_fragmentos_y_textos(tmp_path: Path, slug: str) -> None:
    """Cada fragmento conserva posición, ID, sección y texto exacto."""

    # Arrange
    _write_gold(tmp_path)
    document = load_document(tmp_path / f"{slug}.md", layer="core")

    # Act
    chunks = split_document(document)

    # Assert
    assert [chunk.id for chunk in chunks] == [
        f"{slug}:{position}" for position in range(len(chunks))
    ]
    assert [chunk.position for chunk in chunks] == list(range(len(chunks)))
    assert [
        hashlib.sha256(chunk.text.encode("utf-8")).hexdigest() for chunk in chunks
    ] == CHUNK_SHA256[slug]
    assert [chunk.text for chunk in chunks] == [chunk.text for chunk in split_document(document)]
    assert all(chunk.document_id == slug for chunk in chunks)
    assert all(chunk.section == document.section for chunk in chunks)
    assert all(chunk.layer == "core" for chunk in chunks)
    assert all(len(chunk.text) <= CHUNK_SIZE for chunk in chunks)


def test_huellas_de_sincronizacion_caracterizadas(tmp_path: Path) -> None:
    """Las huellas base y de índice no cambian al mover la lectura de Gold."""

    # Arrange
    _write_gold(tmp_path)
    layer = CorpusLayer("core", (tmp_path,))

    # Act
    base = gold_hashes(layer)
    indexed = gold_index_hashes(layer)

    # Assert
    assert dict(base) == BASE
    assert dict(indexed) == INDEX
    # El índice añade la procedencia solo a su huella.
    assert indexed["informe"] != base["informe"]
    assert indexed["historia"] == base["historia"]


def test_ids_y_payloads_del_indice_caracterizados(tmp_path: Path) -> None:
    """El índice produce los mismos IDs de punto y el mismo payload de cita."""

    # Arrange
    from carvajal.indexing.qdrant import index_gold

    gold_dir = tmp_path / "gold"
    _write_gold(gold_dir)
    store = QdrantVectorStore(
        client=QdrantClient(location=":memory:"),
        collection="caracterizacion",
        dimensions=768,
    )

    # Act
    document = load_document(gold_dir / "informe.md", layer="core")
    chunks = split_document(document)
    metadata = _metadata(document, len(chunks))
    index_gold(gold_dir=gold_dir, store=store, embedder=EmbedderDeterminista(), prune=False)
    records, _ = store.client.scroll(
        collection_name="caracterizacion", limit=100, with_payload=True, with_vectors=False
    )

    # Assert
    payloads = {str(record.id): dict(record.payload or {}) for record in records}
    for position, chunk in enumerate(chunks):
        esperado = point_id(chunk, fingerprint_for(chunk, metadata))
        assert esperado == POINT_IDS["informe"][position]
        assert payloads[esperado] == payload_for(chunk, metadata)
        assert payloads[esperado]["text"] == chunk.text
    assert payloads[POINT_IDS["informe"][0]]["chunks_total"] == "3"


def test_verify_conserva_el_texto_de_los_cuatro_diagnosticos(tmp_path: Path) -> None:
    """Ausente, sobrante, incompleto y modificado se informan igual que hoy.

    Los dobles revientan si la verificación intenta importar o indexar: el
    contrato es que ``--verify`` solo lee.
    """

    # Arrange
    from carvajal.application.sync import sync_corpus

    class Base:
        """PostgreSQL de solo lectura con una huella vieja."""

        def hashes(self, layer: str) -> Mapping[str, str]:
            return {"ausente": "h1", "modificado": "viejo"}

        def import_gold(self, *args: object, **kwargs: object) -> tuple[int, int, int]:
            raise AssertionError("verify no debe importar")

        def count(self, layer: str) -> int:
            return 2

    class Indice:
        """Qdrant de solo lectura: sobrante, incompleto y modificado."""

        def hashes(self, layer: str) -> Mapping[str, str]:
            return {"sobrante": "h9", "modificado": "viejo"}

        def document_ids(self, layer: str) -> set[str]:
            return {"sobrante", "incompleto", "modificado"}

        def snapshot(self, layer: str) -> Snapshot:
            return Snapshot(self.hashes(layer), self.document_ids(layer))

        def index_gold(self, *args: object, **kwargs: object) -> tuple[int, int]:
            raise AssertionError("verify no debe indexar")

        def count(self, layer: str) -> int:
            return 3

    directorio = tmp_path / "gold"
    _write_gold(directorio)
    (directorio / "incompleto.md").write_text(
        "---\ntitle: Incompleto\nhash: h2\n---\n\nCuerpo.", encoding="utf-8"
    )
    (directorio / "modificado.md").write_text(
        "---\ntitle: Modificado\nhash: h2\n---\n\nCuerpo.", encoding="utf-8"
    )
    layer = CorpusLayer("core", (directorio,))

    # Act
    report = sync_corpus([layer], database=Base(), index=Indice(), verify=True)  # type: ignore[arg-type]

    # Assert
    assert report.verified is True
    assert list(report.differences) == [
        "ausente: sobra en PostgreSQL.",
        "historia: falta en PostgreSQL.",
        "incompleto: falta en PostgreSQL.",
        "informe: falta en PostgreSQL.",
        "modificado: PostgreSQL tiene otra huella de sincronización.",
        "core: PostgreSQL tiene 2 documentos y Gold 4.",
        "historia: falta en Qdrant.",
        "incompleto: tiene puntos incompletos en Qdrant.",
        "informe: falta en Qdrant.",
        "modificado: Qdrant tiene otra huella de sincronización.",
        "sobrante: sobra en Qdrant.",
        "core: Qdrant tiene 3 documentos y Gold 4.",
    ]
    assert report.ok is False
