"""Token de intento y efectos anotados en la reserva de entregas del webhook.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-24

``attempt`` identifica la toma vigente de la reserva: ``finish`` y ``release`` solo
actúan sobre la del intento que la tiene, así un intento viejo que termina tarde no borra
la reserva que tomó otro. ``quota_charged`` y ``thread_opened`` anotan que un intento
fallido ya cobró el cupo o abrió el hilo nuevo, para que el reintento no los repita.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade ``attempt``, ``quota_charged`` y ``thread_opened``."""

    op.add_column("webhook_deliveries", sa.Column("attempt", sa.String(32), nullable=True))
    op.add_column(
        "webhook_deliveries",
        sa.Column("quota_charged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("thread_opened", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Quita las columnas; las reservas son efímeras."""

    op.drop_column("webhook_deliveries", "thread_opened")
    op.drop_column("webhook_deliveries", "quota_charged")
    op.drop_column("webhook_deliveries", "attempt")
