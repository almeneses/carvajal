"""Pruebas de la interfaz de línea de comandos."""

from carvajal.cli import build_parser


def test_parser_lista_subcomandos_validos() -> None:
    """El parser reconoce los subcomandos definidos."""

    parser = build_parser()

    assert parser.parse_args(["list"]) is not None
    assert parser.parse_args(["status"]) is not None
