"""SQLAlchemy declarative models: the schema, as code.

Responsibility: declare the tables -- ``tickets``, ``agent_runs``,
``agent_steps``, ``tool_calls``, ``approval_requests``, ``knowledge_documents``,
``knowledge_chunks``, ``citations``, ``audit_events`` -- and expose their
``metadata``, which is what ``migrations/env.py`` points ``target_metadata`` at.

Layer: ``adapters``. Imports SQLAlchemy, ``pgvector`` and ``opspilot.domain``
for the enum types.

M0 note: **stubs only.** The tables land in M1 as an Alembic migration; nothing
here is created yet. Status columns are ``TEXT`` with a Python ``Enum`` on the
model side, not PG ``ENUM``, so a new ``RunStatus`` needs no database type
migration (``docs/data-model.md`` §4). Primary keys are UUIDs (server-side
``gen_random_uuid()``, emulated on SQLite), and all timestamps are
``TIMESTAMPTZ`` UTC named ``*_at``.

The declaration below is the shape a reviewer should expect in M1: a shared
``UUIDPrimaryKey`` mixin and a ``Base`` whose ``metadata`` is the migration
target. Column details are deferred, but the two database-level guarantees the
spec calls load-bearing are noted where they will live:

- the partial unique index on ``tool_calls.idempotency_key`` where
  ``status='executed'`` -- the backstop against a duplicate refund;
- ``UNIQUE (agent_steps.run_id, sequence)`` -- the ordering guarantee the trace
  relies on.
"""

from __future__ import annotations

from typing import Any

# The declarative base and the table classes below are intentionally not
# declared in M0. `metadata` is exposed as the Alembic target so
# `migrations/env.py` is wired correctly from the start, and M1 fills the
# declarations above it.
#
#     from sqlalchemy.orm import DeclarativeBase
#
#     class Base(DeclarativeBase):
#         """Declarative base; ``Base.metadata`` is the Alembic target."""
#
#     class Ticket(Base): ...
#     class AgentRun(Base): ...
#     class AgentStep(Base): ...
#     class ToolCall(Base): ...
#     class ApprovalRequest(Base): ...
#     class KnowledgeDocument(Base): ...
#     class KnowledgeChunk(Base): ...
#     class Citation(Base): ...
#     class AuditEvent(Base): ...


def get_metadata() -> Any:  # noqa: ANN401 -- returns sqlalchemy.MetaData
    """Return the shared ``MetaData`` object for the persistence layer.

    Alembic's ``env.py`` uses this as ``target_metadata`` so autogenerate sees
    the models. Returns the declarative base's metadata once the tables are
    declared in M1. M0 stub.

    Annotated ``Any`` rather than ``MetaData`` because at M0 there is no
    declarative base to hang the type on, and importing sqlalchemy into this
    module's signature ahead of M1 would be the only import in ``adapters`` that
    exists purely to satisfy a type checker. The annotation becomes ``MetaData``
    in M1 and the ``noqa`` goes with it.
    """
    raise NotImplementedError
