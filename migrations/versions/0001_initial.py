"""Esquema inicial: documentos, fragmentos y versiones.

Revision ID: 0001
Revises:
Create Date: 2026-11-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("layer", sa.String(length=32), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'oficial'"),
        ),
        sa.UniqueConstraint("slug", name="uq_documents_slug"),
    )
    op.create_index(
        "ix_documents_layer_section",
        "documents",
        ["layer", "section"],
        unique=False,
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("layer", sa.String(length=32), nullable=False),
        sa.UniqueConstraint(
            "document_id",
            "position",
            name="uq_chunks_document_position",
        ),
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_document_versions_document_created",
        "document_versions",
        ["document_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_versions_document_created",
        table_name="document_versions",
    )
    op.drop_table("document_versions")
    op.drop_table("chunks")
    op.drop_index("ix_documents_layer_section", table_name="documents")
    op.drop_table("documents")
