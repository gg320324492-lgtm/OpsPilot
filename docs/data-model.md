# Data Model

PostgreSQL 16 with pgvector. SQLAlchemy 2.0 declarative models, Alembic
migrations. **Single-tenant in Phase 1** — there is no `organization_id` and no
`workspace_id`. See [§7](#7-what-is-deliberately-absent) for why.

Implementation: `src/opspilot/adapters/persistence/models.py`.

---

## 1. Entity relationships

```
Ticket ──1:N──> AgentRun ──1:N──> AgentStep
                    │
                    ├──1:N──> ToolCall ──1:1──> ApprovalRequest
                    │            │  (only for HIGH_RISK_WRITE)
                    │
                    ├──1:N──> Citation ──N:1──> KnowledgeChunk ──N:1──> KnowledgeDocument
                    │
                    └──1:N──> AuditEvent
```

All primary keys are `UUID` (server-side `gen_random_uuid()`), not autoincrement
integers. The reason is not distributed-systems ambition: run and tool-call ids
appear in customer-facing and approval-facing text, and a sequential id leaks
volume ("ticket #4" tells the world you have four tickets) and invites
enumeration. UUIDs cost nothing here.

All timestamps are `TIMESTAMPTZ`, stored UTC, named `*_at`.

**`TEXT` versus `VARCHAR`.** The two are not interchangeable *as declarations*,
so the tables below name the one each column actually has: `TEXT` for free
text (a subject, a body, a chunk's content) and `VARCHAR` for the short
enumerated or identifier columns (`status`, `permission`, `step_type`,
`model_provider`, `idempotency_key`, and so on). In PostgreSQL they behave the
same — `varchar` with no length limit is accepted anywhere `text` is, and
neither enforces a limit — so the difference is in how the schema is written
down, not in what the database will let you store. The authority for which is
which is the **migration**, and
`tests/unit/test_migration_and_orm_agree_on_column_types.py` asserts that the
ORM and the migration chain declare the same type for every column, because
SQLite would not catch a disagreement: both types have `TEXT` affinity there,
so a `create_all`-built database and an `alembic upgrade`-built one are
identical there and differ on Postgres.

## 2. Tables

### `tickets`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `external_id` | TEXT NULL, unique | The upstream system's id, if the ticket came from one |
| `subject` | TEXT NOT NULL | |
| `body` | TEXT NOT NULL | |
| `customer_email` | TEXT NOT NULL, indexed | |
| `created_at` | TIMESTAMPTZ NOT NULL | |

### `agent_runs`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `ticket_id` | UUID FK → tickets, indexed | |
| `status` | VARCHAR NOT NULL, indexed | `RunStatus` value. Indexed because the worker's claim query filters on it. |
| `model_provider` | VARCHAR NOT NULL | `fake` \| `openai` \| `anthropic` |
| `model_name` | VARCHAR NOT NULL | e.g. `claude-sonnet-5-5` |
| `started_at` | TIMESTAMPTZ NULL | Set on first transition out of `RECEIVED` |
| `completed_at` | TIMESTAMPTZ NULL | Set on entry to `COMPLETED`/`FAILED` |
| `failure_reason` | TEXT NULL | Machine-stable token; see state-machine doc §3 |
| `created_at` | TIMESTAMPTZ NOT NULL | |
| `claimed_until` | TIMESTAMPTZ NULL | When the claim lease expires. See below |
| `claimed_by` | VARCHAR NULL | The `WORKER_ID` holding the lease. See below |

Index for the worker:

```sql
CREATE INDEX ix_agent_runs_claimable
    ON agent_runs (created_at)
    WHERE status IN ('received','classifying','retrieving',
                     'planning','executing','responding');

CREATE INDEX ix_agent_runs_claimed_until
    ON agent_runs (claimed_until)
    WHERE claimed_until IS NOT NULL;
```

A partial index on the claim predicate. It stays small as the table grows,
because completed and failed runs — which will be the overwhelming majority —
are not in it.

**The claim lease.** `claim_next` takes the oldest claimable row whose lease is
absent or expired and sets `claimed_until`/`claimed_by` forward *in the same
transaction as the read*; `renew_lease` pushes the deadline out while the same
worker still holds the row, and a `set_status` into `waiting_approval`,
`completed` or `failed` clears both columns because the row is no longer being
driven. The lease is what makes two workers safe, and it is deliberately **not** a
status: `executing` must stay claimable for a worker that died mid-step, so the
lease sits beside the state machine rather than inside it
(`docs/agent-state-machine.md` §2 has no edge for it, and
`docs/adr/0005` is the argument).

Two columns rather than one because renewal has to be conditional: a worker whose
lease expired and whose run was taken by somebody else renews **zero** rows
(`UPDATE ... WHERE id = ? AND claimed_by = ?`) and learns it lost the run. Without
`claimed_by` a stalled worker waking up would extend the *thief's* lease.

`claimed_until` carries its own partial index because the predicate's second
clause (`claimed_until IS NULL OR claimed_until < now()`) is what admits an
expired lease back into the queue. The trade the lease buys is real and is
recorded in [limitations.md](limitations.md) §2: recovery is automatic, which is
what makes it silent, and the operator-facing half of that is the `claim_lease`
field on `GET /api/runs/{id}` ([api-contract.md](api-contract.md) §3).

### `agent_steps`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `run_id` | UUID FK → agent_runs, indexed | |
| `sequence` | INTEGER NOT NULL | Monotonic within a run. `UNIQUE (run_id, sequence)`. |
| `step_type` | VARCHAR NOT NULL | `state_change` \| `classification` \| `retrieval` \| `planning` \| `response` |
| `input` | JSONB NULL | |
| `output` | JSONB NULL | |
| `latency_ms` | INTEGER NULL | |
| `started_at` | TIMESTAMPTZ NOT NULL | |
| `completed_at` | TIMESTAMPTZ NULL | |

`UNIQUE (run_id, sequence)` is a correctness constraint, not tidiness: the
timeline the dashboard renders is ordered by `sequence`, and a duplicate would
silently reorder a trace. The sequence is allocated inside the same transaction
as the step insert: the run's highest sequence is read with
`ORDER BY sequence DESC LIMIT 1 ... FOR UPDATE` and one is added to it. (Not
`SELECT COALESCE(MAX(sequence),0)+1` — Postgres refuses `FOR UPDATE` on an
aggregate query, so the highest value is read as a row rather than computed.)
On Postgres that select takes a row lock; on SQLite, which has no such clause,
SQLAlchemy's dialect renders the lock away, and the store runs on both
(ADR-0004).

Two writers cannot end up with the same number, and it is worth being precise
about which mechanism does that, because they are not the same thing:

- **The claim path** is the reason there is only ever one writer per run: the
  claim query takes the run row with `FOR UPDATE SKIP LOCKED`.
- **The `UNIQUE (run_id, sequence)` constraint** is the backstop. A writer that
  got through anyway is *refused* by the database rather than allowed to
  reorder a trace.
- **The row lock on the step** is not what prevents the duplicate. A step row is
  immutable, so nothing else contends for it, and on a run with no steps yet the
  locking select matches no row at all and locks nothing.

`tests/unit/test_step_sequence_allocation.py` asserts each of those claims: that
the statement really carries `FOR UPDATE` on Postgres and really runs without it
on SQLite, and that two writers racing for one run leave the table with one step
per sequence number.

**The five step types are the whole vocabulary.** `state_change`,
`classification`, `retrieval`, `planning` and `response` are every
`step_type` a row in a live database can carry. An earlier revision of this
table also listed `tool_call`, `approval` and `audit`; those three are
**reserved by the schema and have no producer** — no code path in `src/`
records a step of any of them, because a tool call, an approval and an audit
event each have a table of their own below rather than a row here. The column
is `TEXT` rather than a PG enum, so a reserved name costs nothing to leave in
the schema and nothing to leave out of the data; the point of listing only the
five is that a reader querying `agent_steps` for `step_type = 'tool_call'`
gets no rows and should know that before they go looking for the bug.

**A note on the `input`/`output` JSONB columns.** In Phase 1 they hold structured
per-step metadata — `{provider, model}`, `{category, confidence}`,
`{count, document_slugs}`, `{tool_name, done}`, `{from_status, to_status, at}` — plus,
on the `response` step, the model's **composed reply** in full. That reply is a
deliberate debugging affordance with a real privacy cost, recorded in
[limitations.md](limitations.md) and slated for redaction in Phase 3; it is what
makes the eval suite and the trace view possible at all. `STORE_FULL_PROMPTS=false`
withholds it. The model *prompt* is not stored: no step writes prompt text, which
is worth stating because the setting is named for it and older revisions of this
file claimed otherwise.

### `tool_calls`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `run_id` | UUID FK → agent_runs, indexed | |
| `step_id` | UUID FK → agent_steps NULL | **Always NULL.** A tool call is proposed *inside* a step, but the step's row is written when the stage ends, so the runtime does not have its id at the moment the call is recorded. The column is the seam for that link and nothing writes to it yet. |
| `tool_name` | VARCHAR NOT NULL | |
| `arguments` | JSONB NOT NULL | **Immutable once an `ApprovalRequest` references this call.** |
| `permission` | VARCHAR NOT NULL | Snapshotted at call time from the registry |
| `status` | VARCHAR NOT NULL | `proposed` \| `rejected` \| `awaiting_approval` \| `executed` \| `failed` |
| `result` | JSONB NULL | |
| `error` | TEXT NULL | |
| `rejection_reason` | TEXT NULL | Which gate rejected it |
| `idempotency_key` | VARCHAR NULL, indexed | `UNIQUE (idempotency_key) WHERE idempotency_key IS NOT NULL AND status='executed'` |
| `latency_ms` | INTEGER NULL | |
| `created_at` | TIMESTAMPTZ NOT NULL | |
| `completed_at` | TIMESTAMPTZ NULL | |

Two things worth stating:

- **`permission` is snapshotted onto the row.** If Phase 2 reclassifies
  `issues.create` from `SAFE_WRITE` to `HIGH_RISK_WRITE`, historical rows must
  keep describing what actually happened at the time. The registry is the source
  of truth for *new* calls; the column is the record of *past* calls.
- **The partial unique index on `idempotency_key`** is the database-level backstop
  against a duplicate refund. Even if every layer of application code were wrong,
  the second insert with the same key fails. Belt, braces, and a database.

### `approval_requests`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `run_id` | UUID FK → agent_runs, indexed | |
| `tool_call_id` | UUID FK → tool_calls, **unique** | One approval per call, enforced |
| `status` | VARCHAR NOT NULL | `pending` \| `approved` \| `rejected` \| `expired`. All four are reachable: the worker sweeps a pending approval older than `APPROVAL_TTL_SECONDS` to `expired` |
| `reason` | TEXT NOT NULL | The model's stated justification, shown to the human |
| `risk_explanation` | TEXT NOT NULL | Deterministic, generated from the policy engine — *not* from the model |
| `arguments_snapshot` | JSONB NOT NULL | Copy of the arguments as shown to the approver |
| `created_at` | TIMESTAMPTZ NOT NULL | |
| `decided_at` | TIMESTAMPTZ NULL | |
| `decided_by` | VARCHAR NULL | Operator identity — **assigned by the server** from the bearer token, never by the client |
| `note` | TEXT NULL | The operator's own remark on the decision, e.g. `"Confirmed in billing console."` Free text, nullable, set only on a decision |
| `context` | JSONB NULL | Supplementary display context for the approver, e.g. `{"company": "ACME", "ticket_subject": "…"}`. Assembled when the run parks. `NULL` when there is nothing to show |

`risk_explanation` being deterministic is the subtle part. The `reason` field is
what the *model* says it is doing, and it is untrusted — an injection could make
it read "routine maintenance, no approval needed". The `risk_explanation` is
generated by `policies.py` from the tool's static permission and the arguments
("This moves 129.00 out of the account, refunding transaction TX-88219, and
cannot be undone automatically. The amount is in the currency the transaction was
charged in."). The approver's UI shows both, labelled differently.

**`decided_by` and `note` are the two halves of a decision, and they come from
two different places.** `decided_by` is the server's answer to *who* acted: it
is resolved from the bearer token and assigned by the server, so a client
cannot name itself and cannot forge the identity of the operator its token
represents. In Phase 1 that value is the single literal `"operator"`, because
Phase 1 has one token and one role — which is a statement about Phase 1's
deployment, not about the mechanism (see
[limitations.md](limitations.md) §1).

`note` is the operator's answer to *why*: free text the person typed, stored
verbatim. It is nullable because a decision does not require one, and it is a
column on the row rather than a field on the decision response because it is
the operator's annotation on the record, not part of the state transition. It
is not audited, not summarised, and not read back into the ledger.

**`expired` is a status that records a *non*-decision.** The worker sweeps a
pending approval older than `APPROVAL_TTL_SECONDS` to `expired`, and doing so
leaves `decided_at` and `decided_by` NULL: nobody decided it, the clock ran
out. The run stays in `waiting_approval` — no decision was made, so no money
moved and there is nothing for the worker to execute. An expired approval
therefore differs from an approved one in every field that records *who did
what*, and a reader who saw a decider on an expired row would be reading
something the system never wrote.

**`context` is a column, and it is deliberately not inside
`arguments_snapshot`.** The approval response carries a `context` field so an
approver can see the surrounding situation without opening a second tab. It is
assembled when the run parks, from data the run has already persisted — the
company from a `crm.get_customer` result the run has already executed, the
ticket's subject from the ticket itself — so it triggers no tool calls and
cannot change what the run has done.

It could not have lived in `arguments_snapshot`, and the reason is worth
recording. The snapshot is filled from the model's tool arguments, which gate 1
validates with `extra="forbid"`, so it cannot carry a key the model did not
propose — a `_context` key inside it could never have been written by
anything. Putting display data there would also give the snapshot a second
meaning, and the snapshot is one thing: what the human approved and what will
be executed. It is immutable, and the whole approval gate rests on that
(`docs/tool-permissions.md` §3.2). So the context gets a column of its own and
the snapshot stays exactly one thing.

Both of the context's fields are optional, so the column is nullable and a
missing context is `NULL` rather than `{}` — `{}` would say "there is context
and it is empty", which is a different claim from "there is no context". A run
that never fetched a customer still parks a valid approval, with no company to
show.

### `knowledge_documents`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `title` | TEXT NOT NULL | |
| `source` | TEXT NOT NULL, unique | Repo-relative path, e.g. `refund-policy.md` |
| `content` | TEXT NOT NULL | Full Markdown |
| `doc_metadata` | JSONB NOT NULL | Front-matter (owner, effective date, tags) |
| `content_hash` | VARCHAR NOT NULL | sha256; re-indexing compares this |
| `indexed_at` | TIMESTAMPTZ NOT NULL | |

Named `doc_metadata` rather than `metadata` because `metadata` is a reserved
attribute on SQLAlchemy declarative classes; the column name in the database is
still `metadata`.

### `knowledge_chunks`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `document_id` | UUID FK → knowledge_documents, indexed | |
| `ordinal` | INTEGER NOT NULL | `UNIQUE (document_id, ordinal)` |
| `anchor` | TEXT NOT NULL | Slug of the nearest heading, e.g. `refund-limits` |
| `heading_path` | TEXT NOT NULL | e.g. `Refund Policy > Limits` |
| `content` | TEXT NOT NULL | |
| `token_count` | INTEGER NOT NULL | |
| `embedding` | `vector(1536)` | pgvector. The width is **frozen at 1536** by migration `0001`; `EMBEDDING_DIM` must be 1536 — see below. |

**`EMBEDDING_DIM` is not a knob.** The migration creates the column as
`vector(1536)` and the width there is a frozen literal, deliberately: a
migration must be a fixed point of history, not a function of the deploying
environment. The setting still reaches the ORM and the embedder, so on SQLite
a different width would "work" — and on Postgres, where the migration is the
authority, it cannot. `PgVectorStore` compares the width it is handed against
the column's and raises `DimensionMismatch` at construction if they differ, so
a deployment that set `EMBEDDING_DIM` to anything but 1536 fails while it is
being wired, not when it inserts its first vector. Changing the width means a
new migration that alters the column, which Phase 1 does not have; the four
places that declare it (`0001`'s literal, `models._EMBEDDING_DIM`,
`PgVectorStore.DEFAULT_DIM` and the setting) are asserted to agree by
`tests/integration/test_migration_schema.py`.

```sql
CREATE INDEX ix_knowledge_chunks_embedding
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
```

HNSW rather than IVFFlat: IVFFlat needs a training pass and a row count estimate
to build useful lists, and with 17 documents and 98 chunks HNSW is
both simpler and faster. Recall is not the Phase 1 bottleneck.

### `citations`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `run_id` | UUID FK → agent_runs, indexed | |
| `document_id` | UUID FK → knowledge_documents | |
| `chunk_id` | UUID FK → knowledge_chunks | |
| `score` | FLOAT NOT NULL | Cosine similarity. An 8-byte float, not a 4-byte `REAL`. |
| `rank` | INTEGER NOT NULL | Position in the retriever's result list |

A row per cited chunk, not a JSON blob on the run. The UI's "Sources" panel is a
join, and the eval's Recall@K metric is a query over `rank` — both of which a
blob would force into application code.

**How `chunk_id` is resolved when a citation is written.** The retrieval hit
knows exactly which chunk it returned, and the store has to find that chunk's
row. It resolves the document by its `source` and the chunk by the hit's
**ordinal** — the pair `UNIQUE (document_id, ordinal)` names, and the pair both
vector stores derive the chunk id from. The ordinal is not recoverable from the
citation's `"{source}#{anchor}"` label: the anchor is a *heading* slug, and a
heading longer than the per-chunk token ceiling is split into several chunks
that all carry that one anchor, so `(document_id, anchor)` can match more than
one chunk row. A record with no ordinal falls back to the anchor and then to
the lowest matching ordinal — deterministic, but not necessarily the chunk that
was retrieved. The label the API renders (`docs/api-contract.md` §3) carries the
anchor only; the ordinal is a write-side field and is not in the response.

### `audit_events`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `run_id` | UUID FK → agent_runs NULL, indexed | Nullable: auth and reindex events have no run |
| `event_type` | VARCHAR NOT NULL, indexed | See below |
| `actor` | VARCHAR NOT NULL | `runtime` \| `worker` |
| `payload` | JSONB NOT NULL | |
| `created_at` | TIMESTAMPTZ NOT NULL, indexed | |

Event types, Phase 1:

```
tool_executed    tool_failed    tool_rejected
approval_requested    run_failed    model_called    retrieval_abstained
```

Append-only by convention: no UPDATE or DELETE path exists in the repository
layer, and `tests/unit/test_audit_is_append_only.py` guards that three ways —
it reads the whole of `src/` with `ast` and fails if any statement mutates the
table, it runs the append and the read paths against a real database with an
SQL listener attached and fails if the SQL that reaches the driver rewrites a
row, and it asserts the public interface offers exactly one append
(`record_audit`) and one read (`list_audit_events`).

**The seven event types are the whole vocabulary.** An earlier revision of this
table listed fourteen, and every one of the other eight — `run_created`,
`state_changed`, `retrieval_performed`, `tool_proposed`, `approval_granted`,
`approval_rejected`, `run_completed` and `knowledge_reindexed` — is **reserved
by the schema and has no producer**: no code path in `src/` writes an event of
any of them.

`retrieval_performed` is the one worth singling out, because it is not merely
unwritten but *misnamed*. The event that exists is `retrieval_abstained`, and
it is written when the agent **declines to act** on the evidence it retrieved —
so a reader who went looking for `retrieval_performed` in the ledger would
find neither the retrieval nor its abstention, and would conclude the run never
retrieved anything.

**`actor` is `runtime` or `worker`, and nothing else.** Every event the agent
loop writes names `runtime`; the one the worker's own loop writes — a run whose
model call failed, so the agent loop never got to record it — names `worker`.
An earlier revision of this table listed `agent`, `operator:<id>` and
`system`, none of which any code path produces: the constant `runtime` is
there precisely so that a writer that is *not* the runtime is distinguishable
from it by reading one column, and that writer has not been written yet.

## 3. Why both `agent_steps` and `audit_events`

They look redundant. They are not, and the difference is the point:

- `agent_steps` is the **execution timeline** — the ordered narrative the
  dashboard renders. It answers "what happened, in what order, how long did it
  take". It is a UI concern and it is allowed to be edited as the UI evolves.
- `audit_events` is the **compliance record** — an append-only ledger of
  security-relevant facts, keyed by `actor`, never rewritten. It answers "who or
  what did this, and was it authorised".

Collapsing them would mean either the dashboard reads a compliance ledger (and
any UI-driven schema change becomes an audit-format change), or the ledger
becomes a rendering concern (and its append-only property is negotiable). The
duplication is a few hundred bytes per run.

## 4. Status value enums

Stored as `VARCHAR` with a Python `Enum` on the model side, not as PG `ENUM` types.
Rationale: PG enums require `ALTER TYPE ... ADD VALUE` migrations that cannot run
inside a transaction in older versions, and a new `RunStatus` in Phase 2 should
not need a database type migration. Validation lives in Pydantic and SQLAlchemy.
The trade is that the database will accept a bogus string if application code is
wrong — accepted, because the four security invariants are checked by queries
over these columns and a bogus value would fail those checks loudly.

## 5. Migrations

Alembic, one migration per milestone, never squashed during Phase 1. The
migration history is part of the review surface: a reviewer should be able to see
that the approval table and the idempotency index both arrived in M1 — they are
in the same revision, `0001`, because the index is what makes a duplicate
refund impossible at the database level and the approval table is what makes a
refund possible at all.

`0003` adds the two columns the human half of the approval gate was missing:
`note` (the operator's remark) and `context` (the approver's display
context). Both are nullable and both default to `NULL`, because `''` is not
"no remark" and `{}` is not "no context" — and because every row written
before the migration already has them that way.

`CREATE EXTENSION IF NOT EXISTS vector` is the first operation of the initial
migration. The `verify` CI job checks that `alembic upgrade head` succeeds
against a real `pgvector/pgvector:pg16` service container.

## 6. Test configuration

Tests run on **SQLite in-memory** by default (see ADR-0004). The consequences,
stated so they are not discovered later:

| Feature | SQLite | Postgres |
|---|---|---|
| `FOR UPDATE SKIP LOCKED` | not supported → single-threaded claim only | supported |
| `JSONB` | maps to `JSON` | native |
| `vector(1536)` | not supported → cosine in Python over a small fixture set | pgvector |
| Partial indexes | supported | supported |
| `gen_random_uuid()` | emulated in the model default | server default |

The worker's concurrency tests live in
`tests/integration/test_worker_claim_postgres.py`, and are marked
`@pytest.mark.postgres`: they run only in CI against the service container,
which is a real gap in local coverage and is why that marker is reported in the
test summary rather than silently skipped. The `FOR UPDATE SKIP LOCKED` clause
is the Postgres-only half; the **lease** half — that a run whose lease is held
is withheld from a second claimant, and that thirty non-overlapping claim
pairs never share a run — is covered locally on SQLite by
`tests/integration/test_worker_claim_lease.py`.

This paragraph used to name `test_two_workers_do_not_claim_the_same_run`, a
test that does not exist; `docs/limitations.md` had already recorded that
correction and this file had not been updated with it.

## 7. What is deliberately absent

| Absent | Reason it is absent in Phase 1 |
|---|---|
| `organization_id` / `workspace_id` | There is one tenant. A tenancy column with one value is a belief, not a design. Phase 2 adds it with the real requirements in hand. |
| `users` / RBAC tables | One operator token. A role table with one role is the same mistake. |
| `tool_definitions` table | Permissions are static code (see tool-permissions.md §2.1). Putting them in a table creates the widening path this design exists to close. |
| Soft-delete columns | Nothing in Phase 1 deletes. When it does, it will be a documented retention policy, not an `is_deleted` flag. |
| Cost/metadata rollups | `model_called` audit events carry the raw numbers; a rollup table is a reporting concern for when there is a report. |
