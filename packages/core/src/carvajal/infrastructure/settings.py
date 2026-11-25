"""Configuración tipada del pipeline, leída del entorno y de ``.env``."""

import json
from logging import getLevelNamesMapping
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Holgura entre el plazo de ``preguntas_frecuentes`` y el del turno cuando se recorta el
# valor por defecto (``Settings._plazo_faq_menor_que_el_turno``).
_FAQ_MARGIN_S = 5.0
# 64 kbps en bytes por segundo: el doble del bitrate con que graba el chat (spec 047).
_VOICE_UPLOAD_BYTES_PER_SECOND = 8_000
# Estilo de la voz del servidor (spec 047); ``VOICE_TTS_INSTRUCTIONS`` vacío vuelve a este.
DEFAULT_VOICE_TTS_INSTRUCTIONS = (
    "Habla en español de Colombia como una asesora comercial cálida y cercana, con un "
    "ritmo pausado y claro. Lee las cifras, los precios y las cantidades con calma."
)


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
    # URL desde la que OpenClaw DESCARGA el PDF para adjuntarlo en WhatsApp (lado
    # servidor, puede ser http con IP: p. ej. http://203.0.113.10:8080). Vacía: el PDF
    # se entrega como enlace con ``public_base_url``.
    media_base_url: str = ""

    # Webhook firmado de mensajería (``POST /api/webhooks/whatsapp``, docs/webhooks.md).
    # Sin WEBHOOK_SECRET el endpoint no existe (404). La allowlist es una lista E.164
    # separada por comas o una lista JSON; vacía rechaza todo. Se guarda tal cual y se
    # normaliza al usarla: una entrada inválida se descarta con un aviso en vez de tumbar
    # la app (y sin que el error de validación repita los números en los logs).
    webhook_secret: str = Field(default="", repr=False)
    webhook_allowed_numbers: Annotated[tuple[str, ...], NoDecode] = Field(default=(), repr=False)

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
    # Spec 054: hasta cuatro cotizaciones por turno (comparativo de cuatro cantidades o
    # varios productos en una frase); el carrito de la conversación vive en el borrador.
    agent_max_quotes: int = Field(default=4, ge=1, le=10)
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

    # Jev de TypeSafe (spec 046): gatekeeper del modo ``tools`` y compuerta de afirmaciones.
    # Apagado por defecto aunque haya clave (research R9, FR-031): el ``.env`` local puede
    # traerla y cada ``make api`` gastaría créditos fuera del libro. Producción lo enciende
    # en su ``.env``. La clave solo sale del entorno y nunca aparece en ``repr`` ni en logs.
    jev_enabled: bool = False
    # Libro de llamadas (JSONL sin texto); vacío, no se anota nada.
    jev_ledger: str = ""
    typesafe_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    jev_base_url: str = "https://api.typesafe.ai/v1"
    # Modelo fijado: un alias móvil cambiaría los umbrales sin aviso.
    jev_model: str = "jev-1.14.0"
    jev_timeout_s: float = Field(default=0.8, ge=0.2, le=3.0)
    jev_router_mode: Literal["on", "shadow", "off"] = "on"
    jev_router_min_confidence: float = Field(default=0.6, ge=0, le=1)
    jev_router_direct_min_confidence: float = Field(default=0.8, ge=0, le=1)
    # Compuerta de afirmaciones (decisión del owner): ``enforce`` sustituye la respuesta no
    # respaldada, ``shadow`` solo juzga y registra, ``off`` la apaga. ``on`` es alias de
    # ``enforce``.
    jev_claims_mode: Literal["enforce", "shadow", "off"] = "enforce"
    jev_claims_threshold: float = Field(default=0.5, ge=0, le=1)
    # Tope por proceso; se reinicia a medianoche de Bogotá. ``0`` = sin llamadas.
    jev_max_calls_per_day: int = Field(default=1000, ge=0, le=100_000)
    jev_max_evidence_chars: int = Field(default=12_000, ge=1_000, le=40_000)

    # Fotos de alimentos (spec 051): Jev clasifica la foto y una tabla determinista elige el
    # empaque. Encendida por defecto (decisión del owner, 2026-11-24): basta la clave de
    # TypeSafe; no depende de ``JEV_ENABLED`` (el gatekeeper de la 046 sigue aparte). Cliente
    # propio con su plazo (las imágenes tardan más que el router), su tope y su circuito.
    food_photo_enabled: bool = True
    food_photo_timeout_s: float = Field(default=4.0, ge=1.0, le=10.0)
    food_photo_max_calls_per_day: int = Field(default=500, ge=0, le=100_000)
    food_photo_min_confidence: float = Field(default=0.6, ge=0, le=1)
    # Carpeta (solo lectura) donde OpenClaw deja los adjuntos de WhatsApp; vacía, el puente
    # MCP no acepta fotos.
    food_photo_media_dir: str = ""

    # Flujo de cotización (spec 054): Jev (Choice) clasifica una respuesta corta que las
    # reglas no resolvieron («de una, ciérrala ya»), solo con una pregunta pendiente. Igual
    # que las fotos, basta la clave de TypeSafe; cliente propio con plazo corto y su tope.
    quote_flow_jev_enabled: bool = True
    quote_flow_jev_timeout_s: float = Field(default=1.5, ge=0.3, le=5.0)
    quote_flow_jev_max_calls_per_day: int = Field(default=500, ge=0, le=100_000)

    # Voz del chat web por el servidor con OpenAI (spec 047). ``browser`` (por defecto)
    # conserva el dictado y la lectura del navegador (spec 040) y no envía audio al
    # servidor; ``openai`` transcribe y sintetiza aquí, con la clave solo del entorno. Si
    # falla, el navegador vuelve a su voz. Los topes son por proceso (como Jev).
    voice_provider: Literal["browser", "openai"] = "browser"
    openai_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    voice_openai_base_url: str = "https://api.openai.com/v1"
    voice_stt_model: str = "gpt-4o-mini-transcribe"
    voice_tts_model: str = "gpt-4o-mini-tts"
    voice_tts_voice: str = "coral"
    voice_tts_instructions: str = DEFAULT_VOICE_TTS_INSTRUCTIONS
    voice_max_audio_seconds: int = Field(default=60, ge=5, le=300)
    # Tamaño máximo del audio subido. ``0`` (por defecto) lo deriva de la duración:
    # ``VOICE_MAX_AUDIO_SECONDS`` a 64 kbps, el doble de los 32 kbps que graba el navegador
    # (480 KB para 60 s). OpenAI admite hasta 25 MB y cobra por duración: un audio grande a
    # bitrate bajo serían muchos minutos.
    voice_max_upload_bytes: int = Field(default=0, ge=0, le=25_000_000)
    # Texto máximo que se sintetiza de un mensaje; el resto se queda en pantalla.
    voice_tts_max_chars: int = Field(default=1200, ge=100, le=4000)
    # Tope diario por proceso (STT + TTS); se reinicia a medianoche de Bogotá.
    voice_max_calls_per_day: int = Field(default=500, ge=0, le=100_000)
    # Límite por sesión del chat (``owner_key``): llamadas por ventana deslizante.
    voice_rate_limit: int = Field(default=20, ge=1, le=1000)
    voice_rate_window_s: int = Field(default=600, ge=10, le=86_400)
    # La sesión del chat la puede fabricar el cliente (cookie de visitante): el límite por
    # IP (en la misma ventana) y el global por minuto acotan a quien rota identidades.
    voice_ip_rate_limit: int = Field(default=60, ge=1, le=10_000)
    voice_global_rate_per_min: int = Field(default=30, ge=1, le=10_000)
    voice_timeout_s: float = Field(default=20.0, ge=1.0, le=120.0)

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

    @field_validator("capture_allowed_domains", "webhook_allowed_numbers", mode="before")
    @classmethod
    def _dominios_separados_por_coma(cls, value: Any) -> Any:
        """Acepta ``a.com,b.com`` (o números E.164) en el entorno además de una lista JSON.

        Con ``NoDecode`` el valor llega crudo: un texto que empieza por ``[`` se lee como
        JSON antes de partirlo por comas. Si no es una lista JSON válida se parte por comas
        y las entradas inválidas se descartan con un aviso al usarlas, sin tumbar la app.
        """

        if isinstance(value, str):
            raw = value.strip()
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = None
                if isinstance(parsed, list):
                    items = (str(item).strip() for item in parsed if item is not None)
                    return tuple(item for item in items if item)
            return tuple(part.strip() for part in raw.split(",") if part.strip())
        return value

    @field_validator("jev_claims_mode", mode="before")
    @classmethod
    def _modo_de_compuerta(cls, value: Any) -> Any:
        """Acepta ``on`` como alias de ``enforce`` (el nombre de la spec 046)."""

        if isinstance(value, str) and value.strip().lower() == "on":
            return "enforce"
        return value.strip().lower() if isinstance(value, str) else value

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
    def _umbrales_de_jev(self) -> "Settings":
        """El umbral de la respuesta directa nunca es menor que el general (research R4)."""

        if self.jev_router_direct_min_confidence < self.jev_router_min_confidence:
            raise ValueError(
                "JEV_ROUTER_DIRECT_MIN_CONFIDENCE debe ser mayor o igual que "
                "JEV_ROUTER_MIN_CONFIDENCE"
            )
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
    def jev_active(self) -> bool:
        """Jev encendido de forma explícita y con clave (la clave sola no lo enciende)."""

        return self.jev_enabled and bool(self.typesafe_api_key.get_secret_value().strip())

    @property
    def food_photo_configured(self) -> bool:
        """Fotos de alimentos encendidas y con clave de TypeSafe (spec 051)."""

        return self.food_photo_enabled and bool(self.typesafe_api_key.get_secret_value().strip())

    @property
    def quote_flow_jev_configured(self) -> bool:
        """Jev para las respuestas cortas del flujo de cotización, con clave (spec 054)."""

        return self.quote_flow_jev_enabled and bool(
            self.typesafe_api_key.get_secret_value().strip()
        )

    @property
    def voice_server_configured(self) -> bool:
        """Voz por el servidor pedida (``VOICE_PROVIDER=openai``) y con clave de OpenAI."""

        return self.voice_provider == "openai" and bool(
            self.openai_api_key.get_secret_value().strip()
        )

    @property
    def voice_upload_limit_bytes(self) -> int:
        """Tope de bytes del audio: el explícito o el derivado de la duración (64 kbps)."""

        if self.voice_max_upload_bytes > 0:
            return self.voice_max_upload_bytes
        return self.voice_max_audio_seconds * _VOICE_UPLOAD_BYTES_PER_SECOND

    @field_validator("voice_max_upload_bytes")
    @classmethod
    def _tope_de_audio(cls, value: int) -> int:
        """``0`` deriva el tope de la duración; uno explícito no baja de 10 KB."""

        if 0 < value < 10_000:
            raise ValueError("VOICE_MAX_UPLOAD_BYTES debe ser 0 (derivado) o al menos 10000")
        return value

    @field_validator("voice_tts_instructions", mode="before")
    @classmethod
    def _instrucciones_de_voz(cls, value: Any) -> Any:
        """Vacío (p. ej. ``${VOICE_TTS_INSTRUCTIONS:-}`` en compose) usa el estilo por defecto."""

        if value is None or (isinstance(value, str) and not value.strip()):
            return DEFAULT_VOICE_TTS_INSTRUCTIONS
        return value

    @field_validator("voice_provider", mode="before")
    @classmethod
    def _proveedor_de_voz(cls, value: Any) -> Any:
        """``VOICE_PROVIDER`` sin distinguir mayúsculas; vacío equivale a ``browser``."""

        if isinstance(value, str):
            return value.strip().lower() or "browser"
        return value

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
