"""Cupo diario por visitante anónimo.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea el contador diario; la clave es el visitante y el día."""

    op.create_table(
        "usage_day",
        sa.Column("owner_key", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("owner_key", "day", name="pk_usage_day"),
    )


def downgrade() -> None:
    """Elimina el contador diario."""

    op.drop_table("usage_day")
