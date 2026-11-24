"""Esquema relacional del corpus, definido con SQLAlchemy Core.

Sin ORM declarativo ni sesiones asíncronas: los repositorios de W1.3 operarán
sobre estas ``Table`` con una ``Session`` síncrona. Los fragmentos no guardan
vectores: el único índice de respuestas es Qdrant (spec 037, US5).
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

__all__ = [
    "chat_feedback",
    "chunks",
    "conversations",
    "document_versions",
    "documents",
    "messages",
    "metadata",
    "quotes",
    "usage_day",
    "visitor_memory",
    "webhook_deliveries",
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

# Marcas del visitante sobre una respuesta (spec 038, US4). La clave es el
# ``request_id`` del turno: la marca se guarda aunque el turno no se haya
# muestreado y nunca fuerza una traza. No se guarda texto.
chat_feedback = Table(
    "chat_feedback",
    metadata,
    Column("request_id", Text, primary_key=True),
    Column("useful", Boolean, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
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


# Memoria silenciosa del Cotizador (spec 042, FR-022). Guarda solo lo que la
# persona contó de sí misma; caduca a los 180 días y se borra cuando lo pide.
visitor_memory = Table(
    "visitor_memory",
    metadata,
    Column("owner_key", Text, primary_key=True),
    Column("name", Text, nullable=True),
    Column("workplace", Text, nullable=True),
    Column("city", Text, nullable=True),
    Column("delivery", Boolean, nullable=False, server_default=text("false")),
    Column("preferences", JSONB, nullable=True),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

# Cotización emitida (spec 042, contrato versionado en la 043): identidad, importes
# separados, vigencias y payload auditable. ``total`` admite ausencia cuando falta un
# componente; ``valid_until`` es la retención de la emisión y ``commercial_valid_until``
# la vigencia que publicó el vendedor, que puede no existir.
quotes = Table(
    "quotes",
    metadata,
    Column("id", Text, primary_key=True),
    Column("owner_key", Text, nullable=False),
    Column("conversation_id", Text, nullable=True),
    Column("message_id", Text, nullable=True),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("issued_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("valid_until", Date, nullable=False),
    Column("commercial_valid_until", Date, nullable=True),
    Column("subtotal", Numeric(14, 2), nullable=True),
    Column("tax_status", String(16), nullable=True),
    Column("tax_rate", Numeric(5, 4), nullable=True),
    Column("shipping_cost", Numeric(14, 2), nullable=True),
    Column("total", Numeric(14, 2), nullable=True),
    Column("currency", String(3), nullable=False),
    Column("status", String(16), nullable=False, server_default=text("'issued'")),
    Column("payload", JSONB, nullable=False),
)

Index("ix_quotes_owner_created", quotes.c.owner_key, quotes.c.created_at.desc())

# Entregas del webhook de mensajería: reserva atómica por ``message_id`` antes de abrir
# hilo, cobrar cupo o llamar al LLM. ``delivery_id`` es el ``client_turn_id`` derivado del
# ``message_id``. ``reply`` solo guarda las respuestas cortas que no dejan turno (nueva
# conversación sin texto, cupo agotado); el texto de un turno vive en ``messages``.
# ``attempt`` es el token de la toma vigente (``NULL``: pendiente liberada, libre);
# ``quota_charged`` y ``thread_opened`` anotan los efectos de un intento fallido.
webhook_deliveries = Table(
    "webhook_deliveries",
    metadata,
    Column("owner_key", Text, primary_key=True),
    Column("delivery_id", String(64), primary_key=True),
    Column("status", String(16), nullable=False),
    Column("reply", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("attempt", String(32), nullable=True),
    Column("quota_charged", Boolean, nullable=False, server_default=text("false")),
    Column("thread_opened", Boolean, nullable=False, server_default=text("false")),
    CheckConstraint("status IN ('pending', 'done')", name="ck_webhook_deliveries_status"),
)

Index("ix_webhook_deliveries_updated", webhook_deliveries.c.updated_at)
