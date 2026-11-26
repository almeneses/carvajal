"""Contadores en proceso de la compuerta de afirmaciones (spec 046, medición 2026-11-26).

El verificador sustituía ~24 % de las respuestas juzgadas en producción y la única forma de
verlo era contar eventos ``claims.judged`` en los logs, que se pierden en cada despliegue.
:class:`ClaimsStats` cuenta, desde que arrancó el proceso, los veredictos de cada turno del
modo ``tools``: juzgadas (respaldadas y no respaldadas), sustituidas, sin veredicto de Jev
(``unavailable`` por motivo), omitidas (``skipped`` por motivo) y apagadas (``off``), más un
histograma de ``p`` de las juzgadas. :func:`claims_health` arma el bloque ``claims`` de
``/health`` con la tasa de sustitución, el umbral y el modo.

Solo números y motivos internos: ningún texto, pregunta ni dato de la persona. Los
contadores son por proceso (la app web y el servidor MCP de WhatsApp llevan los suyos) y se
reinician al reiniciar el contenedor. Seguros entre hilos: el runner del agente corre cada
turno en un hilo del *threadpool*.
"""

import threading
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

__all__ = [
    "CLAIMS_STATS",
    "HISTOGRAM_BINS",
    "ClaimsStats",
    "claims_health",
]

# Cubetas de ``p`` de las respuestas juzgadas: [0,0.1), [0.1,0.2), …, [0.9,1.0].
HISTOGRAM_BINS: Final = 10
JUDGED_STATUSES: Final = frozenset({"supported", "unsupported"})
# Motivo de un ``skipped`` que llegó al final del turno sin motivo registrado.
NO_REASON: Final = "sin_motivo"


def _now() -> datetime:
    return datetime.now(UTC)


class ClaimsStats:
    """Contadores de la compuerta desde el arranque del proceso (o el último :meth:`reset`).

    :meth:`record` recibe el estado del veredicto (``supported``, ``unsupported``,
    ``unavailable``, ``skipped`` u ``off``); un estado desconocido se ignora para que un
    cambio del data-model no rompa el turno.
    """

    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._statuses: Counter[str] = Counter()
        self._unavailable: Counter[str] = Counter()
        self._skipped: Counter[str] = Counter()
        self._histogram = [0] * HISTOGRAM_BINS
        self._substituted = 0
        self._since = clock()

    def reset(self) -> None:
        """Vuelve a cero (lo usan las pruebas; en producción reinicia el proceso)."""

        with self._lock:
            self._statuses.clear()
            self._unavailable.clear()
            self._skipped.clear()
            self._histogram = [0] * HISTOGRAM_BINS
            self._substituted = 0
            self._since = self._clock()

    def record(
        self,
        status: str,
        *,
        reason: str = "",
        p: float | None = None,
        substituted: bool = False,
    ) -> None:
        """Cuenta un veredicto; ``p`` solo cuenta en las juzgadas y ``substituted`` en las
        no respaldadas (en sombra se juzga sin sustituir)."""

        if status not in (*JUDGED_STATUSES, "unavailable", "skipped", "off"):
            return
        with self._lock:
            self._statuses[status] += 1
            if status == "unavailable":
                self._unavailable[reason or NO_REASON] += 1
            elif status == "skipped":
                self._skipped[reason or NO_REASON] += 1
            elif status in JUDGED_STATUSES:
                if p is not None:
                    self._histogram[_bin(p)] += 1
                if substituted and status == "unsupported":
                    self._substituted += 1

    def record_verdict(self, claims: Mapping[str, Any]) -> None:
        """Cuenta el veredicto ``claims`` de un turno (el dict del data-model)."""

        self.record(
            str(claims.get("status") or ""),
            reason=str(claims.get("reason") or ""),
            p=_float(claims.get("p")),
            substituted=bool(claims.get("substituted")),
        )

    def snapshot(self) -> dict[str, Any]:
        """Copia coherente de los contadores, con las tasas sobre las juzgadas."""

        with self._lock:
            supported = self._statuses["supported"]
            unsupported = self._statuses["unsupported"]
            judged = supported + unsupported
            return {
                "since": self._since.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "turns": sum(self._statuses.values()),
                "judged": judged,
                "supported": supported,
                "unsupported": unsupported,
                "substituted": self._substituted,
                "substitution_rate": _rate(self._substituted, judged),
                "unsupported_rate": _rate(unsupported, judged),
                "unavailable": dict(sorted(self._unavailable.items())),
                "skipped": dict(sorted(self._skipped.items())),
                "off": self._statuses["off"],
                "p_histogram": list(self._histogram),
            }


def _bin(p: float) -> int:
    return min(HISTOGRAM_BINS - 1, max(0, int(p * HISTOGRAM_BINS)))


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


# Contadores del proceso: los comparten todos los runners (web, webhook y MCP del mismo
# proceso).
CLAIMS_STATS: Final = ClaimsStats()


def claims_health(
    settings: Any,
    stats: ClaimsStats | None = None,
    *,
    active: bool | None = None,
) -> dict[str, Any]:
    """Bloque ``claims`` de ``/health``: modo, umbral y contadores, sin red ni texto.

    ``active`` dice si el proceso atiende con la compuerta disponible (modo ``tools`` con
    juez de Jev); ``None`` lo deduce de la configuración como el bloque ``agent``: el modo
    que atenderá debe ser ``tools`` (``agent_mode_status``, que también exige DeepSeek
    completo) y Jev debe estar encendido con clave. Sin compuerta, ``mode`` es ``off``
    aunque ``JEV_CLAIMS_MODE`` diga otra cosa.
    """

    if active is None:
        # Diferido: el resto del módulo no necesita la configuración.
        from carvajal.infrastructure.agent_mode import agent_mode_status

        active = agent_mode_status(settings).expected == "tools" and bool(
            getattr(settings, "jev_active", False)
        )
    mode = str(getattr(settings, "jev_claims_mode", "off") or "off") if active else "off"
    return {
        "mode": mode,
        "threshold": float(getattr(settings, "jev_claims_threshold", 0.5)),
        **(stats if stats is not None else CLAIMS_STATS).snapshot(),
    }
