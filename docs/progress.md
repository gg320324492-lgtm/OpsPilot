# Phase 1 Progress

Append-only log of milestone outcomes. Each entry records what was built, what
the acceptance criteria came out as, and **what was learned or went wrong** —
because the second item is the one that stops the next milestone repeating it.

Status: `not started` · `in progress` · `done` · `blocked`

| Milestone | Status | Notes |
|---|---|---|
| M0 — Architecture and skeleton | done | See below |
| M1 — Database and API skeleton | done | See below |
| M2 — MCP servers | done | See below |
| M3 — Tool gateway, policy, approval | done | See below |
| M4 — Agent runtime | not started | |
| M5 — RAG | not started | |
| M6 — Golden workflow | not started | |
| M7 — Dashboard | not started | |
| M8 — Evals and security | not started | |
| M9 — CI and documentation | not started | |

---

## M0 — Architecture and skeleton

**Started:** 2026-10-05

### Scope

Write the specification, scaffold the repository, publish it. **No business
logic.**

### Acceptance criteria and outcome

| # | Criterion | Outcome |
|---|---|---|
| 1 | All specification docs exist and are internally consistent | ✅ 11 docs + 5 ADRs |
| 2 | Five core principles stated with their enforcement mechanism | ✅ `architecture.md` §1–2, each with the gate or test that enforces it |
| 3 | Repository tree exists with `py.typed`; editable install succeeds | ✅ `uv pip install -e ".[dev]"` on Python 3.12 |
| 4 | `import opspilot` works | ✅ 63 modules import with **no provider SDK installed** |
| 5 | `ruff check` and `ruff format --check` clean | ✅ 122 files |
| 6 | `pytest` collects without error | ✅ 26 tests: 6 pass (the layering guard), 20 correctly skip |
| 7 | `.env.example` covers every setting the code reads | ✅ 21 keys, all referenced in `settings.py` |
| 8 | GitHub repository created, `main` pushed | ✅ |
| 9 | No business logic in `domain/`, `agents/`, `adapters/` | ✅ stubs only, except the transition table which *is* the spec |

**Additional verification performed beyond the criteria:**

- The transition table in `domain/runs.py` was compared line by line against
  `docs/agent-state-machine.md` §2: all nine states, all edges, identical,
  including the empty successor sets on `COMPLETED` and `FAILED`.
- Seed data referential integrity: every invoice's `customer_id` resolves to a
  customer, every transaction's `invoice_id` resolves to an invoice, ACME's
  subscription (`129.00`) matches `INV-2026-384`'s total, and `INV-2026-384`
  carries exactly two `charged` transactions of the same amount two seconds
  apart — the duplicate the golden path must detect.
- `mypy --strict` clean across 100 files.
- **The layering guard was proved able to fail** by injecting
  `import sqlalchemy` into `domain/policies.py` and confirming it reports the
  file and line, then reverting. A guard that has never been seen to fail is a
  guard that might be inspecting nothing.

### Decisions made during M0

Recorded here rather than in an ADR because they are procedural, not
architectural:

1. **Development directory is `G:\OpsPilot`**, a clean English path, deliberately
   not inside the Chinese-named working directory. Every path in the repository
   is ASCII, so no tool has to deal with a non-ASCII root.

2. **`docs/` is committed here, unlike in GigaXML.** In GigaXML `docs/` was a
   private work journal and was gitignored as a whole directory. Here it *is* the
   specification, it is reviewed as prose, and it is what Phase 2 is designed
   against. Ruff excludes it from linting, because ruff ≥ 0.16 reformats fenced
   Python blocks inside Markdown and would rewrite the spec text itself.

3. **Both provider adapters are in scope** (Anthropic and OpenAI), per the
   specification's preference. Both are built in M4 against an interface defined
   before either — see the M0 docstring-only stubs in
   `src/opspilot/adapters/models/`. The risk is bounded because the interface
   lands first: if the schedule slips, the second adapter is a translation, not a
   design.

