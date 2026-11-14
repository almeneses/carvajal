"""Configuración tipada del pipeline, leída del entorno y de ``.env``."""

from logging import getLevelNamesMapping
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Valores de configuración del corpus, las capas y los proveedores de LLM."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    base_url: str = "https://www.carvajal.com"
    user_agent: str = "carvajal-corpus/0.1 (+https://github.com/markodavid/carvajal)"
    timeout_seconds: int = 30

    gold_dir: Path = Path("data") / "gold"
    index_dir: Path = Path("data") / "index"
    raw_dir: Path = Path("data") / "raw"  # bronze: HTML crudo
    processed_dir: Path = Path("data") / "processed"  # silver: Markdown limpio

    # Capa extendida: corpus externo importado, solo para recuperación.
    # El núcleo (data/gold) sigue siendo el único que se consolida en el prompt.
    external_dir: Path = Path("data") / "external"
    external_corpus_dir: Path = Path("../proyecto-carvajal")
    external_source: str = "alejandro"
    external_release: str = "corpus-2026-10-23"
    external_categories: tuple[str, ...] = (
        "identity",
        "governance",
        "business",
        "sustainability",
        "technology",
        "news",
    )
    external_max_chars: int = 1_500_000
    layer_core: str = "core"
    layer_extended: str = "extended"
    request_delay_seconds: float = 1.0  # rate limit entre peticiones por host
    respect_robots: bool = True
    min_dedupe_chars: int = 200  # no deduplicar páginas casi vacías

    seeds: tuple[str, ...] = (
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
        "https://www.carvajal.com/pulpa-y-papel/",
        "https://www.carvajal.com/politica-de-innovacion/",
        "https://www.carvajal.com/compromiso-etico/relacionamiento-con-grupos-de-interes/",
    )

    # Fuentes de terceros: publican los indicadores financieros que el sitio oficial
    # solo entrega dentro de PDFs (estados financieros, informe de gestión).
    external_seeds: tuple[str, ...] = (
        "https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828",
        "https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-58-billones-a-septiembre-de-2025-4269762",
        "https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-8-billones-con-corte-al-tercer-trimestre-3999096",
        "https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-1-9-billones-a-junio-un-decrecimiento-de-14-anual-3928992",
        "https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html",
        "https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales",
    )

    llm_provider: str = "ollama"
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-5"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    ollama_model: str = "llama3.2"
    # Los embeddings del corpus se calculan con Ollama. En local el valor por
    # defecto apunta al Ollama del host; en compose.prod.yml se inyecta
    # http://ollama:11434 para usar el servicio de la red de Compose.
    ollama_base_url: str = "http://127.0.0.1:11434"

    questions_file: Path = Path("data") / "questions.json"
    results_file: Path = Path("docs") / "resultados.md"

    # Base de datos de la app web (W1.1). Los valores por defecto son los del
    # servicio db de compose.yml; las credenciales son solo para desarrollo
    # local. Si no se define DATABASE_URL, se deriva de POSTGRES_*, así que
    # cambiar el puerto del contenedor no deja la URL apuntando a otro sitio.
    postgres_user: str = "carvajal"
    postgres_password: str = "carvajal"
    postgres_db: str = "carvajal"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    database_url: str = ""

    # Firma de la cookie anónima del chat. No es autenticación. Si está vacío
    # se usa un secreto aleatorio por proceso; en producción debe fijarse para
    # que las cookies sobrevivan reinicios y compartan valor entre workers.
    session_secret: str = ""

    # Cupo diario de preguntas por visitante anónimo (W4.2). El límite es una
    # política de producto y la medianoche se calcula en la zona indicada, no
    # en el TZ del contenedor.
    daily_requests_limit: int = Field(default=20, ge=0)
    quota_timezone: str = "America/Bogota"

    # RAG del M2: Qdrant es el índice evaluado. Postgres conserva documentos,
    # fragmentos (sin depender de su columna vectorial), chat y memoria. El
    # índice se prepara aparte con `carvajal index-qdrant`; la web arranca sin
    # reconstruirlo y `/health` informa si está disponible.
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "carvajal_chunks"
    qdrant_dimensions: int = Field(default=768, ge=1)
    qdrant_distance: str = "Cosine"
    rag_top_k: int = Field(default=6, ge=1, le=50)
    rag_min_score: float = Field(default=0.30, ge=-1.0, le=1.0)

    # Agente del M2: checkpointer y Store sobre el Postgres existente. La
    # alternativa SQLite sirve para una demo local con archivo persistente.
    agent_checkpointer: str = "postgres"
    agent_sqlite_path: Path = Path("data") / "agent" / "checkpoints.sqlite"
    agent_history_turns: int = Field(default=8, ge=1, le=50)

    # Frontend propio (proceso separado) y orígenes permitidos por CORS.
    # NoDecode evita que pydantic-settings intente JSON antes del validador:
    # se aceptan tanto `a,b` como una lista JSON, y también el valor vacío.
    frontend_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    )

    # Observabilidad (W6.3). Sin SENTRY_DSN no se envía nada fuera; los logs
    # son JSON en stdout y el nivel se ajusta por entorno. El muestreo de
    # trazas es explícito y por defecto queda apagado.
    log_level: str = "INFO"
    sentry_dsn: str = ""
    sentry_environment: str = "local"
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0, le=1)

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def _origenes_separados_por_coma(cls, value: Any) -> Any:
        """Acepta ``a,b`` en el entorno además de una lista JSON."""

        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("log_level")
    @classmethod
    def _nivel_de_log_valido(cls, value: str) -> str:
        """Normaliza el nivel y rechaza nombres que ``logging`` no conoce."""

        level = value.strip().upper()
        if level not in getLevelNamesMapping():
            raise ValueError(f"nivel de log desconocido: {value}")
        return level

    @field_validator("quota_timezone")
    @classmethod
    def _zona_horaria_valida(cls, value: str) -> str:
        """Rechaza una zona desconocida al construir la configuración."""

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"zona horaria desconocida: {value}") from error
        return value

    @model_validator(mode="after")
    def _completar_database_url(self) -> "Settings":
        """Deriva la URL del contenedor local si no se dio una explícita.

        Un ``DATABASE_URL`` vacío equivale a no definirlo. Usuario y contraseña
        se codifican para que caracteres como ``@`` o ``:`` no rompan la URL.
        """

        if not self.database_url:
            usuario = quote(self.postgres_user, safe="")
            clave = quote(self.postgres_password, safe="")
            base = quote(self.postgres_db, safe="")
            self.database_url = (
                f"postgresql://{usuario}:{clave}@127.0.0.1:{self.postgres_port}/{base}"
            )
        return self

    @property
    def index_file(self) -> Path:
        """Ruta del índice JSONL del corpus, derivada del directorio Gold."""

        return self.gold_dir / "index.jsonl"


def load_settings(**overrides: Any) -> Settings:
    """Construye la configuración; sin caché para permitir inyectar entorno en pruebas."""

    return Settings(**overrides)
