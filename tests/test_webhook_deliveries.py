"""Pruebas de la reserva de entregas del webhook (memoria, SQLite y Postgres).

La misma batería corre contra las dos implementaciones de ``DeliveryStore``: reserva
única, reserva pendiente caducada, liberación tras un fallo y purga de filas viejas. La
prueba de concurrencia contra Postgres real lleva la marca ``integration``.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, delete, func, select
from sqlalchemy.pool import StaticPool

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.adapters.storage.deliveries import SqlDeliveryStore
from carvajal.application.deliveries import (
    PENDING_TTL,
    RETENTION,
    DeliveryClaim,
    InMemoryDeliveryStore,
)

OWNER = "wa_dueno"
TURN = "wh_turno"


class Clock:
    """Reloj controlable."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture()
def sqlite_engine() -> Iterator[Any]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    try:
        yield motor
    finally:
        tables.metadata.drop_all(motor)
        motor.dispose()


@pytest.fixture(params=["memoria", "sql"])
def store_and_clock(request: pytest.FixtureRequest, sqlite_engine: Any) -> tuple[Any, Clock]:
    clock = Clock()
    if request.param == "memoria":
        return InMemoryDeliveryStore(now=clock), clock
    return SqlDeliveryStore(build_session_factory(sqlite_engine), now=clock), clock


def test_la_primera_reserva_se_concede_y_la_segunda_queda_pendiente(
    store_and_clock: tuple[Any, Clock],
) -> None:
    # Arrange
    store, _clock = store_and_clock

    # Act
    first = store.claim(OWNER, TURN)
    second = store.claim(OWNER, TURN)
    other_owner = store.claim("wa_otro", TURN)

    # Assert
    assert first == DeliveryClaim("acquired")
    assert second == DeliveryClaim("pending")
    assert other_owner == DeliveryClaim("acquired")


def test_una_entrega_atendida_devuelve_su_respuesta_corta(
    store_and_clock: tuple[Any, Clock],
) -> None:
    # Arrange
    store, _clock = store_and_clock
    store.claim(OWNER, TURN)
    store.finish(OWNER, TURN, "Listo")
    store.claim(OWNER, "wh_con_turno")
    store.finish(OWNER, "wh_con_turno", None)

    # Act
    short = store.claim(OWNER, TURN)
    with_turn = store.claim(OWNER, "wh_con_turno")

    # Assert
    assert short == DeliveryClaim("done", "Listo")
    assert with_turn == DeliveryClaim("done", None)


def test_liberar_permite_que_el_reintento_vuelva_a_tomarla(
    store_and_clock: tuple[Any, Clock],
) -> None:
    # Arrange
    store, _clock = store_and_clock
    store.claim(OWNER, TURN)

    # Act
    store.release(OWNER, TURN)
    retried = store.claim(OWNER, TURN)

    # Assert
    assert retried == DeliveryClaim("acquired")


def test_liberar_no_borra_una_entrega_atendida(store_and_clock: tuple[Any, Clock]) -> None:
    # Arrange
    store, _clock = store_and_clock
    store.claim(OWNER, TURN)
    store.finish(OWNER, TURN, "Listo")

    # Act
    store.release(OWNER, TURN)

    # Assert
    assert store.claim(OWNER, TURN) == DeliveryClaim("done", "Listo")


def test_una_reserva_pendiente_caducada_la_toma_el_siguiente_intento(
    store_and_clock: tuple[Any, Clock],
) -> None:
    # Arrange: el proceso que la tomó cayó a mitad de turno.
    store, clock = store_and_clock
    store.claim(OWNER, TURN)
    clock.advance(PENDING_TTL - timedelta(seconds=1))
    still_pending = store.claim(OWNER, TURN)

    # Act
    clock.advance(timedelta(seconds=2))
    taken = store.claim(OWNER, TURN)
    after = store.claim(OWNER, TURN)

    # Assert
    assert still_pending == DeliveryClaim("pending")
    assert taken == DeliveryClaim("acquired")
    assert after == DeliveryClaim("pending")


def test_las_entregas_viejas_se_purgan(sqlite_engine: Any) -> None:
    # Arrange
    clock = Clock()
    store = SqlDeliveryStore(build_session_factory(sqlite_engine), now=clock)
    store.claim(OWNER, TURN)
    store.finish(OWNER, TURN, "Listo")

    # Act
    clock.advance(RETENTION + timedelta(seconds=1))
    store.claim(OWNER, "wh_nuevo")

    # Assert
    with sqlite_engine.connect() as connection:
        rows = connection.scalar(select(func.count()).select_from(tables.webhook_deliveries))
    assert rows == 1
    assert store.claim(OWNER, TURN) == DeliveryClaim("acquired")


@pytest.mark.integration
def test_postgres_concede_una_sola_reserva_a_intentos_simultaneos(engine: Engine) -> None:
    """Ocho intentos del mismo mensaje a la vez: exactamente uno atiende."""

    # Arrange
    owner = f"wa_{uuid4().hex}"
    store = SqlDeliveryStore(build_session_factory(engine))
    barrier = Barrier(8)

    def attempt(_: int) -> str:
        barrier.wait(timeout=10)
        return store.claim(owner, TURN).status

    try:
        # Act
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(attempt, range(8)))
        store.finish(owner, TURN, None)
        later = store.claim(owner, TURN)

        # Assert
        assert sorted(statuses) == ["acquired"] + ["pending"] * 7
        assert later == DeliveryClaim("done", None)
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(tables.webhook_deliveries).where(
                    tables.webhook_deliveries.c.owner_key == owner
                )
            )
