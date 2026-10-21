# API Contract

FastAPI, JSON only. No GraphQL. Every response body is a Pydantic model —
the OpenAPI document at `/openapi.json` is generated from them and is treated as
part of the contract, not as documentation that drifts.

Base path: `/api`. Errors use a single shape (§6).

---

## 1. Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/tickets` | Create a ticket and enqueue a run |
| `GET` | `/api/tickets` | List tickets |
| `GET` | `/api/tickets/{ticket_id}` | One ticket with its runs |
| `POST` | `/api/runs` | Enqueue a run for an existing ticket |
| `GET` | `/api/runs` | List runs (filterable by status) |
| `GET` | `/api/runs/{run_id}` | Run detail: status, steps, tool calls, citations, claim lease |
| `GET` | `/api/runs/{run_id}/trace` | The ordered execution timeline only |
| `GET` | `/api/runs/{run_id}/audit` | The run's audit ledger (append-only events) |
| `GET` | `/api/approvals` | List approvals (default: `pending`) |
| `GET` | `/api/approvals/{approval_id}` | One approval, with the arguments as shown |
| `POST` | `/api/approvals/{approval_id}/approve` | Approve; resumes the run |
| `POST` | `/api/approvals/{approval_id}/reject` | Reject; run produces an escalation reply |
| `GET` | `/api/knowledge` | List indexed documents and chunk counts |
| `POST` | `/api/knowledge/reindex` | Re-ingest `knowledge/` |
| `GET` | `/health` | Liveness — process is up |
| `GET` | `/ready` | Readiness — DB reachable, migrations at head |

No endpoint returns a model's raw text as a top-level field. Model output is
always embedded in a validated structure.

## 2. `POST /api/tickets`

The only write that starts work.

```jsonc
// request
{
  "subject": "Charged twice for invoice INV-2026-384",
  "body": "We were charged twice for invoice INV-2026-384. Please investigate and fix it.",
  "customer_email": "billing@acme.example",
  "external_id": "ZD-55102"          // optional
}

// 201 response
{
  "ticket": {
    "id": "0f3a…",
    "external_id": "ZD-55102",
    "subject": "Charged twice for invoice INV-2026-384",
    "body": "We were charged twice…",
    "customer_email": "billing@acme.example",
    "created_at": "2026-10-05T10:42:03Z"
  },
  "run": {
    "id": "7c1b…",
    "status": "received",
    "created_at": "2026-10-05T10:42:03Z"
  }
}
```

The handler inserts the ticket and the run in **one transaction** and returns
immediately. It does not call a model and does not call the worker. The run is
picked up by the worker's next poll — a latency of up to `WORKER_POLL_INTERVAL`
(1s default), which is why the response says `status: "received"` and the client
has to ask again later rather than expecting a completed workflow. Phase 1 has
no websocket, and the dashboard polls nothing: its Run Detail screen is a
Server Component with no client-side timer, so an operator refreshes the page
to see a run move.

**Both rows land, or neither does.** The two inserts share one transaction
boundary, so there is no window in which a ticket exists without its run:

- **If the run insert fails** — a constraint violation, a dropped connection, a
  timeout — the transaction rolls back and the client gets `500
  internal_error`. **No ticket row survives.** A ticket with no run is a
  permanent orphan: no worker would ever claim it, nothing would ever
  investigate it, and the client would have no run id to poll, so the failure
  would be silent rather than loud. The same holds in the other order — a run
  with no ticket cannot exist either.
- **`201` means committed.** The response is written only after the transaction
  has committed, so a `201` asserts that both rows are durable and the returned
  `ticket.id` and `run.id` are real.
- **Retrying after a `500` is safe.** A retry with the same `external_id`
  either creates a fresh ticket-and-run pair, or is refused with `409
  ticket_exists` (§9) because the first attempt did commit and only its
  response was lost. It never leaves a ticket behind without a run.

### 2.1 `GET /api/tickets`

The ticket list, newest first. The body is the same envelope every list
endpoint returns: `items` is the page, `total` is the size of the whole
matching set (see §10 for exactly what `total` counts).

```jsonc
{
  "items": [
    {
      "id": "0f3a…",
      "external_id": "ZD-55102",
      "subject": "Charged twice for invoice INV-2026-384",
      "body": "We were charged twice…",
      "customer_email": "billing@acme.example",
      "created_at": "2026-10-05T10:42:03Z"
    },
    {
      "id": "1b7c…",
      "external_id": null,
      "subject": "Subscription charged after cancellation",
      "body": "We cancelled on 2026-09-30 and were…",
      "customer_email": "ops@globex.example",
      "created_at": "2026-10-05T09:17:44Z"
    }
  ],
  "total": 2
}
```

