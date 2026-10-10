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
| `status` | TEXT NOT NULL, indexed | `RunStatus` value. Indexed because the worker's claim query filters on it. |
| `model_provider` | TEXT NOT NULL | `fake` \| `openai` \| `anthropic` |
| `model_name` | TEXT NOT NULL | e.g. `claude-sonnet-5-5` |
| `started_at` | TIMESTAMPTZ NULL | Set on first transition out of `RECEIVED` |
| `completed_at` | TIMESTAMPTZ NULL | Set on entry to `COMPLETED`/`FAILED` |
| `failure_reason` | TEXT NULL | Machine-stable token; see state-machine doc §3 |
| `created_at` | TIMESTAMPTZ NOT NULL | |

Index for the worker:

```sql
CREATE INDEX ix_agent_runs_claimable
    ON agent_runs (created_at)
    WHERE status IN ('received','classifying','retrieving',
                     'planning','executing','responding');
```

A partial index on the claim predicate. It stays small as the table grows,
because completed and failed runs — which will be the overwhelming majority —
are not in it.

### `agent_steps`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `run_id` | UUID FK → agent_runs, indexed | |
| `sequence` | INTEGER NOT NULL | Monotonic within a run. `UNIQUE (run_id, sequence)`. |
| `step_type` | TEXT NOT NULL | `state_change` \| `classification` \| `retrieval` \| `planning` \| `tool_call` \| `approval` \| `response` \| `audit` |
| `input` | JSONB NULL | |
| `output` | JSONB NULL | |
| `latency_ms` | INTEGER NULL | |
| `started_at` | TIMESTAMPTZ NOT NULL | |
| `completed_at` | TIMESTAMPTZ NULL | |

`UNIQUE (run_id, sequence)` is a correctness constraint, not tidiness: the
timeline the dashboard renders is ordered by `sequence`, and a duplicate would
silently reorder a trace. The sequence is allocated inside the same transaction
as the step insert (`SELECT COALESCE(MAX(sequence),0)+1 ... FOR UPDATE`), so two
writers on one run cannot race — and there is only ever one writer per run
anyway, because the claim query takes the row with `FOR UPDATE SKIP LOCKED`.

**A note on the `input`/`output` JSONB columns.** In Phase 1 they hold structured
per-step metadata — `{provider, model}`, `{category, confidence}`,
`{count, document_slugs}`, `{tool_name, done}`, `{from_status, to_status}` — plus,
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
| `step_id` | UUID FK → agent_steps NULL | |
| `tool_name` | TEXT NOT NULL | |
| `arguments` | JSONB NOT NULL | **Immutable once an `ApprovalRequest` references this call.** |
| `permission` | TEXT NOT NULL | Snapshotted at call time from the registry |
| `status` | TEXT NOT NULL | `proposed` \| `rejected` \| `awaiting_approval` \| `executed` \| `failed` |
| `result` | JSONB NULL | |
| `error` | TEXT NULL | |
| `rejection_reason` | TEXT NULL | Which gate rejected it |
| `idempotency_key` | TEXT NULL, indexed | `UNIQUE (idempotency_key) WHERE idempotency_key IS NOT NULL AND status='executed'` |
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
| `status` | TEXT NOT NULL | `pending` \| `approved` \| `rejected` \| `expired` |
| `reason` | TEXT NOT NULL | The model's stated justification, shown to the human |
| `risk_explanation` | TEXT NOT NULL | Deterministic, generated from the policy engine — *not* from the model |
| `arguments_snapshot` | JSONB NOT NULL | Copy of the arguments as shown to the approver |
| `created_at` | TIMESTAMPTZ NOT NULL | |
| `decided_at` | TIMESTAMPTZ NULL | |
| `decided_by` | TEXT NULL | Operator identity (local auth in Phase 1) |

`risk_explanation` being deterministic is the subtle part. The `reason` field is
what the *model* says it is doing, and it is untrusted — an injection could make
it read "routine maintenance, no approval needed". The `risk_explanation` is
generated by `policies.py` from the tool's static permission and the arguments
("This will move $129.00 and cannot be undone automatically."). The approver's UI
shows both, labelled differently.