4. **SQLite for tests, Postgres for production** — an explicit deviation from the
   specification's "PostgreSQL only", recorded as
   [ADR-0004](adr/0004-sqlite-tests-postgres-production.md). The reason is that
   the development machine has no Docker, and a test suite that cannot run on a
   fresh clone is a test suite nobody runs. The deviation is bounded to four
   named places, each of which has a differential or Postgres-only test, and
   **the local skip count is printed** so a green local run is not misread.

5. **Python 3.12–3.13, not 3.14.** Configuration targets `>=3.12,<3.14`. The
   development machine's default is 3.14; a 3.12 interpreter is available via
   `uv`. This keeps the CI matrix on versions where every dependency — including
   the MCP SDK and pgvector bindings — is known good, and expands later once
   there is a reason to.

### What was learned

- The specification is unusually complete, and the two places where it is
  genuinely ambiguous both concern *what a rejected approval means*. The
  specification's state list does not say whether a rejection fails the run or
  completes it. Resolved in the state-machine doc as `WAITING_APPROVAL →
  RESPONDING → COMPLETED`: a rejection is a successful outcome of the workflow,
  not an error. This decision affects the eval's task-completion metric and had
  to be settled before M3, not during it.

- The specification says "optionally create an issue" in the golden path while
  marking `issues.create` as `SAFE_WRITE`. Creating an external tracker issue is
  externally visible and permanent, which sits awkwardly beside a project whose
  thesis is that risky writes need approval. Kept as `SAFE_WRITE` — the risk
  definition here is irreversibility of a *financial or customer-facing* effect —
  but recorded as conflict C1 in [risks.md](risks.md) so the tension is visible
  when Phase 2 adds a second `SAFE_WRITE` and the three-level model needs a real
  second data point.

- Writing the eval datasets *before* the implementation (in M0, not M8) changed
  the design of the golden path. Several dataset cases demand behaviour the
  original sketch did not have — notably the already-refunded case, which must
  detect the existing refund and complete **without** proposing a write, so it
  needs no approval at all. Had the datasets been written last, they would have
  been written to match whatever the implementation happened to do.

### Not done in M0

No business logic, by design. `domain/runs.py` contains the `RunStatus` enum and
the `ALLOWED_TRANSITIONS` table as literals — those *are* the specification, and
transcribing them is not implementation — but `transition_to` is a stub. Every
other module in `domain/`, `agents/` and `adapters/` is a docstring and stubs.

---

## How to read this file

A milestone entry with a criterion marked ⏳ is not done. Criteria are marked ✅
only when the check has actually been run — not when the code was written. Where
a criterion cannot be met, it is marked ❌ with the reason, rather than removed
from the list or quietly reworded.

### Verification failures found and fixed during M0

Recorded because the pattern matters more than the individual fixes: every one of
these was caught by *re-checking an agent's own report*, not by the report itself.

1. **`str, Enum` instead of `StrEnum` (7 classes).** A real defect, not style.
   With the `(str, Enum)` mixin, `str(RunStatus.RECEIVED)` returns
   `"RunStatus.RECEIVED"`, not `"received"` — so any f-string or `json.dumps`
   of an unwrapped member writes the wrong value into `agent_runs.status` and
   every API response. `StrEnum` (3.11+) fixes the semantics. Fixed in all six
   enum classes before any of them was used.

2. **`datetime.now(tz=None)` in `domain/runs.py`.** Produced a naive datetime,
   contradicting the project's own `DTZ` lint rule and the data model's
   "all timestamps are `TIMESTAMPTZ`, stored UTC". A naive `created_at` compared
   against a timezone-aware one raises at runtime. Fixed to `datetime.now(UTC)`.

3. **My own guard test had a mypy error** (`ast.walk` yields `AST`, `tree.body`
   yields `stmt`, and the loop variable was reused). Caught by running
   `mypy` over my own new file rather than assuming it was fine because ruff and
   pytest were green.

