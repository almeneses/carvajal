"""Agregadores de la página «Métricas» (spec 055).

Cada fuente arma una :class:`Card` con números agregados y nunca lanza: si la fuente falla, la
tarjeta dice «no disponible» y por qué, y la página sigue. Las fuentes con red tienen plazo
corto y caché (:class:`TtlCache`); si una consulta falla y hay un dato anterior, se muestra el
anterior marcado como desactualizado.

- **Qdrant**: ``GET /collections/{colección}`` y ``GET /telemetry`` (versión, tamaño, consultas).
- **Jev**: el bloque ``jev`` y ``claims`` de ``/health`` y las llamadas del proceso
  (:data:`~carvajal.infrastructure.metrics.JEV_CALLS`); sin red.
- **LangSmith**: el agregado ``POST /runs/stats`` del proyecto para 24 h y 7 días; nunca
  descarga runs. LangSmith acepta como mucho una consulta de ``/runs/stats`` cada ~8 s (medido
  el 2026-11-26), así que cada ventana tiene su propia caché y las consultas se espacian
  (:class:`LangSmithState`): con la caché fría la página trae una ventana y deja la otra
  «pendiente» unos segundos, en vez de chocar con un 429.
- **DeepSeek**: llamadas, tokens y latencia del proceso con el costo estimado; el saldo de la
  cuenta (``GET /user/balance``) solo para el operador.
- **WhatsApp (OpenClaw)**: lo que guarda la base (mensajes, conversaciones y cotizaciones del
  canal) y, si está configurado, el ``/health`` del servidor MCP por la red interna. El
  gateway de OpenClaw vive en otra VM y la app no lo alcanza.

Nada de esto lleva texto de las personas, ``owner_key``, teléfonos ni secretos.
"""

import math
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Final, Literal

import httpx
import structlog
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from carvajal.adapters.storage import tables
from carvajal.infrastructure.metrics import DEEPSEEK_CALLS, JEV_CALLS, estimated_cost
from carvajal.infrastructure.settings import Settings

