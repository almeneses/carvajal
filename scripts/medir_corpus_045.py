"""Medición reproducible de la fase 045: Gold, verificación y embeddings.

Mide con el corpus local y servicios locales, en el mismo comando, lo que la
spec 045 se propone reducir:

1. Pasadas completas sobre Gold por capa en un ``sync`` con cambios y con poda
   (lecturas de archivo / documentos de la capa).
2. Pasadas completas sobre el índice por capa en ``sync --verify`` (páginas de
   *scroll* y payloads recorridos).
3. Llamadas y costo del embedder por fragmento, incluida la alternativa por
   lote sobre el mismo modelo, para decidir si se optimiza.

El script funciona igual antes y después de la implementación: instrumenta los
nombres que existan en cada versión. Crea una base PostgreSQL temporal (con
migraciones Alembic) y una colección Qdrant temporal, y las elimina al
terminar; nunca toca la base ni la colección de desarrollo ni producción.

Uso:
    export UV_CACHE_DIR=.../carvajal/.uv-cache
    env -u OLLAMA_MODEL uv run python scripts/medir_corpus_045.py --label antes
    env -u OLLAMA_MODEL uv run python scripts/medir_corpus_045.py --label despues

Para apuntar a otro PostgreSQL local, define ``DATABASE_URL`` o pasa
``--database-url``. El puerto 5433 es el que publica ``compose.yml``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

DEFAULT_DATABASE_URL = "postgresql+psycopg://carvajal:carvajal@127.0.0.1:5433/carvajal_045_medicion"
DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"
DEFAULT_COLLECTION = "carvajal_045_medicion"
DEFAULT_GOLD_DIR = Path("data") / "gold"
MICRO_POINTS = 5000
MICRO_DOCUMENTS = 500


@dataclass
class Counters:
    """Conteos de las operaciones que la medición vigila."""

    gold_reads: int = 0
    gold_passes: float = 0.0
    payload_traversals: int = 0
    payloads: int = 0
    scroll_pages: int = 0
    embed_calls: int = 0
    embed_ms: float = 0.0

    def reset(self) -> None:
        """Vuelve a cero antes de cada escenario."""

        self.__init__()  # type: ignore[misc]


@dataclass
class Scenario:
    """Resultado de un escenario medido."""

    name: str
    elapsed_ms: float
    counters: dict[str, Any] = field(default_factory=dict)
    documents: int = 0
    chunks: int = 0
    indexed: int = 0
    differences: int = 0


def _owner_load_document() -> tuple[Any, Any]:
    """Devuelve (módulo dueño, función ``load_document``) de esta versión.

    Antes de la 045 la lectura vive en el adaptador SQL; después, en
    ``carvajal.gold_documents``. La medición sirve para ambas.
    """

    try:
        from carvajal import gold_documents

        return gold_documents, gold_documents.load_document
    except (ImportError, AttributeError):
        from carvajal.adapters.storage import import_corpus

        return import_corpus, import_corpus.load_document


def _owner_split_document() -> Any:
    """Devuelve la función de fragmentación de esta versión."""

    try:
        from carvajal import gold_documents

        return gold_documents.split_document
    except (ImportError, AttributeError):
        from carvajal.adapters.storage import import_corpus

        return import_corpus.split_document


class _InstrumentedLoader:
    """Envuelve ``load_document`` y cuenta una lectura de archivo Gold."""

    def __init__(self, original: Any, counters: Counters) -> None:
        self._original = original
        self._counters = counters

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._counters.gold_reads += 1
        return self._original(*args, **kwargs)


def _instrument_load_document(counters: Counters) -> list[tuple[Any, str, Any]]:
    """Sustituye ``load_document`` en todos los módulos que lo tengan ligado.

    Los consumidores lo importan de dos maneras: ``from ... import load_document``
    (nombre ligado en el módulo) o en línea dentro de la función. Se parchea el
    módulo dueño y cada nombre ligado que apunte a la misma función.
    """

    owner, original = _owner_load_document()
    patched: list[tuple[Any, str, Any]] = []
    import carvajal.adapters.storage.import_corpus as import_corpus
    import carvajal.application.sync as sync_module
    import carvajal.indexing.qdrant as indexing_qdrant

    candidates = [owner, sync_module, import_corpus, indexing_qdrant]
    wrapper = _InstrumentedLoader(original, counters)
    for module in candidates:
        if getattr(module, "load_document", None) is original:
            patched.append((module, "load_document", original))
            module.load_document = wrapper  # type: ignore[attr-defined]
    return patched


def _restore_load_document(patched: list[tuple[Any, str, Any]]) -> None:
    """Devuelve cada módulo a su función original."""

    for module, name, original in patched:
        setattr(module, name, original)


def _instrument_store(store: Any, counters: Counters) -> Any:
    """Cuenta una pasada por ``_payloads`` y las páginas de scroll del cliente."""

    from carvajal.adapters.vector.qdrant_store import QdrantVectorStore

    original_payloads = QdrantVectorStore._payloads

    def counted_payloads(self: Any, layer: str | None = None) -> Any:
        counters.payload_traversals += 1
        for payload in original_payloads(self, layer):
            counters.payloads += 1
            yield payload

    QdrantVectorStore._payloads = counted_payloads  # type: ignore[method-assign]

    original_scroll = store.client.scroll

    def counted_scroll(*args: Any, **kwargs: Any) -> Any:
        counters.scroll_pages += 1
        return original_scroll(*args, **kwargs)

    with suppress(AttributeError):
        store.client.scroll = counted_scroll

    return original_payloads


def _restore_store(original_payloads: Any) -> None:
    """Restaura la clase instrumentada."""

    from carvajal.adapters.vector.qdrant_store import QdrantVectorStore

    QdrantVectorStore._payloads = original_payloads  # type: ignore[method-assign]


class CountingEmbedder:
    """Embedder determinista de 768 dimensiones que cuenta sus llamadas."""

    def __init__(self, counters: Counters) -> None:
        self._counters = counters

    def embed(self, text: str) -> list[float]:
        """Devuelve un vector válido y contabiliza la llamada."""

        started = time.perf_counter()
        self._counters.embed_calls += 1
        self._counters.embed_ms += (time.perf_counter() - started) * 1000
        return [1.0, *([0.0] * 767)]


def _create_database(database_url: str) -> str:
    """Crea la base temporal si falta y aplica las migraciones."""

    import psycopg

    url = make_url(database_url)
    database = url.database or "carvajal_045_medicion"
    admin = psycopg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        dbname="postgres",
        autocommit=True,
    )
    try:
        exists = admin.execute(
            "select 1 from pg_database where datname = %s", (database,)
        ).fetchone()
        if not exists:
            admin.execute(f'CREATE DATABASE "{database}"')
        else:
            admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = %s and pid <> pg_backend_pid()",
                (database,),
            )
    finally:
        admin.close()
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
        capture_output=True,
    )
    return database


def _drop_database(database_url: str) -> None:
    """Elimina la base temporal, si existe."""

    import psycopg

    url = make_url(database_url)
    with suppress(Exception):
        admin = psycopg.connect(
            host=url.host,
            port=url.port,
            user=url.username,
            password=url.password,
            dbname="postgres",
            autocommit=True,
        )
        try:
            admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = %s and pid <> pg_backend_pid()",
                (url.database,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{url.database}"')
        finally:
            admin.close()


def _sync_measurement(
    *,
    label: str,
    gold_dir: Path,
    database_url: str,
    qdrant_url: str,
    collection: str,
) -> list[Scenario]:
    """Mide sync inicial, sync sin cambios y verificación sobre Gold real."""

    from carvajal.adapters.storage.database import build_session_factory
    from carvajal.adapters.storage.sync_postgres import PostgresSync
    from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
    from carvajal.adapters.vector.sync_qdrant import QdrantSync
    from carvajal.application.sync import CorpusLayer, sync_corpus

    engine = create_engine(database_url, pool_pre_ping=True)
    store = QdrantVectorStore(url=qdrant_url, collection=collection, dimensions=768)
    with suppress(Exception):
        if store.client.collection_exists(collection):
            store.client.delete_collection(collection)
    layer = CorpusLayer("core", (gold_dir,))
    counters = Counters()
    embedder = CountingEmbedder(counters)
    scenarios: list[Scenario] = []

    original_payloads = _instrument_store(store, counters)
    patched = _instrument_load_document(counters)
    try:
        database = PostgresSync(build_session_factory(engine))
        index = QdrantSync(store, embedder)  # type: ignore[arg-type]

        def run(name: str, *, prune: bool, verify: bool = False) -> Scenario:
            counters.reset()
            started = time.perf_counter()
            report = sync_corpus(
                [layer],
                database=database,
                index=index,
                prune=prune,
                verify=verify,
            )
            elapsed = (time.perf_counter() - started) * 1000
            documents = len(list(gold_dir.glob("*.md")))
            passes = counters.gold_reads / documents if documents else 0.0
            return Scenario(
                name=name,
                elapsed_ms=round(elapsed, 1),
                counters={
                    **asdict(counters),
                    "gold_passes": round(passes, 2),
                },
                documents=report.documents,
                chunks=report.chunks,
                indexed=report.indexed,
                differences=len(report.differences),
            )

        scenarios.append(run("sync inicial (todo nuevo, poda)", prune=True))
        scenarios.append(run("sync sin cambios con poda", prune=True))
        scenarios.append(run("sync sin cambios sin poda", prune=False))
        scenarios.append(run("verificación --verify", prune=False, verify=True))
    finally:
        _restore_load_document(patched)
        _restore_store(original_payloads)
        engine.dispose()
        store.close()
    print(f"[{label}] escenarios de sincronización medidos", file=sys.stderr)
    return scenarios


def _micro_benchmark_traversal(
    *, qdrant_url: str, collection: str, points: int = MICRO_POINTS
) -> dict[str, Any]:
    """Mide recorrer el índice en dos pasadas frente a una pasada lógica.

    Usa una colección sintética del mismo tamaño que la capa extendida de
    producción para cuantificar el costo por pasada sin depender de datos reales
    que no están en el entorno de desarrollo.
    """

    from qdrant_client import models

    from carvajal.adapters.vector.qdrant_store import QdrantVectorStore

    micro_collection = f"{collection}_micro"
    per_document = max(1, points // MICRO_DOCUMENTS)
    store = QdrantVectorStore(url=qdrant_url, collection=micro_collection, dimensions=1)
    try:
        if store.client.collection_exists(micro_collection):
            store.client.delete_collection(micro_collection)
        store.ensure_collection()
        batch: list[models.PointStruct] = []
        for index in range(points):
            document = index // per_document
            batch.append(
                models.PointStruct(
                    id=index + 1,
                    vector=[1.0],
                    payload={
                        "document_id": f"doc-{document}",
                        "slug": f"doc-{document}",
                        "layer": "core",
                        "content_hash": f"hash-{document}",
                        "chunks_total": str(per_document),
                        "title": f"Doc {document}",
                    },
                )
            )
            if len(batch) == 1000:
                store.client.upsert(micro_collection, points=batch)
                batch = []
        if batch:
            store.client.upsert(micro_collection, points=batch)

        pages: dict[str, int] = {"two_passes": 0, "single_pass": 0}
        original_scroll = store.client.scroll

        def counted(*args: Any, **kwargs: Any) -> Any:
            pages["current"] = pages.get("current", 0) + 1
            return original_scroll(*args, **kwargs)

        store.client.scroll = counted  # type: ignore[method-assign]
        try:
            pages["current"] = 0
            started = time.perf_counter()
            hashes = store.document_hashes("core")
            ids = store.document_ids("core")
            two_ms = (time.perf_counter() - started) * 1000
            pages["two_passes"] = pages["current"]

            single_ms: float | None = None
            snapshot_pages = 0
            if hasattr(store, "snapshot"):
                pages["current"] = 0
                started = time.perf_counter()
                snapshot = store.snapshot("core")  # type: ignore[attr-defined]
                single_ms = (time.perf_counter() - started) * 1000
                snapshot_pages = pages["current"]
                assert dict(snapshot.hashes) == hashes
                assert set(snapshot.document_ids) == ids
        finally:
            store.client.scroll = original_scroll  # type: ignore[method-assign]
        return {
            "puntos": points,
            "documentos": math.ceil(points / per_document),
            "dos_pasadas_ms": round(two_ms, 1),
            "dos_pasadas_paginas": pages["two_passes"],
            "una_pasada_ms": None if single_ms is None else round(single_ms, 1),
            "una_pasada_paginas": snapshot_pages,
            "ahorro_ms": None if single_ms is None else round(two_ms - single_ms, 1),
        }
    finally:
        with suppress(Exception):
            if store.client.collection_exists(micro_collection):
                store.client.delete_collection(micro_collection)
        store.close()


def _embedding_cost(*, gold_dir: Path) -> dict[str, Any]:
    """Mide el costo real de embeber los fragmentos del corpus local."""

    from langchain_ollama import OllamaEmbeddings

    from carvajal.adapters.embeddings.ollama import DEFAULT_MODEL, OllamaEmbedder
    from carvajal.infrastructure.settings import Settings

    settings = Settings()
    load_document = _owner_load_document()[1]
    split_document = _owner_split_document()
    cards_path = gold_dir.parent / "retrieval_cards.json"
    cards: dict[str, str] = {}
    if cards_path.is_file():
        payload = json.loads(cards_path.read_text(encoding="utf-8"))
        cards = {str(k): str(v) for k, v in payload.get("cards", payload).items()}
    texts: list[str] = []
    for path in sorted(gold_dir.glob("*.md")):
        document = load_document(path, layer="core")
        card = cards.get(document.id, "")
        for chunk in split_document(document):
            texts.append(f"{document.title} · {document.section}. {card}\n{chunk.text}")
    embedder = OllamaEmbedder(model=DEFAULT_MODEL, base_url=settings.ollama_base_url)
    try:
        per_text: list[float] = []
        started = time.perf_counter()
        for text in texts:
            began = time.perf_counter()
            embedder.embed(text)
            per_text.append((time.perf_counter() - began) * 1000)
        total_ms = (time.perf_counter() - started) * 1000
        result: dict[str, Any] = {
            "modelo": DEFAULT_MODEL,
            "fragmentos": len(texts),
            "total_ms": round(total_ms, 1),
            "media_ms": round(statistics.fmean(per_text), 2) if per_text else 0.0,
            "p50_ms": round(statistics.median(per_text), 2) if per_text else 0.0,
            "p95_ms": round(sorted(per_text)[max(0, math.ceil(len(per_text) * 0.95) - 1)], 2)
            if per_text
            else 0.0,
            "llamadas": len(texts),
        }
    except Exception as error:  # Ollama apagado o modelo ausente
        return {"error": str(error)}
    # Alternativa por lote con el mismo modelo: solo se mide, no se implementa.
    try:
        batched = OllamaEmbeddings(model=DEFAULT_MODEL, base_url=settings.ollama_base_url)
        started = time.perf_counter()
        batched.embed_documents(texts)
        batch_ms = (time.perf_counter() - started) * 1000
        result["lote_total_ms"] = round(batch_ms, 1)
        result["lote_media_ms"] = round(batch_ms / max(1, len(texts)), 2)
    except Exception as error:  # la alternativa es informativa
        result["lote_error"] = str(error)
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="medicion", help="Nombre del informe.")
    parser.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL,
    )
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--json", type=Path, default=None, help="Guarda el informe JSON.")
    parser.add_argument("--skip-embeddings", action="store_true", help="No mide Ollama.")
    parser.add_argument("--skip-micro", action="store_true", help="No mide la colección sintética.")
    parser.add_argument("--keep", action="store_true", help="No borra la base ni la colección.")
    return parser.parse_args()


def main() -> int:
    """Ejecuta las tres mediciones e imprime el informe JSON."""

    args = _parse_args()
    gold_dir = args.gold_dir.resolve()
    if not gold_dir.is_dir():
        print(f"No existe el directorio Gold: {gold_dir}", file=sys.stderr)
        return 1
    database_url = args.database_url
    database = _create_database(database_url)
    report: dict[str, Any] = {
        "label": args.label,
        "entorno": {
            "gold_dir": str(gold_dir),
            "documentos": len(list(gold_dir.glob("*.md"))),
            "database": f"{make_url(database_url).host}:{make_url(database_url).port}/{database}",
            "qdrant_url": args.qdrant_url,
            "collection": args.collection,
        },
    }
    try:
        # Los logs de la aplicación van a stdout; el informe JSON debe quedar limpio.
        with contextlib.redirect_stdout(sys.stderr):
            report["sincronizacion"] = [
                asdict(scenario)
                for scenario in _sync_measurement(
                    label=args.label,
                    gold_dir=gold_dir,
                    database_url=database_url,
                    qdrant_url=args.qdrant_url,
                    collection=args.collection,
                )
            ]
            if not args.skip_micro:
                report["indice_sintetico"] = _micro_benchmark_traversal(
                    qdrant_url=args.qdrant_url, collection=args.collection
                )
            if not args.skip_embeddings:
                report["embeddings"] = _embedding_cost(gold_dir=gold_dir)
    finally:
        if not args.keep:
            _drop_database(database_url)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
