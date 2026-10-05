"""Unit tests for the SQLAlchemy models against ``docs/data-model.md`` §2.

These introspect ``Base.metadata`` rather than asserting a class exists: the
guarantees the spec calls load-bearing (the partial claim index, the partial
unique idempotency index, the FK graph) are properties of the schema, so the
schema is what is asserted.
"""

from __future__ import annotations

from sqlalchemy import Table, UniqueConstraint

from opspilot.adapters.persistence.models import (
    AgentRun,
    AgentStep,
    ApprovalRequest,
    AuditEvent,
    Base,
    Citation,
    KnowledgeChunk,
    KnowledgeDocument,
    Ticket,
    ToolCall,
    get_metadata,
)
from opspilot.domain.runs import CLAIMABLE


def _table(name: str) -> Table:
    """The ``Table`` for a model, typed (``__table__`` is typed as ``FromClause``)."""
    return Base.metadata.tables[name]


TABLES = {
    "tickets",
    "agent_runs",
    "agent_steps",
    "tool_calls",
    "approval_requests",
    "knowledge_documents",
    "knowledge_chunks",
    "citations",
    "audit_events",
}


def test_metadata_has_every_spec_table() -> None:
    assert set(Base.metadata.tables) == TABLES


def test_get_metadata_returns_base_metadata() -> None:
    assert get_metadata() is Base.metadata


def test_all_primary_keys_are_uuid() -> None:
    for table in Base.metadata.tables.values():
        pk = list(table.primary_key.columns)
        assert len(pk) == 1, table.name
        assert pk[0].name == "id", table.name
        assert pk[0].type.__class__.__name__ == "Uuid", f"{table.name}: {pk[0].type!r}"


def test_timestamps_are_timezone_aware() -> None:
    from sqlalchemy import DateTime

    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.name.endswith("_at"):
                assert isinstance(column.type, DateTime), f"{table.name}.{column.name}"
                assert column.type.timezone is True, f"{table.name}.{column.name}"


def test_claim_index_predicate_matches_domain_claimable() -> None:
    index = next(ix for ix in _table("agent_runs").indexes if ix.name == "ix_agent_runs_claimable")
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert "status IN" in predicate
    # Every claimable status is in the predicate, and nothing else is.
    for status in CLAIMABLE:
        assert f"'{status.value}'" in predicate
    lowered = predicate.lower()
    for excluded in ("waiting_approval", "completed", "failed"):
        assert excluded not in lowered


def test_claim_index_is_partial_on_both_dialects() -> None:
    index = next(ix for ix in _table("agent_runs").indexes if ix.name == "ix_agent_runs_claimable")
    assert index.dialect_options["postgresql"]["where"] is not None
    assert index.dialect_options["sqlite"]["where"] is not None


def test_idempotency_index_is_unique_and_partial() -> None:
    index = next(
        ix
        for ix in _table("tool_calls").indexes
        if ix.name == "uq_tool_calls_idempotency_key_executed"
    )
    assert index.unique
    assert [c.name for c in index.columns] == ["idempotency_key"]
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert "idempotency_key IS NOT NULL" in predicate
    assert "status = 'executed'" in predicate


def test_agent_steps_unique_run_id_sequence() -> None:
    constraints = {
        c.name for c in _table("agent_steps").constraints if isinstance(c, UniqueConstraint)
    }
    assert "uq_agent_steps_run_id_sequence" in constraints
    unique = next(
        c
        for c in _table("agent_steps").constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_agent_steps_run_id_sequence"
    )
    assert {c.name for c in unique.columns} == {"run_id", "sequence"}


def test_knowledge_chunks_unique_document_ordinal() -> None:
    unique = next(
        c
        for c in _table("knowledge_chunks").constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_knowledge_chunks_document_id_ordinal"
    )
    assert {c.name for c in unique.columns} == {"document_id", "ordinal"}


def test_approval_tool_call_id_is_unique() -> None:
    unique = next(
        c for c in _table("approval_requests").constraints if isinstance(c, UniqueConstraint)
    )
    assert {c.name for c in unique.columns} == {"tool_call_id"}


def test_knowledge_documents_source_is_unique() -> None:
    unique = next(
        c for c in _table("knowledge_documents").constraints if isinstance(c, UniqueConstraint)
    )
    assert {c.name for c in unique.columns} == {"source"}


def test_doc_metadata_maps_to_metadata_column() -> None:
    assert "metadata" in _table("knowledge_documents").c
    assert KnowledgeDocument.doc_metadata.property.columns[0].name == "metadata"


def test_foreign_keys_are_declared() -> None:
    expected = {
        ("agent_runs", "ticket_id", "tickets"),
        ("agent_steps", "run_id", "agent_runs"),
        ("tool_calls", "run_id", "agent_runs"),
        ("tool_calls", "step_id", "agent_steps"),
        ("approval_requests", "run_id", "agent_runs"),
        ("approval_requests", "tool_call_id", "tool_calls"),
        ("citations", "run_id", "agent_runs"),
        ("citations", "document_id", "knowledge_documents"),
        ("citations", "chunk_id", "knowledge_chunks"),
        ("knowledge_chunks", "document_id", "knowledge_documents"),
        ("audit_events", "run_id", "agent_runs"),
    }
    found = {
        (table.name, fk.parent.name, fk.column.table.name)
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
    }
    assert expected <= found


def test_approval_tool_call_fk_is_present() -> None:
    fks = {fk.parent.name: fk.column.table.name for fk in _table("approval_requests").foreign_keys}
    assert fks["tool_call_id"] == "tool_calls"


def test_status_columns_are_text_not_enum() -> None:
    for model in (AgentRun, AgentStep, ToolCall, ApprovalRequest, AuditEvent):
        for column in model.__table__.columns:
            assert column.type.__class__.__name__ in {"String", "Text"} or column.name not in {
                "status",
                "step_type",
                "permission",
                "event_type",
            }
    assert _table("agent_runs").c.status.type.__class__.__name__ == "String"
    assert _table("tool_calls").c.permission.type.__class__.__name__ == "String"


def test_table_classes_are_registered() -> None:
    for model, name in (
        (Ticket, "tickets"),
        (AgentRun, "agent_runs"),
        (AgentStep, "agent_steps"),
        (ToolCall, "tool_calls"),
        (ApprovalRequest, "approval_requests"),
        (KnowledgeDocument, "knowledge_documents"),
        (KnowledgeChunk, "knowledge_chunks"),
        (Citation, "citations"),
        (AuditEvent, "audit_events"),
    ):
        assert model.__tablename__ == name
