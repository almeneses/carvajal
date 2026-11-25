"""Borrador de cotización por conversación (spec 054).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-25

Una fila por hilo con las líneas que la persona va armando (cada una es una cotización del
motor, sin folio), lo último que se le mostró y qué respuesta espera el asistente. Al
confirmar, las líneas pasan a una sola fila de ``quotes`` con un folio.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea ``quote_drafts`` y su índice por dueño."""

    op.create_table(
        "quote_drafts",
        sa.Column("conversation_id", sa.Text(), primary_key=True),
        sa.Column("owner_key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quote_drafts_owner", "quote_drafts", ["owner_key"])


def downgrade() -> None:
    """Quita la tabla; los borradores son efímeros (siete días)."""

    op.drop_index("ix_quote_drafts_owner", table_name="quote_drafts")
    op.drop_table("quote_drafts")