`items` holds one ticket per entry, in the same shape as `ticket` in the create
response (§2). `total` is `2` here because only two tickets match; with more
than one page of results `items` would be short while `total` would not.

### 2.2 `GET /api/tickets/{ticket_id}`

One ticket with its runs. No request body.

```jsonc
{
  "ticket": {
    "id": "0f3a…",
    "external_id": "ZD-55102",
    "subject": "Charged twice for invoice INV-2026-384",
    "body": "We were charged twice…",
    "customer_email": "billing@acme.example",
    "created_at": "2026-10-05T10:42:03Z"
  },
  "runs": [
    { "id": "7c1b…", "status": "waiting_approval", "created_at": "2026-10-05T10:42:03Z" }
  ]
}
```

`ticket` is the shape §2 creates and §2.1 lists: `id` (UUID),
`external_id` (string or `null` — a ticket created without one), `subject`,
`body`, `customer_email`, `created_at` (timestamp).

`runs` is a list of run *references* — `id`, `status`, `created_at` — and
nothing else. It is not the summary of §3.1 and not the detail of §3: it says
which runs this ticket has, and says nothing about their steps, tool calls,
citations or claim lease. A store that cannot list a ticket's runs answers
with an empty list; the endpoint does not fail for it.

Errors: `401` without a valid bearer token (§8). `404 ticket_not_found` when
there is no such ticket. `422 schema_invalid` when `ticket_id` is not a UUID.

## 3. `GET /api/runs/{run_id}`

The payload the dashboard's run-detail screen renders.

```jsonc
{
  "id": "7c1b…",
  "ticket_id": "0f3a…",
  "status": "waiting_approval",
  "model_provider": "anthropic",
  "model_name": "claude-sonnet-5-5",
  "started_at": "2026-10-05T10:42:03Z",
  "completed_at": null,
  "failure_reason": null,
  "created_at": "2026-10-05T10:42:03Z",

  // Who is holding the run. Present on every run. This one is parked, and
  // parking releases the claim, so it reads `unclaimed` -- see the field note.
  "claim_lease": {
    "state": "unclaimed",
    "claimed_by": null,
    "claimed_until": null
  },

  "steps": [
    { "sequence": 1, "step_type": "state_change",
      "output": {"from_status": "received", "to_status": "classifying",
                 "at": "2026-10-05T10:42:03.481902+00:00"},
      "latency_ms": 0, "started_at": "…" },
    { "sequence": 2, "step_type": "classification",
      "output": {"category": "duplicate_charge", "confidence": 0.93},
      "latency_ms": 612, "started_at": "…" }
    // …
  ],

  "tool_calls": [
    { "id": "aa01…", "tool_name": "crm.get_customer",
      "arguments": {"customer_email": "billing@acme.example"},
      "permission": "read", "status": "executed",
      "result": {"customer_id": "CUS-1001", "company": "ACME", "plan": "enterprise"},
      "latency_ms": 842, "idempotency_key": null, "error": null },

    { "id": "aa07…", "tool_name": "billing.issue_refund",
      "arguments": {"transaction_id": "TX-88219", "amount": 129.00,
                    "reason": ""},
      "permission": "high_risk_write", "status": "awaiting_approval",
      "result": null, "latency_ms": null,
      "idempotency_key": "refund:7c1b…:TX-88219", "error": null }
  ],

  "citations": [
    { "document": "refund-policy.md", "chunk": "refund-policy.md#refund-limits",
      "score": 0.83, "rank": 1 },
    { "document": "duplicate-charge-sop.md", "chunk": "duplicate-charge-sop.md#detection",
      "score": 0.79, "rank": 2 }
  ],

  "pending_approval": {
    "id": "b7e2…",
    "tool_call_id": "aa07…",
    "status": "pending",
    "reason": "Verified duplicate charge on TX-88219; proposing full refund of $129.00.",
    "risk_explanation": "This moves 129.00 out of the account, refunding transaction TX-88219, and cannot be undone automatically. The amount is in the currency the transaction was charged in.",
    "arguments_snapshot": {"transaction_id": "TX-88219", "amount": 129.00,
                           "reason": ""},
    "created_at": "…"
  },

  "customer_reply": null,

  // The subset of tool_calls with status "failed" -- empty above because the
  // run has not executed anything yet. See the field note below.
  "failed_tool_calls": []
}
```

Field notes:

