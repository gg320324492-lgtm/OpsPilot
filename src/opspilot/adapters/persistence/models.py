"""SQLAlchemy declarative models: the schema, as code.

Responsibility: declare the tables -- ``tickets``, ``agent_runs``,
``agent_steps``, ``tool_calls``, ``approval_requests``, ``knowledge_documents``,
``knowledge_chunks``, ``citations``, ``audit_events`` -- and expose their
``metadata``, which is what ``migrations/env.py`` points ``target_metadata`` at.

Layer: ``adapters``. Imports SQLAlchemy, ``pgvector`` and ``opspilot.domain``
for the enum types.

Status columns are ``TEXT`` with a Python ``Enum`` on the model side, not PG
``ENUM``, so a new ``RunStatus`` needs no database type migration
(``docs/data-model.md`` §4). Primary keys are UUIDs (server-side
``gen_random_uuid()``, emulated on SQLite), and all timestamps are
``TIMESTAMPTZ`` UTC named ``*_at``.

Three database-level guarantees the spec calls load-bearing live here:

- the partial claim index on ``agent_runs.status`` whose predicate is built from
  ``CLAIMABLE`` in ``domain/runs.py`` -- never retyped as string literals;
- the partial unique index on ``tool_calls.idempotency_key`` where
  ``status='executed'`` -- the backstop against a duplicate refund;
- ``UNIQUE (agent_steps.run_id, sequence)`` -- the ordering guarantee the trace
  relies on.

The Postgres/SQLite divergences are confined to ``with_variant`` on the JSON and
vector columns (``docs/adr/0004``): ``JSONB``/``vector(1536)`` on Postgres,
``JSON`` on SQLite. The partial indexes are declared on both dialects because
SQLite supports partial indexes; the ``hnsw`` vector index is Postgres-only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector as _PgVector
from sqlalchemy import (
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator, TypeEngine

from opspilot.domain.runs import CLAIMABLE
from opspilot.settings import get_settings

# Imported directly, NOT guarded by try/except. `pgvector` is a core dependency
# (pyproject.toml), so its absence is a broken install and should fail loudly at
# import. Guarding it would be worse than useless: `load_dialect_impl` below
# falls back to JSON when the type is unavailable, which means a Postgres
# deployment missing pgvector would silently create `embedding` as a JSON column
# and lose vector search entirely -- no error, no warning, just a database that
# cannot answer the query the retrieval layer will run.

# ``JSONB`` on Postgres, ``JSON`` everywhere else (SQLite maps it to TEXT with
# the JSON serializer). ``sqlalchemy.JSON`` is the base and ``.with_variant``
# selects ``JSONB`` where it is available -- the exact construct the spec names.
_JSON = JSON().with_variant(JSONB(), "postgresql")

# The dimension the embedding column is declared at. Read from settings rather
# than typed as 1536 so a different embedder model is one env var away.
_EMBEDDING_DIM: int = get_settings().embedding_dim


class _Vector(TypeDecorator[Any]):
    """A ``vector(EMBEDDING_DIM)`` column on Postgres, plain ``JSON`` on SQLite.

    pgvector has no SQLite representation, and the SQLite test path serves
    cosine similarity from the in-memory vector store instead (ADR-0004). On
    SQLite the column is an inert ``JSON`` -- present so the table shape matches,
    never queried for distance. ``impl`` is ``JSON`` so SQLAlchemy knows how to
    bind and render it when ``pgvector`` is not the active dialect.

    Written as a ``TypeDecorator`` rather than ``JSON().with_variant(Vector, ...)``
    because ``Vector`` is a plain ``UserDefinedType`` that does not subclass a
    common base with ``JSON``, which ``with_variant`` requires.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[object]:
        """Return the pgvector type under Postgres, the ``JSON`` impl otherwise.

        The fallback is selected by *dialect*, never by whether pgvector happens
        to be importable -- an importable-but-unused pgvector must not change
        what SQLite gets, and a missing pgvector must not silently turn a
        Postgres vector column into JSON (the module-level import above makes
        that case impossible anyway).
        """
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PgVector(_EMBEDDING_DIM))
        return dialect.type_descriptor(JSON())


