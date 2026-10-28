"""Pruebas de configuración del corpus."""

from carvajal.config import SEEDS


def test_seeds_no_vacio() -> None:
    """El corpus requiere al menos una semilla de captura."""

    assert len(SEEDS) > 0
