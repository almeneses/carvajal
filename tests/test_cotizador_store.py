"""Pruebas de integración del almacén SQL del Cotizador (spec 042, FR-022)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
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


def _count(session: Session, table: Any, column: str, value: str) -> int:
    query = select(func.count()).select_from(table).where(table.c[column] == value)
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


def _quote(quote_id: str = "q-ana") -> dict[str, Any]:
    payload = {
        "kind": "cotizacion",
        "primary": {"currency": "COP", "presentation": {"subtotal": 3751}},
        "quote": {"id": quote_id, "valid_until": "2026-11-29", "days": 7},
    }
    return {
        "id": quote_id,
        "payload": payload,
        "total": 3751,
        "currency": "COP",
        "valid_until": "2026-11-29",
    }


def test_el_turno_y_su_cotizacion_se_guardan_juntos(session: Session) -> None:
    """El turno persiste el mensaje y la fila auditable en la misma transacción."""

    almacen = SqlCotizadorStore(_factory(session))
    conversation_id = almacen.new("ana")
    quote = _quote()

    almacen.append(
        "ana",
        question="cotiza 20 vasos",
        answer="Son 3751 COP.",
        sources=[quote["payload"]],
        thread_id=conversation_id,
        quote=quote,
    )

    assert _count(session, tables.messages, "conversation_id", conversation_id) == 2
    assert _count(session, tables.quotes, "id", "q-ana") == 1


def test_si_falla_la_cotizacion_no_queda_el_turno(session: Session) -> None:
    """Una cotización que no se puede registrar revierte también sus mensajes."""

    almacen = SqlCotizadorStore(_factory(session))
    conversation_id = almacen.new("ana")
    session.execute(
        insert(tables.quotes).values(
            **{
                "id": "q-ana",
                "owner_key": "cot:ana",
                "conversation_id": conversation_id,
                "valid_until": datetime(2026, 11, 29, tzinfo=UTC).date(),
                "total": 1,
                "currency": "COP",
                "payload": {},
            }
        )
    )

    with pytest.raises(IntegrityError):
        almacen.append(
            "ana",
            question="cotiza 20 vasos",
            answer="Son 3751 COP.",
            sources=[_quote()["payload"]],
            thread_id=conversation_id,
            quote=_quote(),
        )

    assert _count(session, tables.messages, "conversation_id", conversation_id) == 0
