"""Fixtures compartidas: Postgres efímero, Jev apagado y voz del servidor apagada.

``testcontainers`` se importa dentro del fixture para que las pruebas unitarias
no necesiten Docker ni el paquete instalado.

Spec 046 (T003, research R13): ninguna prueba llama a Jev. La fixture *autouse*
:func:`_jev_apagado` fija ``JEV_ENABLED=false`` y ``TYPESAFE_API_KEY`` vacía aunque el
``.env`` local traiga la clave, y hace fallar cualquier conexión de httpx a TypeSafe. Las
pruebas del adaptador usan ``httpx.MockTransport``, que no pasa por el transporte de red.
"""

import os
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING

import httpx
import pytest
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import normalize_database_url

if TYPE_CHECKING:
    from testcontainers.community.postgres import PostgresContainer

# Host de la API de Jev: cualquier conexión real desde las pruebas es un error (SC-008).
JEV_HOST = "api.typesafe.ai"
# Spec 047: la voz del servidor usa OpenAI; las pruebas nunca la llaman de verdad.
OPENAI_HOST = "api.openai.com"
# Spec 056: el Odoo de producción (``http://odoo:8069`` en la red de Docker). Las pruebas del
# adaptador usan ``httpx.MockTransport``, que no pasa por este bloqueo.
ODOO_HOST = "odoo"
BLOCKED_HOSTS = frozenset({JEV_HOST, OPENAI_HOST, ODOO_HOST})


class JevNetworkBlocked(AssertionError):
    """Una prueba intentó conectarse a TypeSafe, a OpenAI o al Odoo de producción."""


@pytest.fixture(autouse=True)
def _jev_apagado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jev y la voz del servidor apagados, sin red hacia TypeSafe ni OpenAI (FR-029, SC-008).

    El ``.env`` local puede traer ``OPENAI_API_KEY`` y ``VOICE_PROVIDER=openai``: aquí se
    fijan vacía y ``browser``. Las pruebas de la voz usan ``httpx.MockTransport``.
    """

    monkeypatch.setenv("JEV_ENABLED", "false")
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    monkeypatch.delenv("JEV_LEDGER", raising=False)
    monkeypatch.setenv("VOICE_PROVIDER", "browser")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    # Un ``.env`` local con PUBLIC_BASE_URL https no debe redirigir las peticiones de
    # TestClient; las pruebas de la redirección la encienden explícitamente.
    monkeypatch.setenv("HTTPS_REDIRECT", "false")
    # El calentamiento de embeddings llamaría al Ollama local al arrancar el TestClient.
    monkeypatch.setenv("EMBED_WARM_UP", "false")
    # Spec 056: con ``ODOO_*`` en la shell o en un ``.env`` local, ``ChatService`` armaría el
    # CRM real y un «sí» crearía pedidos de verdad. Vacías ganan al ``.env``, así que el CRM
    # queda apagado; las pruebas del pedido inyectan ``orders`` o fijan los ajustes.
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_API_KEY"):
        monkeypatch.setenv(name, "")
    for name in ("ODOO_TIMEOUT_S", "ODOO_SALES_LOGIN"):
        monkeypatch.delenv(name, raising=False)  # con sus defaults de ``Settings``
    original = httpx.HTTPTransport.handle_request
    original_async = httpx.AsyncHTTPTransport.handle_async_request

    def guarded(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        if request.url.host in BLOCKED_HOSTS:
            raise JevNetworkBlocked(f"Conexión a {request.url.host} bloqueada en las pruebas.")
        return original(self, request)

    async def guarded_async(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        if request.url.host in BLOCKED_HOSTS:
            raise JevNetworkBlocked(f"Conexión a {request.url.host} bloqueada en las pruebas.")
        return await original_async(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator["PostgresContainer"]:
    """Levanta un Postgres 16 con pgvector; la imagen es configurable por entorno."""

    from testcontainers.community.postgres import PostgresContainer

    image = os.environ.get("TESTCONTAINERS_POSTGRES_IMAGE", "pgvector/pgvector:pg16")
    with PostgresContainer(image, driver="psycopg") as container:
        yield container


@pytest.fixture(scope="session")
def engine(postgres_container: "PostgresContainer") -> Iterator[Engine]:
    """Motor conectado al contenedor, con el esquema creado y limpiado al final."""

    url = normalize_database_url(postgres_container.get_connection_url())
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
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
