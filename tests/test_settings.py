"""Pruebas de la configuración tipada (pydantic-settings)."""

from pathlib import Path
from typing import Any

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
        "OPERATOR_TOKEN",
        "CONFIG_TOKEN",
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
    """El chat usa DeepSeek por defecto; la clave nunca trae un valor por defecto."""

    # Arrange / Act
    settings = Settings(_env_file=None)

    # Assert
    assert settings.llm_provider == "deepseek"
    assert settings.deepseek_api_key == ""


def test_operator_token_por_defecto_vacio_y_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin token la escritura web queda cerrada; CONFIG_TOKEN sigue funcionando."""

    assert Settings(_env_file=None).operator_token == ""

    monkeypatch.setenv("CONFIG_TOKEN", "desde-config")
    settings = Settings(_env_file=None)

    assert settings.operator_token == "desde-config"
    assert settings.config_token == "desde-config"


def test_operator_token_acepta_el_nombre_nuevo(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPERATOR_TOKEN tiene prioridad sobre el alias histórico."""

    monkeypatch.setenv("OPERATOR_TOKEN", "nuevo")
    monkeypatch.setenv("CONFIG_TOKEN", "viejo")

    assert Settings(_env_file=None).operator_token == "nuevo"


def test_operator_token_no_aparece_en_repr() -> None:
    """El secreto no se filtra si alguien registra la configuración."""

    settings = Settings(_env_file=None, operator_token="secreto-muy-largo")

    assert "secreto-muy-largo" not in repr(settings)


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


def test_ollama_num_ctx_por_defecto_y_desde_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """La ventana de contexto de Ollama debe caber el corpus completo."""

    assert Settings(_env_file=None).ollama_num_ctx == 32768

    monkeypatch.setenv("OLLAMA_NUM_CTX", "16384")

    assert Settings(_env_file=None).ollama_num_ctx == 16384


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


def test_monitoreo_apagado_por_defecto() -> None:
    """Sin configuración explícita, el monitoreo no se activa (FR-001)."""

    settings = Settings(_env_file=None)

    assert settings.monitoring_enabled is False
    assert settings.monitoring_sample_rate == 0.2
    assert settings.monitoring_include_content is False
    assert settings.monitoring_timeout_ms == 2000
    assert settings.langsmith_project == "carvajal"
    assert settings.langsmith_endpoint == ""
    assert settings.langsmith_api_key == ""


