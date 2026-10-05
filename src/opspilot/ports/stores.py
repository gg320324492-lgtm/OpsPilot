"""The persistence ports: ``RunStore``, ``ToolCallStore``, ``ApprovalStore``,
``TicketStore``.

Responsibility: the storage boundaries the runtime and API depend on. Splitting
the stores by aggregate (rather than one god-repository) keeps each Protocol
small enough that an adapter is obviously correct, and keeps the audit surface
honest -- ``AuditStore`` deliberately exposes no mutating method, because the
ledger is append-only by convention.

Layer: ``ports``. Imports only ``typing``, ``datetime``/``uuid`` from the stdlib
and ``opspilot.domain``. The SQLAlchemy implementation lives in
``opspilot.adapters.persistence`` and is the *only* place that knows the ORM
exists. See ``docs/data-model.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from opspilot.domain.approvals import ApprovalRequest
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import ToolCallStatus


@dataclass(frozen=True)
class TicketRecord:
    """A ticket as the worker's ``RunContext`` needs it.

    A read model, not the domain entity: the port exposes the three fields the
    runtime prompt needs (subject, body, customer email) so the worker can build
    a ``RunContext`` without reaching into the ORM. ``ports`` may depend on the
    standard library, so a frozen dataclass is the right shape here -- it keeps
    the Protocol importable with no SQLAlchemy present.
    """

    id: UUID
    subject: str
    body: str
    customer_email: str
    external_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class PendingToolCall:
    """A persisted tool call as the worker needs it to resume or diagnose a run.

    Carries the id -- the thing an approval is bound to -- plus the tool name and
    arguments the runtime re-parses on the resume pass. It is deliberately
    *data*, never authority: gate 5 still re-reads the database for the
    approval, so a stale ``status`` here cannot authorise anything.
    """

    tool_call_id: UUID
    run_id: UUID
    tool_name: str
    arguments: dict[str, object] = field(default_factory=dict)
    status: ToolCallStatus = ToolCallStatus.PROPOSED


@runtime_checkable
class TicketStore(Protocol):
    """Reads and writes tickets."""

    async def create(
        self, *, subject: str, body: str, customer_email: str, external_id: str | None = None
    ) -> UUID:
        """Insert a ticket and return its id."""
        ...

    async def get(self, ticket_id: UUID) -> TicketRecord | None:
        """Fetch a ticket by id, as the worker's ``RunContext`` needs it."""
        ...


@runtime_checkable
class RunStore(Protocol):
    """Reads and writes agent runs.

    ``claim_next`` is the worker's queue primitive: ``SELECT ... FOR UPDATE SKIP
    LOCKED LIMIT 1`` over the claimable statuses, ordered by ``created_at``.
    SQLite cannot express ``SKIP LOCKED`` and degrades to a single-threaded
    claim (see ``docs/data-model.md`` §6).
    """

    async def create(self, *, ticket_id: UUID, model_provider: str, model_name: str) -> AgentRun:
        """Insert a run in ``RECEIVED`` and return it."""
        ...

    async def get(self, run_id: UUID) -> AgentRun | None:
        """Fetch a run by id."""
        ...

    async def claim_next(self, *, worker_id: str) -> AgentRun | None:
        """Atomically claim the oldest claimable run, or return ``None``."""
        ...

    async def set_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        failure_reason: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        """Update status and, in the same transaction, write the state-change step."""
        ...


