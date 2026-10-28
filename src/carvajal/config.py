"""Configuración del pipeline: semillas, capas y política HTTP."""

from pathlib import Path

BASE_URL: str = "https://www.carvajal.com"
USER_AGENT: str = "carvajal-corpus/0.1 (+https://github.com/markodavid/carvajal)"
TIMEOUT_SECONDS: int = 30

GOLD_DIR: Path = Path("data") / "gold"

SEEDS: tuple[str, ...] = (
    "https://www.carvajal.com/",
    "https://www.carvajal.com/quienes-somos/",
    "https://www.carvajal.com/historia/",
    "https://www.carvajal.com/proposito-superior/",
    "https://www.carvajal.com/sostenibilidad/",
    "https://www.carvajal.com/noticias/",
    "https://www.carvajal.com/empaques/",
    "https://www.carvajal.com/tecnologia/",
    "https://www.carvajal.com/educacion/",
    "https://www.carvajal.com/comunicacion/",
    "https://www.carvajal.com/espacios/",
    "https://www.carvajal.com/propiedades-e-inversiones/",
    "https://www.carvajal.com/inversionistas/inicio/",
    "https://www.carvajal.com/gobierno-corporativo/",
    "https://www.carvajal.com/compromiso-etico/",
)
