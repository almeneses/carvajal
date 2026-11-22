"""Memoria silenciosa del Cotizador por visitante.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-21

Guarda nombre, lugar de trabajo, ciudad, preferencia de domicilio y gustos que la
persona cuenta en el Cotizador. La clave es el dueño firmado, no un usuario: la
memoria se borra si la persona lo pide y caduca a los 180 días.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea ``visitor_memory`` con una fila por visitante."""

    op.create_table(
        "visitor_memory",
        sa.Column("owner_key", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("workplace", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("delivery", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("preferences", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Elimina la memoria; no se conserva."""

    op.drop_table("visitor_memory")
