"""Interfaz de línea de comandos: capture / ask / status / list."""

import argparse

from carvajal.config import SEEDS
from carvajal.pipeline import capture as run_capture
from carvajal.rag import ask as run_ask
from carvajal.rag import build_retriever, load_gold_documents


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos de la CLI.

    Returns:
        Parser con los subcomandos ``capture``, ``status`` y ``list``.
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
        "--list-seeds",
        action="store_true",
        help="Lista las semillas configuradas y termina.",
    )

    ask_parser = subparsers.add_parser(
        "ask",
        help="Genera una respuesta RAG para una pregunta del corpus.",
    )
    ask_parser.add_argument(
        "question",
        help="Pregunta del usuario.",
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
        seeds = (*SEEDS, *(args.seed or []))
        pages = run_capture(seeds)
        for page in pages:
            print(f"Capturada: {page.url} -> data/gold/{page.slug}.md")
        return 0

    if args.command == "ask":
        retriever = build_retriever(load_gold_documents())
        answer = run_ask(args.question, retriever)
        print(answer)
        return 0

    if args.command == "list":
        for seed in SEEDS:
            print(seed)
        return 0

    if args.command == "status":
        print(f"Semillas configuradas: {len(SEEDS)}")
        return 0

    parser.print_help()
    return 0
