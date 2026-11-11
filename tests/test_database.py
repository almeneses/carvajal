"""Pruebas unitarias de la normalización de URLs de base de datos."""

import pytest

from carvajal.adapters.storage.database import normalize_database_url


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "postgresql://user:secret@127.0.0.1:5432/carvajal",
            "postgresql+psycopg://user:secret@127.0.0.1:5432/carvajal",
        ),
        (
            "postgres://user:secret@127.0.0.1:5432/carvajal",
            "postgresql+psycopg://user:secret@127.0.0.1:5432/carvajal",
        ),
        (
            "postgresql+psycopg2://user:secret@127.0.0.1:5432/carvajal",
            "postgresql+psycopg://user:secret@127.0.0.1:5432/carvajal",
        ),
    ],
)
def test_normaliza_prefijos_heredados(original: str, expected: str) -> None:
    """Las tres formas heredadas terminan usando el driver psycopg 3."""

    # Act
    normalized = normalize_database_url(original)

    # Assert
    assert normalized == expected


def test_conserva_caracteres_escapados_de_la_contrasena() -> None:
    """Un ``%`` o ``@`` ya escapado en la contraseña no se altera."""

    original = "postgresql://user:p%40ss%2Fword@db:5432/carvajal?sslmode=require"

    normalized = normalize_database_url(original)

    assert normalized == "postgresql+psycopg://user:p%40ss%2Fword@db:5432/carvajal?sslmode=require"


def test_url_ya_normalizada_no_cambia() -> None:
    """Reaplicar la normalización es idempotente."""

    original = "postgresql+psycopg://user:secret@db:5432/carvajal"

    assert normalize_database_url(original) == original
