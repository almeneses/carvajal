"""Agregadores de la página «Métricas» (spec 055).

Cada fuente arma una :class:`Card` con números agregados y nunca lanza: si la fuente falla, la
tarjeta dice «no disponible» y por qué, y la página sigue. Las fuentes con red tienen plazo
corto y caché (:class:`TtlCache`); si una consulta falla y hay un dato anterior, se muestra el
anterior marcado como desactualizado.

- **Qdrant**: ``GET /collections/{colección}`` y ``GET /telemetry`` (versión, tamaño, consultas).
- **Jev**: el bloque ``jev`` y ``claims`` de ``/health`` y las llamadas del proceso
  (:data:`~carvajal.infrastructure.metrics.JEV_CALLS`); sin red.
- **LangSmith**: el agregado ``POST /runs/stats`` del proyecto para 24 h y 7 días (dos
  consultas cada diez minutos); nunca descarga runs.
- **DeepSeek**: llamadas, tokens y latencia del proceso con el costo estimado; el saldo de la
  cuenta (``GET /user/balance``) solo para el operador.
- **WhatsApp (OpenClaw)**: lo que guarda la base (mensajes, conversaciones y cotizaciones del
  canal) y, si está configurado, el ``/health`` del servidor MCP por la red interna. El
  gateway de OpenClaw vive en otra VM y la app no lo alcanza.

Nada de esto lleva texto de las personas, ``owner_key``, teléfonos ni secretos.
"""

import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import httpx
import structlog
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from carvajal.adapters.storage import tables
from carvajal.infrastructure.metrics import DEEPSEEK_CALLS, JEV_CALLS, estimated_cost
from carvajal.infrastructure.settings import Settings

__all__ = [
    "Card",
    "MetricsBoard",
    "TtlCache",
    "deepseek_card",
    "jev_card",
    "langsmith_card",
    "qdrant_card",
    "whatsapp_card",
]

logger = structlog.get_logger(__name__)

CardStatus = Literal["ok", "unavailable", "off"]

# Vigencia de la caché por fuente, en segundos.
QDRANT_TTL: Final = 60
LANGSMITH_TTL: Final = 600
BALANCE_TTL: Final = 300
WHATSAPP_TTL: Final = 60
# Vigencia de un fallo: una fuente caída se vuelve a consultar como mucho una vez por minuto.
FAILURE_RETRY_TTL: Final = 60
# Plazos por petición, en segundos: la página no espera a una fuente lenta.
QDRANT_TIMEOUT: Final = 2.0
LANGSMITH_TIMEOUT: Final = 4.0
BALANCE_TIMEOUT: Final = 3.0
MCP_TIMEOUT: Final = 2.0
LANGSMITH_DEFAULT_ENDPOINT: Final = "https://api.smith.langchain.com"
OPENCLAW_NOTE: Final = (
    "El gateway de OpenClaw vive en otra VM y la app no lo alcanza: esto es lo que guarda la "
    "base y lo que observa el servidor MCP que atiende a WhatsApp."
)
WHATSAPP_PREFIX: Final = "wa_"


@dataclass(frozen=True)
class Card:
    """Una tarjeta de la página: estado, motivo y datos agregados de una fuente."""

    key: str
    title: str
    status: CardStatus
    reason: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None
    stale: bool = False
    ttl_s: int = 0


def _now() -> datetime:
    return datetime.now(UTC)


