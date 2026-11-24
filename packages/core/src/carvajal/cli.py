"""Interfaz de línea de comandos: capture, sync, index-qdrant, ask, faq e import."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

from carvajal.report import redact_credentials, safe_report_error

_redact_credentials = redact_credentials


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
    router_parser.add_argument(
        "--langsmith",
        action="store_true",
        help=(
            "Registra la corrida como experimento en LangSmith (spec 038). Sin esta "
            "bandera no se carga el módulo de monitoreo ni se toca la red."
        ),
    )

    agent_parser = subparsers.add_parser(
        "eval-agent",
        help=(
            "Compara el agente actual (routes) y el de herramientas (tools) con turnos "
            "completos en proceso (spec 044): verificaciones por pregunta, cifras con fuente, "
            "latencia, tokens y costo. Llama al proveedor configurado: gasta tokens."
        ),
    )
    agent_parser.add_argument(
        "--mode",
        action="append",
        choices=("routes", "tools"),
        help="Modo a evaluar; se puede repetir. Por defecto, ambos.",
    )
    agent_parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data") / "eval" / "generalization_questions.json",
        help="Conjunto de preguntas (generalización o routing_questions.json).",
    )
    agent_parser.add_argument(
        "--output", type=Path, default=None, help="Archivo Markdown donde guardar el informe."
    )
    agent_parser.add_argument(
        "--json", type=Path, default=None, help="Archivo JSON con los resultados por ítem."
    )
    agent_parser.add_argument(
        "--limit", type=int, default=None, help="Evalúa solo los primeros N ítems."
    )
    agent_parser.add_argument(
        "--ids",
        action="append",
        default=None,
        help="Ítems a evaluar (repetible o separados por comas), p. ej. --ids g-001,g-025.",
    )
    agent_parser.add_argument(
        "--price-input",
        type=float,
        default=None,
        help="Precio en USD por millón de tokens de entrada, para el costo por turno.",
    )
    agent_parser.add_argument(
        "--price-output",
        type=float,
        default=None,
        help="Precio en USD por millón de tokens de salida, para el costo por turno.",
    )
    agent_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Permite que --output o --json sobrescriban un archivo versionado en git. Nunca "
            "permite escribir sobre el conjunto de preguntas."
        ),
    )
    agent_parser.add_argument(
        "--langsmith",
        action="store_true",
        help=(
            "Registra cada modo como experimento en LangSmith. Sin esta bandera no se "
            "carga el módulo de monitoreo ni se toca la red de LangSmith."
        ),
    )

    agent_parser.add_argument(
        "--jev-ledger",
        type=Path,
        default=None,
        help=(
            "Activa Jev en el modo tools (gatekeeper y compuerta, como producción) y anota cada "
            "llamada en este libro, sin texto (spec 046, FR-031). Sin esta bandera no se "
            "construye el juez aunque haya TYPESAFE_API_KEY. Gasta créditos de TypeSafe: solo "
            "con permiso del owner."
        ),
    )
    agent_parser.add_argument(
        "--max-jev-router-calls",
        type=int,
        default=None,
        help="Cuota de llamadas del gatekeeper en esta corrida (con --jev-ledger).",
    )
    agent_parser.add_argument(
        "--max-jev-claims-calls",
        type=int,
        default=None,
        help="Cuota de llamadas de la compuerta de afirmaciones en esta corrida.",
    )

    gatekeeper_parser = subparsers.add_parser(
        "eval-gatekeeper",
        help=(
            "Mide el gatekeeper del modo tools (spec 046): exactitud de las reglas, el "
            "enrutador LLM y Jev sobre un conjunto congelado, rejilla de umbrales y Score. Con "
            "--judge jev o --smoke llama a TypeSafe y exige --ledger."
        ),
    )
    gatekeeper_parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data") / "eval" / "gatekeeper_046.json",
        help="Conjunto (gatekeeper_046.json o routing_questions.json).",
    )
    gatekeeper_parser.add_argument(
        "--judge",
        action="append",
        choices=("rules", "llm", "jev"),
        help="Juez a medir; se puede repetir. Por defecto, rules (jev con --replay-ledger).",
    )
    gatekeeper_parser.add_argument(
        "--smoke",
        action="store_true",
        help="Antes de medir, una llamada de humo a GET /v1/models (cuenta en la cuota).",
    )
    gatekeeper_parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="Libro de llamadas a Jev (JSONL sin texto); obligatorio con jev o --smoke.",
    )
    gatekeeper_parser.add_argument(
        "--max-jev-calls",
        type=int,
        default=None,
        help="Tope de llamadas a Jev de la corrida, humo incluido (research R11).",
    )
    gatekeeper_parser.add_argument(
        "--replay-ledger",
        type=Path,
        default=None,
        help="Reproduce los veredictos grabados en este libro, sin llamar a Jev (FR-032).",
    )
    gatekeeper_parser.add_argument(
        "--agent",
        action="store_true",
        help=(
            "Con --replay-ledger: corre el agente real (solo DeepSeek) sobre los ítems que "
            "terminaron en cautela o en respuesta directa."
        ),
    )
    gatekeeper_parser.add_argument(
        "--only",
        default="cautious,direct",
        help="Con --agent: categorías a correr (cautious, direct), separadas por comas.",
    )
    gatekeeper_parser.add_argument(
        "--output", type=Path, default=None, help="Archivo Markdown donde guardar el informe."
    )
    gatekeeper_parser.add_argument(
        "--json", type=Path, default=None, help="Archivo JSON con los resultados por ítem."
    )
    gatekeeper_parser.add_argument(
        "--force",
        action="store_true",
        help="Permite que --output o --json sobrescriban un archivo versionado en git.",
    )

    claims_parser = subparsers.add_parser(
        "eval-claims",
        help=(
            "Mide la compuerta de afirmaciones con Jev (spec 046): detección y falsas alarmas "
            "por umbral sobre respuestas congeladas. Llama a TypeSafe y exige --ledger."
        ),
    )
    claims_parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data") / "eval" / "claims_046.json",
        help="Conjunto de respuestas con su evidencia y su etiqueta.",
    )
    claims_parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="Libro de llamadas a Jev (JSONL sin texto); obligatorio.",
    )
    claims_parser.add_argument(
        "--max-jev-calls",
        type=int,
        default=None,
        help="Tope de llamadas a Jev de la corrida (research R11).",
    )
    claims_parser.add_argument(
        "--output", type=Path, default=None, help="Archivo Markdown donde guardar el informe."
    )
    claims_parser.add_argument(
        "--json", type=Path, default=None, help="Archivo JSON con los resultados por ítem."
    )
    claims_parser.add_argument(
        "--force",
        action="store_true",
        help="Permite que --output o --json sobrescriban un archivo versionado en git.",
    )

    simulate_parser = subparsers.add_parser(
        "simulate-chat",
        help=(
            "Simula conversaciones de varios turnos contra el chat real (spec 044): un "
            "cliente simulado con DeepSeek escribe los mensajes y cada escenario usa su "
            "propio hilo en Postgres. Gasta tokens del asistente y del cliente."
        ),
    )
    simulate_parser.add_argument(
        "--scenarios",
        type=Path,
        required=True,
        help="JSON de escenarios ({id, persona, goal, opening, max_turns, follow_ups, ...}).",
    )
    simulate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Carpeta de salida (transcripts.jsonl y summary.md). Por defecto, "
            "data/processed/simulations/<fecha UTC>."
        ),
    )
    simulate_parser.add_argument(
        "--limit", type=int, default=None, help="Corre solo los primeros N escenarios."
    )
    simulate_parser.add_argument(
        "--ids",
        action="append",
        default=None,
        help="Escenarios a correr (repetible o separados por comas), p. ej. --ids s-01,s-07.",
    )
    simulate_parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Conversaciones en paralelo (por defecto 3, para no saturar DeepSeek).",
    )
    simulate_parser.add_argument(
        "--price-input",
        type=float,
        default=None,
        help="Precio en USD por millón de tokens de entrada, para el costo estimado.",
    )
    simulate_parser.add_argument(
        "--price-output",
        type=float,
        default=None,
        help="Precio en USD por millón de tokens de salida, para el costo estimado.",
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

    prices_parser = subparsers.add_parser(
        "catalog-prices",
        help=(
            "Normaliza a precio por caja las capturas de fuentes/precios_<país>.csv (spec 039) "
            "y escribe precios.csv y el manifiesto; nunca estima ni convierte precios."
        ),
    )
    prices_parser.add_argument(
        "--country", choices=("CO", "MX"), required=True, help="País cuyas capturas se normalizan."
    )
    prices_parser.add_argument(
        "--out", type=Path, default=None, help="Directorio del catálogo (data/catalog/empaques)."
    )
    prices_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra las diferencias y los descartes sin escribir nada.",
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
        if args.langsmith:
            from carvajal.infrastructure.monitoring import (
                build_eval_client,
                commit_sha,
                dataset_de,
                register_experiment,
            )
            from carvajal.infrastructure.settings import Settings

            settings = Settings()
            client = build_eval_client(settings)
            if client is None:
                print(
                    "Experimento no registrado: falta LANGSMITH_API_KEY.",
                    file=sys.stderr,
                )
            else:
                fecha = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                for report in reports:
                    filas = [
                        {
                            "id": fila["id"],
                            "inputs": {
                                "question": fila["question"],
                                # El historial forma parte de la entrada real.
                                **({"turns": fila["turns"]} if fila["turns"] else {}),
                            },
                            # La verdad de referencia va al ejemplo del dataset;
                            # las predicciones, a la run del experimento.
                            "expected": {
                                "route": fila["expected_route"],
                                "intent": fila["expected_intent"],
                                **fila["expected"],
                            },
                            "outputs": {
                                "route": fila["route"],
                                "intent": fila["intent"],
                                "invalid": fila["invalid"],
                                "latency_ms": fila["latency_ms"],
                                "extracted": fila["extra"],
                            },
                            "latency_ms": fila["latency_ms"],
                        }
                        for fila in report.results
                    ]
                    metricas = {
                        "route_accuracy": round(report.route_accuracy, 4),
                        "intent_accuracy": round(report.intent_accuracy, 4),
                        "field_accuracy": round(report.field_accuracy, 4),
                        "material_violations": report.material_violations,
                        "invalid_outputs": report.invalid_outputs,
                        "latency_mean_ms": round(report.latency_mean_ms, 3),
                        "latency_p95_ms": round(report.latency_p95_ms, 3),
                        "model_calls": report.model_calls,
                    }
                    resultado = register_experiment(
                        client,
                        dataset=dataset_de(args.questions),
                        experiment=f"routing-{report.router}-{fecha}",
                        rows=filas,
                        metrics=metricas,
                        metadata={
                            "router": report.router,
                            "commit": commit_sha() or "desconocido",
                            "date": fecha,
                            "questions": str(args.questions),
                            "model": (
                                settings.decision_model
                                if report.router == "decision"
                                else settings.deepseek_model
                                if settings.llm_provider == "deepseek"
                                else settings.ollama_model
                            )
                            if report.router != "rules"
                            else "",
                        },
                        description="Evaluación del enrutador del agente (spec 036/038).",
                    )
                    print(resultado.as_message(), file=sys.stderr)
        return 0

    if args.command == "eval-agent":
        return _eval_agent(args)

    if args.command == "eval-gatekeeper":
        return _eval_gatekeeper(args)

    if args.command == "eval-claims":
        return _eval_claims(args)

    if args.command == "simulate-chat":
        return _simulate_chat(args)

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

    if args.command == "catalog-prices":
        from carvajal.catalog.loader import DEFAULT_DIR, CatalogError
        from carvajal.catalog.prices import build_prices, write_prices

        out = args.out or DEFAULT_DIR
        try:
            build = build_prices(out, args.country)
        except (CatalogError, ValueError) as error:
            print(f"No se pudieron leer los precios: {error}")
            return 1
        print(
            f"Precios {args.country}: {len(build.offers)} publicables · "
            f"{len(build.added)} nuevos · {len(build.changed)} cambiados · "
            f"{len(build.removed)} retirados · {len(build.discarded)} descartados."
        )
        for item in build.discarded:
            print(f"  - descartado {item['row']}: {item['reason']}")
        for item in build.changed:
            print(f"  - {item['sku']} ({item['source']}): {item['before']} → {item['after']}")
        if args.dry_run:
            return 0
        try:
            total = write_prices(out, build)
        except (OSError, ValueError) as error:
            print(f"No se reescribieron los precios: {error}")
            return 1
        print(f"precios.csv: {total} ofertas en total.")
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


def _tracked_by_git(path: Path) -> bool:
    """El archivo está versionado en el repositorio que lo contiene (sin git: no)."""

    import subprocess

    try:
        completed = subprocess.run(
            ["git", "-C", str(path.parent), "ls-files", "--error-unmatch", "--", path.name],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _unsafe_outputs(args: argparse.Namespace) -> list[str]:
    """Motivos para no escribir ``--output``/``--json``.

    Nunca el conjunto de preguntas (el congelado se leería y se sobrescribiría) ni el mismo
    archivo para ambos; un archivo versionado en git, solo con ``--force``.
    """

    targets = [("--output", args.output), ("--json", args.json)]
    resolved = {flag: path.resolve() for flag, path in targets if path is not None}
    problems: list[str] = []
    questions = args.questions.resolve()
    for flag, path in resolved.items():
        if path == questions:
            problems.append(f"{flag} apunta al conjunto de preguntas {args.questions}")
        elif not args.force and path.parent.is_dir() and _tracked_by_git(path):
            problems.append(f"{flag} apunta a un archivo versionado en git: {path}")
    if len(resolved) == 2 and resolved["--output"] == resolved["--json"]:
        problems.append("--output y --json apuntan al mismo archivo")
    return problems


def _eval_agent(args: argparse.Namespace) -> int:
    """``carvajal eval-agent``: informe comparativo de los modos ``routes`` y ``tools``.

    Corre turnos completos en proceso con persistencia en memoria (sin Postgres). Solo
    escribe donde indican ``--output`` y ``--json`` (nunca sobre el conjunto ni sobre un
    archivo versionado, salvo ``--force``); sin ``--langsmith`` no toca LangSmith.

    Códigos de salida: 0 si la corrida terminó; 1 si no pudo empezar o si algún modo no
    obtuvo ninguna respuesta del proveedor (todo en error o en respaldo por ``provider`` o
    ``timeout``); 130 si se interrumpió (se guarda lo ya corrido).
    """

    from carvajal.agent.toolagent.evaluation import (
        MODES,
        ModeReport,
        build_runtime,
        evaluate_mode,
        load_question_set,
        render_report,
        results_payload,
        select_items,
    )
    from carvajal.infrastructure.settings import Settings

    modes = list(dict.fromkeys(args.mode or MODES))
    ids = [part.strip() for value in args.ids or [] for part in value.split(",") if part.strip()]
    if args.ids is not None and not ids:
        # Una variable de shell vacía no debe convertirse en «todo el conjunto».
        print("--ids vacío: indica al menos un identificador.", file=sys.stderr)
        return 1
    if problems := _unsafe_outputs(args):
        for problem in problems:
            print(
                f"No se escribe el informe: {problem} (usa --force si es a propósito).",
                file=sys.stderr,
            )
        return 1
    try:
        question_set = load_question_set(args.questions)
        items = select_items(question_set.items, ids=ids, limit=args.limit)
    except (OSError, ValueError, KeyError) as error:
        print(f"No se pudo cargar el conjunto {args.questions}: {error}", file=sys.stderr)
        return 1
    settings = Settings()
    quotas = (args.max_jev_router_calls, args.max_jev_claims_calls)
    if args.jev_ledger is None and any(quota is not None for quota in quotas):
        print("Las cuotas de Jev necesitan --jev-ledger.", file=sys.stderr)
        return 1
    judge = None
    if args.jev_ledger is not None:
        # Spec 046 (FR-031): Jev solo con libro explícito, con las cuotas de la corrida.
        judge = _measurement_judge(settings, args.jev_ledger, "eval-agent --jev-ledger")
        if judge is None:
            return 1
        from carvajal.agent.gatekeeper.evaluation import QuotaJudge

        judge = QuotaJudge(judge, router_calls=quotas[0], claims_calls=quotas[1])
    try:
        runtime = (
            build_runtime(settings, modes)
            if judge is None
            else build_runtime(settings, modes, judge=judge)
        )
    except ModuleNotFoundError as error:
        print(f"eval-agent necesita el checkout del repositorio (apps/): {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"No se pudo preparar la evaluación: {error}", file=sys.stderr)
        return 1

    def progress(result: Any) -> None:
        state = "error" if result.error else "ok" if result.passed else "FALLO"
        print(f"[{result.mode}] {result.item.id}: {state}", file=sys.stderr)

    # Cada resultado entra a su informe en cuanto termina: una interrupción conserva lo
    # ya corrido (y pagado) y lo escribe con un aviso.
    reports: list[ModeReport] = []
    interrupted = False
    try:
        for mode in modes:
            report = ModeReport(mode=mode)
            reports.append(report)
            for item in items:
                if judge is not None:
                    # Cada llamada del ítem queda en el libro con su etiqueta.
                    from carvajal.agent.gatekeeper.evaluation import label_judge

                    label_judge(judge, "v3", item.id)
                evaluate_mode(runtime.drivers[mode], [item], progress=progress, report=report)
    except KeyboardInterrupt:
        interrupted = True
        print("Corrida interrumpida: se guarda lo ya corrido.", file=sys.stderr)
    finally:
        runtime.close()
        if judge is not None:
            judge.close()
    prices = {"price_in": args.price_input, "price_out": args.price_output}
    table = render_report(reports, question_set, interrupted=interrupted, **prices)
    print(table)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(table + "\n", encoding="utf-8")
        print(f"Informe guardado en {args.output}", file=sys.stderr)
    if args.json is not None:
        payload = results_payload(reports, question_set, interrupted=interrupted, **prices)
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"Resultados por ítem guardados en {args.json}", file=sys.stderr)
    if interrupted:
        return 130
    if args.langsmith:
        _register_agent_experiments(reports, args, settings)
    degraded = [report.mode for report in reports if report.degraded]
    if degraded:
        print(
            f"Ningún turno de {', '.join(degraded)} obtuvo respuesta del proveedor: revisa la "
            "clave, la red y el plazo.",
            file=sys.stderr,
        )
        return 1
    return 0


def _measurement_judge(settings: Any, ledger: Path, command: str) -> Any:
    """Juez de Jev para una medición: exige la clave y anota en ``ledger`` (FR-031).

    Se construye aunque ``JEV_ENABLED`` sea ``false``: el libro explícito es el opt-in.
    """

    from carvajal.adapters import judge as judge_module

    judge = judge_module.build_judge(settings, ledger=ledger, force=True)
    if judge is None:
        print(f"{command} necesita TYPESAFE_API_KEY en el entorno.", file=sys.stderr)
    return judge


def _write_outputs(args: argparse.Namespace, text: str, payload: dict[str, Any]) -> None:
    print(text)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"Informe guardado en {args.output}", file=sys.stderr)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"Resultados por ítem guardados en {args.json}", file=sys.stderr)


def _refuse_outputs(args: argparse.Namespace) -> bool:
    problems = _unsafe_outputs(args)
    for problem in problems:
        print(
            f"No se escribe el informe: {problem} (usa --force si es a propósito).",
            file=sys.stderr,
        )
    return bool(problems)


def _eval_gatekeeper(args: argparse.Namespace) -> int:
    """``carvajal eval-gatekeeper``: exactitud por juez, rejilla de umbrales y Score.

    Con ``jev`` o ``--smoke`` llama a TypeSafe: exige ``--ledger`` y respeta
    ``--max-jev-calls`` (humo incluido). Con ``--replay-ledger`` reproduce los veredictos sin
    red y, con ``--agent``, corre el agente real sobre los ítems en cautela o directos.
    """

    from carvajal.agent.gatekeeper import evaluation as gk_eval
    from carvajal.infrastructure.settings import Settings

    if args.agent and args.replay_ledger is None:
        print("--agent necesita --replay-ledger.", file=sys.stderr)
        return 1
    judges = list(dict.fromkeys(args.judge or (["jev"] if args.replay_ledger else ["rules"])))
    live = args.replay_ledger is None and ("jev" in judges or args.smoke)
    if live and args.ledger is None:
        print(
            "eval-gatekeeper con Jev (--judge jev o --smoke) exige --ledger (FR-031).",
            file=sys.stderr,
        )
        return 1
    if _refuse_outputs(args):
        return 1
    try:
        gate_set = gk_eval.load_gate_items(args.questions)
    except (OSError, ValueError, KeyError) as error:
        print(f"No se pudo cargar el conjunto {args.questions}: {error}", file=sys.stderr)
        return 1
    settings = Settings()

    if args.replay_ledger is not None:
        try:
            replay = gk_eval.ReplayJudge.from_ledger(args.replay_ledger)
        except (OSError, ValueError) as error:
            print(f"No se pudo leer el libro {args.replay_ledger}: {error}", file=sys.stderr)
            return 1
        if args.agent:
            only = {part.strip() for part in str(args.only).split(",") if part.strip()}
            driver, close = gk_eval.build_replay_driver(settings, replay)
            try:
                rows = gk_eval.replay_agent(
                    gate_set.items, replay, driver, settings=settings, only=only
                )
            finally:
                if close is not None:
                    close()
            context = {"questions": args.questions, "ledger": args.replay_ledger}
            _write_outputs(
                args,
                gk_eval.render_replay_report(rows, **context),
                gk_eval.replay_payload(rows, **context),
            )
            return 0
        jev: Any = replay
    elif live:
        jev = _measurement_judge(settings, args.ledger, "eval-gatekeeper")
        if jev is None:
            return 1
    else:
        jev = None

    budget = args.max_jev_calls
    smoke = None
    try:
        if args.smoke and args.replay_ledger is None:
            smoke = gk_eval.run_smoke(jev)
            budget = None if budget is None else max(0, budget - 1)
        llm_router = None
        if "llm" in judges:
            from carvajal.agent.routers import build_router

            llm_router = build_router("llm")
        reports = gk_eval.eval_gatekeeper(
            gate_set.items,
            judges=judges,
            jev=jev,
            llm_router=llm_router,
            settings=settings,
            max_jev_calls=budget,
        )
    finally:
        if jev is not None and hasattr(jev, "close"):
            jev.close()
    context = {
        "questions": args.questions,
        "settings": settings,
        "smoke": smoke,
        "gate_set": gate_set,
    }
    _write_outputs(
        args,
        gk_eval.render_gatekeeper_report(reports, **context),
        gk_eval.gatekeeper_payload(reports, **context),
    )
    return 0


def _eval_claims(args: argparse.Namespace) -> int:
    """``carvajal eval-claims``: detección y falsas alarmas de la compuerta por umbral."""

    from carvajal.agent.gatekeeper import evaluation as gk_eval
    from carvajal.infrastructure.settings import Settings

    if args.ledger is None:
        print("eval-claims llama a Jev y exige --ledger (FR-031).", file=sys.stderr)
        return 1
    if _refuse_outputs(args):
        return 1
    try:
        claims_set = gk_eval.load_claims_set(args.questions)
    except (OSError, ValueError, KeyError) as error:
        print(f"No se pudo cargar el conjunto {args.questions}: {error}", file=sys.stderr)
        return 1
    settings = Settings()
    judge = _measurement_judge(settings, args.ledger, "eval-claims")
    if judge is None:
        return 1
    try:
        report = gk_eval.eval_claims(
            claims_set.items, judge, settings=settings, max_jev_calls=args.max_jev_calls
        )
    finally:
        judge.close()
    context = {"questions": args.questions, "claims_set": claims_set}
    _write_outputs(
        args,
        gk_eval.render_claims_report(report, **context),
        gk_eval.claims_payload(report, **context),
    )
    return 0


def _simulate_chat(args: argparse.Namespace) -> int:
    """``carvajal simulate-chat``: conversaciones simuladas contra el ``ChatService`` real.

    Escribe ``transcripts.jsonl`` (una conversación por línea, en cuanto termina) y
    ``summary.md`` en ``--output-dir``; nunca sobrescribe una corrida anterior.

    Códigos de salida: 0 si la corrida terminó; 1 si no pudo empezar o si ningún turno
    obtuvo respuesta (todos en error); 130 si se interrumpió (se guarda lo ya corrido).
    """

    from carvajal.agent.toolagent.simulation import (
        build_real_simulator,
        load_scenarios,
        render_summary,
        run_simulation,
        runtime_mode,
        select_scenarios,
    )
    from carvajal.infrastructure.settings import Settings

    ids = [part.strip() for value in args.ids or [] for part in value.split(",") if part.strip()]
    if args.ids is not None and not ids:
        print("--ids vacío: indica al menos un identificador.", file=sys.stderr)
        return 1
    if args.concurrency < 1:
        print("--concurrency debe ser un entero ≥ 1.", file=sys.stderr)
        return 1
    try:
        scenarios = select_scenarios(load_scenarios(args.scenarios), ids=ids, limit=args.limit)
    except (OSError, ValueError) as error:
        print(f"No se pudieron cargar los escenarios {args.scenarios}: {error}", file=sys.stderr)
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir: Path = args.output_dir or Path("data") / "processed" / "simulations" / stamp
    transcripts_file = output_dir / "transcripts.jsonl"
    summary_file = output_dir / "summary.md"
    if transcripts_file.exists() or summary_file.exists():
        print(f"{output_dir} ya tiene una corrida: elige otra carpeta.", file=sys.stderr)
        return 1
    settings = Settings()
    try:
        simulator = build_real_simulator(settings)
        mode = runtime_mode()
    except ModuleNotFoundError as error:
        print(
            f"simulate-chat necesita el checkout del repositorio (apps/): {error}", file=sys.stderr
        )
        return 1
    except Exception as error:
        print(f"No se pudo preparar la simulación: {error}", file=sys.stderr)
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)

    def write(transcript: Any) -> None:
        with transcripts_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(transcript.as_dict(), ensure_ascii=False, default=str) + "\n")
        state = transcript.finished_by + (f" ({transcript.error})" if transcript.error else "")
        print(
            f"[{transcript.scenario.id}] {len(transcript.turns)} turnos: {state}", file=sys.stderr
        )

    collected: list[Any] = []

    def on_transcript(transcript: Any) -> None:
        collected.append(transcript)
        write(transcript)

    interrupted = False
    try:
        transcripts = run_simulation(
            simulator, scenarios, concurrency=args.concurrency, on_transcript=on_transcript
        )
    except KeyboardInterrupt:
        interrupted = True
        print("Simulación interrumpida: se guarda lo ya corrido.", file=sys.stderr)
        order = {scenario.id: n for n, scenario in enumerate(scenarios)}
        transcripts = sorted(collected, key=lambda t: order.get(t.scenario.id, 0))
    summary = render_summary(
        transcripts,
        scenarios_path=str(args.scenarios),
        price_in=args.price_input,
        price_out=args.price_output,
        header={
            "Fecha (UTC)": stamp,
            "Modo del agente": f"{mode} (AGENT_MODE={settings.agent_mode})",
            "Modelo": settings.deepseek_model,
            "Concurrencia": args.concurrency,
            "Transcripciones": str(transcripts_file),
        },
        interrupted=interrupted,
    )
    summary_file.write_text(summary + "\n", encoding="utf-8")
    print(summary)
    print(f"Transcripciones en {transcripts_file}; resumen en {summary_file}", file=sys.stderr)
    if interrupted:
        return 130
    turns = [turn for transcript in transcripts for turn in transcript.turns]
    if not turns or all(turn.error for turn in turns):
        print("Ningún turno obtuvo respuesta: revisa Postgres, Qdrant y DeepSeek.", file=sys.stderr)
        return 1
    return 0


def _register_agent_experiments(
    reports: Sequence[Any], args: argparse.Namespace, settings: Any
) -> None:
    """Registra cada modo como experimento de LangSmith (red externa, solo con la bandera)."""

    from carvajal.agent.toolagent.evaluation import experiment_rows
    from carvajal.infrastructure.monitoring import (
        build_eval_client,
        commit_sha,
        dataset_de,
        register_experiment,
    )

    client = build_eval_client(settings)
    if client is None:
        print("Experimento no registrado: falta LANGSMITH_API_KEY.", file=sys.stderr)
        return
    fecha = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    text_model = (
        settings.deepseek_model if settings.llm_provider == "deepseek" else settings.ollama_model
    )
    for report in reports:
        resultado = register_experiment(
            client,
            # Dataset propio: ``eval-router`` usa ``dataset_de`` sobre el mismo archivo de
            # enrutamiento con otras entradas y otra verdad de referencia.
            dataset=f"{dataset_de(args.questions)}-agent",
            experiment=f"agent-{report.mode}-{fecha}",
            rows=experiment_rows(report),
            metrics=report.metrics(price_in=args.price_input, price_out=args.price_output),
            metadata={
                "mode": report.mode,
                "commit": commit_sha() or "desconocido",
                "date": fecha,
                "questions": str(args.questions),
                "router": settings.agent_router if report.mode == "routes" else "",
                "model": settings.deepseek_model if report.mode == "tools" else text_model,
                "price_input_usd_per_million": args.price_input,
                "price_output_usd_per_million": args.price_output,
            },
            description="Evaluación comparativa del agente routes frente a tools (spec 044).",
        )
        print(resultado.as_message(), file=sys.stderr)


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
        print(f"  ! {safe_report_error(error)}")


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
        print(f"  ! {safe_report_error(error)}")


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
