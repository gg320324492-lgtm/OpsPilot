# Case study: duplicate-charge investigation with a human-approved refund path

**OpsPilot — an open-source AI operations agent for billing-support workflows.**

This is a personal, independent project: it was designed and built end to end by
the author, and it is presented as an engineering case study, not as a customer
engagement. There is no client contract behind it, no customer is named, and no
business outcome is claimed on anyone's behalf. Every number below comes from
the repository's own documented runs, test suites and review records, and every
link points at a file in the repository.

---

## The business problem

Billing-support teams handle the same ticket over and over: *"We were charged
twice."* Each one requires pulling the invoice, comparing the transactions,
checking refund policy, deciding, executing the refund and leaving an audit
trail. The procedure is repetitive, but the execution is sensitive — it moves
money, and a wrong refund is costly in both cash and trust.

Large language models are good at exactly the front half: reading the ticket,
retrieving policy, querying systems, spotting the duplicate, drafting a
decision. They are not acceptable as the back half: an unsupervised model that
can move money is not a system anyone should run.

The question this project answers is: **can an agent do the legwork while a
human keeps sole, verifiable authority over the refund?**

## My role

This is an independent personal project, and I am its sole designer and
implementer. I designed the architecture, built the FastAPI backend and the
PostgreSQL/pgvector schema, wrote the agent runtime and its gating pipeline,
implemented the three MCP servers, built the Next.js dashboard, defined the
evaluation harness, set up CI, and wrote the documentation — including the
honest-limitations file this case study draws on. It is not a deployment for a
paying customer, and it does not claim production revenue or savings figures.

## The solution

The reference scenario is a full duplicate-charge investigation captured
end to end on **2026-10-14** and documented in
[`docs/case-study-duplicate-charge.md`](case-study-duplicate-charge.md).

**Intake and investigation.** A ticket arrives through the dashboard form and
starts a run driven by an explicit agent state machine. The agent retrieves
policy with pgvector-backed RAG — retrieved chunks are wrapped in a labelled,
explicitly untrusted data block, because retrieved text is data, not
instruction. It then queries the surrounding systems through MCP.

**Systems via MCP.** Three MCP servers — CRM, Billing and Issues — are built on
the official Python SDK and speak the real MCP protocol through the project's
`ToolGateway` contract, covering the nine tools specified in
[`docs/mcp-contracts.md`](mcp-contracts.md). In this demo they carry
deterministic seed data standing in for Salesforce, Stripe and Jira. **They are
in-repo mocks: the refund is simulated, no card is charged, and no money
moves.**

**Five deterministic gates.** Between the model's proposal and any external
write sit five gates, none of which the model or retrieved text can influence:
(1) Pydantic schema validation, (2) tool-registry lookup, (3) static permission
lookup, (4) a policy engine with risk classification, amount ceilings and an
idempotency pre-check, and (5) the approval gate. The CI-enforced invariant is
stated as a query: **every executed `HIGH_RISK_WRITE` tool call must have a
matching approved `ApprovalRequest` — violations = 0** — and it holds
regardless of what any prompt says.

**The run itself.** The agent finds two charges of $129.00, two seconds apart,
against one paid invoice, proposes `billing.issue_refund` on the second, and the
policy engine classifies it `HIGH_RISK_WRITE`: the run parks for a human. The
operator sees a deterministic explanation plus the model's reasoning, and the
approval row has **snapshotted the arguments at park time**, so what the human
approves is byte-for-byte what later executes. Approving releases the run back
to the queue; the worker — claiming work with `FOR UPDATE SKIP LOCKED` —
executes the refund exactly once under an idempotency key (`replayed:false`).
A repeat approval is refused with `409 approval_already_decided`. Every gate
writes an `AuditEvent`.

## The approval trade-offs

The design deliberately buys control with latency:

- **Fail closed.** Any money-moving proposal stops at the approval gate. The
  model cannot bypass it; a prompt-injected "issue $10,000" still hits the
  policy engine and a human who sees the amount.
- **Snapshot, then execute.** The approver's view and the executed arguments
  are the same row, and a test asserts they cannot diverge between approval and
  execution.
- **Unforgeable attribution.** `decided_by` is assigned by the server from the
  bearer token; the request body cannot name an approver.
- **Durability over process.** The approval is a PostgreSQL row, not in-process
  state, so a worker crash between approval and execution loses nothing and
  duplicates nothing.
- **Known boundary.** Phase 1–2 operate with a single operator token and no
  separation of duties; roles and approval spheres are explicit Phase 3 work
  ([`docs/limitations.md`](limitations.md)).

## Measurable results

From the repository's own records:

- **1,365 Python tests passed** (2 skipped, 16 marker-deselected) in the
  published Phase 2 review of 2026-11-14
  ([`docs/phase2-review-2026-11-14.md`](phase2-review-2026-11-14.md)), alongside
  58 web pure-logic specs and 114 desktop lifecycle tests (1 failure tracked).
- **Five gates, one invariant, zero violations**: the approval-bypass invariant
  is enforced by CI as a database query, not a helper unit test.
- **The documented run**: duplicate detected → refund proposed → parked →
  approved → executed exactly once → duplicate approval refused with 409.
- **Injection posture**: structural gates hold even when retrieved documents
  contain adversarial instructions; the project claims containment, not prompt
  immunity.

One honesty note: the demo run used a deterministic provider so that it proves
the gates and wiring, not model quality. Measuring real-model proposal quality
is an explicit, ongoing workstream, and its published results so far
([`docs/phase2-review-2026-11-14.md`](phase2-review-2026-11-14.md)) fall short
of the project's own acceptance bar — reported here as open work, not hidden.

## Limitations and evidence

Simulated third-party systems; simulated refund; single-operator security
model; real-model validation in progress. Full detail:
[`docs/limitations.md`](limitations.md), [`docs/progress.md`](progress.md),
[`docs/architecture.md`](architecture.md),
[`docs/case-study-duplicate-charge.md`](case-study-duplicate-charge.md), and the
run's screenshots under `docs/assets/screenshots/`.
