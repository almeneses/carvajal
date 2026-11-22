"""Retira la columna de vectores de los fragmentos.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-17

El único índice de respuestas es Qdrant; ningún flujo escribía vectores en
PostgreSQL, así que la columna quedaba vacía y aparentaba funcionar. La
migración es reversible: el ``downgrade`` vuelve a crearla como ``vector(768)``
nulable, sin índice, usando SQL directo para no depender del paquete pgvector.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Elimina ``chunks.embedding`` y su índice si quedara de versiones previas."""

    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_column("chunks", "embedding")


def downgrade() -> None:
    """Restaura la columna nulable; la extensión ``vector`` se conserva."""

    op.execute("ALTER TABLE chunks ADD COLUMN embedding vector(768)")
