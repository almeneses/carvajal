"""Repositorio SQL de usuarios OIDC y sesiones revocables."""

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from carvajal.adapters.storage import tables

__all__ = ["SessionUser", "SqlAuthRepository", "User"]


@dataclass(frozen=True)
class User:
    """Usuario autenticado en la app."""

    id: str
    email: str
    name: str


@dataclass(frozen=True)
class SessionUser:
    """Sesión válida y su usuario."""

    user: User
    expires_at: datetime


class SqlAuthRepository:
    """Identidad y sesiones sobre Postgres, con ``flush()`` y sin ``commit()``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_user(self, *, provider: str, subject: str, email: str, name: str) -> User:
        """Crea o actualiza el usuario del proveedor y devuelve su fila."""

        statement = (
            insert(tables.users)
            .values(id=uuid4().hex, provider=provider, subject=subject, email=email, name=name)
            .on_conflict_do_update(
                constraint="uq_users_provider_subject",
                set_={"email": email, "name": name, "last_login_at": func.now()},
            )
            .returning(tables.users.c.id, tables.users.c.email, tables.users.c.name)
        )
        fila = self._session.execute(statement).one()
        self._session.flush()
        return User(id=fila.id, email=fila.email, name=fila.name)

    def create_session(self, *, session_id: str, user_id: str, expires_at: datetime) -> None:
        """Registra una sesión; ``session_id`` es el hash del token, no el token."""

        self._session.execute(
            insert(tables.sessions).values(id=session_id, user_id=user_id, expires_at=expires_at)
        )
        self._session.flush()

    def find_session(self, session_id: str) -> SessionUser | None:
        """Devuelve el usuario si la sesión existe, no está revocada ni caducada."""

        fila = self._session.execute(
            select(
                tables.users.c.id,
                tables.users.c.email,
                tables.users.c.name,
                tables.sessions.c.expires_at,
            )
            .join(tables.sessions, tables.sessions.c.user_id == tables.users.c.id)
            .where(
                tables.sessions.c.id == session_id,
                tables.sessions.c.revoked_at.is_(None),
                tables.sessions.c.expires_at > func.now(),
            )
        ).first()
        if fila is None:
            return None
        return SessionUser(User(fila.id, fila.email, fila.name), fila.expires_at)

    def revoke_session(self, session_id: str) -> None:
        """Marca la sesión como revocada; logout deja de ser efectivo al instante."""

        self._session.execute(
            update(tables.sessions)
            .where(tables.sessions.c.id == session_id, tables.sessions.c.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        self._session.flush()