### `knowledge_documents`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `title` | TEXT NOT NULL | |
| `source` | TEXT NOT NULL, unique | Repo-relative path, e.g. `refund-policy.md` |
| `content` | TEXT NOT NULL | Full Markdown |
| `doc_metadata` | JSONB NOT NULL | Front-matter (owner, effective date, tags) |
| `content_hash` | TEXT NOT NULL | sha256; re-indexing compares this |
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
| `embedding` | `vector(1536)` | pgvector; dimension from `EMBEDDING_DIM` setting |

```sql
CREATE INDEX ix_knowledge_chunks_embedding
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
```

HNSW rather than IVFFlat: IVFFlat needs a training pass and a row count estimate
to build useful lists, and with 30 documents and a few hundred chunks HNSW is
both simpler and faster. Recall is not the Phase 1 bottleneck.

### `citations`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `run_id` | UUID FK → agent_runs, indexed | |
| `document_id` | UUID FK → knowledge_documents | |
| `chunk_id` | UUID FK → knowledge_chunks | |
| `score` | REAL NOT NULL | Cosine similarity |
| `rank` | INTEGER NOT NULL | Position in the retriever's result list |

A row per cited chunk, not a JSON blob on the run. The UI's "Sources" panel is a
join, and the eval's Recall@K metric is a query over `rank` — both of which a
blob would force into application code.

### `audit_events`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `run_id` | UUID FK → agent_runs NULL, indexed | Nullable: auth and reindex events have no run |
| `event_type` | TEXT NOT NULL, indexed | See below |
| `actor` | TEXT NOT NULL | `agent` \| `operator:<id>` \| `system` |
| `payload` | JSONB NOT NULL | |
| `created_at` | TIMESTAMPTZ NOT NULL, indexed | |

Event types, Phase 1:

```
run_created    state_changed   model_called     retrieval_performed
tool_proposed  tool_rejected   tool_executed    tool_failed
approval_requested  approval_granted  approval_rejected
run_completed  run_failed      knowledge_reindexed
```

Append-only by convention: no UPDATE or DELETE path exists in the repository
layer, and `test_audit_is_append_only` asserts the repository exposes no
mutating method.

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

Stored as `TEXT` with a Python `Enum` on the model side, not as PG `ENUM` types.
Rationale: PG enums require `ALTER TYPE ... ADD VALUE` migrations that cannot run
inside a transaction in older versions, and a new `RunStatus` in Phase 2 should
not need a database type migration. Validation lives in Pydantic and SQLAlchemy.
The trade is that the database will accept a bogus string if application code is
wrong — accepted, because the four security invariants are checked by queries
over these columns and a bogus value would fail those checks loudly.

## 5. Migrations

Alembic, one migration per milestone, never squashed during Phase 1. The
migration history is part of the review surface: a reviewer should be able to see
that the approval table arrived in M1 and the idempotency index in M3.

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

The worker's concurrency test (`test_two_workers_do_not_claim_the_same_run`) is
marked `@pytest.mark.postgres` and runs only in CI against the service container.
It is skipped locally, which is a real gap in local coverage and is why that
marker is reported in the test summary rather than silently skipped.

## 7. What is deliberately absent

| Absent | Reason it is absent in Phase 1 |
|---|---|
| `organization_id` / `workspace_id` | There is one tenant. A tenancy column with one value is a belief, not a design. Phase 2 adds it with the real requirements in hand. |
| `users` / RBAC tables | One operator token. A role table with one role is the same mistake. |
| `tool_definitions` table | Permissions are static code (see tool-permissions.md §2.1). Putting them in a table creates the widening path this design exists to close. |
| Soft-delete columns | Nothing in Phase 1 deletes. When it does, it will be a documented retention policy, not an `is_deleted` flag. |
| Cost/metadata rollups | `model_called` audit events carry the raw numbers; a rollup table is a reporting concern for when there is a report. |