class TtlCache:
    """Caché por clave con vigencia; ante un fallo conserva el último dato disponible.

    Un fallo también se guarda, con una vigencia corta (:data:`FAILURE_RETRY_TTL`, o la de
    la propia tarjeta si la fuente pidió esperar): así una fuente caída se consulta como
    mucho una vez por minuto y no en cada visita, y se recupera sin esperar la vigencia
    completa. La recarga de cada clave va de a una: si varias visitas llegan con el dato
    caducado, una consulta la fuente y las demás esperan su resultado.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        # Clave → (momento en que se guardó, tarjeta, vigencia en segundos).
        self._items: dict[str, tuple[float, Card, float]] = {}

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def _fresh(self, key: str) -> tuple[Card | None, Card | None]:
        """La tarjeta vigente (si la hay) y la última guardada, vigente o no."""

        with self._lock:
            cached = self._items.get(key)
        if cached is None:
            return None, None
        stored_at, card, valid_for = cached
        return (card if self._clock() - stored_at < valid_for else None), card

    def get(self, key: str, ttl: int, loader: Callable[[], Card]) -> Card:
        current, _ = self._fresh(key)
        if current is not None:
            return current
        with self._key_lock(key):
            # Otra visita pudo recargarla mientras esta esperaba el turno.
            current, previous = self._fresh(key)
            if current is not None:
                return current
            fresh = loader()
            valid_for: float = ttl
            if fresh.status == "unavailable":
                valid_for = fresh.ttl_s or min(ttl, FAILURE_RETRY_TTL)
                if previous is not None and previous.status == "ok":
                    # El dato anterior sirve más que un «no disponible»; se marca y se reintenta
                    # cuando vence la vigencia corta.
                    fresh = replace(previous, stale=True, reason=fresh.reason, ttl_s=int(valid_for))
            with self._lock:
                self._items[key] = (self._clock(), fresh, valid_for)
            return fresh

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


def _failure(error: Exception) -> str:
    """Motivo legible de un fallo de red, sin URL, cabeceras ni cuerpo."""

    if isinstance(error, httpx.TimeoutException):
        return "No respondió a tiempo."
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        if code in (401, 403):
            return f"Rechazó las credenciales (HTTP {code})."
        if code == 429:
            return "Pidió esperar antes de volver a consultar (HTTP 429)."
        return f"Respondió con error (HTTP {code})."
    if isinstance(error, httpx.HTTPError):
        return "No se pudo conectar."
    return f"Respuesta inesperada ({type(error).__name__})."


# --- Qdrant -------------------------------------------------------------------------------


def _qdrant_query_stats(responses: Mapping[str, Any]) -> dict[str, Any] | None:
    """Consultas de búsqueda que contó Qdrant (todas sus colecciones) desde que arrancó."""

    count = 0
    total_us = 0.0
    max_us = 0.0
    for endpoint, by_code in responses.items():
        if not endpoint.startswith("POST /collections/{name}/points/") or not any(
            endpoint.endswith(suffix) for suffix in ("/query", "/search", "/search/batch")
        ):
            continue
        for stats in (by_code or {}).values():
            count += int(stats.get("count") or 0)
            total_us += float(stats.get("total_duration_micros") or 0)
            max_us = max(max_us, float(stats.get("max_duration_micros") or 0))
    if not count:
        return None
    return {
        "count": count,
        "avg_ms": round(total_us / count / 1000, 1),
        "max_ms": round(max_us / 1000, 1),
    }


def qdrant_card(settings: Settings, client: httpx.Client) -> Card:
    """Estado de la colección del chat y, si se puede, telemetría del servidor."""

    collection = settings.qdrant_collection
    base = settings.qdrant_url.rstrip("/")
    headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key.strip() else {}
    try:
        response = client.get(
            f"{base}/collections/{collection}", headers=headers, timeout=QDRANT_TIMEOUT
        )
        if response.status_code == 404:
            return Card(
                "qdrant", "Qdrant", "unavailable", f"La colección «{collection}» no existe."
            )
        response.raise_for_status()
        info = response.json().get("result") or {}
    except Exception as error:
        logger.info("metrics.qdrant_failed", error_type=type(error).__name__)
        return Card("qdrant", "Qdrant", "unavailable", _failure(error), updated_at=_now())
    params = (info.get("config") or {}).get("params") or {}
    vectors = params.get("vectors") or {}
    hnsw = (info.get("config") or {}).get("hnsw_config") or {}
    data: dict[str, Any] = {
        "collection": collection,
        "status": info.get("status"),
        "optimizer": info.get("optimizer_status"),
        "points": info.get("points_count"),
        "indexed_vectors": info.get("indexed_vectors_count"),
        "segments": info.get("segments_count"),
        "dimensions": vectors.get("size") if isinstance(vectors, Mapping) else None,
        "distance": vectors.get("distance") if isinstance(vectors, Mapping) else None,
        "hnsw_m": hnsw.get("m"),
        "hnsw_ef_construct": hnsw.get("ef_construct"),
        "on_disk_payload": params.get("on_disk_payload"),
    }
    try:
        telemetry = client.get(
            f"{base}/telemetry",
            params={"details_level": 3},
            headers=headers,
            timeout=QDRANT_TIMEOUT,
        )
        telemetry.raise_for_status()
        result = telemetry.json().get("result") or {}
        data["version"] = (result.get("app") or {}).get("version")
        data["memory_resident_bytes"] = (result.get("memory") or {}).get("resident_bytes")
        for item in (result.get("collections") or {}).get("collections") or []:
            if item.get("id") != collection:
                continue
            size = 0
            for shard in item.get("shards") or []:
                local = shard.get("local") or {}
                size += int(local.get("vectors_size_bytes") or 0)
                size += int(local.get("payloads_size_bytes") or 0)
            data["size_bytes"] = size or None
        rest = ((result.get("requests") or {}).get("rest") or {}).get("responses") or {}
        data["queries"] = _qdrant_query_stats(rest)
    except Exception as error:
        # La telemetría es un extra: sin ella la tarjeta sigue con la colección.
        logger.info("metrics.qdrant_telemetry_failed", error_type=type(error).__name__)
        data["telemetry_unavailable"] = _failure(error)
    status: CardStatus = "ok" if data["status"] in {"green", "yellow"} else "unavailable"
    reason = "" if status == "ok" else f"La colección está en estado «{data['status']}»."
    return Card("qdrant", "Qdrant", status, reason, data, updated_at=_now(), ttl_s=QDRANT_TTL)


# --- Jev ----------------------------------------------------------------------------------


def jev_card(
    settings: Settings,
    *,
    jev: Mapping[str, Any],
    claims: Mapping[str, Any],
    photo: Mapping[str, Any],
) -> Card:
    """Gatekeeper, compuerta de afirmaciones y fotos de este proceso; sin red."""

    calls = JEV_CALLS.snapshot()
    # Sin clave o con JEV_ENABLED=false, el gatekeeper y la compuerta no actúan aunque la
    # configuración tenga un modo: la tarjeta no dice «on» junto a «Apagado».
    active = bool(jev.get("configured")) and bool(jev.get("enabled"))
    data = {
        "configured": jev.get("configured"),
        "enabled": jev.get("enabled"),
        "available": jev.get("available"),
        "model": jev.get("model"),
        "router": jev.get("router") if active else "off",
        "claims_mode": claims.get("mode") if active else "off",
        "threshold": claims.get("threshold"),
        "calls_today": jev.get("calls_today"),
        "cap": settings.jev_max_calls_per_day,
        "last_error": jev.get("last_error"),
        "photo_status": photo.get("status"),
        "photo_calls_today": photo.get("calls_today"),
        "photo_cap": settings.food_photo_max_calls_per_day,
        "claims": {
            "since": claims.get("since"),
            "turns": claims.get("turns"),
            "judged": claims.get("judged"),
            "supported": claims.get("supported"),
            "unsupported": claims.get("unsupported"),
            "substituted": claims.get("substituted"),
            "substitution_rate": claims.get("substitution_rate"),
            "unavailable": claims.get("unavailable") or {},
            "skipped": claims.get("skipped") or {},
            "p_histogram": list(claims.get("p_histogram") or []),
        },
        "calls": calls,
    }
    configured = bool(jev.get("configured"))
    if not configured:
        return Card(
            "jev", "Jev (TypeSafe)", "off", "Falta TYPESAFE_API_KEY.", data, updated_at=_now()
        )
    if not jev.get("enabled") and photo.get("status") == "off":
        reason = "Gatekeeper y compuerta apagados (JEV_ENABLED=false) y fotos apagadas."
        return Card("jev", "Jev (TypeSafe)", "off", reason, data, updated_at=_now())
    reason = "" if jev.get("enabled") else "Solo fotos: gatekeeper y compuerta apagados."
    return Card("jev", "Jev (TypeSafe)", "ok", reason, data, updated_at=_now())


# --- LangSmith ----------------------------------------------------------------------------


def _langsmith_window(stats: Mapping[str, Any]) -> dict[str, Any]:
    runs = int(stats.get("run_count") or 0)
    rate = stats.get("error_rate")
    return {
        "traces": runs,
        "errors": round(runs * float(rate)) if rate is not None else None,
        "error_rate": None if rate is None else round(float(rate), 4),
        "p50_s": _round(stats.get("latency_p50"), 2),
        "p99_s": _round(stats.get("latency_p99"), 2),
        "prompt_tokens": stats.get("prompt_tokens"),
        "completion_tokens": stats.get("completion_tokens"),
        "last_run": stats.get("last_run_start_time"),
    }


def _round(value: Any, digits: int) -> float | None:
    try:
        return None if value is None else round(float(value), digits)
    except (TypeError, ValueError):
        return None


def langsmith_card(
    settings: Settings, client: httpx.Client, *, now: datetime | None = None
) -> Card:
    """Trazas raíz del proyecto en 24 h y 7 días con el agregado de LangSmith."""

    title = "LangSmith"
    key = settings.langsmith_api_key.strip()
    if not settings.monitoring_enabled or not key:
        reason = "El monitoreo está apagado (MONITORING_ENABLED o LANGSMITH_API_KEY)."
        return Card("langsmith", title, "off", reason, updated_at=_now())
    base = (settings.langsmith_endpoint.strip() or LANGSMITH_DEFAULT_ENDPOINT).rstrip("/")
    headers = {"x-api-key": key}
    moment = now or _now()
    environment = settings.langsmith_agent_environment.strip()
    try:
        sessions = client.get(
            f"{base}/sessions",
            params={"name": settings.langsmith_project, "limit": 1},
            headers=headers,
            timeout=LANGSMITH_TIMEOUT,
        )
        sessions.raise_for_status()
        found = sessions.json()
        if not found:
            reason = f"El proyecto «{settings.langsmith_project}» no existe en LangSmith."
            return Card("langsmith", title, "unavailable", reason, updated_at=_now())
        project_id = found[0]["id"]
        windows: dict[str, Any] = {}
        for label, delta in (("24h", timedelta(hours=24)), ("7d", timedelta(days=7))):
            body: dict[str, Any] = {
                "session": [project_id],
                "start_time": (moment - delta).isoformat(),
                "is_root": True,
            }
            if environment:
                body["filter"] = (
                    'and(eq(metadata_key, "agent_environment"), '
                    f'eq(metadata_value, "{environment}"))'
                )
            stats = client.post(
                f"{base}/runs/stats", json=body, headers=headers, timeout=LANGSMITH_TIMEOUT
            )
            stats.raise_for_status()
            windows[label] = _langsmith_window(stats.json())
    except Exception as error:
        logger.info("metrics.langsmith_failed", error_type=type(error).__name__)
        # Si LangSmith pidió esperar (429), no se vuelve a consultar hasta la vigencia
        # completa; ante otros fallos la caché reintenta en un minuto.
        limited = isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429
        return Card(
            "langsmith",
            title,
            "unavailable",
            _failure(error),
            updated_at=_now(),
            ttl_s=LANGSMITH_TTL if limited else 0,
        )
    data = {
        "project": settings.langsmith_project,
        "environment": environment or None,
        "sample_rate": settings.monitoring_sample_rate,
        "windows": windows,
    }
    return Card("langsmith", title, "ok", "", data, updated_at=_now(), ttl_s=LANGSMITH_TTL)


# --- DeepSeek -----------------------------------------------------------------------------


def deepseek_balance(settings: Settings, client: httpx.Client) -> dict[str, Any]:
    """Saldo de la cuenta de DeepSeek (``GET /user/balance``); solo para el operador."""

    base = settings.deepseek_base_url.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    try:
        response = client.get(
            f"{base}/user/balance",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key.strip()}"},
            timeout=BALANCE_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        logger.info("metrics.deepseek_balance_failed", error_type=type(error).__name__)
        return {"status": "unavailable", "reason": _failure(error)}
    balances = [
        {
            "currency": str(item.get("currency") or ""),
            "total": str(item.get("total_balance") or ""),
            "granted": str(item.get("granted_balance") or ""),
            "topped_up": str(item.get("topped_up_balance") or ""),
        }
        for item in payload.get("balance_infos") or []
        if isinstance(item, Mapping)
    ]
    return {
        "status": "ok",
        "available": bool(payload.get("is_available")),
        "balances": balances,
        "updated_at": _now(),
    }


def deepseek_card(settings: Settings, *, balance: Mapping[str, Any] | None = None) -> Card:
    """Llamadas, tokens, latencia y costo estimado de este proceso; saldo si se entrega."""

    title = "DeepSeek"
    calls = DEEPSEEK_CALLS.snapshot()
    cost = estimated_cost(
        calls["input_tokens"],
        calls["output_tokens"],
        input_per_million=settings.deepseek_price_input_per_million,
        output_per_million=settings.deepseek_price_output_per_million,
    )
    data = {
        "model": settings.deepseek_model,
        "calls": calls,
        "cost_usd": cost,
        "price_input": settings.deepseek_price_input_per_million,
        "price_output": settings.deepseek_price_output_per_million,
        "balance": dict(balance) if balance is not None else None,
    }
    if settings.llm_provider != "deepseek":
        reason = f"El chat usa otro proveedor (LLM_PROVIDER={settings.llm_provider})."
        return Card("deepseek", title, "off", reason, data, updated_at=_now())
    if not settings.deepseek_api_key.strip():
        return Card("deepseek", title, "off", "Falta DEEPSEEK_API_KEY.", data, updated_at=_now())
    return Card("deepseek", title, "ok", "", data, updated_at=_now())


# --- WhatsApp (OpenClaw) ------------------------------------------------------------------


def channel_usage(factory: sessionmaker[Session], *, now: datetime | None = None) -> dict[str, Any]:
    """Mensajes, conversaciones y cotizaciones por canal (web o WhatsApp), solo conteos."""

    moment = now or _now()
    since_day = moment - timedelta(hours=24)
    since_week = moment - timedelta(days=7)
    conversations = tables.conversations
    messages = tables.messages
    quotes = tables.quotes
    channel = case(
        (func.substr(conversations.c.owner_key, 1, len(WHATSAPP_PREFIX)) == WHATSAPP_PREFIX, "wa"),
        else_="web",
    )
    usage: dict[str, Any] = {
        name: {
            "messages_24h": 0,
            "messages_7d": 0,
            "conversations_24h": 0,
            "conversations_7d": 0,
            "chats_7d": 0,
            "quotes_7d": 0,
            "last_at": None,
        }
        for name in ("wa", "web")
    }
    with factory() as session:
        rows = session.execute(
            select(
                channel.label("channel"),
                func.count().filter(messages.c.created_at >= since_day),
                func.count(),
                func.count(func.distinct(messages.c.conversation_id)).filter(
                    messages.c.created_at >= since_day
                ),
                func.count(func.distinct(messages.c.conversation_id)),
                func.count(func.distinct(conversations.c.owner_key)),
                func.max(messages.c.created_at),
            )
            .select_from(messages.join(conversations))
            .where(messages.c.role == "user", messages.c.created_at >= since_week)
            .group_by(channel)
        ).all()
        for name, day, week, conv_day, conv_week, chats, last in rows:
            usage[name].update(
                messages_24h=day,
                messages_7d=week,
                conversations_24h=conv_day,
                conversations_7d=conv_week,
                chats_7d=chats,
                last_at=last,
            )
        quote_channel = case(
            (func.substr(quotes.c.owner_key, 1, len(WHATSAPP_PREFIX)) == WHATSAPP_PREFIX, "wa"),
            else_="web",
        )
        for name, count in session.execute(
            select(quote_channel, func.count())
            .where(quotes.c.issued_at.is_not(None), quotes.c.issued_at >= since_week)
            .group_by(quote_channel)
        ).all():
            usage[name]["quotes_7d"] = count
    return usage


def mcp_health(settings: Settings, client: httpx.Client) -> dict[str, Any]:
    """``/health`` del servidor MCP (contadores del proceso de WhatsApp) o el motivo del fallo."""

    url = settings.metrics_mcp_health_url.strip()
    # El bearer de solo ``/health``: la app web no recibe ``MCP_TOKEN``, que abre ``/mcp``.
    token = settings.mcp_health_token.strip()
    if not url or not token:
        return {
            "status": "off",
            "reason": (
                "La app no tiene METRICS_MCP_HEALTH_URL y MCP_HEALTH_TOKEN para consultar el MCP."
            ),
        }
    try:
        response = client.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=MCP_TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        logger.info("metrics.mcp_failed", error_type=type(error).__name__)
        return {"status": "unavailable", "reason": _failure(error)}
    metrics = payload.get("metrics") or {}
    claims = payload.get("claims") or {}
    return {
        "status": "ok",
        "whatsapp": metrics.get("whatsapp") or {},
        "jev": metrics.get("jev") or {},
        "deepseek": metrics.get("deepseek") or {},
        "claims": {
            "judged": claims.get("judged"),
            "substituted": claims.get("substituted"),
            "substitution_rate": claims.get("substitution_rate"),
        },
    }


def whatsapp_card(
    settings: Settings,
    client: httpx.Client,
    factory: sessionmaker[Session] | None,
) -> Card:
    """Uso del canal según la base y lo que observa el MCP; OpenClaw queda fuera de alcance."""

    title = "WhatsApp (OpenClaw)"
    data: dict[str, Any] = {"note": OPENCLAW_NOTE, "usage": None, "usage_error": None}
    if factory is not None:
        try:
            data["usage"] = channel_usage(factory)
        except Exception as error:
            logger.info("metrics.channel_usage_failed", error_type=type(error).__name__)
            data["usage_error"] = "La base no respondió."
    else:
        data["usage_error"] = "Sin base de datos configurada."
    data["mcp"] = mcp_health(settings, client)
    if data["usage"] is None and data["mcp"]["status"] != "ok":
        reason = data["usage_error"] or ""
        return Card("whatsapp", title, "unavailable", reason, data, updated_at=_now())
    return Card("whatsapp", title, "ok", "", data, updated_at=_now(), ttl_s=WHATSAPP_TTL)


# --- Tablero ------------------------------------------------------------------------------


@dataclass
class MetricsBoard:
    """Arma las tarjetas con caché; las pruebas inyectan el transporte y la base."""

    settings: Settings
    session_factory: Callable[[], sessionmaker[Session]] | None = None
    transport: httpx.BaseTransport | None = None
    cache: TtlCache = field(default_factory=TtlCache)

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, follow_redirects=False)

    def _factory(self) -> sessionmaker[Session] | None:
        if self.session_factory is None:
            return None
        try:
            return self.session_factory()
        except Exception as error:
            logger.info("metrics.session_factory_failed", error_type=type(error).__name__)
            return None

    def _load(
        self, key: str, title: str, loader: Callable[[httpx.Client], Card]
    ) -> Callable[[], Card]:
        """Cargador con su propio cliente HTTP que nunca lanza.

        Cada tarea del pool abre su cliente: ``httpx.Client`` no promete ser seguro entre
        hilos. Si la fuente devuelve algo inesperado (un JSON con otra forma), la tarjeta
        queda «no disponible» y el resto de la página sigue.
        """

        def run() -> Card:
            try:
                with self._client() as client:
                    return loader(client)
            except Exception as error:
                logger.info("metrics.card_failed", card=key, error_type=type(error).__name__)
                return Card(key, title, "unavailable", _failure(error), updated_at=_now())

        return run

    def cards(
        self,
        *,
        operator: bool,
        jev: Mapping[str, Any],
        claims: Mapping[str, Any],
        photo: Mapping[str, Any],
    ) -> list[Card]:
        """Las cinco tarjetas; las fuentes con red se consultan en paralelo."""

        settings = self.settings
        with ThreadPoolExecutor(max_workers=4) as pool:
            qdrant = pool.submit(
                self.cache.get,
                "qdrant",
                QDRANT_TTL,
                self._load("qdrant", "Qdrant", lambda client: qdrant_card(settings, client)),
            )
            langsmith = pool.submit(
                self.cache.get,
                "langsmith",
                LANGSMITH_TTL,
                self._load(
                    "langsmith", "LangSmith", lambda client: langsmith_card(settings, client)
                ),
            )
            whatsapp = pool.submit(
                self.cache.get,
                "whatsapp",
                WHATSAPP_TTL,
                self._load(
                    "whatsapp",
                    "WhatsApp (OpenClaw)",
                    lambda client: whatsapp_card(settings, client, self._factory()),
                ),
            )
            balance = None
            if operator and settings.deepseek_api_key.strip():
                balance = pool.submit(
                    self.cache.get,
                    "deepseek_balance",
                    BALANCE_TTL,
                    self._load(
                        "deepseek_balance", "Saldo", lambda client: _balance_card(settings, client)
                    ),
                )
            balance_data: dict[str, Any] | None = None
            if balance is not None:
                card = balance.result()
                balance_data = dict(card.data) or {"status": card.status, "reason": card.reason}
            cards = [
                qdrant.result(),
                jev_card(settings, jev=jev, claims=claims, photo=photo),
                langsmith.result(),
                deepseek_card(settings, balance=balance_data),
                whatsapp.result(),
            ]
        return cards


def _balance_card(settings: Settings, client: httpx.Client) -> Card:
    """El saldo como tarjeta interna, para reutilizar la caché con su vigencia."""

    balance = deepseek_balance(settings, client)
    status: CardStatus = "ok" if balance["status"] == "ok" else "unavailable"
    return Card(
        "deepseek_balance",
        "Saldo",
        status,
        str(balance.get("reason") or ""),
        balance,
        updated_at=_now(),
        ttl_s=BALANCE_TTL,
    )