@runtime_checkable
class ToolCallStore(Protocol):
    """Reads and writes tool calls."""

    async def record_proposed(
        self, *, run_id: UUID, tool_name: str, arguments: dict[str, object], permission: str
    ) -> UUID:
        """Insert a ``proposed`` tool call and return its id."""
        ...

    async def set_status(
        self,
        tool_call_id: UUID,
        status: ToolCallStatus,
        *,
        result: dict[str, object] | None = None,
        error: str | None = None,
        rejection_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        """Move a tool call to a new status and record its outcome."""
        ...

    async def get(self, tool_call_id: UUID) -> PendingToolCall | None:
        """Fetch one tool call by id, or ``None``.

        The resume pass uses this to rebuild the proposal for the exact call a
        human decided: the persisted ``tool_name``/``arguments`` are what the
        gates re-parse, so approval re-enters them for that same call rather than
        for a fresh proposal that would carry a new id.
        """
        ...

    async def find_awaiting_approval(self, run_id: UUID) -> PendingToolCall | None:
        """Find the run's ``awaiting_approval`` tool call, oldest first, or ``None``.

        This is Gap B's discovery query: a run re-queued from ``WAITING_APPROVAL``
        is in ``EXECUTING`` with exactly one call parked at the approval gate, and
        the worker needs its id to pass as ``resume_tool_call_id``.
        """
        ...

    async def find_executed(
        self, *, run_id: UUID, idempotency_key: str
    ) -> dict[str, object] | None:
        """Return the recorded result of an ``executed`` call with this key.

        Gap A's lookup: gate 4's idempotency short-circuit reads whether the side
        effect already happened for ``(run_id, idempotency_key)`` and, if so,
        remembers the result instead of re-executing. ``None`` means "nothing has
        ever executed for this key", which is the safe direction.
        """
        ...

    async def find_executed_call_id(self, *, run_id: UUID, idempotency_key: str) -> UUID | None:
        """Return the id of the ``executed`` call holding this key, or ``None``.

        On a replay the runtime must not stamp the *same* key onto a second
        ``executed`` row: the partial unique index (and the CI invariant for
        high-risk executions) forbid two. This read tells the replay branch
        whether a real competing row exists, so the duplicate proposal is
        recorded as a rejection rather than a second keyed execution.
        """
        ...


@runtime_checkable
class ApprovalStore(Protocol):
    """Reads and writes approval requests."""

    async def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist a pending approval for one tool call."""
        ...

    async def get_for_tool_call(self, tool_call_id: UUID) -> ApprovalRequest | None:
        """Fetch the (unique) approval bound to a tool call."""
        ...

    async def get_for_run(self, run_id: UUID) -> ApprovalRequest | None:
        """Fetch the run's most recent approval, or ``None``.

        The worker uses this on the rejection path: a run flipped to
        ``RESPONDING`` because a human declined has a rejected approval, and the
        reply that follows must escalate rather than pretend a refund happened.
        """
        ...

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str,
        note: str | None = None,
        decided_at: datetime,
    ) -> ApprovalRequest:
        """Record a human decision and flip the run back to a claimable state."""
        ...

    async def has_approved(self, tool_call_id: UUID) -> bool:
        """Whether an approved request exists for *this* tool call.

        A database read on purpose: the approval may have been granted by the
        API process while the worker was down, and the check is bound to the
        tool call id so approving one refund cannot authorise another.
        """
        ...


@dataclass(frozen=True)
class CitationRecord:
    """One persisted citation, as the run-detail router needs it.

    The field names are the router's, not the table's: ``runs.py`` reads
    ``.document``, ``.chunk``, ``.score`` and ``.rank`` off each row and renders
    ``chunk`` as ``"{document}#{anchor}"`` (``docs/api-contract.md`` §3). The
    store resolves the document slug and composes the chunk string, so the
    router never joins tables itself.
    """

    document: str
    chunk: str
    score: float
    rank: int


@dataclass(frozen=True)
class KnowledgeDocumentRecord:
    """One indexed document, as the knowledge router needs it.

    ``GET /api/knowledge`` reads ``.source``, ``.title``, ``.chunk_count``,
    ``.indexed_at`` and ``.content_hash`` (``knowledge.py``). ``chunk_count`` is
    a derived count -- the table has no such column -- which is why this is a
    read model rather than the ORM row.
    """

    source: str
    title: str
    chunk_count: int
    indexed_at: datetime
    content_hash: str


@runtime_checkable
class CitationStore(Protocol):
    """Reads and writes a run's citations.

    A row per cited chunk (``docs/data-model.md`` §2): the UI's Sources panel is
    a join and the eval's Recall@K metric is a query over ``rank``, both of
    which a JSON blob on the run would force into application code.
    """

    async def create(
        self, *, run_id: UUID, document_id: UUID, chunk_id: UUID, score: float, rank: int
    ) -> None:
        """Persist one citation for a run."""
        ...

    async def create_many(self, run_id: UUID, records: list[CitationRecord]) -> None:
        """Persist a run's retrieval hits as citations.

        Each ``CitationRecord`` carries its ``chunk`` as ``"{slug}#{anchor}"``;
        the store resolves the ``document_id`` and ``chunk_id`` the ``citations``
        table's foreign keys require. The chunk rows must already exist, which
        they do because retrieval reads them -- see the store docstring.
        """
        ...

    async def list_citations(self, run_id: UUID) -> list[CitationRecord]:
        """Return a run's citations, best rank first."""
        ...


@runtime_checkable
class KnowledgeDocumentStore(Protocol):
    """Reads and writes ``knowledge_documents`` rows."""

    async def upsert_document(
        self,
        *,
        source: str,
        title: str,
        content: str,
        metadata: dict[str, object],
        content_hash: str,
    ) -> UUID:
        """Insert or update a document by ``source`` and return its id."""
        ...

    async def content_hash(self, source: str) -> str | None:
        """Return the stored content hash for ``source``, or ``None``.

        The ingestion path compares this before doing any work, so an unchanged
        document is skipped and a reindex is idempotent (``docs/api-contract.md``
        §9).
        """
        ...

    async def list_documents(self) -> list[KnowledgeDocumentRecord]:
        """Return every indexed document with its chunk count."""
        ...
