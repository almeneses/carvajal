"""Pruebas del cupo diario con un repositorio en memoria."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from carvajal.application.quota import DailyQuota, local_now, seconds_until_tomorrow

BOGOTA = ZoneInfo("America/Bogota")


class FakeUsage:
    """Repositorio en memoria con la misma semántica que el adaptador SQL."""

    def __init__(self) -> None:
        self.consumo: dict[tuple[str, date], int] = {}

    def try_consume(self, owner_key: str, day: date, limit: int) -> bool:
        if limit <= 0:
            return False
        clave = (owner_key, day)
        actual = self.consumo.get(clave, 0)
        if actual >= limit:
            return False
        self.consumo[clave] = actual + 1
        return True


AHORA = datetime(2026, 11, 12, 23, 0, tzinfo=BOGOTA)


def _cuota(limit: int = 2) -> tuple[DailyQuota, FakeUsage]:
    uso = FakeUsage()
    return DailyQuota(uso, limit=limit, now=lambda: AHORA), uso


def test_concede_hasta_el_limite_y_despues_bloquea() -> None:
    """La tercera petición con límite dos se rechaza y dice cuándo vuelve."""

    # Arrange
    cuota, _ = _cuota(limit=2)

    # Act
    primera = cuota.consume("ana")
    segunda = cuota.consume("ana")
    tercera = cuota.consume("ana")

    # Assert
    assert primera.allowed and segunda.allowed
    assert not tercera.allowed
    assert tercera.retry_after == 3600
    assert primera.retry_after == 0


def test_visitantes_y_dias_son_independientes() -> None:
    """El cupo es por visitante y por día, no global."""

    # Arrange
    momento = [AHORA]
    cuota = DailyQuota(FakeUsage(), limit=1, now=lambda: momento[0])

    # Act / Assert
    assert cuota.consume("ana").allowed
    assert not cuota.consume("ana").allowed
    assert cuota.consume("luis").allowed

    momento[0] = momento[0] + timedelta(days=1)
    assert cuota.consume("ana").allowed


def test_limite_cero_no_concede() -> None:
    """Un límite de cero bloquea sin escribir contadores."""

    cuota, uso = _cuota(limit=0)

    resultado = cuota.consume("ana")

    assert not resultado.allowed
    assert resultado.retry_after == 3600
    assert uso.consumo == {}


def test_limite_negativo_es_un_error_de_programacion() -> None:
    """Un límite negativo no es una política válida."""

    with pytest.raises(ValueError, match="negativo"):
        DailyQuota(FakeUsage(), limit=-1)


def test_segundos_hasta_la_medianoche_conserva_la_zona() -> None:
    """El reintento apunta a la medianoche local, no a la del contenedor."""

    assert seconds_until_tomorrow(AHORA) == 3600
    assert seconds_until_tomorrow(datetime(2026, 11, 12, 23, 59, 59, tzinfo=BOGOTA)) == 1


def test_segundos_hasta_la_medianoche_admite_hora_sin_zona() -> None:
    """Una hora ingenua conserva el cálculo histórico, sin mezclar zonas."""

    assert seconds_until_tomorrow(datetime(2026, 11, 12, 23, 0)) == 3600


def test_la_hora_por_defecto_usa_la_zona_del_cupo() -> None:
    """``local_now`` no depende del ``TZ`` del proceso."""

    momento = local_now("America/Bogota")

    assert momento.tzinfo is not None
    assert momento.utcoffset() == timedelta(hours=-5)