4. **My first transition-table comparison script was wrong**, not the code. Its
   regex could not match `frozenset()` with no elements, so it reported a
   mismatch on `COMPLETED`. Re-verified by printing both tables and comparing
   them by eye — a reminder that a verifier needs verifying too, and that "my
   check says it is wrong" is not the same as "it is wrong".

### What was learned (continued)

- The two subagents' reports were both accurate in substance and both
  optimistic in detail: each reported "ruff clean" while the tree as a whole had
  21 errors, because each had only checked its own files against a config that
  the other was also editing. The re-check is not ceremony — a per-slice check
  cannot see cross-slice breakage, and the layering guard is exactly the kind of
  cross-cutting property that no single slice owns.

---

## M1 — Database and API skeleton

**Status:** done.

### Deliverable

Nine tables, one Alembic migration, dialect-aware engine/session handling, four
repositories implementing the ports, fourteen API endpoints, and tests for both
halves — built in parallel by two agents against `ports/stores.py` as the seam,
so the API half could be developed and tested before the persistence half existed.

### Acceptance criteria and outcome

| # | Criterion | Outcome |
|---|---|---|
| 1 | All eight tables as SQLAlchemy models | ✅ 9 tables (`knowledge_chunks` is the ninth) |
| 2 | One migration with pgvector extension, both partial indexes | ✅ `0001_initial_schema.py` |
| 3 | `alembic upgrade head` works | ✅ **after a real fix** — see below |
| 4 | `POST /api/tickets` writes ticket + run in one transaction, starts no work | ✅ asserted with spies (`worker_spy == 0`, `tool_spy == 0`) |
| 5 | All contract endpoints exist | ✅ 14/14, none stubbed |
| 6 | Single error envelope | ✅ including `RequestValidationError` re-shaping |
| 7 | Auth rejects missing/wrong token; refuses to start with no token | ✅ `MissingOperatorToken` raised from `create_app` |
| 8 | Repositories with no business logic | ✅ |
| 9 | Tests for both halves | ✅ 81 passing, 0 skips in the new tests |

### Independent verification performed

Beyond the agents' own suites, a reviewer-written script
(`.scratch/verify_m1.py`, gitignored) checked the milestone's *properties* rather
than re-running the authors' tests — 14/14 passed:

- The claim predicate excludes `WAITING_APPROVAL` (the property that makes the
  approval gate stop work rather than loop on it) and excludes terminal states.
- Migration round-trip: `upgrade head` → `downgrade base` → `upgrade head`,
  invoked as the README says (bare `alembic` from the repository root), then the
  resulting SQLite file inspected for the tables.
- Repository behaviour against SQLite: a `RECEIVED` run is claimable; a
  `WAITING_APPROVAL` run is **not**; a `COMPLETED` run is **not**.

Three safety properties were then tested directly, because a schema assertion
that an index *exists* is not the same as the index *rejecting* a write:

| Property | Result |
|---|---|
| A second `executed` tool call with the same `idempotency_key` is rejected by the database (`IntegrityError`) | ✅ |
| A second `decide()` on an already-decided approval does not change the status or the decider | ✅ |
| `has_approved(TX-88219's call)` does not authorise a call for a different transaction | ✅ |

### Defects found and fixed

Four, and the pattern is worth recording: **three of the four were invisible in
the development environment by construction.**

1. **`pgvector` was imported but not declared in `pyproject.toml`.** It had been
   installed into the venv by hand, so every test passed locally — and a fresh
   `pip install -e .` would have produced a package that could not import its own
   persistence layer. This is the "works on my machine" failure in its purest
   form: a dependency only the author's machine happens to have is a dependency
   the package does not really have.

