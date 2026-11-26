"""Contadores en proceso para la página de métricas (spec 055).

Cada proceso (la app web y el servidor MCP de WhatsApp) lleva los suyos desde que arrancó:
se reinician con cada despliegue o reinicio del contenedor. Solo números, estados y tipos de
llamada; nunca texto de la persona, ``owner_key`` ni teléfonos. Seguros entre hilos: el chat
corre cada turno en un hilo del *threadpool*.

- :class:`CallStats` cuenta llamadas a un servicio externo (Jev, DeepSeek): por tipo y por
  estado, tokens de entrada y salida y la latencia de las últimas :data:`LATENCY_WINDOW`
  llamadas para los percentiles p50 y p95.
- :class:`EventCounts` cuenta eventos con nombre (turnos de WhatsApp, errores, no
  autorizados…) y la hora del último.
"""

import math
import threading
from collections import Counter, deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final

__all__ = [
    "DEEPSEEK_CALLS",
    "JEV_CALLS",
    "LATENCY_WINDOW",
    "WHATSAPP_EVENTS",
    "CallStats",
    "EventCounts",
    "estimated_cost",
    "percentile",
]

# Latencias que se conservan para los percentiles: las más recientes, sin crecer sin límite.
LATENCY_WINDOW: Final = 1000


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Percentil por rango más cercano (``fraction`` entre 0 y 1); ``None`` sin datos."""

    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return ordered[rank - 1]


class CallStats:
    """Llamadas a un servicio externo desde que arrancó el proceso (o el último :meth:`reset`)."""

    def __init__(self, clock: Callable[[], datetime] = _now, window: int = LATENCY_WINDOW) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._window = window
        self._reset()

    def _reset(self) -> None:
        self._kinds: Counter[str] = Counter()
        self._statuses: Counter[str] = Counter()
        # Llamadas que no salieron (cupo del día, circuito abierto): no son llamadas ni
        # entran en los percentiles, pero /metricas debe mostrar los cortes.
        self._blocked: Counter[str] = Counter()
        # Llamadas correctas sin uso informado (streaming sin ``stream_usage``): sus tokens no
        # suman y el costo estimado se queda corto.
        self._without_usage = 0
        self._latencies: deque[float] = deque(maxlen=self._window)
        self._input_tokens = 0
        self._output_tokens = 0
        self._last_at: datetime | None = None
        self._since = self._clock()

    def reset(self) -> None:
        """Vuelve a cero (lo usan las pruebas; en producción reinicia el proceso)."""

        with self._lock:
            self._reset()

    def record(
        self,
        *,
        kind: str = "",
        status: str = "ok",
        latency_ms: float | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        usage_known: bool = True,
    ) -> None:
        """Cuenta una llamada; los valores negativos o raros no rompen el turno.

        ``usage_known=False`` marca una llamada cuyo proveedor no informó tokens.
        """

        with self._lock:
            if not usage_known:
                self._without_usage += 1
            self._kinds[kind or "otro"] += 1
            self._statuses[status or "desconocido"] += 1
            if latency_ms is not None and latency_ms >= 0:
                self._latencies.append(float(latency_ms))
            self._input_tokens += max(0, int(input_tokens or 0))
            self._output_tokens += max(0, int(output_tokens or 0))
            self._last_at = self._clock()

    def block(self, status: str) -> None:
        """Cuenta una llamada que no se hizo por ``status`` (``cap``, ``circuit_open``…)."""

        with self._lock:
            self._blocked[status or "desconocido"] += 1

    def snapshot(self) -> dict[str, Any]:
        """Copia coherente: llamadas, estados, tipos, tokens y latencia p50/p95 en ms."""

        with self._lock:
            latencies = list(self._latencies)
            calls = sum(self._statuses.values())
            ok = self._statuses.get("ok", 0)
            p50 = percentile(latencies, 0.5)
            p95 = percentile(latencies, 0.95)
            return {
                "since": _iso(self._since),
                "last_at": _iso(self._last_at),
                "calls": calls,
                "errors": calls - ok,
                "by_status": dict(sorted(self._statuses.items())),
                "by_kind": dict(sorted(self._kinds.items())),
                "blocked": dict(sorted(self._blocked.items())),
                "without_usage": self._without_usage,
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "latency_ms": {
                    "p50": None if p50 is None else round(p50),
                    "p95": None if p95 is None else round(p95),
                    "samples": len(latencies),
                },
            }


class EventCounts:
    """Eventos con nombre desde que arrancó el proceso, con la hora del último."""

    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._counts: Counter[str] = Counter()
        self._last_at: datetime | None = None
        self._since = clock()

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._last_at = None
            self._since = self._clock()

    def count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[name] += amount
            self._last_at = self._clock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "since": _iso(self._since),
                "last_at": _iso(self._last_at),
                "counts": dict(sorted(self._counts.items())),
            }


def estimated_cost(
    input_tokens: int, output_tokens: int, *, input_per_million: float, output_per_million: float
) -> float:
    """Costo estimado en USD con el precio por millón de tokens de entrada y de salida."""

    return round(
        input_tokens * input_per_million / 1_000_000
        + output_tokens * output_per_million / 1_000_000,
        4,
    )


# Contadores del proceso: Jev (gatekeeper, compuerta, flujos y fotos), DeepSeek (agente,
# redacción y resumidor) y lo que ve el puente de WhatsApp.
JEV_CALLS: Final = CallStats()
DEEPSEEK_CALLS: Final = CallStats()
WHATSAPP_EVENTS: Final = EventCounts()
