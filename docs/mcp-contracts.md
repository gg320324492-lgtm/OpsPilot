# MCP Server Contracts

Three demo MCP servers. Each is a real MCP server (official Python SDK), each
owns its own JSON file (`mcp_servers/_store.py`), each is deterministic. They
stand in for Salesforce, Stripe and Jira.

Implementation: `mcp_servers/{crm,billing,issues}/server.py`.

Each server runs over **stdio** — it is run by `server.run(transport="stdio")`
and speaks the protocol over stdin/stdout. Which process runs it, and whether it
is a subprocess at all, is the `MCP_TRANSPORT` setting; see "Why stdio and not
HTTP/SSE" below.

**Why the SDK and not a hand-rolled JSON-RPC loop:** the point of using MCP is
that the tool surface is a protocol, not a function call. Writing the protocol
myself would make the demo a simulation of MCP rather than an instance of it, and
the `ToolGateway` abstraction would then be hiding a fiction. The SDK is the real
dependency; the seed data and the domain semantics are what is simulated.

**Why stdio and not HTTP/SSE:** a stdio MCP server is a child of the process that
speaks to it, over a pipe. stdio needs no port allocation, no network config, and
no auth between the worker and the servers, and it fails loudly (a broken pipe)
rather than quietly (a 502). HTTP transport is a Phase 2 topic when a server
needs to be shared across hosts.

**Which one actually runs is `MCP_TRANSPORT`.** There are two transports, both
real:

- `MCP_TRANSPORT=inprocess` (**the default**) builds each server inside the
  API/worker process and calls it directly. Tool registration, the generated JSON
  Schema, the `structured_output` serialisation and the `CallToolResult` envelope
  are all exercised; only the pipe is skipped. This is what the whole test suite
  runs on.
- `MCP_TRANSPORT=stdio` spawns each server as a real child process from the
  `MCP_CRM_COMMAND` / `MCP_BILLING_COMMAND` / `MCP_ISSUES_COMMAND` settings and
  calls it over stdin/stdout. This is the only path that covers stdio framing,
  the subprocess handshake and the wire serialisation.

A stdio server is therefore a **child of the worker**, not a sibling service in
Compose: it has no port and no listener, so there is nothing for another
container to connect to. `docker-compose.yml` declares no MCP services for that
reason, and says so in full at the end of the file. The commands are read by
`worker/__main__.py::build_worker_gateway` → `adapters/tools/mcp_gateway.py::
build_stdio_servers_from_settings`, spawned by `adapters/tools/mcp_stdio.py`, and
proven end to end by `tests/integration/test_mcp_stdio_transport.py`.

Two details worth knowing before editing a command. It is a command *line*, split
into an explicit argv list with `shlex` and spawned **with no shell** — a `.env`
value can name a server and nothing else, so `;`, `&&`, `|` and `$(...)` reach the
child as literal argument text. And it defaults to *the interpreter running
OpsPilot*, not a bare `python`, because a bare `python` on `PATH` is frequently
the system interpreter whose site-packages has no `mcp`.

---

## 1. `crm` server

### `crm.get_customer`

| | |
|---|---|
| Permission | `READ` |
| Idempotent | yes (pure read) |

```jsonc
// input
{ "customer_email": "billing@acme.example" }
// or
{ "customer_id": "CUS-1001" }

// output
{
  "customer_id": "CUS-1001",
  "company": "ACME",
  "contact_name": "Dana Whitfield",
  "email": "billing@acme.example",
  "plan": "enterprise",
  "status": "active",
  "created_at": "2024-03-11T00:00:00Z"
}
```

Exactly one of `customer_email` / `customer_id` must be given. Neither → the
server returns a `validation_error` result, never a partial record. Both, and
they disagree → `validation_error`, because guessing which one the caller meant
is how a wrong customer gets refunded.

### `crm.get_account`

```jsonc
{ "customer_id": "CUS-1001" }
→ { "account_id": "ACC-2201", "customer_id": "CUS-1001",
    "billing_contact_email": "billing@acme.example",
    "payment_terms": "net30", "currency": "USD",
    "billing_address": {"country": "US", "region": "OR"} }
```

### `crm.get_subscription`

```jsonc
{ "customer_id": "CUS-1001" }
→ { "subscription_id": "SUB-4410", "customer_id": "CUS-1001",
    "plan": "enterprise", "seat_count": 120,
    "monthly_amount": 129.00, "currency": "USD",
    "status": "active",
    "current_period_start": "2026-10-01T00:00:00Z",
    "current_period_end": "2026-11-01T00:00:00Z" }
```

`monthly_amount: 129.00` is the number the golden-path duplicate charge is
against. Seeding it so it matches the invoice is what makes the demo legible.

---

## 2. `billing` server

This is the server where correctness matters. It mutates state.

### `billing.get_invoice`

