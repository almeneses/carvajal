"""Pruebas de integración del almacén SQL del Cotizador (spec 042, FR-022)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.views.cotizador import SqlCotizadorStore
from carvajal.adapters.storage import tables
from carvajal.quotation.memory import EMPTY, MEMORY_DAYS

pytestmark = pytest.mark.integration


def _factory(session: Session) -> sessionmaker[Session]:
    """Fábrica sobre la conexión del fixture, con savepoints por transacción."""

    return sessionmaker(bind=session.connection(), join_transaction_mode="create_savepoint")


def _rows(session: Session, owner_key: str) -> int:
    query = (
        select(func.count())
        .select_from(tables.visitor_memory)
        .where(tables.visitor_memory.c.owner_key == owner_key)
    )
    return session.execute(query).scalar_one()


def test_el_perfil_caducado_se_borra_de_verdad(session: Session) -> None:
    """Leer un perfil vencido confirma el borrado en vez de descartarlo."""

    session.execute(
        insert(tables.visitor_memory).values(
            owner_key="cot:ana",
            name="Ana",
            workplace="cafetería",
            city="Bogotá",
            delivery=True,
            preferences=["vasos de papel"],
            updated_at=datetime.now(UTC) - timedelta(days=MEMORY_DAYS + 1),
        )
    )

    almacen = SqlCotizadorStore(_factory(session))

    assert almacen.memory("ana") == dict(EMPTY)
    assert _rows(session, "cot:ana") == 0


def test_el_perfil_vigente_se_conserva(session: Session) -> None:
    """La misma lectura no toca un perfil dentro de la caducidad."""

    session.execute(
        insert(tables.visitor_memory).values(
            owner_key="cot:luis",
            name="Luis",
            delivery=False,
            updated_at=datetime.now(UTC) - timedelta(days=MEMORY_DAYS - 1),
        )
    )

    almacen = SqlCotizadorStore(_factory(session))

    assert almacen.memory("luis")["name"] == "Luis"
    assert _rows(session, "cot:luis") == 1
