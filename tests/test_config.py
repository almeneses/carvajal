"""Pruebas de configuración del corpus."""

from carvajal.config import EXTERNAL_SEEDS, SEEDS


def test_seeds_no_vacio() -> None:
    """El corpus requiere al menos una semilla de captura."""

    assert len(SEEDS) > 0


def test_external_seeds_apuntan_a_dominios_de_terceros() -> None:
    """Las semillas externas no pertenecen al dominio oficial de Carvajal."""

    assert len(EXTERNAL_SEEDS) > 0
    assert all("carvajal.com" not in url for url in EXTERNAL_SEEDS)