- `citations[].chunk` is `"{document_slug}#{anchor}"` — a stable string a reader
  can grep for in `knowledge/`. The UI links it. The anchor is the slug of the
  *heading* the chunk sits under, so when one heading is longer than the
  per-chunk token ceiling and is split into several chunks, those chunks share
  an anchor: the string names the section, not always one chunk within it. The
  citation row itself always points at exactly one chunk — the store resolves
  it by the chunk's ordinal, not by the anchor (`docs/data-model.md` §2). The
  ordinal is a write-side field and is not part of this response.
- `pending_approval` is present only when the run is parked. It is the only
  place in the API where an approver-visible payload is nested inside a run.
- **`customer_reply` is `null` until the run reaches `COMPLETED`, and then it
  carries two non-null fields:**

  ```jsonc
  {
    "body": "We have issued a full refund of $129.00 for invoice INV-2026-384…",
    "escalated": false
  }
  ```

  `body` is the reply text the run composed. `escalated` distinguishes "we
  could not complete this, a human is on it" from "here is your refund": an
  escalated run says the customer is being handled by a person, and the field
  is what tells the dashboard which of the two it is looking at. The run
  detail shows `null` above because the run shown there has not completed.
- **`steps[].output` has a different shape per `step_type`, and the shapes are
  fixed by the writers.** A `state_change` step carries the transition it
  accompanies: `{from_status, to_status, at}`. A `classification` step carries
  `{category, confidence}` and nothing else. The other three step types carry
  `{count, document_slugs}` (`retrieval`), `{tool_name, done}` (`planning`) and
  `{escalated, chars, body}` (`response`).

  There is no `reasoning_summary` and no `to`: no code path writes either, and
  an earlier revision of this document showed both. The trace's own `label` and
  `detail` (§4) are assembled server-side from these fields, which is why the
  shape matters there too.
- **`tool_calls[].arguments` is the *validated* call, and it is not the whole
  proposal.** Gate 1 parses the model's arguments against the tool's schema and
  dumps the result with `exclude_none=True`, so the dictionary holds every
  field that schema declares, except any the schema excludes — no more and no
  fewer. For `billing.issue_refund` those are `transaction_id`, `amount` and
  `reason`; `reason` is a non-`None` default, so it is *always* present even
  when the model named none.

  There is no `currency` among them, and that is the point rather than an
  omission. The currency a refund moves is the currency the transaction was
  charged in — a row only the billing server can read — and a currency in a
  proposal is the model's guess at a fact no gate can know, so it never reaches
  the approver, the snapshot or the dispatch (`docs/tool-permissions.md` §3.2).

  The `idempotency_key` is deliberately **not** one of them: a model may not
  choose it, because the policy engine derives it from the run and the
  transaction (`docs/tool-permissions.md` §4). It is stored in its own column,
  which is the `idempotency_key` field beside `arguments` on the same row.
- **`claim_lease`** says whether a worker is holding this run *right now*, which
  is not the same question as `status`. `state` is `unclaimed` (nothing has ever
  claimed it), `held` (a lease whose deadline is in the future, so no second
  worker can take the row) or `expired` (the holder died or stalled — nobody is
  driving it and it is claimable again). `claimed_by` is the `WORKER_ID` holding
  it and `claimed_until` is the deadline.

  It is a field rather than a status because the claim deliberately does **not**
  move the run: `claim_next` takes the row and writes the lease in the same
  transaction without touching `status`, and `docs/agent-state-machine.md` §2 is
  a specification that has no state for it. So before this field existed, a run
  that was claimed and then abandoned and a run nothing ever claimed were the
  same response body — both `received`, both with no steps. Measured against a
  healthy worker, the first went unclaimed for the full **300 s** of
  `WORKER_CLAIM_LEASE_SECONDS` while the dashboard told an operator the worker
  had not claimed it ([limitations.md](limitations.md) §2).

  `claimed_by` and `claimed_until` are `null` **together**, and `state` is
  `unclaimed` whenever they are: either nothing ever claimed the run, or it
  reached a state that releases the row (`waiting_approval`, `completed`,
  `failed`). A run that reaches a terminal state therefore never reports a
  holder, even if a worker held it a moment earlier.

  `state` is **computed by the server**, not derived by the client from
  `claimed_until` and a local clock — for the same reason the timeline's `label`
  and `detail` are assembled server-side (§4). One decision, made once, is the
  same decision in the dashboard, in the README's screenshot and in anybody
  else's client.
