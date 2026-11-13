"""Garantiza una sola cabeza de Alembic.

Dos pistas de trabajo pueden crear revisiones en paralelo; si ambas cuelgan de
la misma revisión anterior, el grafo queda con dos cabezas y el ``upgrade``
falla. El fallo debe verse en CI, no al desplegar.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_tiene_una_sola_cabeza() -> None:
    """El grafo de migraciones tiene exactamente una cabeza."""

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))

    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, f"cabezas de Alembic: {heads}"