__all__ = [
    "RETRYABLE_SOURCES",
    "Card",
    "LangSmithState",
    "MetricsBoard",
    "RetryOutcome",
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
# LangSmith: el id del proyecto no cambia; se renueva cada hora y, si falla, sirve el anterior.
LANGSMITH_PROJECT_TTL: Final = 3600
# Espacio mínimo entre dos ``POST /runs/stats``: con 6,4 s LangSmith responde 429 y con 8,5 s
# acepta (medido el 2026-11-26 con la clave del proyecto).
LANGSMITH_STATS_SPACING: Final = 9.0
# Una ventana que falló (429 u otro error) se reintenta a los 30 s, o cuando diga Retry-After.
LANGSMITH_RETRY_TTL: Final = 30
# Un Retry-After mayor se acota: la ventana se reintenta como mucho a los 10 min.
LANGSMITH_MAX_RETRY_AFTER: Final = 600
# Plazo total de la tarjeta (todas sus peticiones y esperas) y reintentos dentro de una carga:
# solo se espera si la espera cabe en el plazo; si no, la ventana queda pendiente.
LANGSMITH_BUDGET: Final = 3.0
LANGSMITH_MAX_RETRIES: Final = 2
# Reintento manual («Reintentar»): como mucho uno por fuente cada 15 s, para todas las visitas.
RETRY_COOLDOWN: Final = 15
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
    # Cuándo se vuelve a consultar la fuente, si la tarjeta está caída, desactualizada o
    # incompleta; la página lo muestra como «se reintenta en N s».
    retry_at: datetime | None = None


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
        # Clave → momento de la última consulta real a la fuente (enfriamiento del reintento).
        self._loaded_at: dict[str, float] = {}

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
            with self._lock:
                self._loaded_at[key] = self._clock()
            fresh = loader()
            # Una tarjeta buena puede pedir menos vigencia (LangSmith con una ventana pendiente).
            valid_for: float = min(ttl, fresh.ttl_s) if fresh.ttl_s > 0 else ttl
            if fresh.status == "unavailable":
                valid_for = fresh.ttl_s or min(ttl, FAILURE_RETRY_TTL)
                retry_at = _now() + timedelta(seconds=valid_for)
                if previous is not None and previous.status == "ok":
                    # El dato anterior sirve más que un «no disponible»; se marca y se reintenta
                    # cuando vence la vigencia corta.
                    fresh = replace(
                        previous,
                        stale=True,
                        reason=fresh.reason,
                        ttl_s=int(valid_for),
                        retry_at=retry_at,
                    )
                else:
                    fresh = replace(fresh, retry_at=retry_at)
            with self._lock:
                self._items[key] = (self._clock(), fresh, valid_for)
            return fresh

    def expire(self, key: str) -> None:
        """Da por caducada la clave sin borrarla: la próxima visita recarga y, si falla,
        conserva el dato anterior como desactualizado."""

        with self._lock:
            cached = self._items.get(key)
            if cached is not None:
                self._items[key] = (cached[0], cached[1], 0.0)

    def since_load(self, key: str) -> float | None:
        """Segundos desde la última vez que se consultó la fuente de verdad (no la caché)."""

        with self._lock:
            loaded_at = self._loaded_at.get(key)
        return None if loaded_at is None else self._clock() - loaded_at

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._loaded_at.clear()


def _failure(error: Exception, source: str = "") -> str:
    """Motivo legible de un fallo de red, sin URL, cabeceras ni cuerpo."""

    if isinstance(error, httpx.TimeoutException):
        return "No respondió a tiempo."
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        if code in (401, 403):
            return f"Rechazó las credenciales (HTTP {code})."
        if code == 429:
            return f"{source or 'La fuente'} limita cuántas consultas acepta por minuto (HTTP 429)."
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


# Ventanas de la tarjeta: clave, alcance y etiqueta (en este orden se consultan con la caché fría).
LANGSMITH_WINDOWS: Final = (
    ("24h", timedelta(hours=24), "Últimas 24 h"),
    ("7d", timedelta(days=7), "Últimos 7 días"),
)
# Tiempo mínimo que debe quedar del plazo para lanzar una petición.
LANGSMITH_MIN_REQUEST: Final = 1.0
LANGSMITH_PENDING: Final = (
    "LangSmith acepta una consulta cada pocos segundos y esta ventana espera su turno."
)


def _retry_after(response: httpx.Response) -> float | None:
    """Segundos que pide esperar ``Retry-After`` (número o fecha HTTP), acotados; o ``None``."""

    value = response.headers.get("retry-after", "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        seconds = (moment - _now()).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(0.0, seconds), float(LANGSMITH_MAX_RETRY_AFTER))


@dataclass
class _Window:
    """Lo último que se sabe de una ventana: su dato bueno y, si falló, cuándo reintentar."""

    data: dict[str, Any] | None = None
    fetched_at: float = 0.0
    updated_at: datetime | None = None
    retry_at: float = 0.0
    reason: str = ""
    forced: bool = False


class LangSmithState:
    """Memoria de LangSmith entre cargas: id del proyecto, cada ventana y el turno de consulta.

    ``POST /runs/stats`` acepta como mucho una petición cada ~8 s: nunca se lanzan dos más
    juntas que :data:`LANGSMITH_STATS_SPACING`. Cada ventana tiene su propia vigencia
    (:data:`LANGSMITH_TTL`); si no le toca turno, la tarjeta muestra su dato anterior o la deja
    «pendiente» y se vuelve a armar en cuanto pueda consultarla. Un fallo (429 u otro) frena
    solo a su ventana unos segundos (:data:`LANGSMITH_RETRY_TTL` o lo que diga ``Retry-After``).
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        spacing: float = LANGSMITH_STATS_SPACING,
        budget: float = LANGSMITH_BUDGET,
    ) -> None:
        self.clock = clock
        self.sleep = sleep
        self.spacing = spacing
        self.budget = budget
        self.lock = threading.Lock()
        self._scope: tuple[str, ...] = ()
        self.project: tuple[str, float] | None = None
        self.next_stats_at = 0.0
        self.windows: dict[str, _Window] = {label: _Window() for label, _, _ in LANGSMITH_WINDOWS}

    def bind(self, scope: tuple[str, ...]) -> None:
        """Olvida lo guardado si cambió el endpoint, el proyecto o el entorno."""

        if scope != self._scope:
            self._scope = scope
            self.project = None
            self.windows = {label: _Window() for label, _, _ in LANGSMITH_WINDOWS}

    def force(self) -> None:
        """Reintento manual: todas las ventanas vuelven a consultarse en cuanto haya turno.

        No toca el turno entre consultas: un reintento no puede provocar un 429.
        """

        with self.lock:
            for window in self.windows.values():
                window.forced = True
                window.retry_at = 0.0

    def due(self, window: _Window) -> bool:
        return (
            window.data is None
            or window.forced
            or self.clock() - window.fetched_at >= LANGSMITH_TTL
        )


def _langsmith_unavailable(reason: str, retry_in: float) -> Card:
    return Card(
        "langsmith",
        "LangSmith",
        "unavailable",
        reason,
        updated_at=_now(),
        ttl_s=max(1, math.ceil(retry_in)),
    )


def _langsmith_project(
    settings: Settings,
    client: httpx.Client,
    state: LangSmithState,
    *,
    base: str,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[str | None, str, float]:
    """Id del proyecto (caché de una hora; si la renovación falla, sirve el anterior).

    Devuelve ``(id, motivo, reintentar en)``; sin id, el motivo dice por qué.
    """

    cached = state.project
    if cached is not None and state.clock() - cached[1] < LANGSMITH_PROJECT_TTL:
        return cached[0], "", 0.0
    try:
        response = client.get(
            f"{base}/sessions",
            params={"name": settings.langsmith_project, "limit": 1},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        found = response.json()
    except Exception as error:
        logger.info("metrics.langsmith_project_failed", error_type=type(error).__name__)
        if cached is not None:
            # Sirve el id anterior y no vuelve a pedirlo hasta dentro de 30 s.
            renew_at = state.clock() - LANGSMITH_PROJECT_TTL + LANGSMITH_RETRY_TTL
            state.project = (cached[0], renew_at)
            return cached[0], "", 0.0
        retry_in: float = LANGSMITH_RETRY_TTL
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
            retry_in = max(retry_in, _retry_after(error.response) or 0.0)
        return None, _failure(error, "LangSmith"), retry_in
    if not found:
        state.project = None
        reason = f"El proyecto «{settings.langsmith_project}» no existe en LangSmith."
        return None, reason, float(LANGSMITH_RETRY_TTL)
    state.project = (str(found[0]["id"]), state.clock())
    return state.project[0], "", 0.0


def langsmith_card(
    settings: Settings,
    client: httpx.Client,
    *,
    now: datetime | None = None,
    state: LangSmithState | None = None,
) -> Card:
    """Trazas raíz del proyecto en 24 h y 7 días con el agregado de LangSmith.

    Sin ``state`` no hay memoria entre llamadas: se consulta la primera ventana y la otra
    queda pendiente (no hay turno para ella). El tablero guarda un :class:`LangSmithState`.
    """

    title = "LangSmith"
    key = settings.langsmith_api_key.strip()
    if not settings.monitoring_enabled or not key:
        reason = "El monitoreo está apagado (MONITORING_ENABLED o LANGSMITH_API_KEY)."
        return Card("langsmith", title, "off", reason, updated_at=_now())
    state = state or LangSmithState()
    with state.lock:
        return _langsmith_load(settings, client, state, key=key, moment=now or _now())


def _langsmith_load(
    settings: Settings,
    client: httpx.Client,
    state: LangSmithState,
    *,
    key: str,
    moment: datetime,
) -> Card:
    base = (settings.langsmith_endpoint.strip() or LANGSMITH_DEFAULT_ENDPOINT).rstrip("/")
    headers = {"x-api-key": key}
    environment = settings.langsmith_agent_environment.strip()
    state.bind((base, settings.langsmith_project, environment))
    deadline = state.clock() + state.budget

    def remaining() -> float:
        return deadline - state.clock()

    project_id, reason, retry_in = _langsmith_project(
        settings,
        client,
        state,
        base=base,
        headers=headers,
        timeout=max(0.1, min(LANGSMITH_TIMEOUT, remaining())),
    )
    if project_id is None:
        return _langsmith_unavailable(reason, retry_in)

    spans = {label: delta for label, delta, _ in LANGSMITH_WINDOWS}
    # Con la caché fría, primero la que no tiene dato; luego la más vieja.
    order = sorted(
        spans,
        key=lambda label: (state.windows[label].data is not None, state.windows[label].fetched_at),
    )
    retries = 0
    for label in order:
        window = state.windows[label]
        while state.due(window) and state.clock() >= window.retry_at:
            wait = state.next_stats_at - state.clock()
            if wait > 0:
                # Solo se espera si la espera cabe en el plazo; si no, la ventana queda en turno.
                if wait + LANGSMITH_MIN_REQUEST > remaining():
                    break
                state.sleep(wait)
            if remaining() < LANGSMITH_MIN_REQUEST:
                break
            body: dict[str, Any] = {
                "session": [project_id],
                "start_time": (moment - spans[label]).isoformat(),
                "is_root": True,
            }
            if environment:
                body["filter"] = (
                    'and(eq(metadata_key, "agent_environment"), '
                    f'eq(metadata_value, "{environment}"))'
                )
            # El turno cuenta desde que sale la petición: una que no responde a tiempo también
            # pudo contar para el límite de LangSmith.
            state.next_stats_at = state.clock() + state.spacing
            try:
                response = client.post(
                    f"{base}/runs/stats",
                    json=body,
                    headers=headers,
                    timeout=max(0.1, min(LANGSMITH_TIMEOUT, remaining())),
                )
                response.raise_for_status()
                data = _langsmith_window(response.json())
            except Exception as error:
                logger.info(
                    "metrics.langsmith_failed", window=label, error_type=type(error).__name__
                )
                window.reason = _failure(error, "LangSmith")
                window.forced = False
                limited = (
                    isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429
                )
                if not limited:
                    window.retry_at = state.clock() + LANGSMITH_RETRY_TTL
                    break
                pause = _retry_after(error.response)
                pause = state.spacing if pause is None else pause
                state.next_stats_at = max(state.next_stats_at, state.clock() + pause)
                if retries < LANGSMITH_MAX_RETRIES and pause + LANGSMITH_MIN_REQUEST <= remaining():
                    retries += 1
                    continue  # el siguiente giro espera ``pause`` y lo reintenta
                window.retry_at = state.clock() + max(pause, float(LANGSMITH_RETRY_TTL))
                break
            window.data = data
            window.fetched_at = state.clock()
            window.updated_at = _now()
            window.reason = ""
            window.retry_at = 0.0
            window.forced = False
    return _langsmith_summary(settings, state, environment)


def _langsmith_summary(settings: Settings, state: LangSmithState, environment: str) -> Card:
    """Arma la tarjeta con lo que haya de cada ventana y cuándo se completa."""

    current = state.clock()
    wall = _now()
    windows: dict[str, Any] = {}
    checks: list[float] = []
    attempts: list[float] = []
    for label, _, title in LANGSMITH_WINDOWS:
        window = state.windows[label]
        item: dict[str, Any] = dict(window.data or {})
        expired = window.data is not None and current - window.fetched_at >= LANGSMITH_TTL
        if window.data is None:
            item["status"] = "pending"
        else:
            item["status"] = "stale" if expired else "ok"
        item["label"] = title
        item["note"] = window.reason or (LANGSMITH_PENDING if window.data is None else "")
        item["updated_at"] = window.updated_at
        item["retry_at"] = None
        if state.due(window):
            attempt = max(window.retry_at, state.next_stats_at, current)
            attempts.append(attempt)
            checks.append(attempt)
            item["retry_at"] = wall + timedelta(seconds=attempt - current)
        else:
            checks.append(window.fetched_at + LANGSMITH_TTL)
        windows[label] = item
    ttl_s = min(LANGSMITH_TTL, max(1, math.ceil(min(checks) - current)))
    retry_at = wall + timedelta(seconds=min(attempts) - current) if attempts else None
    if all(item["status"] == "pending" for item in windows.values()):
        reason = next((w.reason for w in state.windows.values() if w.reason), LANGSMITH_PENDING)
        return replace(_langsmith_unavailable(reason, ttl_s), retry_at=retry_at)
    failures = [w.reason for w in state.windows.values() if w.reason]
    data = {
        "project": settings.langsmith_project,
        "environment": environment or None,
        "sample_rate": settings.monitoring_sample_rate,
        "windows": windows,
    }
    return Card(
        "langsmith",
        "LangSmith",
        "ok",
        # Solo si fallaron las dos: con una ventana pendiente, la fila lo dice.
        failures[0] if len(failures) == len(windows) else "",
        data,
        updated_at=_now(),
        stale=any(item["status"] == "stale" for item in windows.values()),
        ttl_s=ttl_s,
        retry_at=retry_at,
    )


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
            .select_from(
                messages.join(conversations, messages.c.conversation_id == conversations.c.id)
            )
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

# Fuentes que admiten «Reintentar» → si es solo para el operador. Jev y DeepSeek se arman en
# cada carga sin red y no lo necesitan; el saldo de DeepSeek es del operador.
RETRYABLE_SOURCES: Final[Mapping[str, bool]] = {
    "qdrant": False,
    "langsmith": False,
    "whatsapp": False,
    "deepseek_balance": True,
}


@dataclass(frozen=True)
class RetryOutcome:
    """Resultado de un reintento manual: hecho, en espera (con segundos) o rechazado."""

    status: Literal["done", "wait", "unknown", "forbidden"]
    wait_s: int = 0


@dataclass
class MetricsBoard:
    """Arma las tarjetas con caché; las pruebas inyectan el transporte y la base."""

    settings: Settings
    session_factory: Callable[[], sessionmaker[Session]] | None = None
    transport: httpx.BaseTransport | None = None
    cache: TtlCache = field(default_factory=TtlCache)
    langsmith: LangSmithState = field(default_factory=LangSmithState)
    _retry_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def retry_wait(self, source: str) -> int:
        """Segundos que faltan para poder reintentar la fuente (0 si ya se puede)."""

        since = self.cache.since_load(source)
        if since is None:
            return 0
        return max(0, math.ceil(RETRY_COOLDOWN - since))

    def retry(self, source: str, *, operator: bool) -> RetryOutcome:
        """Reintento manual: da la fuente por caducada para que la próxima carga la consulte.

        Nunca llama a la fuente: la consulta la hace la carga siguiente, de a una por fuente.
        Dentro del enfriamiento (:data:`RETRY_COOLDOWN` desde la última consulta real, para
        todas las visitas) no hace nada y dice cuánto falta.
        """

        if source not in RETRYABLE_SOURCES:
            return RetryOutcome("unknown")
        if RETRYABLE_SOURCES[source] and not operator:
            return RetryOutcome("forbidden")
        with self._retry_lock:
            wait = self.retry_wait(source)
            if wait:
                return RetryOutcome("wait", wait)
            self.cache.expire(source)
            if source == "langsmith":
                self.langsmith.force()
        logger.info("metrics.retry_requested", source=source)
        return RetryOutcome("done")

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
                    "langsmith",
                    "LangSmith",
                    lambda client: langsmith_card(settings, client, state=self.langsmith),
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
