"""Configuración tipada del pipeline, leída del entorno y de ``.env``."""

from logging import getLevelNamesMapping
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Holgura entre el plazo de ``preguntas_frecuentes`` y el del turno cuando se recorta el
# valor por defecto (``Settings._plazo_faq_menor_que_el_turno``).
_FAQ_MARGIN_S = 5.0


class Settings(BaseSettings):
    """Valores de configuración del corpus, las capas y los proveedores de LLM."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
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

    # LLM del chat en la nube por defecto (DeepSeek); Ollama queda para los embeddings y,
    # solo si se fija ``LLM_PROVIDER=ollama``, para el chat sin red. Sin DEEPSEEK_API_KEY la
    # app arranca igual y avisa (``agent_mode.chat_llm_blocker``).
    llm_provider: str = "deepseek"
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-5"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    # Tiempo máximo de espera por llamada al proveedor de LLM.
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    ollama_model: str = "llama3.2"
    # La FAQ y el chat envían prompts grandes: sin una ventana explícita,
    # Ollama recorta el contexto y puede responder con datos incompletos.
    ollama_num_ctx: int = Field(default=32768, ge=2048, le=131072)
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

    # Token compartido de operador para la carga y la captura web. No es un
    # sistema de cuentas: un solo secreto de despliegue, comparado en tiempo
    # constante. Si queda vacío, la escritura web queda deshabilitada y el
    # operador usa la CLI interna. ``CONFIG_TOKEN`` se conserva como alias del
    # mecanismo anterior para no romper despliegues existentes.
    operator_token: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices(
            "operator_token",
            "OPERATOR_TOKEN",
            "config_token",
            "CONFIG_TOKEN",
        ),
    )

    # Servidor MCP para WhatsApp (apps/mcp, vía OpenClaw). ``mcp_token`` es el
    # bearer que exige el endpoint ``/mcp``; sin él, el servidor no arranca. El
    # puerto se publica solo en loopback y se alcanza por un túnel SSH.
    mcp_token: str = Field(default="", repr=False)
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8765, ge=1, le=65535)
    # URL pública de la web (p. ej. http://203.0.113.10:8080) para los enlaces
    # firmados al PDF de una cotización. Vacía: WhatsApp no ofrece el PDF.
    public_base_url: str = ""

    # Captura web desde la consola de Configuración, solo para operadores
    # autorizados con OPERATOR_TOKEN. El máximo de URLs por ejecución sigue al
    # de la primera versión (todas las semillas configuradas) y el valor
    # inicial es de cinco. El límite, el timeout y el guard SSRF son defensas
    # adicionales, no autorización.
    capture_default_urls: int = Field(default=5, ge=1)
    capture_max_bytes: int = Field(default=5_000_000, ge=1)
    capture_timeout_seconds: int = Field(default=30, ge=1)
    capture_max_redirects: int = Field(default=5, ge=0, le=10)
    # Solo para desarrollo y pruebas: permite capturar desde loopback y redes
    # privadas. En producción debe quedar en False.
    capture_allow_private_hosts: bool = False
    # Límite por IP para el endpoint de operador: N capturas por ventana. Cero
    # desactiva el límite (solo para desarrollo).
    capture_requests_limit: int = Field(default=5, ge=0)
    capture_window_seconds: int = Field(default=3600, ge=1)
    # Lista opcional de dominios permitidos (separados por coma). Vacía
    # significa cualquier host público que pase el guard SSRF.
    capture_allowed_domains: Annotated[tuple[str, ...], NoDecode] = ()

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
    # Enrutador del agente (spec 036, FR-013): rules (por defecto), llm o decision.
    agent_router: str = "rules"
    # Modelo de decisión para el enrutador «decision», como paquete.modulo:fabrica.
    decision_model: str = ""
    # Spec 044: «tools» es el agente con herramientas (por defecto desde el 2026-11-23,
    # decisión del owner); «routes» es el agente por rutas y sirve de interruptor de
    # emergencia. El modo «routes» se construye siempre: atiende el atajo de saludos y el
    # respaldo cuando el proveedor falla, y todos los turnos si el modo «tools» no puede
    # arrancar: exige LLM_PROVIDER=deepseek y DeepSeek completo (infrastructure.agent_mode).
    agent_mode: Literal["routes", "tools"] = "tools"
    agent_max_tool_calls: int = Field(default=4, ge=1, le=8)
    agent_turn_timeout_s: float = Field(default=45, gt=0, le=300)
    agent_max_quotes: int = Field(default=3, ge=1, le=10)
    # Spec 045: memoria de corto y largo plazo. El resumen acumulado de los turnos que salen
    # de la ventana es determinista por defecto hasta medir el prompt del resumidor (T097);
    # «off» lo apaga. La memoria del dueño caduca como la del Cotizador.
    agent_summary_mode: Literal["llm", "deterministic", "off"] = "deterministic"
    agent_summary_timeout_s: float = Field(default=8, ge=1, le=30)
    agent_summary_max_chars: int = Field(default=1500, ge=300, le=4000)
    agent_memory_days: int = Field(default=180, ge=1, le=730)
    # Plazo de la generación de ``preguntas_frecuentes`` (único LLM dentro del turno,
    # research R18): siempre menor que ``agent_turn_timeout_s``.
    agent_faq_timeout_s: float = Field(default=25, ge=5, le=40)

    # Observabilidad (W6.3). Sin SENTRY_DSN no se envía nada fuera; los logs
    # son JSON en stdout y el nivel se ajusta por entorno. El muestreo de
    # trazas es explícito y por defecto queda apagado.
    log_level: str = "INFO"
    sentry_dsn: str = ""
    sentry_environment: str = "local"
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0, le=1)

    # Monitoreo del agente con LangSmith (spec 038). Apagado por defecto: sin
    # MONITORING_ENABLED y sin llave no se construye cliente ni sale un byte
    # del proceso. ``repr=False`` mantiene la credencial fuera de cualquier
    # ``repr`` o log accidental de la configuración.
    monitoring_enabled: bool = False
    langsmith_api_key: str = Field(default="", repr=False)
    langsmith_project: str = "carvajal"
    # Identidad opcional para distinguir orígenes dentro del mismo proyecto.
    langsmith_agent_id: str = ""
    langsmith_agent_environment: str = ""
    # Vacío usa el servicio público; el endpoint nunca se publica en /health.
    langsmith_endpoint: str = ""
    monitoring_sample_rate: float = Field(default=0.2, ge=0, le=1)
    # true solo en desarrollo y evaluación; en producción el contenido no sale.
    monitoring_include_content: bool = False
    monitoring_timeout_ms: int = Field(default=2000, ge=1, le=60_000)

    @field_validator("capture_allowed_domains", mode="before")
    @classmethod
    def _dominios_separados_por_coma(cls, value: Any) -> Any:
        """Acepta ``a.com,b.com`` en el entorno además de una lista JSON."""

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
    def _plazo_faq_menor_que_el_turno(self) -> "Settings":
        """El plazo de ``preguntas_frecuentes`` es siempre menor que el del turno (R18).

        Si el turno vence antes, el turno cae al respaldo por plazo, la herramienta nunca
        usa su caché de respaldo y su hilo sigue llamando al LLM. Un
        ``AGENT_FAQ_TIMEOUT_S`` explícito que no lo cumple falla al arrancar; el valor por
        defecto (25 s) se recorta al del turno menos 5 s, o a la mitad si el turno es corto.
        """

        turn = self.agent_turn_timeout_s
        if self.agent_faq_timeout_s < turn:
            return self
        if "agent_faq_timeout_s" in self.model_fields_set:
            raise ValueError(
                "AGENT_FAQ_TIMEOUT_S debe ser menor que AGENT_TURN_TIMEOUT_S "
                f"({self.agent_faq_timeout_s} ≥ {turn})"
            )
        self.agent_faq_timeout_s = turn - _FAQ_MARGIN_S if turn > 2 * _FAQ_MARGIN_S else turn / 2
        return self

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
    def config_token(self) -> str:
        """Alias del token de operador que usaba la Configuración anterior."""

        return self.operator_token

    @property
    def index_file(self) -> Path:
        """Ruta del índice JSONL del corpus, derivada del directorio Gold."""

        return self.gold_dir / "index.jsonl"

    @property
    def capture_max_urls(self) -> int:
        """Máximo de URLs por captura: todas las semillas configuradas.

        Reproduce el límite de la primera interfaz, donde el control llegaba
        hasta el total de semillas oficiales y de terceros.
        """

        return len(self.seeds) + len(self.external_seeds)


def load_settings(**overrides: Any) -> Settings:
    """Construye la configuración; sin caché para permitir inyectar entorno en pruebas."""

    return Settings(**overrides)