- **`failed_tool_calls`** repeats, as a summary, every entry of `tool_calls`
  whose `status` is `failed`. It exists because `tool_calls` alone did not
  survive contact with a real deployment.

  Observed live against the Compose stack: a run reached `completed` with
  `failure_reason: null`, five citations and a customer reply stating the
  refund had been issued, while its `billing.issue_refund` row sat at
  `status: "failed"` with `error: "invalid_state"`. The failed row *was* in
  `tool_calls` — one status string deep in a list a reader has to know to
  scan. A run status is not a sufficient signal for "money did not move", and
  `failure_reason` is `null` by design on a run that completed (§3 of
  `agent-state-machine.md`), so neither of the two fields a glance lands on
  could show it.

  This field is filtered **server-side** rather than in the client so the
  dashboard cannot forget to, and it includes *reads*: a refused read is
  reported here even though it does not change the run's status, because an
  operator deciding whether to trust a run needs the whole list. What it does
  not do is imply the run failed — see `failed_tool_calls` vs. `status` in
  `docs/agent-state-machine.md` §3.1.

### 3.1 `GET /api/runs`

The run list, newest first, optionally filtered by `status`. Same envelope as
§2.1: `items` is the page, `total` is the size of the whole matching set.

```jsonc
{
  "items": [
    {
      "id": "7c1b…",
      "ticket_id": "0f3a…",
      "status": "waiting_approval",
      "model_provider": "anthropic",
      "model_name": "claude-sonnet-5-5",
      "started_at": "2026-10-05T10:42:03Z",
      "completed_at": null,
      "failure_reason": null,
      "created_at": "2026-10-05T10:42:03Z"
    },
    {
      "id": "6d90…",
      "ticket_id": "1b7c…",
      "status": "completed",
      "model_provider": "anthropic",
      "model_name": "claude-sonnet-5-5",
      "started_at": "2026-10-05T09:17:45Z",
      "completed_at": "2026-10-05T09:18:31Z",
      "failure_reason": null,
      "created_at": "2026-10-05T09:17:44Z"
    }
  ],
  "total": 2
}
```

A listed run is a **summary**: no steps, no tool calls, no citations, and no
claim lease. `claim_lease` in particular is absent here — whether a worker is
holding a run *right now* is a question about a run in flight, and the detail
endpoint (§3) is where an operator goes to ask it. `status` is the run's own
state; `failure_reason` is `null` on a run that completed and carries the
machine-stable token on one that failed (§3 of
`docs/agent-state-machine.md`).

### 3.2 `POST /api/runs`

Enqueue a new run for a ticket that already exists. The body is one required
field, and it is required for the same reason every other body is: a request
that could name its own subject would let a caller write the document the
system reasons about.

```jsonc
// request
{ "ticket_id": "0f3a…" }

// 201
{ "id": "9b04…", "status": "received", "created_at": "2026-10-05T11:02:00Z" }
```

The body model forbids unknown fields, so a client that sends anything else is
told so rather than having it quietly ignored.

**It always creates a new run.** Re-running an investigation is a legitimate
action, so this does not deduplicate against an existing run for the same
ticket (§9).

Raises `404 ticket_not_found` when the ticket does not exist.

## 4. `GET /api/runs/{run_id}/trace`

The timeline, without tool arguments or model payloads — the cheap endpoint the
dashboard renders a run's progress from. A `steps` array of
`{sequence, step_type, label, detail, latency_ms, at}` where `label` is already
human-readable and `detail` is a short summary string. Rendering this requires no
client-side logic beyond ordering, which is the point: the timeline is assembled
server-side so the screenshot in the README and the dashboard show the same
thing.

The dashboard reads this endpoint server-side, once per page load, and does not
poll it: its Run Detail screen is a Server Component with no client-side timer,
so an operator refreshes the page to watch a run move.

## 4.1 `GET /api/runs/{run_id}/audit`

The run's audit ledger — the append-only record, not the execution timeline.

```jsonc
{
  "run_id": "7c1b…",
  "events": [
    {
      "id": "e91a…",
      "event_type": "model_called",
      "created_at": "2026-10-05T10:42:04Z",
      "actor": "runtime",
      "payload": { "provider": "anthropic", "model": "claude-sonnet-5-5",
                   "latency_ms": 612, "tokens_available": true,
                   "input_tokens": 980, "output_tokens": 41,
                   "estimated_cost_usd": 0.0031 }
    },
    {
      "id": "e91f…",
      "event_type": "approval_requested",
      "created_at": "2026-10-05T10:42:09Z",
      "actor": "runtime",
      "payload": { "tool_call_id": "aa07…", "tool_name": "billing.issue_refund",
                   "risk_explanation": "This moves 129.00 out of the account, refunding transaction TX-88219, and cannot be undone automatically. The amount is in the currency the transaction was charged in.",
                   "arguments": { "transaction_id": "TX-88219", "amount": 129.00,
                                  "reason": "" } }
    },
    {
      "id": "e927…",
      "event_type": "tool_failed",
      "created_at": "2026-10-05T10:42:31Z",
      "actor": "runtime",
      "payload": { "tool_call_id": "aa07…", "tool_name": "billing.issue_refund",
                   "permission": "high_risk_write", "ok": false, "latency_ms": 7,
                   "error": "invalid_state" }
    }
  ]
}
```