# The claim predicate, built from the domain enum -- the domain wins if the two
# ever disagree (rules 2 and 3 of the task, ``docs/agent-state-machine.md`` §5).
_CLAIMABLE_STATUSES: tuple[str, ...] = tuple(sorted(s.value for s in CLAIMABLE))
_CLAIM_PREDICATE: str = "status IN (" + ", ".join(f"'{s}'" for s in _CLAIMABLE_STATUSES) + ")"


class Base(DeclarativeBase):
    """Declarative base; ``Base.metadata`` is the Alembic target."""

    # Naming convention so Alembic emits predictable constraint names and a
    # reviewer can name an index in a diff rather than describing it.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    """A UUID primary key, server-defaulted on Postgres, emulated for SQLite.

    ``gen_random_uuid()`` is a Postgres server default; SQLite has no such
    function, so the Python default runs there. The Python default is present on
    both dialects so an object is usable before flush.
    """
    return mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def _utcnow() -> datetime:
    """A timezone-aware UTC ``now``, the default for every ``created_at``."""
    return datetime.now(UTC)


class Ticket(Base):
    """An inbound support request. ``docs/data-model.md`` §2."""

    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    external_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    customer_email: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class AgentRun(Base):
    """One execution of the workflow for one ticket. ``docs/data-model.md`` §2."""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    model_provider: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        # The worker's claim predicate. Partial so completed and failed runs --
        # the overwhelming majority -- stay out of the index.
        Index(
            "ix_agent_runs_claimable",
            "created_at",
            postgresql_where=text(_CLAIM_PREDICATE),
            sqlite_where=text(_CLAIM_PREDICATE),
        ),
    )


class AgentStep(Base):
    """The ordered execution timeline. ``docs/data-model.md`` §2."""

    __tablename__ = "agent_steps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String, nullable=False)
    input: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # A duplicate (run_id, sequence) would silently reorder the trace.
        UniqueConstraint("run_id", "sequence", name="uq_agent_steps_run_id_sequence"),
    )


class ToolCall(Base):
    """A single tool invocation. ``docs/data-model.md`` §2."""

    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=False, index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_steps.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    permission: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # The database-level backstop against a duplicate refund: even if every
        # layer of application code were wrong, the second executed call with the
        # same key fails here. Partial so a still-proposed call, or several
        # rejected ones, may share a key without colliding.
        Index(
            "uq_tool_calls_idempotency_key_executed",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND status = 'executed'"),
            sqlite_where=text("idempotency_key IS NOT NULL AND status = 'executed'"),
        ),
    )


class ApprovalRequest(Base):
    """A human-decided authorisation for one tool call. ``docs/data-model.md`` §2."""

    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=False, index=True
    )
    tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_explanation: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_snapshot: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)


class KnowledgeDocument(Base):
    """An indexed policy document. ``docs/data-model.md`` §2."""

    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # ``metadata`` is reserved on the declarative class, so the attribute is
    # ``doc_metadata`` while the database column keeps the spec's name.
    doc_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", _JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class KnowledgeChunk(Base):
    """An embedded chunk of a policy document. ``docs/data-model.md`` §2."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    anchor: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(_Vector(), nullable=True)

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_knowledge_chunks_document_id_ordinal"),
        # HNSW rather than IVFFlat: no training pass, and recall is not the
        # Phase 1 bottleneck. Postgres-only -- SQLite serves search in Python.
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Citation(Base):
    """One cited chunk on a run. ``docs/data-model.md`` §2."""

    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id"), nullable=False
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_chunks.id"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class AuditEvent(Base):
    """An append-only compliance record. ``docs/data-model.md`` §2.

    Append-only by convention: no UPDATE or DELETE path exists in the repository
    layer, and ``test_audit_is_append_only`` asserts the repository exposes no
    mutating method.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Nullable: auth and reindex events have no run.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )


def get_metadata() -> MetaData:
    """Return the shared ``MetaData`` object for the persistence layer.

    Alembic's ``env.py`` uses this as ``target_metadata`` so autogenerate sees
    the models.
    """
    return Base.metadata


__all__ = [
    "AgentRun",
    "AgentStep",
    "ApprovalRequest",
    "AuditEvent",
    "Base",
    "Citation",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "Ticket",
    "ToolCall",
    "get_metadata",
]
