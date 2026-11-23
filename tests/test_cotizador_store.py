"""Pruebas de integración del almacén SQL del Cotizador (specs 042 y 043)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from apps.api.views.cotizador import SqlCotizadorStore
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.quotes import SqlQuoteRepository
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


def _historic_payload(quote_id: str = "q-042") -> dict[str, Any]:
    """Payload de la 042: importes anidados y sin contrato versionado."""

    return {
        "kind": "cotizacion",
        "primary": {
            "currency": "COP",
            "tax_status": "incluido",
            "source_url": "https://example.com/precio",
            "captured_at": "2026-11-18",
            "presentation": {
                "basis": "paquete",
                "units": 50,
                "count": 1,
                "units_bought": 50,
                "units_surplus": 30,
                "subtotal": "3751",
            },
        },
        "delivery": {"requested": False},
        "total": "3751",
        "quote": {"id": quote_id, "valid_until": "2026-11-29", "days": 7},
    }


def _insert_historic(session: Session, *, message_id: str | None = None) -> None:
    session.execute(
        insert(tables.quotes).values(
            id="q-042",
            owner_key="cot:ana",
            message_id=message_id,
            valid_until=date(2026, 11, 29),
            total=Decimal("3751"),
            currency="COP",
            payload=_historic_payload(),
        )
    )


def test_lee_importes_historicos_por_folio_y_dueno_sin_message_id(session: Session) -> None:
    """Una fila de la 042 sin ``message_id`` se resuelve por folio y dueño."""

    _insert_historic(session)
    repo = SqlQuoteRepository(session)

    record = repo.resolve("q-042", "cot:ana")

    assert record is not None
    assert record.version == 1
    assert record.folio == "q-042"
    assert record.subtotal == Decimal("3751")
    assert record.total == Decimal("3751")
    assert record.currency == "COP"
    assert record.tax_status == "incluido"
    assert record.shipping_cost is None
    assert record.commercial_valid_until is None
    assert record.presentation["subtotal"] == "3751"


def test_lee_por_message_id_y_cae_al_folio_si_falta(session: Session) -> None:
    _insert_historic(session, message_id="m-042")
    repo = SqlQuoteRepository(session)

    por_mensaje = repo.resolve("q-042", "cot:ana", message_id="m-042")
    por_folio = repo.resolve("q-042", "cot:ana", message_id="m-inexistente")

    assert por_mensaje is not None and por_mensaje.folio == "q-042"
    assert por_folio is not None and por_folio.folio == "q-042"


def test_un_historico_ajeno_no_se_lee(session: Session) -> None:
    _insert_historic(session)
    repo = SqlQuoteRepository(session)

    assert repo.resolve("q-042", "cot:luis") is None
    assert repo.resolve("q-042", "cot:luis", message_id="m-042") is None


def test_clasifica_real_simulado_y_ambiguo_para_la_lectura_publica(
    session: Session,
) -> None:
    """La matriz de procedencia distingue las tres ramas del histórico."""

    from carvajal.adapters.storage.quotes import QuoteRecord, classify_quote_payload

    real = _historic_payload("q-real")
    simulated = {
        **_historic_payload("q-demo"),
        "simulated": True,
        "origin": "demo",
    }
    ambiguo = {"kind": "cotizacion", "primary": {"currency": "COP"}}

    assert classify_quote_payload(real, owner_key="cot:ana") == "real"
    assert classify_quote_payload(simulated, owner_key="cot:ana") == "simulado"
    assert classify_quote_payload(ambiguo, owner_key="cot:ana") == "ambiguo"
    assert (
        QuoteRecord({"id": "q-demo", "owner_key": "cot:ana", "payload": simulated}).provenance
        == "simulado"
    )


def test_for_conversation_no_cruza_al_dueno(session: Session) -> None:
    session.execute(
        insert(tables.quotes).values(
            id="q-conv",
            owner_key="cot:ana",
            conversation_id="hilo-1",
            valid_until=date(2026, 11, 29),
            total=Decimal("3751"),
            currency="COP",
            payload=_historic_payload("q-conv"),
        )
    )
    repo = SqlQuoteRepository(session)

    assert [item.folio for item in repo.for_conversation("hilo-1", "cot:ana")] == ["q-conv"]
    assert repo.for_conversation("hilo-1", "cot:luis") == []


def test_persiste_y_lee_el_resultado_versionado_con_total_pendiente(session: Session) -> None:
    """La 043 separa importes y admite total ausente; la lectura los devuelve igual."""

    payload = {
        "version": 2,
        "kind": "cotizacion",
        "commercial": {
            "version": 2,
            "presentation": {
                "basis": "paquete",
                "units": 50,
                "count": 6,
                "units_bought": 300,
                "units_surplus": 0,
                "price_per_presentation": "3751",
                "subtotal": "22506",
            },
            "tax": {"status": "incluido", "rate": None},
            "shipping": {"requested": True, "status": "pending", "cost": None},
            "subtotal": "22506",
            "total": None,
            "valid_until": None,
        },
        "primary": {"currency": "COP", "presentation": {"subtotal": "22506"}},
        "quote": {"id": "q-043", "issued_at": "2026-11-23T10:00:00+00:00"},
    }
    repo = SqlQuoteRepository(session)

    repo.record(
        "q-043",
        "cot:ana",
        payload=payload,
        total=None,
        currency="COP",
        valid_until=date(2026, 11, 30),
        version=2,
        issued_at=datetime(2026, 11, 23, 10, 0, tzinfo=UTC),
        subtotal=Decimal("22506"),
        tax_status="incluido",
        shipping_cost=None,
        commercial_valid_until=None,
    )

    record = repo.get("q-043", "cot:ana")

    assert record is not None
    assert record.version == 2
    assert record.subtotal == Decimal("22506")
    assert record.shipping_cost is None
    assert record.total is None
    assert record.commercial_valid_until is None
    assert record.issued_at == datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
    assert record.presentation["price_per_presentation"] == "3751"
