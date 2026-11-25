"""Repositorio del borrador de cotización (spec 054): guardado atómico por hilo."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.drafts import SqlDraftRepository
from carvajal.quotation.draft import QuoteDraft


def test_guardar_dos_veces_reemplaza_la_fila_en_sqlite() -> None:
    # Arrange
    motor = create_engine("sqlite+pysqlite:///:memory:")
    tables.metadata.create_all(motor)

    # Act
    with Session(motor) as session:
        repository = SqlDraftRepository(session)
        repository.save("hilo-1", "dueno", QuoteDraft().touch(awaiting="algo_mas"))
        repository.save("hilo-1", "dueno", QuoteDraft().touch(awaiting="confirmar"))
        session.commit()

    # Assert: una sola fila, con el último borrador.
    with Session(motor) as session:
        rows = session.execute(select(tables.quote_drafts)).all()
        assert len(rows) == 1
        assert SqlDraftRepository(session).get("hilo-1").awaiting == "confirmar"


@pytest.mark.integration
def test_dos_turnos_que_guardan_el_primer_borrador_a_la_vez_no_chocan(engine: Engine) -> None:
    """Sin fila previa, dos turnos guardan a la vez: ninguno falla por la clave primaria.

    El primero inserta y deja su transacción abierta; el segundo guarda mientras tanto. Con
    «select y luego insert» el segundo no veía la fila sin confirmar, insertaba y, al
    confirmar el primero, abortaba con ``IntegrityError``. Con ``ON CONFLICT DO UPDATE``
    espera el bloqueo del primero y actualiza la fila.
    """

    # Arrange
    conversation_id = f"hilo-{uuid4().hex}"
    primero_guardo = Event()

    def primero() -> None:
        with Session(engine) as session:
            SqlDraftRepository(session).save(
                conversation_id, "dueno", QuoteDraft().touch(awaiting="algo_mas")
            )
            primero_guardo.set()
            time.sleep(0.5)  # el segundo ya está guardando contra la fila sin confirmar
            session.commit()

    def segundo() -> None:
        assert primero_guardo.wait(timeout=10)
        with Session(engine) as session:
            SqlDraftRepository(session).save(
                conversation_id, "dueno", QuoteDraft().touch(awaiting="confirmar")
            )
            session.commit()

    try:
        # Act
        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(primero), pool.submit(segundo)]
            for futuro in futuros:
                futuro.result(timeout=30)  # ninguno lanza IntegrityError

        # Assert: una fila, con la escritura del segundo (la última).
        with Session(engine) as session:
            rows = session.execute(
                select(tables.quote_drafts).where(
                    tables.quote_drafts.c.conversation_id == conversation_id
                )
            ).all()
            assert len(rows) == 1
            assert SqlDraftRepository(session).get(conversation_id).awaiting == "confirmar"
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(tables.quote_drafts).where(
                    tables.quote_drafts.c.conversation_id == conversation_id
                )
            )
