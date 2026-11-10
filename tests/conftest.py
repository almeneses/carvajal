"""Fixtures compartidas: Postgres efímero para las pruebas de integración.

``testcontainers`` se importa dentro del fixture para que las pruebas unitarias
no necesiten Docker ni el paquete instalado.
"""

import os
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import normalize_database_url

if TYPE_CHECKING:
    from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_container() -> Iterator["PostgresContainer"]:
    """Levanta un Postgres 16 desechable; la imagen es configurable por entorno."""

    from testcontainers.community.postgres import PostgresContainer

    image = os.environ.get("TESTCONTAINERS_POSTGRES_IMAGE", "postgres:16-alpine")
    with PostgresContainer(image, driver="psycopg") as container:
        yield container


@pytest.fixture(scope="session")
def engine(postgres_container: "PostgresContainer") -> Iterator[Engine]:
    """Motor conectado al contenedor, con el esquema creado y limpiado al final."""

    url = normalize_database_url(postgres_container.get_connection_url())
    engine = create_engine(url, pool_pre_ping=True)
    tables.metadata.create_all(engine)
    try:
        yield engine
    finally:
        tables.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    """Sesión envuelta en una transacción externa que siempre se revierte."""

    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture()
def migration_database_url(postgres_container: "PostgresContainer") -> Iterator[str]:
    """Crea una base vacía de nombre único y la borra al terminar la prueba."""

    admin_url = normalize_database_url(postgres_container.get_connection_url())
    name = f"migrations_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin_engine.dispose()