@pytest.mark.parametrize("valor", ["-0.1", "1.1", "no-es-un-numero"])
def test_monitoring_sample_rate_invalido_falla(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    """El muestreo del monitoreo debe quedar entre 0 y 1 (FR-007)."""

    monkeypatch.setenv("MONITORING_SAMPLE_RATE", valor)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("valor", ["0", "-5", "70000", "no-es-un-numero"])
def test_monitoring_timeout_invalido_falla(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    """El timeout del cliente debe ser positivo y breve."""

    monkeypatch.setenv("MONITORING_TIMEOUT_MS", valor)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_la_llave_de_langsmith_no_se_expone_en_el_repr() -> None:
    """La credencial queda fuera de cualquier ``repr`` accidental (FR-005)."""

    settings = Settings(_env_file=None, langsmith_api_key="lsv2_pt_secreta")

    assert "lsv2_pt_secreta" not in repr(settings)
    assert settings.langsmith_api_key == "lsv2_pt_secreta"


def test_capture_max_urls_es_el_total_de_semillas() -> None:
    """El máximo de la captura sigue al total de semillas configuradas."""

    settings = Settings(
        _env_file=None,
        seeds=("https://example.com/a", "https://example.com/b"),
        external_seeds=("https://example.org/c",),
    )

    assert settings.capture_max_urls == 3


def test_capture_allowed_domains_acepta_lista_separada_por_coma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La allowlist se puede definir como texto en el entorno."""

    monkeypatch.setenv("CAPTURE_ALLOWED_DOMAINS", "carvajal.com, larepublica.co")

    settings = Settings(_env_file=None)

    assert settings.capture_allowed_domains == ("carvajal.com", "larepublica.co")


def test_modo_del_agente_por_defecto_y_limites() -> None:
    """Spec 044: el agente con herramientas es el modo por defecto desde el 2026-11-23."""

    settings = Settings(_env_file=None)

    assert settings.agent_mode == "tools"
    assert settings.agent_max_tool_calls == 4
    assert settings.agent_turn_timeout_s == 45
    assert settings.agent_max_quotes == 4  # spec 054: comparativo de hasta cuatro cantidades


def test_memoria_del_agente_por_defecto() -> None:
    """Spec 045 (T001): resumen determinista hasta medir el resumidor, 180 días de memoria."""

    # Arrange / Act
    settings = Settings(_env_file=None)

    # Assert
    assert settings.agent_summary_mode == "deterministic"
    assert settings.agent_summary_timeout_s == 8
    assert settings.agent_summary_max_chars == 1500
    assert settings.agent_memory_days == 180
    assert settings.agent_faq_timeout_s == 25
    assert settings.agent_faq_timeout_s < settings.agent_turn_timeout_s


@pytest.mark.parametrize(("turno", "faq"), [("20", "40"), ("25", "25")])
def test_un_plazo_de_faq_no_menor_que_el_del_turno_falla_al_arrancar(
    monkeypatch: pytest.MonkeyPatch, turno: str, faq: str
) -> None:
    """Contrato: el plazo de ``preguntas_frecuentes`` es menor que el del turno."""

    # Arrange
    monkeypatch.setenv("AGENT_TURN_TIMEOUT_S", turno)
    monkeypatch.setenv("AGENT_FAQ_TIMEOUT_S", faq)

    # Act / Assert
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_un_turno_corto_recorta_el_plazo_de_faq_por_defecto() -> None:
    """Sin ``AGENT_FAQ_TIMEOUT_S`` explícito, el plazo por defecto se ajusta al del turno."""

    # Act
    corto = Settings(_env_file=None, agent_turn_timeout_s=20)
    minimo = Settings(_env_file=None, agent_turn_timeout_s=0.25)

    # Assert
    assert corto.agent_faq_timeout_s == 15
    assert 0 < minimo.agent_faq_timeout_s < minimo.agent_turn_timeout_s


def test_memoria_del_agente_desde_el_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los ajustes de la 045 se leen del entorno con sus nombres en mayúsculas."""

    # Arrange
    monkeypatch.setenv("AGENT_SUMMARY_MODE", "llm")
    monkeypatch.setenv("AGENT_SUMMARY_TIMEOUT_S", "12")
    monkeypatch.setenv("AGENT_SUMMARY_MAX_CHARS", "900")
    monkeypatch.setenv("AGENT_MEMORY_DAYS", "30")
    monkeypatch.setenv("AGENT_FAQ_TIMEOUT_S", "15")

    # Act
    settings = Settings(_env_file=None)

    # Assert
    assert settings.agent_summary_mode == "llm"
    assert settings.agent_summary_timeout_s == 12
    assert settings.agent_summary_max_chars == 900
    assert settings.agent_memory_days == 30
    assert settings.agent_faq_timeout_s == 15


@pytest.mark.parametrize(
    ("nombre", "valor"),
    [
        ("AGENT_SUMMARY_MODE", "siempre"),
        ("AGENT_SUMMARY_TIMEOUT_S", "0.5"),
        ("AGENT_SUMMARY_TIMEOUT_S", "31"),
        ("AGENT_SUMMARY_MAX_CHARS", "299"),
        ("AGENT_SUMMARY_MAX_CHARS", "4001"),
        ("AGENT_MEMORY_DAYS", "0"),
        ("AGENT_MEMORY_DAYS", "731"),
        ("AGENT_FAQ_TIMEOUT_S", "4"),
        ("AGENT_FAQ_TIMEOUT_S", "41"),
    ],
)
def test_memoria_del_agente_fuera_de_rango_falla(
    monkeypatch: pytest.MonkeyPatch, nombre: str, valor: str
) -> None:
    """Un valor fuera del rango declarado en la spec 045 no arranca la aplicación."""

    # Arrange
    monkeypatch.setenv(nombre, valor)

    # Act / Assert
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("modo", ["routes", "tools"])
def test_modo_del_agente_desde_el_entorno(monkeypatch: pytest.MonkeyPatch, modo: str) -> None:
    """``AGENT_MODE`` elige el modo; ``routes`` es el interruptor para volver atrás."""

    monkeypatch.setenv("AGENT_MODE", modo)

    assert Settings(_env_file=None).agent_mode == modo


REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("archivo", "esperado"),
    [
        ("compose.yml", "AGENT_MODE: ${AGENT_MODE:-tools}"),
        ("compose.prod.yml", "AGENT_MODE: ${AGENT_MODE:-tools}"),
        (".env.example", "AGENT_MODE=tools"),
    ],
)
def test_el_modo_por_defecto_coincide_en_settings_compose_y_plantilla(
    archivo: str, esperado: str
) -> None:
    """La decisión del 2026-11-23 (evidencia §10) fija ``tools`` en los cuatro sitios."""

    # Arrange
    lineas = [linea.strip() for linea in (REPO / archivo).read_text().splitlines()]
    declaradas = [linea for linea in lineas if linea.startswith(("AGENT_MODE:", "AGENT_MODE="))]

    # Act
    modo_por_defecto = Settings(_env_file=None).agent_mode

    # Assert
    assert declaradas == [esperado]
    assert esperado.endswith(f"{modo_por_defecto}}}") or esperado.endswith(f"={modo_por_defecto}")


@pytest.mark.parametrize(
    ("nombre", "valor"),
    [
        ("AGENT_MODE", "agente"),
        ("AGENT_MAX_TOOL_CALLS", "0"),
        ("AGENT_MAX_TOOL_CALLS", "9"),
        ("AGENT_TURN_TIMEOUT_S", "0"),
        ("AGENT_MAX_QUOTES", "0"),
    ],
)
def test_modo_y_limites_invalidos(monkeypatch: pytest.MonkeyPatch, nombre: str, valor: str) -> None:
    """Un modo desconocido o un límite fuera de rango falla al arrancar."""

    monkeypatch.setenv(nombre, valor)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# --- Jev de TypeSafe (spec 046, T001) ---------------------------------------------------------


def test_jev_por_defecto_apagado_y_con_valores_del_data_model() -> None:
    # Arrange / Act
    settings = Settings(_env_file=None)

    # Assert
    assert settings.jev_enabled is False
    assert settings.jev_active is False
    assert settings.jev_ledger == ""
    assert settings.typesafe_api_key.get_secret_value() == ""
    assert settings.jev_base_url == "https://api.typesafe.ai/v1"
    assert settings.jev_model == "jev-1.14.0"
    assert settings.jev_timeout_s == 0.8
    assert settings.jev_router_mode == "on"
    assert settings.jev_router_min_confidence == 0.6
    assert settings.jev_router_direct_min_confidence == 0.8
    assert settings.jev_claims_mode == "enforce"
    assert settings.jev_claims_threshold == 0.5
    assert settings.jev_max_calls_per_day == 1000
    assert settings.jev_max_evidence_chars == 12000


def test_la_clave_sola_no_enciende_jev(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: el ``.env`` local trae la clave pero no ``JEV_ENABLED``.
    monkeypatch.delenv("JEV_ENABLED", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "clave-local")

    # Act
    settings = Settings(_env_file=None)

    # Assert
    assert settings.jev_enabled is False
    assert settings.jev_active is False
    assert Settings(_env_file=None, jev_enabled=True).jev_active is True


def test_la_clave_de_jev_nunca_sale_en_repr() -> None:
    settings = Settings(_env_file=None, typesafe_api_key="secreto-typesafe")

    assert "secreto-typesafe" not in repr(settings)
    assert "secreto-typesafe" not in str(settings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"jev_timeout_s": 0.1},
        {"jev_timeout_s": 3.5},
        {"jev_router_min_confidence": 1.2},
        {"jev_claims_threshold": -0.1},
        {"jev_max_calls_per_day": -1},
        {"jev_max_evidence_chars": 500},
        {"jev_router_mode": "auto"},
        {"jev_claims_mode": "a-veces"},
        {"jev_router_min_confidence": 0.7, "jev_router_direct_min_confidence": 0.6},
    ],
)
def test_rangos_de_jev(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_on_es_alias_de_enforce_en_la_compuerta() -> None:
    assert Settings(_env_file=None, jev_claims_mode="on").jev_claims_mode == "enforce"
    assert Settings(_env_file=None, jev_claims_mode="shadow").jev_claims_mode == "shadow"
