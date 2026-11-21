"""Estado compartido y duradero de la captura web.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20

La captura web pasa de un registro en memoria a cuatro tablas del PostgreSQL
existente, para que tres workers de uvicorn compartan estado, progreso,
exclusión global y cupo por IP:

- ``capture_jobs``: una fila por captura, con su estado, petición, resultado,
  latido (``updated_at``) y worker que la atiende. No guarda la IP.
- ``capture_events``: progreso ordenado por ``seq`` para que cualquier worker
  reproduzca el SSE, incluso después de que el job termine.
- ``capture_control``: fila única (``id = 1``) que da la exclusión global y
  conserva el testigo y el latido del worker que posee la captura.
- ``capture_rate_limit``: marcas del cupo por IP en la ventana deslizante,
  indexadas por un resumen HMAC de la IP (nunca la IP en claro).

Todo es aditivo: las tablas del corpus y del chat no se tocan. El downgrade
elimina solo estas cuatro tablas.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea las tablas de estado de captura y la fila de control."""

    op.create_table(
        "capture_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("outcome", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("worker", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("lease_token", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'error', 'interrupted')",
            name="ck_capture_jobs_status",
        ),
    )
    op.create_index("ix_capture_jobs_status_updated", "capture_jobs", ["status", "updated_at"])
    op.create_index("ix_capture_jobs_created_at", "capture_jobs", ["created_at"])

    op.create_table(
        "capture_events",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("capture_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("data", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_capture_events_job_seq", "capture_events", ["job_id", "seq"])

    op.create_table(
        "capture_control",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("worker", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id = 1", name="ck_capture_control_single_row"),
    )
    op.execute(
        "INSERT INTO capture_control (id, job_id, worker, heartbeat_at) VALUES (1, NULL, '', NULL)"
    )

    op.create_table(
        "capture_rate_limit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_capture_rate_limit_owner_created", "capture_rate_limit", ["owner_hash", "created_at"]
    )


def downgrade() -> None:
    """Elimina las tablas de captura; solo se pierde estado operativo."""

    op.drop_index("ix_capture_rate_limit_owner_created", table_name="capture_rate_limit")
    op.drop_table("capture_rate_limit")
    op.drop_table("capture_control")
    op.drop_index("ix_capture_events_job_seq", table_name="capture_events")
    op.drop_table("capture_events")
    op.drop_index("ix_capture_jobs_created_at", table_name="capture_jobs")
    op.drop_index("ix_capture_jobs_status_updated", table_name="capture_jobs")
    op.drop_table("capture_jobs")