`events` is in the order the ledger was written, oldest first, and it is
**complete**: nothing is summarised and nothing is filtered out. The event types a
run can write are `model_called`, `tool_executed`, `tool_failed`,
`tool_rejected`, `approval_requested`, `retrieval_abstained` and `run_failed`.
`payload`'s shape depends on `event_type` — the two that matter most:

- `tool_failed` carries `{tool_call_id, tool_name, permission, ok, latency_ms,
  error}`, where `error` is the code the tool server answered with
  (`invalid_state`, `not_found`, `mcp_unavailable`). It is a **separate event
  type** from `tool_executed`, not an `ok: false` payload inside it, because the
  event type is what a ledger reader queries on and a name that asserts the
  opposite of its own contents is worse than no record.
- `model_called` carries the provider, model, latency and token counts, and its
  token fields are `null` when the provider reports no usage — an unknown call
  contributes nothing rather than a fabricated zero.

**Which endpoint answers which question.** `/trace` is the *execution timeline*:
which step ran, in what order, how long it took. This is the *compliance record*:
which events were appended, by whom, with what payload. A step says a tool was
called; the ledger says the run was authorised to call it and what came back. Only
the ledger is append-only, and only the ledger holds the events no other surface
shows — a model call, an abstention, a failed write.

For the question "did this run lose money anywhere", read `failed_tool_calls` on
the run-detail payload (§3): it is the filtered, server-computed summary of the
same failure, and it is the field a glance lands on. This endpoint is for
"everything of record about this run", including the events nothing else
surfaces — and it is the only one of the three that exists to be read by an
auditor rather than by a dashboard.

Errors: `401` without a valid bearer token (§8). `404 run_not_found` when there
is no such run. An empty `events` list is **not** an error: it means the run
exists and has recorded nothing yet. The distinction is deliberate — conflating
"this run has an empty ledger" with "there is no such run" would make a mistyped
run id indistinguishable from a run that had just been created.

## 5. Approvals

### `GET /api/approvals?status=pending`

```jsonc
{
  "items": [
    {
      "id": "b7e2…",
      "run_id": "7c1b…",
      "tool_call_id": "aa07…",
      "status": "pending",
      "reason": "Verified duplicate charge on TX-88219; proposing full refund of $129.00.",
      "risk_explanation": "This moves 129.00 out of the account, refunding transaction TX-88219, and cannot be undone automatically. The amount is in the currency the transaction was charged in.",
      "arguments_snapshot": {"transaction_id": "TX-88219", "amount": 129.00,
                             "reason": ""},
      "created_at": "2026-10-05T10:42:09Z",
      "decided_at": null,
      "decided_by": null,
      "note": null,
      "context": {
        "company": "ACME",
        "ticket_subject": "Charged twice for invoice INV-2026-384"
      }
    }
  ],
  "total": 1
}
```

**`context` is supplementary, and every field in it is optional.** It exists so
the approver can decide without opening a second tab, and it is assembled from
**data the run has already persisted** — the company from a `crm.get_customer`
result the run has already executed, the ticket's subject from the ticket
itself — so assembling it triggers **no tool calls** and cannot change what the
run has done. A run that never fetched a customer still parks a valid approval,
and that approval's `context` carries a `null` `company` rather than an error or
a set of empty strings. The **whole** object is `null` only when the ticket has
no subject either — and `subject` is required on a ticket, so in practice the
object is present with a `company` that may or may not be known.

The context is stored in a column of its own, not inside `arguments_snapshot`.
The snapshot is one thing — what the human approved and what will be executed —
and it is immutable; a display-only string inside it would give it a second
meaning and break the invariant the approval gate rests on
(`docs/tool-permissions.md` §3.2).

### `GET /api/approvals?status=expired`

The same envelope, filtered to approvals that timed out. `status` accepts
`pending`, `approved`, `rejected` and `expired`; the default is `pending`, so
the inbox a client opens by default is the one that answers "what is waiting
for me".

**`expired` is a real status, and this is what makes it one.** An approval
that nobody decides within `APPROVAL_TTL_SECONDS` (default 3600) is swept to
`expired` by the worker, and it stops being decidable: a decision on it is
refused with `409 approval_expired` (§6). Before the TTL existed, `expired`
was in the enum and in this filter but **no code path could produce it**, so
the filter was a `WHERE status = 'expired'` that returned nothing, always —
a tab that could only ever be empty, telling the operator "there are no
expired approvals" when the truth was "no approval can expire".

