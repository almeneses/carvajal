"""Esquema relacional del corpus, definido con SQLAlchemy Core.

Sin ORM declarativo ni sesiones asíncronas: los repositorios de W1.3 operarán
sobre estas ``Table`` con una ``Session`` síncrona. La columna ``embedding`` de
``chunks`` es un ``Vector`` de pgvector y la administra ``PgVectorStore``.
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)

__all__ = ["chunks", "document_versions", "documents", "metadata"]

metadata = MetaData()

documents = Table(
    "documents",
    metadata,
    Column("id", Text, primary_key=True),
    Column("url", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("slug", Text, nullable=False),
    Column("section", String(64), nullable=False),
    Column("layer", String(32), nullable=False),
    Column("markdown", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("source_type", String(32), nullable=False, server_default=text("'oficial'")),
    UniqueConstraint("slug", name="uq_documents_slug"),
)

Index("ix_documents_layer_section", documents.c.layer, documents.c.section)

chunks = Table(
    "chunks",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "document_id",
        Text,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("text", Text, nullable=False),
    Column("position", Integer, nullable=False),
    Column("section", String(64), nullable=False),
    Column("layer", String(32), nullable=False),
    Column("embedding", Vector(768), nullable=True),
    UniqueConstraint("document_id", "position", name="uq_chunks_document_position"),
)

document_versions = Table(
    "document_versions",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "document_id",
        Text,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("markdown", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

Index(
    "ix_document_versions_document_created",
    document_versions.c.document_id,
    document_versions.c.created_at,
)
