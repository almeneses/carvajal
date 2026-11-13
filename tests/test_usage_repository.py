"""Pruebas de integración del cupo diario contra Postgres."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.usage import SqlUsageRepository

pytestmark = pytest.mark.integration

DIA = date(2026, 11, 12)


def test_concede_hasta_el_limite(session: Session) -> None:
    """El adaptador concede exactamente ``limit`` peticiones y contabiliza."""

    # Arrange
    uso = SqlUsageRepository(session)

    # Act
    concesiones = [uso.try_consume("ana", DIA, 2) for _ in range(3)]

    # Assert
    assert concesiones == [True, True, False]
    fila = session.execute(select(tables.usage_day)).one()
    assert (fila.owner_key, fila.day, fila.requests) == ("ana", DIA, 2)


def test_visitantes_y_dias_son_independientes(session: Session) -> None:
    """Cada visitante y cada día tienen su propio contador."""

    # Arrange
    uso = SqlUsageRepository(session)

    # Act / Assert
    assert uso.try_consume("ana", DIA, 1)
    assert not uso.try_consume("ana", DIA, 1)
    assert uso.try_consume("luis", DIA, 1)
    assert uso.try_consume("ana", DIA + timedelta(days=1), 1)


def test_limite_cero_no_escribe(session: Session) -> None:
    """Sin cupo no se crea fila alguna."""

    # Arrange
    uso = SqlUsageRepository(session)

    # Act
    concedido = uso.try_consume("ana", DIA, 0)

    # Assert
    assert not concedido
    assert session.scalar(select(tables.usage_day)) is None