| | |
|---|---|
| Permission | `READ` |

```jsonc
{ "invoice_id": "INV-2026-384" }
→ {
    "invoice_id": "INV-2026-384",
    "customer_id": "CUS-1001",
    "issued_at": "2026-10-01T00:00:00Z",
    "due_at": "2026-10-31T00:00:00Z",
    "currency": "USD",
    "line_items": [
      { "description": "Enterprise plan — October 2026", "amount": 129.00 }
    ],
    "total": 129.00,
    "amount_paid": 129.00,
    "status": "paid"
  }
```

### `billing.list_transactions`

```jsonc
{ "invoice_id": "INV-2026-384" }
→ {
    "transactions": [
      { "transaction_id": "TX-88218", "invoice_id": "INV-2026-384",
        "amount": 129.00, "currency": "USD", "status": "charged",
        "processed_at": "2026-10-01T02:14:07Z",
        "payment_method": "card_****4242" },
      { "transaction_id": "TX-88219", "invoice_id": "INV-2026-384",
        "amount": 129.00, "currency": "USD", "status": "charged",
        "processed_at": "2026-10-01T02:14:09Z",
        "payment_method": "card_****4242" }
    ],
    "total_charged": 258.00
  }
```

This is the detection surface: two `charged` rows, same invoice, same amount,
2 seconds apart. The agent's job is to notice it; the server's job is to report
it faithfully and *not* to editorialise. The server does not say "duplicate" —
that judgement belongs to the agent and the policy engine, and a tool that
pre-decides the question makes the agent's reasoning untestable.

### `billing.issue_refund`

| | |
|---|---|
| **Permission** | **`HIGH_RISK_WRITE`** |
| Idempotent | **yes, on `idempotency_key` — mandatory** |
| Mutates | yes |

```jsonc
// input — idempotency_key is REQUIRED; a call without one fails gate 1
{
  "transaction_id": "TX-88219",
  "amount": 129.00,
  "idempotency_key": "refund:7c1b…:TX-88219",
  "reason": "Duplicate charge confirmed against invoice INV-2026-384"
}

// output — first call
{
  "refund_id": "REF-10091",
  "transaction_id": "TX-88219",
  "amount": 129.00,
  "currency": "USD",
  "status": "refunded",
  "idempotency_key": "refund:7c1b…:TX-88219",
  "created_at": "2026-10-05T10:44:33Z",
  "replayed": false
}

// output — same key, second call: SAME refund_id, no second refund
{
  "refund_id": "REF-10091",
  ...,
  "replayed": true                  // ← the honest flag
}
```

Refusals, each a structured error result rather than an exception:

| Condition | Error code |
|---|---|
| Missing `idempotency_key` | `validation_error` |
| Unknown `transaction_id` | `not_found` |
| Transaction not in `charged` status (already refunded, voided, pending) | `invalid_state` |
| `amount` > transaction amount | `amount_exceeds_transaction` |
| `amount` <= 0 | `validation_error` |

**`replayed: true` is the flag the agent's trace exposes.** Without it, a
replayed refund would look identical to a fresh one and the run's audit would
claim two refunds when one happened.

**State transition on success:**

```
transactions.status:  charged  →  refunded
transactions.refund_id = REF-10091
refunds: INSERT (refund_id, transaction_id, amount, idempotency_key, created_at)
```

Both writes are in one transaction. The `refunds.idempotency_key` column has a
`UNIQUE` constraint — so even under concurrent duplicate delivery, the second
insert fails and the handler returns the existing row with `replayed: true`
rather than a 500.

This uniqueness-in-the-store, not just in application logic, is the reason
`tests/integration/test_refund_idempotency.py` can call the server directly,
bypassing the agent, and still be a meaningful test.

---

## 3. `issues` server

### `issues.search`

| | |
|---|---|
| Permission | `READ` |

```jsonc
{ "query": "duplicate billing ACME" }
→ { "issues": [ { "key": "OPS-1039", "title": "…", "status": "open",
                  "priority": "medium", "created_at": "…" } ],
    "total": 1 }
```

### `issues.create`

| | |
|---|---|
| Permission | `SAFE_WRITE` |
| Audited | **mandatory** |
| Mutates | yes |

```jsonc
{ "title": "Duplicate billing incident for ACME (INV-2026-384)",
  "description": "…", "priority": "high",
  "labels": ["billing", "duplicate-charge"] }
→ { "key": "OPS-1042", "title": "…", "status": "open",
    "priority": "high", "created_at": "…" }
```

Keys are allocated as `OPS-{1000+n}` from a counter in the store, so the first
created issue in a fresh database is always `OPS-1001` — a detail that makes
assertions in tests readable and the demo reproducible.

`SAFE_WRITE` means no approval, but the `tool_executed` audit event is written
before the call returns. Creating an issue in an external tracker is externally
visible and permanent; "safe" here means "does not move money", not "does not
matter".