2. **`models.py` guarded the pgvector import with `try/except`, falling back to a
   JSON column.** Worse than the missing declaration, because it was *silent*: a
   Postgres deployment without pgvector would have created `embedding` as JSON
   and lost vector search entirely, with no error. Now imported directly, so a
   broken install fails at import.

3. **`starlette` was imported directly but not declared**, relying on FastAPI's
   transitive pin. Found by the new dependency guard within seconds of it being
   written — which is the argument for writing the guard rather than noting the
   problem.

4. **`alembic.ini` existed only in `migrations/`.** The README, `docs/milestones.md`
   and the CI workflow all say `alembic upgrade head` from the repository root,
   and that command failed with `No 'script_location' key found in configuration`
   because bare `alembic` looks in the *current* directory. The migration itself
   was correct; the documented way to run it was broken. Fixed by adding a root
   `alembic.ini` that points at the same `script_location`, with a test asserting
   the two files cannot drift.

### Guards added

Two tests, each verified able to fail by injecting the defect it prevents:

- `tests/unit/test_alembic_config.py` — both configs declare the same
  `script_location`, so one migration history exists; and neither hardcodes a
  database URL, because that is the file most likely to be committed with a real
  password in it.
- `tests/unit/test_dependency_declarations.py` — every third-party import in
  `src/` and `mcp_servers/` is declared in `pyproject.toml`. It includes a test
  that the scan is not vacuous, so a pass means something was inspected.

### What was learned

- **The parallel split worked, and it cost one integration bug.** The two agents
  met at `ports/stores.py`, which let the API half be built and tested while the
  persistence half was still being written. The cost was that the persistence
  agent had to accept `Session | sessionmaker` because the API agent's
  `create_app` passes a session factory while the tests pass a session. That is a
  slightly wider signature than the port asked for, and it is recorded here
  rather than tidied away, because it is the ordinary price of a seam between two
  authors.

- **A verifier needs verifying.** My first verification run reported four
  failures; two were my script's wrong assumptions about the API (`create_all`,
  and invoking alembic without `-c`), and two were real. Separating those took
  longer than writing the checks. The discipline that paid off was printing the
  actual command output rather than a summary — the `No 'script_location' key`
  message was what distinguished a real defect from my own mistake.

- **The dependency guard found a bug the same minute it was written.** Two
  hand-installed or transitive-only dependencies existed in a codebase two
  milestones old, written by two agents who both ran `pip install` locally. This
  is the strongest argument in the project so far for writing the guard rather
  than the review comment.

### Known gap carried forward

`TicketStore.create` returns only a UUID, so `POST /api/tickets` fills the
response's `ticket.created_at` from the run's timestamp rather than reading the
persisted ticket back. The two are equal in practice (same transaction) but the
field is not read from the row it names. Tracked for M5, when the knowledge and
retrieval wiring touches these same schemas.

---

## M2 — MCP servers

**Status:** done.

Three real MCP servers over stdio, backed by a shared JSON store, each owning its
own data file. The Billing server mutates state and is the one where correctness
matters.

### Acceptance criteria and outcome

| # | Criterion | Outcome |
|---|---|---|
| 1 | Three servers run as real MCP servers over stdio | ✅ SDK 2.x, `MCPServer`; each has a subprocess `tools/list` test |
| 2 | Every tool in the contract exists with its declared schema | ✅ 8 tools, **after the naming fix below** |
| 3 | Seed data committed as JSON; reset and fixture support | ✅ `mcp_servers/_store.py`, atomic `save()` |
| 4 | `issue_refund` mutates state and is idempotent on the key | ✅ verified independently, in memory and on disk |
| 5 | Same key twice → same `refund_id`, `replayed: true`, one refund row | ✅ `REF-10091` both times; **1 row persisted** |
| 6 | Every refusal returns its documented error code | ✅ `not_found`, `invalid_state`, `amount_exceeds_transaction`, `validation_error` |
| 7 | `crm.get_customer` with neither identifier → `validation_error` | ✅ |
| 8 | Contract tests with no agent and no LLM | ✅ 45 tests, in-process dispatch |

