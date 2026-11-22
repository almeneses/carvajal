"""Cotizaciones emitidas con identidad, vigencia y auditoría.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-21

Cada cotización que el Cotizador entrega se guarda con su identificador, el dueño
firmado, el hilo y mensaje, el total, la moneda, la vigencia y el payload completo
que se mostró. La fila es el registro para auditar o reclamar; el historial sigue
leyendo el mensaje.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea ``quotes`` con índice por dueño y fecha."""

    op.create_table(
        "quotes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("owner_key", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("total", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'issued'"), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_quotes_owner_created",
        "quotes",
        ["owner_key", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Elimina la tabla; las cotizaciones no se conservan."""

    op.drop_index("ix_quotes_owner_created", table_name="quotes")
    op.drop_table("quotes")