---

## 4. The `knowledge` tool is not an MCP server

`knowledge.search` is registered in the tool registry with permission `READ` and
`server="internal"`. It is implemented in-process against pgvector rather than as
a fourth MCP server.

The reasoning: retrieval is not an external system. Modelling it as a remote
service would add a process, a transport and a failure mode to buy nothing, and
it would obscure the fact that retrieval results feed the *prompt assembly* step
rather than the *action* step. It is still a registered tool with a `ToolSpec`,
so the same permission machinery covers it and the eval's tool-selection dataset
can include it uniformly.

## 5. Cross-cutting rules

These hold for all three servers:

1. **No server reads the other servers' databases.** `billing` cannot ask `crm`
   who the customer is. The agent composes the picture. This is what makes the
   trace meaningful — every cross-domain fact in a run's reasoning came from a
   call the reviewer can see.
2. **Errors are results, not exceptions.** A tool that throws becomes a transport
   error and loses its error code. Each server returns a structured error object
   with a `code`, which the gateway maps to `ToolCall.error` and
   `rejection_reason`. The agent can then reason about *why* a tool failed, which
   is what makes the "already refunded → do not refund again" scenario work.
3. **Reads are pure.** `get_*` and `list_*` never write, including never writing
   a "last accessed" timestamp.
4. **Every mutation takes an idempotency key or is naturally idempotent.**
   `issues.create` is the exception, and it is `SAFE_WRITE` with a full audit
   trail; a duplicate issue is annoying, not a financial event. If Phase 2 adds a
   second mutating financial tool, it inherits the mandatory-key rule.
5. **Seed data is versioned in the repository** as JSON under
   `mcp_servers/<name>/seed.json`, loaded by a `--reset` flag and by a pytest
   fixture. The demo is reproducible because the data is in git, not in someone's
   database.

## 6. Contract testing

Each server is tested without the agent and without an LLM, in
`tests/integration/test_mcp_*.py`:

- Every declared tool returns a response matching its declared output schema.
- `billing.issue_refund` with a bad transaction id returns `not_found`, and the
  transaction is unchanged afterwards (asserted by re-reading it).
- `billing.issue_refund` twice with one key → one refund row, `replayed` is
  `false` then `true`.
- `billing.issue_refund` on an already-refunded transaction → `invalid_state`.
- `crm.get_customer` with neither identifier → `validation_error`.
- `issues.create` twice allocates two distinct keys (it is *not* idempotent, and
  the test states that as the contract).

The MCP CI job (`mcp-contract`) runs
`tests/integration/test_mcp_contract_names.py`, which checks that each server
registers exactly the tool names this document specifies. That is a *name*
contract, and it is in-process — see §7 for the transport coverage, which lives
in the main `test` job rather than in `mcp-contract`.

## 7. Exercising the transport itself

Everything in §6 runs **in-process**, which covers every contract above — the
schemas, the structured errors, the idempotency behaviour — and skips only the
pipe. `tests/integration/test_mcp_stdio_transport.py` covers the pipe (40 tests,
of which the ones that actually spawn a child outnumber the pure-parser
assertions). The three that matter most:

1. `crm.get_customer` is answered by a real child process, verified by checking
   that a `crm` server process actually appeared in the OS process table while
   the call was in flight — not by inspecting the gateway's own state, which
   would pass just as well if nothing had been spawned. The helper that does the
   looking explicitly excludes its own `powershell` probe from the match and
   fails the test if it cannot enumerate at all, because the first version of
   this check matched the probe and made the assertion below it vacuous; a
   sibling test asserts that self-check directly, with no server running.
2. One server is spawned **once** and reused across calls, and `aclose` reaps
   it — the two facts that make a per-call respawn, and a per-run leak,
   failures rather than performance details. Both are asserted on the PID that
   was observed going in rather than on a count, because a count taken against a
   moving baseline absorbs an extra process.
3. A server that cannot start surfaces as a named error naming the command and
   quoting the child's own stderr, rather than as `ExceptionGroup: unhandled
   errors in a TaskGroup (1 sub-exception)`.

**There is no `MCP_TRANSPORT=stdio` environment variable in the test suite**, and
none is needed. Isolation is by construction rather than by process environment:
each test builds its own `Settings(MCP_TRANSPORT="stdio")` in-process and hands
it to its own `MCPToolGateway`, so the transport is a per-object decision and
the ambient environment cannot affect it. (A `MCP_TRANSPORT` in the surrounding
shell *would* reach `Settings`, so an operator running pytest with it exported
can still influence the default-path tests — which is one more reason the stdio
tests do not rely on it.)

The default transport stays in-process for the rest of the suite, because
flipping it would change what the other ~670 tests exercise, and a subprocess
server owns its own on-disk store, so tests sharing one would leak state into
each other.
