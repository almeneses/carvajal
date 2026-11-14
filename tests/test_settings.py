"""Pruebas de la configuración tipada (pydantic-settings)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from carvajal import config
from carvajal.infrastructure.settings import Settings


@pytest.fixture(autouse=True)
def _sin_entorno_pg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Aísla las pruebas de las variables de Postgres y del ``.env`` del repo."""

    monkeypatch.chdir(tmp_path)
    for nombre in (
        "DATABASE_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_PORT",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(nombre, raising=False)


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


def test_ollama_base_url_por_defecto() -> None:
    """Sin configuración, los embeddings apuntan al Ollama local."""

    settings = Settings(_env_file=None)

    assert settings.ollama_base_url == "http://127.0.0.1:11434"


def test_ollama_base_url_desde_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """En el compose de producción la URL apunta al servicio `ollama`."""

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")

    settings = Settings(_env_file=None)

    assert settings.ollama_base_url == "http://ollama:11434"


def test_fachada_coincide_con_settings() -> None:
    """``config`` reexporta exactamente los valores de ``Settings``."""

    settings = Settings(_env_file=None)

    assert settings.seeds == config.SEEDS
    assert config.INDEX_FILE == config.GOLD_DIR / "index.jsonl"


def test_database_url_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    """La URL por defecto apunta al contenedor de ``compose.yml``."""

    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql://carvajal:carvajal@127.0.0.1:5432/carvajal"


def test_database_url_se_sobrescribe_por_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """``DATABASE_URL`` del entorno gana al valor por defecto."""

    monkeypatch.setenv("DATABASE_URL", "postgresql://otro:clave@db:5432/otra")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql://otro:clave@db:5432/otra"


def test_database_url_deriva_del_puerto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cambiar ``POSTGRES_PORT`` mueve la URL derivada con él."""

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_PORT", "5544")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql://carvajal:carvajal@127.0.0.1:5544/carvajal"


def test_database_url_codifica_las_credenciales(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una clave con caracteres reservados no malforma la URL."""

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:word/1")

    settings = Settings(_env_file=None)

    assert settings.database_url == (
        "postgresql://carvajal:p%40ss%3Aword%2F1@127.0.0.1:5432/carvajal"
    )


def test_database_url_vacia_deriva(monkeypatch: pytest.MonkeyPatch) -> None:
    """``DATABASE_URL`` vacío equivale a no definirlo."""

    monkeypatch.setenv("DATABASE_URL", "")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql://carvajal:carvajal@127.0.0.1:5432/carvajal"


def test_database_url_usa_usuario_y_base_del_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """La URL derivada refleja ``POSTGRES_USER`` y ``POSTGRES_DB``."""

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "otro")
    monkeypatch.setenv("POSTGRES_DB", "otradb")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql://otro:carvajal@127.0.0.1:5432/otradb"


def test_database_url_se_lee_del_archivo_env(tmp_path: Path) -> None:
    """``DATABASE_URL`` del archivo ``.env`` también gana a la derivada."""

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://archivo:clave@127.0.0.1:5432/archivo\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.database_url == "postgresql://archivo:clave@127.0.0.1:5432/archivo"


def test_postgres_port_invalido_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un ``POSTGRES_PORT`` no numérico falla al construir la configuración."""

    monkeypatch.setenv("POSTGRES_PORT", "no-es-un-numero")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("puerto", ["0", "70000"])
def test_postgres_port_fuera_de_rango_falla(monkeypatch: pytest.MonkeyPatch, puerto: str) -> None:
    """Un puerto fuera de 1..65535 se rechaza al construir la configuración."""

    monkeypatch.setenv("POSTGRES_PORT", puerto)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_cuota_diaria_por_defecto() -> None:
    """La política de cupo tiene un límite razonable y una zona explícita."""

    settings = Settings(_env_file=None)

    assert settings.daily_requests_limit == 20
    assert settings.quota_timezone == "America/Bogota"


@pytest.mark.parametrize("valor", ["-1", "no-es-un-numero"])
def test_daily_requests_limit_invalido_falla(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    """Un límite negativo o no numérico se rechaza al construir la configuración."""

    monkeypatch.setenv("DAILY_REQUESTS_LIMIT", valor)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_quota_timezone_desconocida_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una zona horaria inexistente se detecta al arrancar, no en la petición."""

    monkeypatch.setenv("QUOTA_TIMEZONE", "No/Existe")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_log_level_por_defecto_y_normalizado(monkeypatch: pytest.MonkeyPatch) -> None:
    """El nivel de log se normaliza a mayúsculas al construirlo."""

    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings(_env_file=None)

    assert settings.log_level == "DEBUG"


def test_log_level_desconocido_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un nivel que ``logging`` no conoce se rechaza al arrancar."""

    monkeypatch.setenv("LOG_LEVEL", "VERBOSO")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("valor", ["-0.1", "1.1", "no-es-un-numero"])
def test_sentry_traces_sample_rate_invalido_falla(
    monkeypatch: pytest.MonkeyPatch, valor: str
) -> None:
    """El muestreo de Sentry debe quedar entre 0 y 1."""

    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", valor)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
