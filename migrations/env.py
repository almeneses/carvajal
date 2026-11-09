"""Entorno de Alembic para las migraciones del esquema relacional.

La URL se resuelve en este orden: ``DATABASE_URL`` del entorno, luego
``Settings().database_url``. En ambos casos se normaliza al driver psycopg 3.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import normalize_database_url
from carvajal.infrastructure.settings import Settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = tables.metadata


def _database_url() -> str:
    """Resuelve y normaliza la URL de la base de datos a migrar."""

    raw = os.environ.get("DATABASE_URL") or Settings().database_url
    url = normalize_database_url(raw)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return url


def run_migrations_offline() -> None:
    """Genera el SQL de la migración sin conectarse a la base."""

    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Aplica las migraciones sobre una conexión real."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
