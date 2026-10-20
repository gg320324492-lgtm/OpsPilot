"""The persistence ports: ``RunStore``, ``ToolCallStore``, ``ApprovalStore``,
``TicketStore``, ``TicketIntake``.

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

from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus


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

    async def count(self) -> int:
        """How many tickets exist, ignoring any page.

        **On the port, unlike ``list``**, and the difference is the contract.
        ``docs/api-contract.md`` §10 says a list endpoint's ``total`` is "the
        size of the whole matching set", computed "by a separate count over the
        same filtered query, not by measuring the list that was returned". A
        count the port does not require is a count the router has to do without,
        and the only number left to it is the page length -- which is the lie
        the contract forbids. So the capability the contract promises is
        required of the store that backs the endpoint.
        """
        ...


@runtime_checkable
class TicketIntake(Protocol):
    """Create a ticket and its run as one unit of work.

    The two rows must land or neither does. A ticket with no run is a customer
    waiting for an investigation that will never happen, and the dashboard
    would report nothing wrong.

    This is a port of its own rather than a method on ``TicketStore`` because
    the unit of work is not the ticket: it is the ticket *and* the run, and
    the two live in different aggregates. ``TicketStore.create`` still exists
    and still means "insert one ticket"; it is what a caller uses when there
    is no run to make. What it cannot express is "and nothing else", which is
    the whole of this port.
    """

    async def create(
        self,
        *,
        subject: str,
        body: str,
        customer_email: str,
        external_id: str | None,
        model_provider: str,
        model_name: str,
    ) -> tuple[UUID, AgentRun]:
        """Insert the ticket and its run in one transaction; return both."""
        ...


@runtime_checkable
class RunStore(Protocol):
    """Reads and writes agent runs.

    ``claim_next`` is the worker's queue primitive: ``SELECT ... WHERE status IN
    (claimable) AND (claimed_until IS NULL OR claimed_until < now()) ORDER BY
    created_at FOR UPDATE SKIP LOCKED LIMIT 1``, which in the same transaction
    sets the row's lease (``claimed_until``/``claimed_by``) forward. The lease is
    what keeps a claimed run out of a second worker's hands for the duration of
    the drive; ``renew_lease`` extends it while the holder is alive, and a lease
    that expires makes the run claimable again (``docs/adr/0005``,
    ``docs/limitations.md`` §2). SQLite cannot express ``SKIP LOCKED`` and
    degrades to a single-threaded claim, but the lease predicate still holds (see
    ``docs/data-model.md`` §6).
    """

    async def create(self, *, ticket_id: UUID, model_provider: str, model_name: str) -> AgentRun:
        """Insert a run in ``RECEIVED`` and return it."""
        ...

    async def get(self, run_id: UUID) -> AgentRun | None:
        """Fetch a run by id."""
        ...

    async def count(self, *, status: RunStatus | None = None) -> int:
        """How many runs match ``status``, ignoring any page.

        **On the port, unlike ``list``**, and the difference is the contract.
        ``docs/api-contract.md`` §10 says a list endpoint's ``total`` is "the
        size of the whole matching set", computed "by a separate count over the
        same filtered query, not by measuring the list that was returned". A
        count the port does not require is a count the router has to do without,
        and the only number left to it is the page length -- which is the lie
        the contract forbids. So the capability the contract promises is
        required of the store that backs the endpoint.
        """
        ...

    async def claim_next(self, *, worker_id: str, lease_seconds: float) -> AgentRun | None:
        """Claim the oldest run whose lease is absent or expired, or ``None``.

        Sets the lease forward in the same transaction as the read; a row held
        by a live worker (lease in the future) is not returned.
        """
        ...

    async def renew_lease(self, run_id: UUID, *, worker_id: str, lease_seconds: float) -> bool:
        """Extend a held run's lease; ``False`` if this worker no longer holds it."""
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
        latency_ms: int | None = None,
    ) -> None:
        """Move a tool call to a new status and record its outcome.

        ``latency_ms`` is the dispatch duration the gateway measured, for the
        transitions where one exists (``executed``/``failed`` after a real
        dispatch). It is keyword-only and defaults to ``None``, which means
        "this transition measured nothing" and leaves the column NULL --
        additive, so every existing caller is unaffected.
        """
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
        """Persist a pending approval for one tool call.

        The whole domain object is persisted, so the operator's ``note`` and the
        approver's ``context`` travel with it. The context is assembled by the
        runtime from already-persisted data -- no tool call is triggered to
        build it -- and it is optional: an approval with no context is valid,
        and so is one whose context has only one of its two fields.
        """
        ...

    async def get_for_tool_call(self, tool_call_id: UUID) -> ApprovalRequest | None:
        """Fetch the (unique) approval bound to a tool call."""
        ...

    async def count(self, *, status: ApprovalStatus | None = None) -> int:
        """How many approvals match ``status``, ignoring any page.

        **On the port, unlike ``list``**, and the difference is the contract.
        ``docs/api-contract.md`` §10 says a list endpoint's ``total`` is "the
        size of the whole matching set", computed "by a separate count over the
        same filtered query, not by measuring the list that was returned". A
        count the port does not require is a count the router has to do without,
        and the only number left to it is the page length -- which is the lie
        the contract forbids. So the capability the contract promises is
        required of the store that backs the endpoint.
        """
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
        """Record a human decision and flip the run back to a claimable state.

        The decision is a **conditional** update (``WHERE id = ? AND status =
        'pending'``), so under concurrency exactly one writer moves the row and
        the others affect none.

        ``decided_by`` is the identity of the operator who made the decision,
        and it is **server-assigned**: the value is the authenticated
        operator's identity, never one the request body chose. A client that
        could name the operator would be able to put somebody else's name on
        a refund they approved themselves.

        ``note`` is the operator's own remark on the decision. It is persisted
        with the decision rather than dropped.

        Raises:
            ApprovalAlreadyDecided: if the approval has already been decided.
                The writer that lost the conditional update is told it lost;
                it is not handed the winner's decision as if it were its own,
                because a caller cannot tell the two apart and the contract
                promises a 409 for the second click (``docs/api-contract.md``
                §6).
            NotFoundError: if no approval with that id exists.
        """
        ...

    async def has_approved(self, tool_call_id: UUID) -> bool:
        """Whether an approved request exists for *this* tool call.

        A database read on purpose: the approval may have been granted by the
        API process while the worker was down, and the check is bound to the
        tool call id so approving one refund cannot authorise another.
        """
        ...

    async def expire_pending(self, *, before: datetime) -> int:
        """Mark every pending approval created before ``before`` as expired.

        The sweep behind ``APPROVAL_TTL_SECONDS``: an approval nobody has
        decided within the TTL stops being decidable, so the operator is not
        left clicking "approve" on a refund whose run has moved on, and the
        inbox does not accumulate rows that look actionable forever.

        **The run is not touched.** An expired approval leaves its run parked
        in ``WAITING_APPROVAL``: the money still has not moved, and no decision
        was made, so there is nothing for the worker to do about it. This
        method changes the *approval's* status and nothing else -- the run's
        status is the run store's business.

        **The conditional update is the whole method.** ``WHERE status =
        'pending' AND created_at < :before`` means a sweep never overwrites a
        decision that landed a moment earlier, and never touches an approval
        that was already expired. It is idempotent: running it twice expires
        the same rows once.

        Args:
            before: The cutoff. Approvals created at or after this instant are
                left alone; the store does not compute the cutoff itself, so
                the TTL stays a setting of the caller and the store has no
                clock of its own.

        Returns:
            How many approvals were marked expired.
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
class StepRow:
    """One ``agent_steps`` row, as the trace endpoints read it.

    The field names are the router's (``runs.py``'s ``StepRow`` Protocol), so the
    run store satisfies that Protocol without the router importing the adapter.
    """

    sequence: int
    step_type: str
    output: dict[str, object] | None
    latency_ms: int | None
    started_at: datetime


@dataclass(frozen=True)
class ToolCallRow:
    """One ``tool_calls`` row, as the run-detail router reads it.

    ``permission`` and ``status`` are the domain enums, not the stored strings:
    the router's Protocol is typed with the enums (``docs/api-contract.md`` §3),
    so the conversion happens here, once, at the storage boundary.
    """

    id: UUID
    tool_name: str
    arguments: dict[str, object]
    permission: Permission
    status: ToolCallStatus
    result: dict[str, object] | None
    latency_ms: int | None
    idempotency_key: str | None
    error: str | None


@dataclass(frozen=True)
class CitationRow:
    """One ``citations`` join row, as the run-detail router reads it.

    Distinct from :class:`CitationRecord`, which is the *write* shape a caller
    hands to ``create_many``. This is the read shape, and the split is
    deliberate: a writer supplies a document slug and an anchor, while a reader
    gets the composed ``"{slug}#{anchor}"`` string the UI links.
    """

    document: str
    chunk: str
    score: float
    rank: int


@dataclass(frozen=True)
class PendingApprovalRow:
    """One pending approval, as run detail's approver card reads it.

    The approver-visible projection of :class:`ApprovalRequest`: the same
    fields minus the run id (the caller already has it) and the decision
    columns (a pending row has none). Named separately because
    ``RunDetail.pending_approval`` is a nested object rather than a bare id,
    and the run store -- not the approval store -- is what the router probes
    for it, since the router holds only a ``RunStore``.
    """

    id: UUID
    tool_call_id: UUID
    status: ApprovalStatus
    reason: str
    risk_explanation: str
    arguments_snapshot: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class CustomerReplyRow:
    """A completed run's customer-visible reply.

    A read model because there is no ``customer_replies`` table: the reply is
    the model output recorded on the run's ``response`` step, and reading it back
    means projecting that step rather than selecting a row. The escalation flag
    travels with it because "this was an escalation" changes how an operator
    reads the body, and the flag is only recorded there too.
    """

    body: str
    escalated: bool


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
