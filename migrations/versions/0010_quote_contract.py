"""Contrato versionado de cotizaciones con importes separados.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-23

La cotización de la 043 distingue presentación, subtotal, IVA, envío y total. El
total puede faltar cuando no se conoce un componente; la vigencia comercial se
guarda aparte de la retención de la emisión. Las filas de la 042 se leen con
``version=1`` y conservan sus importes y su payload.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade el contrato versionado sin borrar cotizaciones históricas."""

    op.add_column(
        "quotes",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "quotes",
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("quotes", sa.Column("commercial_valid_until", sa.Date(), nullable=True))
    op.add_column("quotes", sa.Column("subtotal", sa.Numeric(14, 2), nullable=True))
    op.add_column("quotes", sa.Column("tax_status", sa.String(16), nullable=True))
    op.add_column("quotes", sa.Column("tax_rate", sa.Numeric(5, 4), nullable=True))
    op.add_column("quotes", sa.Column("shipping_cost", sa.Numeric(14, 2), nullable=True))
    op.alter_column("quotes", "total", existing_type=sa.Numeric(14, 2), nullable=True)


def downgrade() -> None:
    """Retira las columnas nuevas; rellena totales ausentes para poder restaurar NOT NULL."""

    op.execute("UPDATE quotes SET total = COALESCE(subtotal, 0) WHERE total IS NULL")
    op.alter_column("quotes", "total", existing_type=sa.Numeric(14, 2), nullable=False)
    op.drop_column("quotes", "shipping_cost")
    op.drop_column("quotes", "tax_rate")
    op.drop_column("quotes", "tax_status")
    op.drop_column("quotes", "subtotal")
    op.drop_column("quotes", "commercial_valid_until")
    op.drop_column("quotes", "issued_at")
    op.drop_column("quotes", "version")
