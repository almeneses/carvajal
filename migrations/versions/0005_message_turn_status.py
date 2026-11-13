"""Estado de mensaje e idempotencia del turno.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade ``status`` y ``client_turn_id`` para no duplicar reintentos."""

    op.add_column(
        "messages",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="complete"),
    )
    op.add_column(
        "messages",
        sa.Column("client_turn_id", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_messages_status",
        "messages",
        "status IN ('complete', 'incomplete')",
    )
    op.create_index(
        "uq_messages_turn",
        "messages",
        ["conversation_id", "client_turn_id"],
        unique=True,
        postgresql_where=sa.text("client_turn_id IS NOT NULL AND role = 'assistant'"),
    )


def downgrade() -> None:
    """Revierte las columnas y sus restricciones."""

    op.drop_index("uq_messages_turn", table_name="messages")
    op.drop_constraint("ck_messages_status", "messages", type_="check")
    op.drop_column("messages", "client_turn_id")
    op.drop_column("messages", "status")
