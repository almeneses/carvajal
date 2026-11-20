"""Añade las marcas del visitante sobre una respuesta.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19

Una marca es útil / no útil sobre el turno identificado por ``request_id``.
La clave primaria hace la operación idempotente: volver a marcar actualiza en
lugar de duplicar. No se guarda texto ni se fuerza el muestreo del monitoreo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea ``chat_feedback`` con ``request_id`` como clave."""

    op.create_table(
        "chat_feedback",
        sa.Column("request_id", sa.Text(), primary_key=True),
        sa.Column("useful", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Elimina la tabla; las marcas no se conservan."""

    op.drop_table("chat_feedback")