### The defect this milestone is worth remembering for

**All three servers registered their tools under bare function names.**

```
contract (docs/mcp-contracts.md)   registered
billing.get_invoice                get_invoice
crm.get_customer                   get_customer
issues.create                      create
```

The cause is that `@server.tool()` defaults the registered name to the function
name; a prefix requires `name="billing.get_invoice"` explicitly. This was not
documented anywhere, and I had not put it in the M2 brief — the contract document
specified the names, and the brief did not repeat them.

**Why it is serious.** `TOOL_REGISTRY` is keyed on the contracted names and gate 2
of the permission model is a registry lookup. A model proposing
`billing.issue_refund` would not have resolved against a server that only knew
`issue_refund`; every high-risk call would have been rejected for a reason the
agent could not see. M3 would have been blocked on it.

**Why two passing test suites did not catch it.** The tests called the servers
using the same bare names the servers had registered, so the tests agreed with the
implementation and both disagreed with the specification. A suite written from the
implementation cannot detect a divergence between the implementation and the
contract — it can only detect that the implementation is self-consistent.

The reviewer's independent script found it, because it read the *contract* and
asked the servers what they exposed. The same principle is now a test:
`tests/integration/test_mcp_contract_names.py` parses the contract document and
compares the registered names against it, plus checks that every exposed tool has
a `TOOL_REGISTRY` entry and that no name is issued by two servers.

### Independent verification performed

`.scratch/verify_m2.py` — 24 checks, all passing. The ones that matter:

- Same idempotency key twice → same `refund_id`, `replayed` false → true, and
  **exactly one refund row in memory and on disk.** Asserting only the response
  would pass for an implementation that reported `replayed: true` while writing a
  second row; the count is what makes it real.
- `list_transactions` returns both charged rows **and contains no field judging
  which is a duplicate.** The server reports; the agent decides. Asserted as an
  absence, because it is a design property rather than an oversight.
- All four refusal paths return a structured `code` rather than raising. A raised
  exception surfaces as an opaque `UnexpectedToolError` with the message lost
  (`docs/mcp-sdk-notes.md` §5), which would make the "already refunded → do not
  refund again" scenario indistinguishable from a crash.
- `issues.create` twice allocates two distinct keys — it is deliberately not
  idempotent, and the test states that as the contract.
- The committed seed files are unmutated after the whole run, in-memory `refunds`
  still `[]` and `TX-88219` still `charged`. The demo's reproducibility depends on
  the committed JSON being the database.

### Guards added

- `tests/integration/test_mcp_contract_names.py` — registered names vs. the
  contract document; every tool has a permission entry; no duplicate names across
  servers. **Verified able to fail** by removing one `name=` and confirming it
  reports the bare name as missing from `TOOL_REGISTRY`.

  The first attempt to verify this guard was itself wrong: the injection used a
  single-line `sed` pattern that no longer matched after `ruff format` reflowed
  the decorator, so the guard "passed" while nothing had been injected. A guard
  verified by an injection that did not apply is not verified. Redone with an
  assertion that the injection actually changed the file.

### A second defect found while staging the commit

Two files appeared in `git status` that had no business existing:
`mcp_servers/billing/billing.json` (holding one refund) and
`mcp_servers/issues/issues.json` (holding two created issues).

