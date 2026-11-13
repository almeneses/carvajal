"""Pruebas de integración del cupo diario contra Postgres."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select
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


def test_reserva_concurrente_no_supera_el_limite(engine: Engine) -> None:
    """Dos sesiones a la vez: solo una gana el único cupo disponible.

    Cada hilo abre su propia transacción y confirma, de modo que la segunda
    petición espera el bloqueo de fila del ``ON CONFLICT`` y vuelve a evaluar
    el ``WHERE requests < limit`` ya con el contador actualizado.
    """

    # Arrange
    owner_key = f"concurrente-{uuid4().hex}"
    barrera = Barrier(2)

    def reservar() -> bool:
        with Session(engine) as session:
            uso = SqlUsageRepository(session)
            barrera.wait(timeout=10)
            concedido = uso.try_consume(owner_key, DIA, 1)
            session.commit()
            return concedido

    try:
        # Act
        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(reservar) for _ in range(2)]
            concesiones = [futuro.result(timeout=30) for futuro in futuros]

        # Assert
        assert sorted(concesiones) == [False, True]
        with Session(engine) as session:
            fila = session.execute(
                select(tables.usage_day).where(
                    tables.usage_day.c.owner_key == owner_key,
                    tables.usage_day.c.day == DIA,
                )
            ).one()
        assert fila.requests == 1
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(tables.usage_day).where(tables.usage_day.c.owner_key == owner_key)
            )
