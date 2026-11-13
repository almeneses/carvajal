"""Cupo diario por visitante: reserva atómica y hora de reinicio."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from carvajal.application.ports import UsageRepository

__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_TIMEZONE",
    "DailyQuota",
    "QuotaResult",
    "local_now",
    "seconds_until_tomorrow",
]

DEFAULT_LIMIT = 20
DEFAULT_TIMEZONE = "America/Bogota"


@dataclass(frozen=True)
class QuotaResult:
    """Resultado de pedir cupo: si se concedió y cuándo se reinicia."""

    allowed: bool
    retry_after: int = 0


def local_now(timezone: str = DEFAULT_TIMEZONE) -> datetime:
    """Hora actual en la zona del cupo, independiente del ``TZ`` del contenedor."""

    return datetime.now(ZoneInfo(timezone))


def seconds_until_tomorrow(now: datetime) -> int:
    """Segundos que faltan hasta la próxima medianoche en la zona de ``now``."""

    manana = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo)
    return max(1, int((manana - now).total_seconds()))


class DailyQuota:
    """Concede hasta ``limit`` peticiones por visitante y día."""

    def __init__(
        self,
        usage: UsageRepository,
        *,
        limit: int = DEFAULT_LIMIT,
        timezone: str = DEFAULT_TIMEZONE,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if limit < 0:
            raise ValueError("El límite diario no puede ser negativo.")
        self._usage = usage
        self._limit = limit
        self._timezone = timezone
        self._now = now

    def consume(self, owner_key: str) -> QuotaResult:
        """Reserva una petición o indica cuándo se reinicia el cupo."""

        momento = self._now() if self._now is not None else local_now(self._timezone)
        if self._usage.try_consume(owner_key, momento.date(), self._limit):
            return QuotaResult(allowed=True)
        return QuotaResult(allowed=False, retry_after=seconds_until_tomorrow(momento))
