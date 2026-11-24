"""Reserva de entregas del webhook de mensajería.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-24

Cada ``message_id`` del relé se reserva de forma atómica antes de abrir hilo, cobrar cupo
o llamar al LLM: un reintento concurrente o una petición firmada reenviada no repiten el
turno. Las filas caducan al día; ``reply`` solo guarda respuestas cortas sin turno.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea ``webhook_deliveries`` con clave ``(owner_key, delivery_id)``."""

    op.create_table(
        "webhook_deliveries",
        sa.Column("owner_key", sa.Text(), primary_key=True),
        sa.Column("delivery_id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'done')", name="ck_webhook_deliveries_status"),
    )
    op.create_index("ix_webhook_deliveries_updated", "webhook_deliveries", ["updated_at"])


def downgrade() -> None:
    """Elimina las reservas; son efímeras y no se conservan."""

    op.drop_index("ix_webhook_deliveries_updated", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
