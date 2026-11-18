"""Interfaz de línea de comandos: capture, sync, index-qdrant, ask, faq e import."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError

from carvajal.adapters.storage.database import build_engine, build_session_factory
from carvajal.config import (
    EXTERNAL_DIR,
    EXTERNAL_SEEDS,
    GOLD_DIR,
    INDEX_FILE,
    QUESTIONS_FILE,
    REQUEST_DELAY_SECONDS,
    RESULTS_FILE,
    SEEDS,
)
from carvajal.corpus import load_gold_documents

if TYPE_CHECKING:
    from carvajal.application.sync import CorpusLayer, SyncReport
    from carvajal.fetching import WebClient
    from carvajal.importer import ImportReport
    from carvajal.infrastructure.settings import Settings
    from carvajal.pipeline import CaptureReport

_CREDENTIALS_IN_URL = re.compile(r"(?P<scheme>://[^:/@\s]+:)[^@\s]+@")


def _redact_credentials(message: str) -> str:
    """Oculta la contraseña de una URL sin perder usuario, host ni diagnóstico."""

    return _CREDENTIALS_IN_URL.sub(r"\g<scheme>***@", message)


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos de la CLI.

    Returns:
        Parser con los subcomandos ``capture``, ``ask``, ``eval``, ``status`` y ``list``.
    """

    parser = argparse.ArgumentParser(
        prog="carvajal",
        description="Pipeline de captura y curaduría del corpus web de Carvajal.",
    )
    subparsers = parser.add_subparsers(dest="command")

    capture_parser = subparsers.add_parser(
        "capture",
        help="Captura las páginas de las semillas y genera el entregable Gold.",
    )
    capture_parser.add_argument(
        "--seed",
        action="append",
        default=None,
        help="Semilla adicional a capturar (puede repetirse).",
    )
    capture_parser.add_argument(
        "--scope",
        choices=("all", "oficial", "terceros"),
        default="all",
        help="Qué semillas capturar: todo, solo el sitio oficial o solo fuentes de terceros.",
    )
    capture_parser.add_argument(
        "--list-seeds",
        action="store_true",
        help="Lista las semillas configuradas y termina.",
    )
    capture_parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Segundos de espera entre peticiones al mismo host (rate limit).",
    )
    capture_parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="No consultar robots.txt (solo para pruebas locales).",
    )
    capture_parser.add_argument(
        "--solo-gold",
        action="store_true",
        help="Se detiene en Gold: no importa a PostgreSQL ni indexa en Qdrant.",
    )

    ask_parser = subparsers.add_parser(
        "ask",
        help="Genera una respuesta RAG para una pregunta del corpus.",
    )
    ask_parser.add_argument(
        "question",
        help="Pregunta del usuario.",
    )

    eval_parser = subparsers.add_parser(
        "eval",
        help="Ejecuta las preguntas de prueba y genera el informe de resultados.",
    )
    eval_parser.add_argument(
        "--questions",
        type=Path,
        default=QUESTIONS_FILE,
        help="Archivo JSON con las preguntas de prueba.",
    )
    eval_parser.add_argument(
        "--output",
        type=Path,
        default=RESULTS_FILE,
        help="Archivo Markdown donde se escriben los resultados.",
    )

    summarize_parser = subparsers.add_parser(
        "summarize",
        help="Genera un resumen ejecutivo del corpus (tarea de LLM).",
    )
    summarize_parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs") / "resumen.md",
        help="Archivo Markdown donde se escribe el resumen.",
    )
    summarize_parser.add_argument(
        "--focus",
        default=None,
        help="Tema opcional sobre el que centrar el resumen.",
    )

    faq_parser = subparsers.add_parser(
        "faq",
        help="Genera una FAQ del corpus para un cliente primerizo (tarea de LLM).",
    )
    faq_parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs") / "faq.md",
        help="Archivo Markdown donde se escribe la FAQ.",
    )

    subparsers.add_parser(
        "import",
        help="Importa el corpus externo a la capa extendida (solo recuperación).",
    )

    migrate_parser = subparsers.add_parser(
        "migrate-gold",
        help=(
            "Migra el corpus Gold (data/gold) a la base de datos relacional. "
            "El directorio es autoritativo para su capa: los documentos que ya no "
            "existan en disco se podan de la base."
        ),
    )
    migrate_parser.add_argument(
        "--gold", type=Path, default=GOLD_DIR, help="Directorio Gold a importar."
    )
    migrate_parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Permite importar un directorio Gold vacío y podar toda la capa.",
    )

    index_parser = subparsers.add_parser(
        "index-qdrant",
        help="Indexa data/gold en Qdrant con IDs estables y poda lo obsoleto.",
    )
    index_parser.add_argument(
        "--gold", type=Path, default=GOLD_DIR, help="Directorio Gold del núcleo a indexar."
    )
    index_parser.add_argument(
        "--layer",
        choices=("core", "extended", "all"),
        default="core",
        help="Capa a indexar (por defecto, el núcleo).",
    )
    index_parser.add_argument(
        "--recreate",
        action="store_true",
        help="Borra la colección y la reconstruye desde cero.",
    )
    index_parser.add_argument(
        "--no-prune",
        action="store_true",
        help="No elimina puntos que ya no pertenecen al corpus (no recomendado).",
    )
    index_parser.add_argument(
        "--batch-size", type=int, default=32, help="Fragmentos por lote de embeddings."
    )

    sync_parser = subparsers.add_parser(
        "sync",
        help="Reconcilia Gold con PostgreSQL y Qdrant de forma incremental.",
    )
    sync_parser.add_argument(
        "--layer",
        choices=("core", "extended", "all"),
        default="core",
        help="Capa a sincronizar (por defecto, el núcleo).",
    )
    sync_parser.add_argument(
        "--prune",
        action="store_true",
        help="Elimina de ambos almacenes los documentos que ya no están en Gold.",
    )
    sync_parser.add_argument(
        "--verify",
        action="store_true",
        help="No escribe: compara huellas y conteos y sale con código 1 si difieren.",
    )
    sync_parser.add_argument(
        "--adopt",
        action="store_true",
        help="Completa source_key y raw_hash en el registro legado antes de sincronizar.",
    )

    router_parser = subparsers.add_parser(
        "eval-router",
        help=(
            "Compara enrutadores del agente (spec 036) sobre data/eval/routing_questions.json: "
            "ruta, datos extraídos, reglas de material, latencia y llamadas al modelo."
        ),
    )
    router_parser.add_argument(
        "--router",
        action="append",
        choices=("rules", "llm", "decision"),
        help="Enrutador a evaluar; se puede repetir. Por defecto, rules.",
    )
    router_parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data") / "eval" / "routing_questions.json",
        help="Conjunto de evaluación del enrutamiento.",
    )
    router_parser.add_argument(
        "--output", type=Path, default=None, help="Archivo Markdown donde guardar la tabla."
    )

    catalog_parser = subparsers.add_parser(
        "catalog-build",
        help=(
            "Regenera las referencias de México del catálogo de empaques (spec 036) desde "
            "el corpus capturado; conserva las filas curadas a mano de otros países."
        ),
    )
    catalog_parser.add_argument(
        "--corpus", type=Path, required=True, help="Carpeta del corpus externo con las fichas HTML."
    )
    catalog_parser.add_argument(
        "--captured-at",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        required=True,
        help="Fecha de captura del corpus (AAAA-MM-DD); se guarda en cada fila.",
    )
    catalog_parser.add_argument(
        "--out", type=Path, default=None, help="Directorio del catálogo (data/catalog/empaques)."
    )
    catalog_parser.add_argument(
        "--allow-missing",
        action="store_true",
        help=(
            "Acepta que el corpus no incluya SKUs ya presentes en referencias.csv; "
            "sin esta bandera la orden aborta para no borrar referencias por un corpus parcial."
        ),
    )

    subparsers.add_parser(
        "status",
        help="Muestra el estado del corpus (semillas y entregables Gold).",
    )

    subparsers.add_parser(
        "list",
        help="Lista las semillas del corpus.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la CLI.

    Args:
        argv: Argumentos de línea de comandos (por defecto: ``sys.argv[1:]``).

    Returns:
        Código de salida del comando.
    """

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "capture":
        from carvajal.pipeline import capture as run_capture

        seeds = (*_seeds_for(args.scope), *(args.seed or []))
        if args.list_seeds:
            for seed in seeds:
                print(seed)
            return 0
        client = _build_client(args)
        report = run_capture(seeds, client=client)
        for page in report.pages:
            print(f"Capturada: {page.url} -> data/gold/{page.slug}.md")

        if not args.solo_gold and report.slugs:
            from carvajal.application.sync import CorpusLayer
            from carvajal.infrastructure.settings import Settings

            settings = Settings()
            try:
                sync_report = _sync_corpus(
                    settings,
                    [CorpusLayer("core", (settings.gold_dir,))],
                    only=set(report.slugs.values()),
                )
            except Exception as error:  # el corpus queda en Gold y se repara con sync
                report.errors.append(f"No se pudo sincronizar el corpus: {error}")
            else:
                print_sync_report(sync_report)
                report.errors.extend(sync_report.errors)

        print_report(report)
        return 0 if not report.errors else 1

    if args.command == "ask":
        from carvajal.rag import ask as run_ask
        from carvajal.rag import build_retriever, build_system_prompt

        documents = load_gold_documents()
        retriever = build_retriever(documents)
        answer = run_ask(args.question, retriever, system_prompt=build_system_prompt(documents))
        print(answer)
        return 0

    if args.command == "eval":
        from carvajal.rag import (
            build_retriever,
            build_system_prompt,
            evaluate,
            load_questions,
            render_results_markdown,
        )

        documents = load_gold_documents()
        retriever = build_retriever(documents)
        questions = load_questions(args.questions)
        results = evaluate(questions, retriever, system_prompt=build_system_prompt(documents))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_results_markdown(results), encoding="utf-8")
        print(f"Evaluación completada: {len(results)} preguntas -> {args.output}")
        return 0

    if args.command == "summarize":
        from carvajal.rag import summarize

        documents = load_gold_documents()
        content = summarize(documents, focus=args.focus)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(_task_document("Resumen ejecutivo del corpus", content), "utf-8")
        print(f"Resumen generado: {args.output}")
        return 0

    if args.command == "faq":
        from carvajal.rag import generate_faq

        documents = load_gold_documents()
        content = generate_faq(documents)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(_task_document("FAQ del corpus", content), "utf-8")
        print(f"FAQ generada: {args.output}")
        return 0

    if args.command == "import":
        from carvajal.importer import import_external

        try:
            report = import_external()
        except FileNotFoundError as error:
            print(error)
            return 1
        print_import_report(report)
        return 0

    if args.command == "eval-router":
        from carvajal.agent.routers import build_router
        from carvajal.agent.routers.decision import DecisionModelUnavailable
        from carvajal.agent.routers.evaluation import evaluate_router, load_items, render_report
        from carvajal.catalog.loader import load_catalog

        items = load_items(args.questions)
        catalog = load_catalog()
        reports = []
        for name in args.router or ["rules"]:
            try:
                router = build_router(name)
            except (DecisionModelUnavailable, ValueError) as error:
                print(f"No se evaluó el enrutador {name}: {error}")
                return 1
            reports.append(evaluate_router(router, items, catalog=catalog))
        table = render_report(reports)
        print(table)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(table + "\n", encoding="utf-8")
            print(f"Tabla guardada en {args.output}")
        return 0

    if args.command == "catalog-build":
        from carvajal.catalog.build import (
            build_mx_references,
            write_references,
        )
        from carvajal.catalog.loader import DEFAULT_DIR, REFERENCES, load_catalog

        if not args.corpus.is_dir():
            print(f"No existe la carpeta del corpus: {args.corpus}")
            return 1
        out = args.out or DEFAULT_DIR
        references_file = out / REFERENCES
        if not out.is_dir():
            print(
                f"No existe el directorio del catálogo: {out}; créalo antes de regenerar "
                "para no escribir en una ruta equivocada."
            )
            return 1
        if not references_file.is_file() and not args.allow_missing:
            print(
                f"No existe {references_file}: revisa --out o usa --allow-missing para "
                "crear un catálogo nuevo (se perderían las filas curadas a mano)."
            )
            return 1
        pages = sorted(args.corpus.rglob("*.html"))
        references, notes = build_mx_references(pages, args.captured_at)
        if not references:
            print("No se encontró ninguna ficha de la tienda de México en el corpus.")
            return 1
        manifest = (
            "referencias_mx",
            {
                "origin": "Tienda en línea de Carvajal Empaques México (fichas de producto)",
                "generated_by": "carvajal catalog-build",
                "captured_at": args.captured_at.isoformat(),
                "pages_read": len(pages),
                "rows": len(references),
                "notes": notes,
            },
        )

        def _valida_catalogo() -> None:
            load_catalog.cache_clear()
            try:
                load_catalog(str(out))
            except Exception as error:
                # ``_replace_all`` restaura ambos archivos; la CLI solo informa.
                raise ValueError(f"el catálogo no valida: {error}") from error

        try:
            # El catálogo se valida dentro de la transacción: si no valida, la
            # restauración deja CSV y manifiesto anteriores intactos.
            total = write_references(
                out,
                references,
                allow_missing=args.allow_missing,
                notes=notes,
                manifest=manifest,
                check=_valida_catalogo,
            )
        except (OSError, ValueError) as error:
            print(f"No se reescribió el catálogo: {error}")
            return 1
        print(f"Referencias de México: {len(references)} (total en el archivo: {total}).")
        for note in notes:
            print(f"  - {note}")
        return 0

    if args.command == "migrate-gold":
        from carvajal.adapters.storage.import_corpus import GoldImportError, migrate_gold

        engine: Engine | None = None
        try:
            # La URL se valida al construir el motor (ArgumentError), antes de conectar.
            try:
                engine = build_engine()
            except (SQLAlchemyError, ValueError) as error:
                print(f"Configuración de base de datos inválida: {_redact_credentials(str(error))}")
                return 1
            try:
                with build_session_factory(engine).begin() as session:
                    report = migrate_gold(session, gold_dir=args.gold, allow_empty=args.allow_empty)
            except (OperationalError, InterfaceError) as error:
                print(f"No se pudo conectar a la base de datos: {_redact_credentials(str(error))}")
                return 1
            except GoldImportError as error:
                print(f"Corpus Gold no importable: {error}")
                return 1
        finally:
            if engine is not None:
                engine.dispose()
        print(f"Documentos importados: {report.documents}")
        print(f"Fragmentos importados: {report.chunks}")
        if report.removed:
            print(f"Documentos podados: {report.removed}")
        return 0

    if args.command == "index-qdrant":
        from carvajal.adapters.embeddings import EmbeddingError, OllamaEmbedder
        from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
        from carvajal.application.sync import corpus_layers
        from carvajal.indexing.qdrant import index_gold
        from carvajal.infrastructure.settings import Settings

        settings = Settings()
        layers = corpus_layers(args.gold, settings.external_dir, which=args.layer)
        areas = [
            (directory, layer.name)
            for layer in layers
            for directory in layer.directories
            if directory.is_dir()
        ]
        if not areas:
            print(f"No hay documentos Gold en la capa {args.layer}.")
            return 1
        store = QdrantVectorStore(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection=settings.qdrant_collection,
            dimensions=settings.qdrant_dimensions,
            distance=settings.qdrant_distance,
        )
        try:
            report = index_gold(
                gold_dirs=areas,
                store=store,
                embedder=OllamaEmbedder(base_url=settings.ollama_base_url),
                recreate=args.recreate,
                prune=not args.no_prune,
                batch_size=max(1, args.batch_size),
                prune_layers=tuple(layer.name for layer in layers),
            )
        except (FileNotFoundError, ValueError, EmbeddingError) as error:
            print(f"No se pudo indexar: {error}")
            return 1
        except Exception as error:  # Qdrant inaccesible
            print(f"No se pudo contactar con Qdrant en {settings.qdrant_url}: {error}")
            return 1
        finally:
            store.close()
        print(f"Colección: {report.collection}")
        print(f"Documentos: {report.documents}")
        print(f"Fragmentos: {report.chunks}")
        print(f"Puntos indexados: {report.indexed}")
        print(f"Puntos obsoletos eliminados: {report.deleted}")
        print(f"Puntos finales: {report.points}")
        print(f"Dimensión: {report.dimensions}")
        print(f"Duración: {report.elapsed_ms} ms")
        return 0

    if args.command == "sync":
        from carvajal.application.adopt import adopt_registry
        from carvajal.application.sync import corpus_layers
        from carvajal.gold import corpus_lock
        from carvajal.infrastructure.settings import Settings
        from carvajal.registry import corpus_areas_for

        settings = Settings()
        if args.adopt and args.verify:
            print("--adopt escribe el registro y --verify promete no escribir; usa uno de los dos.")
            return 2
        if args.adopt:
            root = settings.gold_dir.parent
            with corpus_lock(root):
                adoption = adopt_registry(
                    corpus_areas_for(settings.gold_dir, settings.external_dir),
                    raw_dir=settings.raw_dir,
                )
            print(
                f"Adopción: {len(adoption.adopted)} adoptadas, "
                f"{len(adoption.conflicts)} archivadas como legacy, "
                f"{adoption.unchanged} sin cambios."
            )
            for conflict in adoption.conflicts:
                print(
                    f"  ! {conflict.slug}: {conflict.source_key} "
                    f"(la fuente vigente es {conflict.conflicts_with})"
                )
        layers = corpus_layers(settings.gold_dir, settings.external_dir, which=args.layer)
        if not layers:
            print(f"No hay documentos Gold en la capa {args.layer}.")
            return 0
        try:
            report = _sync_corpus(
                settings,
                layers,
                prune=args.prune,
                verify=args.verify,
            )
        except Exception as error:  # Qdrant, PostgreSQL o un Gold corrupto
            print(f"No se pudo sincronizar: {_redact_credentials(str(error))}")
            return 1
        print_sync_report(report)
        for difference in report.differences:
            print(f"  ! {difference}")
        return 0 if report.ok else 1

    if args.command == "list":
        for seed in (*SEEDS, *EXTERNAL_SEEDS):
            print(seed)
        return 0

    if args.command == "status":
        print(f"Semillas oficiales: {len(SEEDS)}")
        print(f"Semillas de terceros: {len(EXTERNAL_SEEDS)}")
        _print_corpus_status()
        return 0

    parser.print_help()
    return 0


def _build_client(args: argparse.Namespace) -> WebClient:
    """Construye el cliente HTTP con el rate limit y la política pedidos."""

    from carvajal.fetching import WebClient

    delay = REQUEST_DELAY_SECONDS if args.delay is None else args.delay
    return WebClient(delay=delay, respect_robots=not args.ignore_robots)


def _task_document(title: str, content: str) -> str:
    """Envuelve la salida de una tarea de LLM con un encabezado y la fecha."""

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"# {title}\n\n> Generado con LLM sobre el corpus Gold · {generated}\n\n{content}\n"


def print_report(report: CaptureReport) -> None:
    """Imprime las métricas de la ejecución del pipeline."""

    print()
    print("Resumen de la captura")
    print(f"  Descubiertas (semillas): {report.discovered}")
    print(f"  Descargadas:             {report.fetched}")
    print(f"  Escritas en Gold:        {report.written}")
    print(f"     - actualizadas:       {len(report.updated)}")
    print(f"     - sin cambios:        {len(report.unchanged)}")
    print(f"  Descartadas:             {report.discarded}")
    print(f"     - por robots.txt:     {len(report.robots_denied)}")
    print(f"     - duplicadas:         {len(report.duplicates)}")
    print(f"     - con error:          {len(report.errors)}")
    print(f"  Caracteres:              {report.chars:,}")
    print(f"  Tokens (estimados):      {report.tokens_estimate:,}")
    print(f"  Duración:                {report.duration_seconds} s")
    for error in report.errors:
        print(f"  ! {error}")


def _sync_corpus(
    settings: Settings,
    layers: Sequence[CorpusLayer],
    *,
    only: set[str] | None = None,
    prune: bool = False,
    verify: bool = False,
) -> SyncReport:
    """Construye los adaptadores reales y ejecuta la sincronización."""

    from carvajal.adapters.embeddings import OllamaEmbedder
    from carvajal.adapters.storage.sync_postgres import PostgresSync
    from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
    from carvajal.adapters.vector.sync_qdrant import QdrantSync
    from carvajal.application.sync import sync_corpus

    engine = build_engine()
    store = QdrantVectorStore(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        collection=settings.qdrant_collection,
        dimensions=settings.qdrant_dimensions,
        distance=settings.qdrant_distance,
    )
    try:
        return sync_corpus(
            layers,
            database=PostgresSync(build_session_factory(engine)),
            index=QdrantSync(
                store,
                OllamaEmbedder(base_url=settings.ollama_base_url),
            ),
            only=only,
            prune=prune,
            verify=verify,
            lock_root=settings.gold_dir.parent,
        )
    finally:
        store.close()
        engine.dispose()


def print_sync_report(report: SyncReport) -> None:
    """Imprime el resultado de una sincronización o verificación."""

    print()
    print("Verificación del corpus" if report.verified else "Sincronización del corpus")
    print(f"  Nuevos:                  {report.new}")
    print(f"  Actualizados:            {report.updated}")
    print(f"  Sin cambios:             {report.unchanged}")
    print(f"  Podados:                 {report.pruned}")
    print(f"  Puntos indexados:        {report.indexed}")
    print(f"  Documentos importados:   {report.documents}")
    print(f"  Fragmentos importados:   {report.chunks}")
    for error in report.errors:
        print(f"  ! {error}")


def print_import_report(report: ImportReport) -> None:
    """Imprime las métricas de la importación del corpus externo."""

    print()
    print("Resumen de la importación")
    print(f"  Importados:      {report.imported}")
    print(f"  Caracteres:      {report.chars:,}")
    print(f"  Duplicados:      {report.duplicates}")
    print(f"  Descartados:     {report.skipped}")


def _print_corpus_status() -> None:
    """Resume el corpus: núcleo, capa extendida y tamaño total."""

    core = sorted(GOLD_DIR.glob("*.md"))
    external = sorted(EXTERNAL_DIR.glob("*/gold/*.md"))
    official = sum(1 for path in core if _front_matter_value(path, "source_type") == "oficial")
    chars = sum(path.stat().st_size for path in (*core, *external))

    print(f"Documentos en Gold (núcleo): {len(core)}")
    print(f"  - oficiales: {official}")
    print(f"  - terceros:  {len(core) - official}")
    print(f"Capa extendida: {len(external)}")
    for source_dir in sorted(
        path for path in EXTERNAL_DIR.glob("*") if path.is_dir() and not path.name.endswith(".tmp")
    ):
        count = len(list((source_dir / "gold").glob("*.md")))
        print(f"  - {source_dir.name}: {count}")
    print(f"Caracteres: {chars:,} (aprox. {round(chars / 4):,} tokens)")
    print(f"Índice JSONL: {'sí' if INDEX_FILE.exists() else 'no'}")


def _front_matter_value(path: Path, key: str) -> str:
    """Lee un campo del front matter de un Markdown Gold."""

    for line in path.read_text(encoding="utf-8").splitlines()[1:20]:
        if line.startswith("---"):
            break
        name, separator, value = line.partition(":")
        if separator and name.strip() == key:
            return value.strip()
    return ""


def _seeds_for(scope: str) -> tuple[str, ...]:
    """Selecciona las semillas según el alcance solicitado."""

    if scope == "oficial":
        return SEEDS
    if scope == "terceros":
        return EXTERNAL_SEEDS
    return (*SEEDS, *EXTERNAL_SEEDS)