**Expiring an approval does not move the run.** The run stays in
`waiting_approval`: no decision was made, so no money moved and there is
nothing for the worker to execute. `decided_at` and `decided_by` stay `null`
on an expired approval, because nobody decided it — a row that named an
operator would be naming a person who did not act.

### `POST /api/approvals/{id}/approve`

```jsonc
// request — empty body, or:
{ "note": "Confirmed in billing console." }

// 200
{ "id": "b7e2…", "status": "approved",
  "decided_at": "2026-10-05T10:44:32Z", "decided_by": "operator",
  "note": "Confirmed in billing console.",
  "run": { "id": "7c1b…", "status": "executing" } }
```

**The `note` comes back.** It is the operator's own remark, it is accepted on
the decision, and it is persisted with it — so the decision response echoes
it, and so does every later read of that approval (`GET /api/approvals/{id}`,
and `GET /api/approvals?status=approved`). A remark that was accepted and
then invisible would be a remark the operator believed they had left.

**The identity is the server's to assign.** `decided_by` in the response is
resolved from the bearer token and from nothing else. **The request body has no
`decided_by` field**: the body model forbids unknown fields, so a client that
sends one is refused with `422 schema_invalid` rather than having it quietly
ignored. A client therefore cannot declare who it is, and cannot forge the
name of the operator its token represents. What `decided_by` is *not* is a
permission: in Phase 1 there is one token and one identity, so the value
records who the server believes is acting, not a distinction between two
people ([limitations.md](limitations.md) §1).

**`note` is the operator's own words, and it is kept.** The optional `note` is
a free-text remark from the operator ("Confirmed in billing console.") and it
is persisted on the approval row, so the reason a human gave survives the
decision rather than being read once and dropped. It is a column on the row
and nothing more: it is not translated, not audited, and not summarised into
the ledger. `decided_by` says who decided and `note` says why, and the two are
different fields because they answer different questions.

The transition is `pending → approved` guarded by a conditional update
(`UPDATE ... WHERE id = ? AND status = 'pending'`). A second approve returns
`409 Conflict` rather than double-granting — the dashboard's double-click, the
two-tabs-open case, and two operators pressing the button at the same moment,
all land here (§6).

Approving **transitions the run** (`WAITING_APPROVAL → EXECUTING`) and makes it
claimable again. It does **not** execute the refund inline. Approval is a state
change; execution is the worker's job. Doing it inline would mean a human clicking
a button runs a payment call in an HTTP request, with no trace if the response
stream drops.

### `POST /api/approvals/{id}/reject`

Same shape. Transitions the run to `RESPONDING` (not `FAILED` — see
state-machine doc §3). The run then produces an escalation reply from the trace.

## 6. Errors

One shape, everywhere:

```jsonc
{
  "error": {
    "code": "approval_already_decided",
    "message": "Approval b7e2… was already approved by operator at 2026-10-05T10:44:32Z.",
    "details": { "approval_id": "b7e2…", "status": "approved" }
  }
}
```

The HTTP status is carried by `code`:

| HTTP | Codes |
|---|---|
| 400 | `validation_error` — a query parameter the endpoint cannot use: a `status` that is not an `ApprovalStatus` (§5) or not a `RunStatus` (§3.1) |
| 401 | `unauthorized` — the bearer token is missing, malformed, or wrong (§8) |
| 404 | `ticket_not_found`, `run_not_found`, `approval_not_found` — the route's own "no such entity"; `not_found` — the framework's, for a path that does not exist |
| 405 | `method_not_allowed` — a known path, the wrong method |
| 409 | `approval_already_decided`, `approval_expired`, `run_not_awaiting_approval`, `run_already_completed`, `ticket_exists`, `illegal_transition`, `approval_arguments_changed`, `policy_denied` |
| 422 | `schema_invalid` — the request body failed its schema (Pydantic); `unknown_tool` — the gate refused a tool that is not registered |
| 500 | `internal_error` (message is generic; the detail is in the log, keyed by a `trace_id` returned in `details`) |
| 503 | *(no code)* — `/ready` answers `{"status":"not_ready","checks":{…}}`, which is not this envelope (§7) |

`code` is a stable machine identifier. Clients branch on it; they never parse
`message`.

**Three of the codes are raised by the API's exception handlers rather than by
a route**, and a reader looking only at the routers would not find them:
`illegal_transition` (a run asked to move between two states the state machine
forbids), `approval_arguments_changed` (the tool call's arguments changed under
an approval that had already been shown to a human) and `policy_denied` (the
policy engine refused a proposal) are all `409`. `unauthorized`, `not_found`
and `method_not_allowed` are the framework's own statuses, re-enveloped by the
same handler. A client that branches on `code` must therefore treat the whole
table as reachable, not just the routes it can see.

