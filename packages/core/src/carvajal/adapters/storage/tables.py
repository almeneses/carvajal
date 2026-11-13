"""Esquema relacional del corpus, definido con SQLAlchemy Core.

Sin ORM declarativo ni sesiones asíncronas: los repositorios de W1.3 operarán
sobre estas ``Table`` con una ``Session`` síncrona. La columna ``embedding`` de
``chunks`` es un ``Vector`` de pgvector y la administra ``PgVectorStore``.
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB

__all__ = [
    "chunks",
    "conversations",
    "document_versions",
    "documents",
    "messages",
    "metadata",
    "usage_day",
]

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

conversations = Table(
    "conversations",
    metadata,
    Column("id", Text, primary_key=True),
    Column("owner_key", Text, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

Index(
    "ix_conversations_owner_updated",
    conversations.c.owner_key,
    conversations.c.updated_at.desc(),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "conversation_id",
        Text,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("sources", JSONB, nullable=True),
    Column("status", String(16), nullable=False, server_default=text("'complete'")),
    Column("client_turn_id", String(64), nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
    CheckConstraint(
        "status IN ('complete', 'incomplete')",
        name="ck_messages_status",
    ),
)

# Un turno tiene dos mensajes (usuario y asistente) con el mismo
# ``client_turn_id``; la unicidad se aplica solo al asistente, que es la fila
# que identifica el turno persistido.
Index(
    "uq_messages_turn",
    messages.c.conversation_id,
    messages.c.client_turn_id,
    unique=True,
    postgresql_where=text("client_turn_id IS NOT NULL AND role = 'assistant'"),
)
Index(
    "uq_messages_turn_user",
    messages.c.conversation_id,
    messages.c.client_turn_id,
    unique=True,
    postgresql_where=text("client_turn_id IS NOT NULL AND role = 'user'"),
)

Index(
    "ix_messages_conversation_created",
    messages.c.conversation_id,
    messages.c.created_at,
    messages.c.id,
)

# Cupo diario por visitante anónimo (W4.2). La clave es el identificador de la
# cookie firmada, no un usuario: W4.1 añadirá `user_id` cuando exista OIDC.
usage_day = Table(
    "usage_day",
    metadata,
    Column("owner_key", Text, primary_key=True),
    Column("day", Date, primary_key=True),
    Column("requests", Integer, nullable=False, server_default="0"),
    Column("tokens", BigInteger, nullable=False, server_default="0"),
)
