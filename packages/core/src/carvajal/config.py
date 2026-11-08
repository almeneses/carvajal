"""Configuración del pipeline: semillas, capas, política HTTP y LLM."""

from pathlib import Path

from carvajal.infrastructure.settings import load_settings

# Los valores viven en ``infrastructure/settings.py`` (pydantic-settings). Aquí solo
# se exponen como constantes en mayúsculas para no romper a los consumidores.
settings = load_settings()

BASE_URL: str = settings.base_url
USER_AGENT: str = settings.user_agent
TIMEOUT_SECONDS: int = settings.timeout_seconds

GOLD_DIR: Path = settings.gold_dir
INDEX_DIR: Path = settings.index_dir
RAW_DIR: Path = settings.raw_dir
PROCESSED_DIR: Path = settings.processed_dir
INDEX_FILE: Path = settings.index_file

# Capa extendida: corpus externo importado, solo para recuperación.
EXTERNAL_DIR: Path = settings.external_dir
EXTERNAL_CORPUS_DIR: Path = settings.external_corpus_dir
EXTERNAL_SOURCE: str = settings.external_source
EXTERNAL_RELEASE: str = settings.external_release
EXTERNAL_CATEGORIES: tuple[str, ...] = settings.external_categories
EXTERNAL_MAX_CHARS: int = settings.external_max_chars
LAYER_CORE: str = settings.layer_core
LAYER_EXTENDED: str = settings.layer_extended
REQUEST_DELAY_SECONDS: float = settings.request_delay_seconds
RESPECT_ROBOTS: bool = settings.respect_robots
MIN_DEDUPE_CHARS: int = settings.min_dedupe_chars

SEEDS: tuple[str, ...] = settings.seeds
EXTERNAL_SEEDS: tuple[str, ...] = settings.external_seeds

LLM_PROVIDER: str = settings.llm_provider
ANTHROPIC_API_KEY: str = settings.anthropic_api_key
CLAUDE_MODEL: str = settings.claude_model
DEEPSEEK_API_KEY: str = settings.deepseek_api_key
DEEPSEEK_MODEL: str = settings.deepseek_model
DEEPSEEK_BASE_URL: str = settings.deepseek_base_url

QUESTIONS_FILE: Path = settings.questions_file
RESULTS_FILE: Path = settings.results_file