**Two more codes exist as fallbacks.** `_code_for_status` maps a bare
`HTTPException`'s status onto a code, and its last two branches are
`validation_error` (for a 400 nobody raises) and `error` (for any status not
in the table). Neither is reachable today — the only bare `HTTPException` the
API raises is the 401 — but a client must not crash on them.

(The 400 is deliberate and the 422 is not: `validation_error` is this table's
400, and a bare `HTTPException(422)` would map to `error`, because the code
that means "your body did not validate" is `schema_invalid` and the API raises
that itself rather than letting a bare `HTTPException` reach the fallback.)

**No client ever sees a `not_ready` code.** An earlier revision of this table
listed one for the 503. The code still sits in `src/opspilot/api/errors.py`'s
code-to-status table — which is a table of codes the API *could* send — but
nothing sends it: `/ready` reports an unready database with a `status`/`checks`
body (§7), and it is the only route that answers 503 at all. A client must
therefore read `/ready`'s body rather than branch on a code it will never see.

**`approval_expired` is not `approval_already_decided`.** An approval that
somebody else already decided says "already decided", and names the actor and
the moment. An approval that timed out says "expired", and names nobody,
because nobody decided it — the clock ran out while the run stayed parked
(§5). Both are 409; they are different facts, and a client that shows one
message for both will tell an operator that a person decided something when
the truth is that no person ever did.

`ticket_exists` is the code `POST /api/tickets` returns when the
`external_id` it was given already exists (§9), and it is the **only** 409 that
endpoint can produce.

**`approval_already_decided` covers the sequential case and the concurrent
one, and they are not the same bug.** The sequential case is the ordinary one:
an operator clicks Approve, the approval is no longer `pending`, and the
second click is refused. The concurrent case is two requests for the *same*
approval arriving at the same moment — two tabs, two operators, a double-click
that reaches the server twice.

The second case is the one that decides the money. Both requests pass the
handler's pre-check (each reads the approval while it is still `pending`), so
the decision itself has to be made by the database: the store's update is
conditional — `UPDATE ... WHERE id = ? AND status = 'pending'` — and **at most
one of the two can match a row.** The one that matches grants the refund and
moves the run to `executing`; the one that matches nothing is told it lost, and
the route turns that into `409 approval_already_decided` rather than reporting
a success it did not perform.

So the invariant is stated twice, in two places, and both have to hold: the
grant happens **at most once** (the conditional update), and the loser is
**told** (409, not 200). A store that granted once and returned `200` to both
callers would satisfy the first and break the second — the client that lost the
race would be told the refund was approved, which it was, but also that its own
approval did it, which it did not. `docs/tool-permissions.md` §6 records the
two-layer shape of this guard and the test that pins it.

Note the asymmetry this leaves in the API: a `409` is the *correct* answer to
a repeat approval, not an error condition a client should retry through. The
money moved once, and the second caller is being told it did not move.

## 7. Health and readiness

`GET /health` → `200 {"status":"ok","version":"0.1.0"}`. Process liveness only —
it touches nothing.

`GET /ready` → checks the database connection (a `SELECT 1`) and that
`alembic_version` matches the migration head. Returns
`{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`, or `503` with
each failing check named — `database` is `unreachable` when no connection can be
opened, `migrations` is `unmigrated` (no `alembic_version` row), `behind` (a
revision other than head) or `unknown` (the migration scripts could not be read,
or the database half failed first). A check that cannot run is *not* reported
`ok`: an absent checker answers `503` with both checks `unconfigured`, because a
probe that has not checked the database must not certify it. The distinction
matters in Compose: `api`'s healthcheck and the worker's
`depends_on: api: condition: service_healthy` key on `/ready`, never `/health` —
a process that is alive but pointed at an unreachable or unmigrated database is
exactly the state that produces a confusing "table does not exist" failure three
layers down.

## 8. Authentication

Phase 1: a single operator token.

```
Authorization: Bearer $OPSPILOT_OPERATOR_TOKEN
```

One token, one role. Every endpoint requires it except the two token-free
probes, `/health` and `/ready` (§7; §8.1 explains why they stay exempt). This is
honest about what Phase 1 is: a single-operator local deployment. There is no login
screen, no session, no password hashing, and no pretense of RBAC. SSO, roles and
per-user identity are Phase 3, listed in
[limitations.md §1](limitations.md).

