"""Vector de los fragmentos en pgvector, con índice HNSW coseno.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea la extensión, la columna de 768 dimensiones y su índice HNSW.

    Crear la extensión exige un rol con privilegios (superusuario o ``CREATE``
    en la base); en un Postgres gestionado se provisiona antes del ``upgrade``.
    """

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("chunks", sa.Column("embedding", Vector(768), nullable=True))
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Quita el índice y la columna; la extensión se conserva por ser compartida."""

    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    op.drop_column("chunks", "embedding")