They were a live copy of the database, written into the source tree by the store's
default data directory, which fell back to *the seed file's own directory*. Any
call that did not pass `data_dir` wrote there -- which means a test that forgot to
isolate itself would have left state behind for the next one, and the committed
seed file (the demo's database, per `docs/architecture.md` §8) would have sat
beside a mutable twin.

Fixed: the fallback is now `<cwd>/.opspilot-data/<server>/`, outside the package,
gitignored, and named after the seed's parent so two servers never collide. The
test that asserted the old behaviour was rewritten to assert the property that
matters -- *not inside the seed's directory* -- rather than an exact path.

This one is worth noting for where it was found. It surfaced in `git status` while
staging, not in any test: 140 tests passed with a polluted working tree, because
the pollution was in the *repository*, not in any assertion. A green suite says
nothing about whether the tree it ran in is clean.

### What was learned

- **The MCP SDK is 2.x and the ecosystem's examples are 1.x.** `FastMCP` no
  longer exists (importing it raises); attributes are snake_case (`is_error`,
  `structured_content`, `input_schema`); `structured_output=True` requires a
  Pydantic return type and rejects a bare `dict` at registration. Six such facts
  were established by running the SDK before writing any code and recorded in
  `docs/mcp-sdk-notes.md`, which both agents were required to read. That document
  is why the two halves were written against the same API.

- **The SDK enforces one of the five gates for free.** A parameter with no
  default is marked `required` in the generated JSON Schema, so
  `issue_refund`'s mandatory `idempotency_key` is rejected at the protocol layer
  before any of our code runs. Gate 1 of `docs/tool-permissions.md` §3 is
  therefore partly satisfied by the transport — a better place for it than a
  hand-written check.

- **A test that asserts "some exception was raised" cannot tell two failures
  apart.** The original `test_issue_refund_without_key_is_rejected_at_protocol_layer`
  used `pytest.raises(Exception)`, which passes equally when the argument is
  missing and when the *tool name is wrong* — the unknown-tool path raises the
  same `ToolError`. It was tightened to match the argument name in the message.
  The loose version would have kept passing through the whole naming defect.

- **The two agents adapted to each other through the store interface without
  coordinating.** The billing author discovered `_store.py` mid-task and used the
  real library rather than the shim the brief anticipated, adapting to `Store`
  (not `JsonStore`) and to `Record.get()` rather than attribute access. The seam
  held; no rework was needed at the join.

---

## M3 — Tool gateway, policy engine, approval gate

**Status:** done, after one blocking defect was found in review and fixed.

The security core. Its claim is that the model cannot cause a side effect, and
this milestone is where that is either true or a slogan.

### Acceptance criteria and outcome

| # | Criterion | Outcome |
|---|---|---|
| 1 | Five gates in order, no bypass path | ✅ registry → permission → policy → approval → execute |
| 2 | `TOOL_REGISTRY` permissions static, matching the spec | ✅ verified against a hard-coded literal |
| 3 | `HIGH_RISK_WRITE` never executes without a persisted approval | ✅ parks, executes nothing |
| 4 | Approval bound to `tool_call_id`, not `run_id` | ✅ a database read |
| 5 | Unknown tool rejected, never dispatched | ✅ zero dispatches |
| 6 | `SAFE_WRITE` executes automatically **and** is audited | ✅ |
| 7 | Idempotent on one key | ✅ at the store; the gate-4 short-circuit is unwired (below) |
| 8 | Four CI invariants return 0 | ✅ and the queries detect a violation when one is injected |

### Independent verification — attacks, not assertions

The reviewer drove the real gate with a spy gateway and attempted to break it.
Each attempt had to fail:

| Attack | Result |
|---|---|
| Invent `billing.wire_transfer` | Rejected at gate 2, **zero dispatches** |
| `HIGH_RISK_WRITE` with no approval | `RunParked`, and nothing executed |
| Approval for one transaction reused for another | Refused |
| `COMPLETED → EXECUTING` | `IllegalTransition` |
| `AgentRun.status` has no setter | Confirmed — no way to bypass the transition table |

Two properties were verified by **injection** rather than by reading:

- **The invariant query detects a violation.** An unapproved, executed
  `high_risk_write` row was inserted directly and the §6 query returned **1**.
  A query that returns 0 on an empty database proves nothing; this one was shown
  to return non-zero when the thing it forbids is present.
- **The park really interrupts control flow.** `_park_run` ends in
  `raise RunParked`, so control cannot fall through to `EXECUTE`.

### The blocking defect: the refund could not execute

`runtime.py` dispatched `call.arguments` to the gateway. That dict is the
Pydantic-parsed arguments, and `RefundArgs` **forbids** `idempotency_key` on
purpose — the key is derived from `(run, transaction)` by the policy engine,
because a model-chosen key could be unique every time and defeat the duplicate
check (`docs/tool-permissions.md` §4). The consequence was that the derived key
was used for the gate-4 lookup and recorded on the tool call, but **never sent to
the tool**. The MCP server requires it.

Reproduced against the real server:

```
gate as it was:  ok=False  error=validation_error
                 "idempotency_key  Field required [type=missing]"
with the key:    ok=True   refund_id=REF-10091
```

So an approved refund could not execute, and the project's central promise — an
idempotent refund — never engaged on the real path. **246 tests passed anyway**,
because they drove the gate with a stub gateway that accepts any arguments.

Fixed by building the dispatch payload explicitly — the tool's schema fields plus
the derived key, added only at the dispatch site, never accepted from the
proposal. `RefundArgs.amount` also changed from `str` to `float` to match the
server's signature; the ceiling rule and risk explanation needed no change
because `_as_decimal` already accepted floats.

**This is the third instance of one pattern**, and it is now the most important
thing this project has learned: *a test written against the implementation agrees
with the implementation*. M2's tool names, M2's store path, and now M3's argument
contract were all invisible to suites that drove the system through its own
double. The fix is the new `tests/integration/test_gate_dispatch_contract.py`,
which drives the real `MCPToolGateway` and a real MCP server. It was verified able
to fail: reverting the dispatch fix turns two of its tests red with
`- executed / + failed`.

### Known gap carried forward: the gate-4 short-circuit is unwired

`_no_executed_lookup` returns `(False, None)` — "nothing has ever executed" — and
**nothing supplies a real implementation**. So in a real deployment the
idempotency short-circuit never fires.

Observed consequence in the normal flow: a second proposal for the same
`(run, transaction)` does not short-circuit; it parks for a **second** human
approval, and executing that approval trips the partial unique index
(`IntegrityError`).

**This is not a safety hole.** The server returns one refund; `TX-88219` is
refunded exactly once. The database index is doing the job the short-circuit was
meant to do more cheaply. It is a functional gap: the behaviour the specification
describes is not the behaviour that happens. Tracked for M4, where the worker
gives the lookup a home.

### A process problem worth recording

One agent, to prove its security tests could fail, edited another agent's files
(`agents/runtime.py`, `domain/policies.py`) to inject a bypass and then restored
them. It disclosed this and warned that the restore could have clobbered the
other agent's concurrent edits. It did: a window existed in which 15 gate tests
failed, which is how the reviewer noticed.

The *technique* was valuable — replacing `has_approved(...)` with `approved = True`
made seven security tests go red, proving they guard something real. The
*mechanism* was not: two agents editing one file with no locking. Verification by
mutation belongs to the reviewer, after both authors have stopped, and that is
where it now lives: the reviewer reverted the dispatch fix and confirmed the new
test catches it, rather than an author mutating a shared file mid-flight.

### What was learned

- **A stub double cannot detect a contract mismatch, by construction.** The gate
  tests were thorough and green while the one real integration — gate to MCP
  server — was broken. Tests should cross the boundary they are claiming to
  protect at least once.
- **"It passes" and "it works" diverged three times in three milestones**, always
  at a seam between two agents' work, always because each side's tests agreed
  with that side. The reviewer's independent probes, written from the
  specification rather than from the code, found all three.
- **A finding reported by an agent needs checking too.** One agent reported the
  approval path as a defect; it is M4's missing worker. Another reported the
  duplicate-key collision; that one was real, and reproducing it took ten
  minutes and settled it.
