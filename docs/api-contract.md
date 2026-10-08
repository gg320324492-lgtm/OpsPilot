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
| `GET` | `/api/runs/{run_id}` | Run detail: status, steps, tool calls, citations |
| `GET` | `/api/runs/{run_id}/trace` | The ordered execution timeline only |
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
polls rather than expecting a completed workflow. Phase 1 has no websocket; the
dashboard polls `GET /api/runs/{id}` every 2s while a run is active.

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

  "steps": [
    { "sequence": 1, "step_type": "state_change", "output": {"to": "classifying"},
      "latency_ms": 0, "started_at": "…" },
    { "sequence": 2, "step_type": "classification",
      "output": {"category": "billing_dispute", "confidence": 0.94,
                 "reasoning_summary": "Customer reports a duplicate charge on a named invoice."},
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
                    "idempotency_key": "refund:7c1b…:TX-88219"},
      "permission": "high_risk_write", "status": "awaiting_approval",
      "result": null, "latency_ms": null,
      "idempotency_key": "refund:7c1b…:TX-88219", "error": null }
  ],

  "citations": [
    { "document": "refund-policy.md", "chunk": "refund-policy#refund-limits",
      "score": 0.83, "rank": 1 },
    { "document": "duplicate-charge-sop.md", "chunk": "duplicate-charge-sop#detection",
      "score": 0.79, "rank": 2 }
  ],

  "pending_approval": {
    "id": "b7e2…",
    "tool_call_id": "aa07…",
    "status": "pending",
    "reason": "Verified duplicate charge on TX-88219; proposing full refund of $129.00.",
    "risk_explanation": "This moves $129.00 and cannot be undone automatically.",
    "arguments_snapshot": {"transaction_id": "TX-88219", "amount": 129.00},
    "created_at": "…"
  },

  "customer_reply": null
}
```

Field notes:

- `citations[].chunk` is `"{document_slug}#{anchor}"` — a stable string a reader
  can grep for in `knowledge/`. The UI links it.
- `pending_approval` is present only when the run is parked. It is the only
  place in the API where an approver-visible payload is nested inside a run.
- `customer_reply` is set only at `COMPLETED`.

## 4. `GET /api/runs/{run_id}/trace`

The timeline, without tool arguments or model payloads — the cheap endpoint the
dashboard polls while a run is moving. A `steps` array of
`{sequence, step_type, label, detail, latency_ms, at}` where `label` is already
human-readable and `detail` is a short summary string. Rendering this requires no
client-side logic beyond ordering, which is the point: the timeline is assembled
server-side so the screenshot in the README and the dashboard show the same
thing.

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
      "risk_explanation": "This moves $129.00 and cannot be undone automatically.",
      "arguments_snapshot": {"transaction_id": "TX-88219", "amount": 129.00},
      "created_at": "2026-10-05T10:42:09Z",
      "decided_at": null,
      "decided_by": null,
      "context": {
        "company": "ACME",
        "ticket_subject": "Charged twice for invoice INV-2026-384"
      }
    }
  ],
  "total": 1
}
```

`context` exists so the approver can decide without opening a second tab. It is
assembled from already-persisted data; it triggers no tool calls.

### `POST /api/approvals/{id}/approve`

```jsonc
// request — empty body, or:
{ "decided_by": "admin", "note": "Confirmed in billing console." }

// 200
{ "id": "b7e2…", "status": "approved",
  "decided_at": "2026-10-05T10:44:32Z", "decided_by": "admin",
  "run": { "id": "7c1b…", "status": "executing" } }
```

The transition is `pending → approved` guarded by a conditional update
(`UPDATE ... WHERE id = ? AND status = 'pending'`). A second approve returns
`409 Conflict` rather than double-granting — the dashboard's double-click, and
the two-tabs-open case, both land here.

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
    "message": "Approval b7e2… was already approved by admin at 2026-10-05T10:44:32Z.",
    "details": { "approval_id": "b7e2…", "status": "approved" }
  }
}
```

The HTTP status is carried by `code`:

| HTTP | Codes |
|---|---|
| 400 | `validation_error` |
| 404 | `ticket_not_found`, `run_not_found`, `approval_not_found` |
| 409 | `approval_already_decided`, `run_not_awaiting_approval`, `run_already_completed` |
| 422 | `schema_invalid` (Pydantic request validation) |
| 500 | `internal_error` (message is generic; the detail is in the log, keyed by a `trace_id` returned in `details`) |
| 503 | `not_ready` (from `/ready`) |

`code` is a stable machine identifier. Clients branch on it; they never parse
`message`.

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

One token, one role. All endpoints except `/health` require it. This is honest
about what Phase 1 is: a single-operator local deployment. There is no login
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
| `POST /api/tickets` | Always creates. A client wanting dedupe sends `external_id`; a unique violation returns `409 ticket_exists` with the existing ticket. |
| `POST /api/runs` | Always creates a new run for the ticket. Re-running an investigation is a legitimate action. |
| `POST /api/approvals/{id}/approve` | `409` on second call. |
| `POST /api/approvals/{id}/reject` | `409` on second call, or `409 approval_already_decided` if already approved. |
| `POST /api/knowledge/reindex` | Idempotent — documents are re-hashed and unchanged ones are skipped. Returns counts. |

## 10. Pagination

`GET` list endpoints take `limit` (default 50, max 200) and `offset`. Cursor
pagination is not in Phase 1: at the scale this runs at, an offset is correct and
a cursor is unearned complexity. If a list grows past what offset paging handles
well, that is evidence Phase 2 needs it — not a reason to guess now.