The token is compared with `secrets.compare_digest`. If
`OPSPILOT_OPERATOR_TOKEN` is unset, the API refuses to start with a clear error
rather than defaulting to open — a default-open auth is worse than no auth,
because it looks like auth.

### 8.1 Browser access (CORS)

The API sends `Access-Control-Allow-Origin` to the origins named in
`OPSPILOT_CORS_ORIGINS` (comma-separated, default `http://localhost:3000`, the
dashboard). Credentials are allowed, `Authorization` is in
`Access-Control-Allow-Headers`, and the allowed methods are the contract's own:
`GET`, `POST`, `OPTIONS`.

**CORS is a browser policy, not an authorisation mechanism.** It answers one
question — *may this page read the response* — and it answers it only inside a
browser. Anything else that can reach port 8000 (`curl`, a server, another
container, a compromised process) is entirely unaffected by it, and a token sent
from a non-allowed origin authenticates exactly as it does from an allowed one.
The bearer token above remains the only thing that authorises a request. Do not
read a correct CORS configuration as evidence that the API is protected; it
means the dashboard works.

Two consequences worth stating plainly, because both are easy to get backwards:

- A Next.js rewrite proxy would achieve the same working dashboard without a
  CORS policy, since the browser would only ever see same-origin requests. It
  was not chosen because it lives in a config file outside this test suite: a
  proxy that silently fails returns "no data" rather than a startup failure. The
  policy is here, and it is tested.
- `*` is refused. The settings model rejects it, `create_app` re-checks it, and
  `tests/integration/test_api_cors.py` fails if it ever appears. A wildcard would
  let any page the operator visits while the API runs read every response from an
  API whose endpoints can issue refunds. Setting `OPSPILOT_CORS_ORIGINS` empty is
  supported and means *no* cross-origin reader — which is not the same as every
  origin.

`/health` and `/ready` are token-exempt (§7) and remain so. CORS changes nothing
about them: they are still reachable by any origin that asks, and they still
return only a version string and named readiness checks. Nothing new is exposed
through them.

## 9. Idempotency and concurrency on the write endpoints

| Endpoint | Behaviour on repeat |
|---|---|
| `POST /api/tickets` | Always creates. A client wanting dedupe sends `external_id`; a unique violation returns `409 ticket_exists` with the existing ticket. The ticket and its run are written in one transaction (§2), so a failed write leaves neither, and a retry after a `500` cannot find a half-created ticket. |
| `POST /api/runs` | Always creates a new run for the ticket. Re-running an investigation is a legitimate action. |
| `POST /api/approvals/{id}/approve` | `409 approval_already_decided` on the second call — whether it arrives a second later or concurrently (§6). The grant itself happens at most once. |
| `POST /api/approvals/{id}/reject` | `409` on second call, or `409 approval_already_decided` if already approved. |
| `POST /api/knowledge/reindex` | Idempotent — documents are re-hashed and unchanged ones are skipped. Returns counts. |

## 10. Pagination

`GET` list endpoints take `limit` (default 50, max 200) and `offset`. Cursor
pagination is not in Phase 1: at the scale this runs at, an offset is correct and
a cursor is unearned complexity. If a list grows past what offset paging handles
well, that is evidence Phase 2 needs it — not a reason to guess now.

**Every list endpoint returns the same envelope:** `{items, total}`.

- **`items` is the page.** It holds the rows for this request only, after
  `limit` and `offset` have been applied, in the order the endpoint documents
  (newest first for tickets, runs and approvals).
- **`total` is the size of the whole matching set.** It counts the records that
  match the request's filters, **before** `limit` and `offset` trim the page —
  it is *not* the number of rows on this page. `len(items)` is the page count;
  `total` is the total count.

The distinction is the whole reason the field exists. A client that wants to
render "showing 20 of 87" needs the 87, and `total` is where the 87 comes from;
a client that confuses `total` with the page length renders "showing 20 of 20"
on every page but the last, and a pagination control built on that renders
exactly one page. The number is computed by a separate count over the same
filtered query, not by measuring the list that was returned.

Filters count too: `total` is the number of rows matching **the filters that
were applied**, so `GET /api/runs?status=waiting_approval` reports the number
of waiting runs, not the number of runs. And `total` does not depend on
`limit` or `offset`: the same query with `limit=1` and with `limit=50` reports
the same `total`.

`GET /api/knowledge` is the exception to the shape rather than to the rule: it
takes no `limit`/`offset` and paginates nothing, so its `items` is the whole
list and its `total` is the same number. The definition still holds — `total`
is the count of matching records, and there is no page to trim.

The two list examples are §2.1 (`GET /api/tickets`) and §3.1 (`GET /api/runs`);
§5 carries the third (`GET /api/approvals`).
