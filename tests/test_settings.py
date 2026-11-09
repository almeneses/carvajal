"""Pruebas de la configuración tipada (pydantic-settings)."""

from pathlib import Path

import pytest

from carvajal import config
from carvajal.infrastructure.settings import Settings


def test_valores_por_defecto() -> None:
    """Sin entorno ni ``.env``, los valores reproducen las constantes históricas."""

    settings = Settings(_env_file=None)

    assert settings.gold_dir == Path("data") / "gold"
    assert settings.index_dir == Path("data") / "index"
    assert settings.raw_dir == Path("data") / "raw"
    assert settings.processed_dir == Path("data") / "processed"
    assert settings.external_dir == Path("data") / "external"
    assert settings.questions_file == Path("data") / "questions.json"
    assert settings.results_file == Path("docs") / "resultados.md"


def test_semillas_por_defecto_no_vacias() -> None:
    """Las semillas del sitio y de terceros siempre traen al menos una URL."""

    settings = Settings(_env_file=None)

    assert len(settings.seeds) > 0
    assert len(settings.external_seeds) > 0
    assert all("carvajal.com" not in url for url in settings.external_seeds)


def test_proveedor_llm_por_defecto() -> None:
    """El proveedor por defecto es Ollama, es decir, sin API key."""

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "ollama"


def test_index_file_derivado() -> None:
    """El índice JSONL se deriva del directorio Gold."""

    settings = Settings(_env_file=None)

    assert settings.index_file == settings.gold_dir / "index.jsonl"


def test_override_de_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las variables de entorno pisan los valores por defecto."""

    monkeypatch.setenv("BASE_URL", "https://ejemplo.test")
    monkeypatch.setenv("TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("EXTERNAL_CORPUS_DIR", "/tmp/corpus-donante")

    settings = Settings(_env_file=None)

    assert settings.base_url == "https://ejemplo.test"
    assert settings.timeout_seconds == 45
    assert settings.llm_provider == "deepseek"
    assert settings.external_corpus_dir == Path("/tmp/corpus-donante")


def test_tupla_desde_entorno_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las tuplas se parsean desde JSON en la variable de entorno."""

    monkeypatch.setenv("SEEDS", '["https://ejemplo.test/"]')

    settings = Settings(_env_file=None)

    assert settings.seeds == ("https://ejemplo.test/",)


def test_fachada_coincide_con_settings() -> None:
    """``config`` reexporta exactamente los valores de ``Settings``."""

    settings = Settings(_env_file=None)

    assert settings.seeds == config.SEEDS
    assert config.INDEX_FILE == config.GOLD_DIR / "index.jsonl"


def test_database_url_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    """La URL por defecto apunta al contenedor de ``compose.yml``."""

    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql://carvajal:carvajal@localhost:5432/carvajal"
