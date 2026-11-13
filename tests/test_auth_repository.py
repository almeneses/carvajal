"""Pruebas de integración de usuarios OIDC y sesiones contra Postgres."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables
from carvajal.adapters.storage.auth import SqlAuthRepository

pytestmark = pytest.mark.integration


def _usuario(repo: SqlAuthRepository) -> str:
    usuario = repo.upsert_user(
        provider="google",
        subject="s1",
        email="ana@example.com",
        name="Ana",
    )
    return usuario.id


def test_upsert_es_idempotente(session: Session) -> None:
    """El mismo (provider, subject) reutiliza la fila y actualiza el perfil."""

    # Arrange
    repo = SqlAuthRepository(session)

    # Act
    primero = repo.upsert_user(provider="google", subject="s1", email="a@example.com", name="Ana")
    segundo = repo.upsert_user(provider="google", subject="s1", email="b@example.com", name="Ana B")

    # Assert
    assert primero.id == segundo.id
    assert segundo.email == "b@example.com"
    assert session.scalar(select(func.count()).select_from(tables.users)) == 1


def test_sesion_valida_se_encuentra_y_se_revoca(session: Session) -> None:
    """Una sesión viva devuelve su usuario; revocarla la invalida al instante."""

    # Arrange
    repo = SqlAuthRepository(session)
    user_id = _usuario(repo)
    repo.create_session(
        session_id="h1",
        user_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    # Act / Assert
    encontrada = repo.find_session("h1")
    assert encontrada is not None
    assert encontrada.user.id == user_id

    repo.revoke_session("h1")
    assert repo.find_session("h1") is None


def test_sesion_caducada_no_vale(session: Session) -> None:
    """Una sesión expirada no autentica aunque no esté revocada."""

    # Arrange
    repo = SqlAuthRepository(session)
    user_id = _usuario(repo)
    repo.create_session(
        session_id="h2",
        user_id=user_id,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    # Act
    encontrada = repo.find_session("h2")

    # Assert
    assert encontrada is None


def test_borrar_usuario_borra_sus_sesiones(session: Session) -> None:
    """La clave foránea en cascada no deja sesiones huérfanas."""

    # Arrange
    repo = SqlAuthRepository(session)
    user_id = _usuario(repo)
    repo.create_session(
        session_id="h3",
        user_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    # Act
    session.execute(delete(tables.users).where(tables.users.c.id == user_id))

    # Assert
    assert session.scalar(select(func.count()).select_from(tables.sessions)) == 0
