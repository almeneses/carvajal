"""Configuración del pipeline: semillas, capas, política HTTP y LLM."""

import os
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

LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

QUESTIONS_FILE: Path = Path("data") / "questions.json"
RESULTS_FILE: Path = Path("docs") / "resultados.md"
