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
| M4 — Agent runtime | done | See below |
| M5 — RAG | done | See below. Four defects found in review, three fixed; three findings carried into M6 |
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

---

## M4 — Agent runtime

**Status:** done. **The golden path is executable end to end for the first time.**

### Acceptance criteria and outcome

| # | Criterion | Outcome |
|---|---|---|
| 1 | `transition_to` real, one implementation | ✅ `IllegalTransition` raised in exactly one place in `src/` |
| 2 | `FakeModelProvider` replays recorded responses; a miss raises | ✅ `UnmatchedFixtureError` names the request hash — no default |
| 3 | Anthropic + OpenAI adapters sharing one path | ✅ `_shared.py`; SDKs imported inside methods |
| 4 | Prompt assembly labels retrieved text untrusted | ✅ verified by injecting an instruction string |
| 5 | Worker claims and drives; parks on approval | ✅ |
| 6 | **Gap A**: gate-4 idempotency lookup wired | ✅ `find_executed_call_id` |
| 7 | **Gap B**: an approved refund actually executes | ✅ `_resolve_resume` finds the parked call |
| 8 | Rejection completes with escalation, no refund | ✅ |

### Independent verification — the golden path, driven by the worker

`.scratch/verify_m4.py` creates a ticket, **drains the worker** (no hand-pushed
proposals), approves the way the API does, drains again, and then asks the MCP
server whether the money moved. 12/12:

```
drain1 → waiting_approval        parked: billing.issue_refund, high_risk_write, keyed
approval: pending, "This moves $129.00 USD out of the account…"
admin approves → drain2 → completed
THE SERVER SAYS: TX-88219 refunded, TX-88218 still charged
after a second drain: still exactly one refund; the drain had nothing to do
reject path: completed, and the server holds no refund
```

The last checks are the ones worth having: the system's own status is a
self-consistent claim, while the server's transaction row is what happened.

### The defect this milestone was worth running for

**`Store.__init__` read the seed, never the live file.**

`save()` wrote `billing.json` correctly — the file on disk showed `refunds: 1`
and `TX-88219: refunded`. But every newly constructed `Store` called
`_read_seed()`, which reads `self._seed_path`, and `self._path` was never read.
So a second `MCPToolGateway()` on the same directory reported `TX-88219` as
`charged` with no refunds.

Reproduced directly:

```
gateway A: refund ok, REF-10091        billing.json: refunds=1
gateway B: sees refunded []            <-- same directory
```

The consequences are the interesting part:

- A **worker restarted after a refund** would read `TX-88219` as `charged` again
  — exactly the state the duplicate-refund defence consults. The Postgres
  `tool_calls` partial unique index would still have caught a second refund, so
  this was not an open money hole, but the store's own guard was inert.
- The `refunds` idempotency list reset on every construction, so the MCP server's
  replay behaviour only held within a single process lifetime.
- Any code path that rebuilt a gateway mid-run diverged from the one that
  performed the write.

Fixed by loading `self._path` when it exists and falling back to the seed on
first run — the standard seed-then-live-file arrangement, and the one the class
docstring already described ("the live store and the seed are distinct and
`reset` is meaningful"). `_read_document` now takes a path so a malformed *live*
file names itself rather than reporting the seed as broken, and `reset()` keeps
its meaning.

**A test was pinning the defect in place.** `test_fixture_store_is_isolated_from_the_committed_seed`
asserted *"a fresh build re-seeds from the committed seed, so no prior refund"* —
it was written from the implementation and it agreed with the implementation. Its
real intent was store isolation between tests, which is a property of the
*data directory*. That is now two tests: one asserting two directories are
isolated, one asserting a write survives reconstruction, one asserting `reset`
still returns to the seed. All three were needed; the original blurred them.

### Known gaps carried forward

- **`find_executed_call_id`** (agent B flagged this itself): the replay branch's
  final status depends on a second store read rather than purely on
  `evaluate_policy`'s verdict. It exists so a stub lookup claiming a prior
  execution with no backing row still resolves; a reviewer should decide whether
  `evaluate_policy` should own that decision outright.
- **`TicketStore.create` returns only a UUID** (from M1), so `POST /api/tickets`
  fills the response's `ticket.created_at` from the run's timestamp rather than
  reading the ticket back. Tracked since M1.
- **Retrieval is a stub in the pump** — the `RETRIEVING` state runs and records no
  hits. M5 fills it. No citations are fabricated in the meantime.

### What was learned

- **The end-to-end probe caught what 316 passing tests did not**, and it caught a
  different thing from the last two: not a contract mismatch but a persistence
  semantic. Driving the whole system and then asking an external party (the MCP
  server) for the outcome is what made it visible.
- **Three defects in four milestones have been a test agreeing with the
  implementation.** This one was the clearest: the test's own docstring described
  the bug as the design.
- **An agent's self-reported uncertainty was accurate.** Agent B flagged its
  `find_executed_call_id` discriminator as the thing it was least sure about, and
  that is genuinely the weakest part of the change. Asking for that explicitly in
  the brief is worth the sentence.
- **Agent A modified `pyproject.toml` beyond its brief** (three narrow per-file
  `ANN401` ignores). Kept: each is scoped to one file, the SDK client types are
  genuinely dynamic, and the comment explains why `Any` is the honest annotation
  rather than a fabricated Protocol. Noted because the boundary was crossed even
  though the result was right.

---

## M5a — RAG: chunking, embeddings, vector stores

**Started:** 2026-10-05

### Scope

The retrieval primitives only: `chunking.py`, `embeddings.py`, `memory_store.py`,
`pgvector_store.py`, and their tests. `ingest.py`, `search.py`, the agents, the
API, the worker, `tests/integration/test_retrieval.py` and
`tests/security/test_prompt_injection.py` are another agent's M5b and were not
touched. This section covers M5a only.

### What was built

| File | Change |
|---|---|
| `chunking.py` | `estimate_tokens`, `slugify_heading`, `split_document` implemented |
| `embeddings.py` | `LocalDeterministicEmbedder.embed`, `ProviderEmbedder.embed`, `build_embedder` implemented |
| `memory_store.py` | `upsert` / `search` implemented; `derive_chunk_id` added |
| `pgvector_store.py` | `upsert` / `search` implemented against `knowledge_chunks` |
| `tests/unit/test_chunking.py` | 24 tests |
| `tests/unit/test_embeddings.py` | 15 tests |
| `tests/unit/test_vector_stores.py` | 16 tests, 2 `postgres`-marked (skipped locally) |

### The vector-dimension decision

**The column stays `vector(1536)`; the dimension is *not* made freely
configurable, and no migration was added.**

`knowledge_chunks.embedding` is `vector(1536)`
(`migrations/versions/0001_initial_schema.py`), and `settings.embedding_dim`
defaults to `1536`. The two agree by default. If an operator sets
`EMBEDDING_DIM` to anything else, the model's `TypeDecorator` would render a
`vector(N)` for N≠1536 that no migration created, and `PgVectorStore` could
never insert a row.

Three options were considered:

1. **Make it genuinely configurable** with a migration per value — rejected: a
   migration cannot be written for "any N", so this is not a real option, and a
   migration for a *specific* second value is untestable locally (no Postgres,
   no Docker — ADR-0004).
2. **Silently accept any dimension** on an untyped `vector` column — rejected:
   drops the width constraint and pushes the failure into pgvector at insert
   time.
3. **Keep 1536, and reject any other value loudly at construction** — chosen.

`PgVectorStore.__init__` raises `DimensionMismatch(ValueError)` when `dim` is
not 1536, naming both numbers and the migration that would be required. This is
the "acceptable answer" the brief allowed: a non-default dimension is a
configuration error, not a supported mode, until an operator writes the
migration. `EMBEDDING_DIM` remains a setting because the model reads it, but the
production store refuses everything but the column's dimension.

### Decisions a reviewer should check

- **Where `document_slug` comes from.** `ChunkRecord` (in `ports/`) carries
  `document_id`, not the slug, and `SearchHit` requires the slug because the
  citation is `"{document_slug}#{anchor}"`. Both stores are constructed with a
  `slug_lookup: Callable[[UUID], str]` and resolve the slug the same way. The
  alternative — adding `document_slug` to `ChunkRecord` — would denormalise the
  port and force every caller to supply a value the port's own docs say is
  derived from the document row. The lookup keeps `ports/` describing what is
  stored. `PgVectorStore` accepts the identical keyword, and a test asserts both
  resolve through it.
- **How `chunk_id` is derived.** `ChunkRecord` has no id, but `SearchHit` needs
  one (the `citations.chunk_id` FK). It is derived deterministically via
  `derive_chunk_id(document_id, ordinal) = uuid5(namespace, "{doc}:{ordinal}")`
  — the same function in both stores. This is what makes upsert a true
  replacement and what lets the differential test compare ids, not just scores.
  A random id per call would give the same chunk a new id on every reindex.
- **Anchor uniqueness** is GitHub-style: a repeated heading yields `notes`,
  `notes-1`, `notes-2`. Content before the first heading uses the **empty-string
  anchor**, so its citation is `"{slug}#"` and the reader greps the slug — there
  is no rendered heading to grep for, and inventing one would produce a citation
  that does not resolve.
- **Overlap is real text.** `_split_body` splits on whitespace and repeats the
  actual trailing words, so the tail of chunk N is byte-identical to the head of
  chunk N+1. A test finds the longest common boundary and asserts it is
  non-trivial — a token-count-only assertion would pass on a broken overlap.

### Commands run (M5a)

```
pytest tests/unit -x -q                    199 passed, 3 skipped
mypy --strict src/opspilot/adapters/retrieval   Success: no issues found in 7 source files
ruff check src/opspilot/adapters/retrieval tests   All checks passed!
ruff format  src/opspilot/adapters/retrieval tests  53 files left unchanged
pytest tests -q                            371 passed, 8 skipped
```

No `type: ignore` was added to `src/opspilot/adapters/retrieval`.

### Mutation verification

Not "does the suite go red" — *which named test* catches each deliberate break.
Each was applied, observed, and reverted:

| Break applied | Test(s) that failed |
|---|---|
| Anchor dedup dropped (duplicate headings share an anchor) | `test_duplicate_headings_get_distinct_anchors`, `test_duplicate_headings_are_deduplicated_github_style` |
| Overlap forced to zero words | `test_consecutive_chunks_share_identical_text`, `test_overlap_reduces_effective_step` |
| Punctuation kept in slug | `test_slugify_removes_punctuation`, `test_slugify_matches_github_anchor_for_a_typical_heading` |
| `estimate_tokens` → floor division, no floor | `test_estimate_tokens_never_zero_for_non_empty_text` |
| Embedding not normalised | `test_local_embed_is_unit_norm`, `test_local_embed_handles_empty_string` |
| Embedding made non-deterministic (`os.urandom`) | `test_local_embed_is_deterministic`, `test_local_embed_is_deterministic_across_instances` |
| `build_embedder` silently falls back to local | `test_build_embedder_unknown_name_raises_and_names_accepted_values` |
| `embed` returns one vector regardless of input | `test_local_embed_returns_one_vector_per_text`, `test_local_embed_differs_for_different_texts` |
| Sort key loses score direction and tie-break | `test_memory_search_returns_top_k_best_first`, `test_memory_search_ties_break_deterministically`, `test_memory_store_satisfies_shared_contract` |
| `upsert` appends instead of replacing | `test_memory_upsert_replaces_by_document_and_ordinal`, `test_memory_store_satisfies_shared_contract` |
| `rank` starts at 0 | `test_memory_search_returns_top_k_best_first`, `test_memory_search_ranks_start_at_one_and_are_dense` |
| `derive_chunk_id` returns a random uuid | `test_memory_search_hit_chunk_id_is_deterministic` |
| `PgVectorStore` dimension guard removed | `test_pgvector_store_rejects_dim_mismatch_loudly` |
| Memory store ignores `slug_lookup` | `test_memory_search_resolves_document_slug_via_lookup`, `test_memory_search_ties_break_deterministically` |

**One mutation did not go red, and that is a finding.** Removing
`slugify_heading`'s `lstrip("#")` left all tests green — the `_SLUG_DROP_RE`
(`[^\w\s-]`) already strips `#` as punctuation, so the `lstrip` is redundant.
It is kept as defence-in-depth but a reviewer should know no test covers it,
because no test *can*: the code path is unreachable for any input.

### Known gaps carried forward

- **The pgvector store is not executed by the local suite.** No Postgres and no
  Docker here (ADR-0004), so `PgVectorStore.upsert` and `search` — including the
  `<=>` distance expression and the join to `knowledge_documents` — are
  exercised only by the `postgres`-marked tests, which skip locally. They pass
  as *skipped*, not as *passed*. The differential test against
  `InMemoryVectorStore` is written and requires the CI `verify` job.
- **The differential test cannot run here.** It is present in
  `test_vector_stores.py` and skipped; per `docs/milestones.md` §M5 it is first
  proven green in M9.
- **`PgVectorStore` reads the column dimension from a class constant, not from
  `settings.embedding_dim`.** The constant is `1536`, matching the migration.
  If a future milestone adds a migration to change the column, the constant and
  the migration must move together; there is no single source of truth for the
  pair yet.


---

## M5c — `knowledge.search` as a callable `READ` tool

**Started:** 2026-10-05

> **CORRECTION (2026-10-06).** The first attempt at this slice built a **fourth
> MCP server** (`mcp_servers/knowledge/server.py`) and wired it into the gateway
> with an `_SERVER_ALIASES = {"internal": "knowledge"}` translation. That violated
> `docs/mcp-contracts.md` §4, titled "The `knowledge` tool is not an MCP server",
> which says retrieval "is implemented in-process against pgvector rather than as
> a fourth MCP server". Its nine tests all passed — they verified the server was
> built *correctly* and none asked whether it should be built at all.
>
> The server, the alias and `tests/integration/test_mcp_knowledge.py` have been
> **deleted**. The replacement is `src/opspilot/adapters/tools/knowledge_tool.py`
> (`KnowledgeTool`), an in-process dispatcher returning the same `ToolResult`
> shape as `MCPToolGateway`; `knowledge.search` stays registered with
> `server="internal"` and `Permission.READ`. A new guard,
> `tests/integration/test_knowledge_is_not_mcp.py`, reads §4's claim out of the
> document and fails if the fourth server grows back. The section below is kept
> as the record of the original (reverted) attempt; the wiring that shipped is
> described in "M5c (rewired)" further down.

### Scope (original attempt — reverted)

One focused slice: make `knowledge.search` — already declared in
`TOOL_REGISTRY` (`domain/tools.py:68`) as `permission=READ, server="internal"` —
a real, callable tool reachable through the tool gateway. Previously no server
implemented it and no gateway could dispatch it.

Files owned by this slice: `mcp_servers/knowledge/server.py` (new),
`src/opspilot/adapters/tools/mcp_gateway.py`, `tests/integration/test_mcp_knowledge.py`
(new), and this section. The retrieval backend
(`src/opspilot/adapters/retrieval/**`) and the agent/API/worker wiring are
another agent's M5b and were not touched.

### What was built

| File | Change |
|---|---|
| `mcp_servers/knowledge/server.py` | New in-process MCP server; one tool `knowledge.search`, `structured_output=True`, Pydantic `SearchResult` return model |
| `src/opspilot/adapters/tools/mcp_gateway.py` | `_KNOWN_SERVERS` gains `knowledge`; `_SERVER_ALIASES` maps the registry's `internal` to the `knowledge` server; `build_in_process_servers` builds it, optionally with an injected retriever |
| `tests/integration/test_mcp_knowledge.py` | 9 tests driving the real gateway in-process |

### Decisions a reviewer should check

- **One retrieval, not two.** The server delegates to the shared search through
  an injected `retriever: (query, top_k) -> RetrievalOutcome` callable, so the
  tool and the agent cannot disagree about what retrieval *is*
  (`adapters/retrieval/search.py`). The callable is injected rather than
  constructed here because the embedder and vector store are
  deployment-specific; the server module therefore imports no database driver.
- **The `internal` → `knowledge` alias lives in the gateway, not the registry.**
  `knowledge.search`'s `ToolSpec.server` is `"internal"` (a
  permission-bearing, spec-transcribed fact), while the MCP server's name is
  `knowledge`. Translating at the dispatch site keeps the static registry
  unchanged — it is not the place to accommodate a transport detail.
- **`not_configured` is a refusal, not an empty success.** With no retriever
  bound, the tool returns `code="not_configured"` rather than raising (which
  would lose the code, `mcp-sdk-notes.md` §5) or returning zero hits with
  `code=None` (which is indistinguishable from a configured index that found
  nothing). `abstained=True` is the third case and is a *success*. A retriever
  that raises becomes `code="retrieval_failed"`.
- **No `mcp_servers/_store.py`.** The shared JSON store owns the three external
  servers' seed data; the knowledge backend is a vector index owned by the
  retrieval adapter. This server holds no state, so importing `_store` would be
  a dependency with no use.

### Test-name provenance

The tool name is asserted against the **contract documents** —
`docs/mcp-contracts.md` (§4) and `docs/tool-permissions.md` (§2) — parsed at test
time, not hard-coded and not read back from the server, following
`tests/integration/test_mcp_contract_names.py`. One test asserts the two
documents agree on the name.

### Mutation verification

Each break was applied, the named test(s) confirmed red, then reverted (backups
restored, suite re-run green).

| Break applied | Test(s) that failed |
|---|---|
| `name="knowledge.search"` dropped from the decorator (bare function name) | `test_server_registers_the_tool_under_the_contracted_name` + 5 dispatch tests |
| `not_configured` replaced with an empty success | `test_not_configured_is_a_refusal_with_a_code`, `test_gateway_reports_not_configured_as_ok_false` |
| `abstained` hard-coded `False` | `test_abstention_is_a_successful_result_carrying_the_flag` |
| retriever called with the default `top_k`, ignoring the argument | `test_gateway_dispatch_passes_query_and_top_k` |
| `knowledge` removed from `_KNOWN_SERVERS` | 4 gateway dispatch tests |
| `_SERVER_ALIASES` emptied (`internal` unresolved) | 4 gateway dispatch tests |

### Commands run

```
pytest -q                   415 passed, 9 skipped
mypy --strict               Success for this slice's files; 10 errors in tests/unit/test_search.py (M5b, in flight)
ruff check . / format       Clean for this slice's files; errors in M5b's ingest/citations/search tests (in flight)
```

### Least-confident decision

The `retriever` callable signature `(query, top_k) -> RetrievalOutcome` is
invented by this slice. The runtime's own port is
`RetrievalCallable = (query) -> list[SearchHit]` (no `top_k`, no
`abstained`/`top_score`). A future wiring may want the server's callable to
return the runtime's simpler shape, which would drop the `abstained` flag from
the tool result — the flag this milestone specifically asked for. Recorded so the
M5b/M6 wiring can reconcile the two rather than discover it at the seam.

### Not done in M5c

No production wiring of a real embedder/vector store into
`build_in_process_servers`; the application supplies the retriever. The gateway
default builds the knowledge server with no backend, answering
`not_configured`, which is the honest default until the deployment wires one.

---

## M5c (rewired) — retrieval in-process, citations persisted, API and worker wired

**Date:** 2026-10-06. This is the slice that shipped; it supersedes the original
M5c above.

### What changed

| File | Change |
|---|---|
| `mcp_servers/knowledge/` | **Deleted** (the fourth MCP server) |
| `tests/integration/test_mcp_knowledge.py` | **Deleted** (9 tests that verified the server was built, not that it should be) |
| `src/opspilot/adapters/tools/mcp_gateway.py` | `knowledge` removed from `_KNOWN_SERVERS`; `_SERVER_ALIASES` removed; `build_in_process_servers` no longer takes `retriever` or builds a knowledge server |
| `src/opspilot/adapters/tools/knowledge_tool.py` | **New.** `KnowledgeTool`, the in-process `knowledge.search` dispatcher returning a `ToolResult`; same refusal codes (`not_configured`, `retrieval_failed`, `validation_error`, `unknown_tool`) |
| `src/opspilot/adapters/wiring.py` | **New.** `build_retrieval_stack(settings, *, session_factory)` builds embedder + vector store + the runtime `RetrievalCallable` + `KnowledgeTool` + reindex runner + `KnowledgeDocumentStore` from settings |
| `src/opspilot/agents/runtime.py` | `run_loop` gains `citation_store` and `retrieval_min_score`; `_retrieve` persists strong hits as citations; below threshold the run abstains and escalates via `PLANNING -> RESPONDING` |
| `src/opspilot/worker/loop.py` | `drain_once`/`poll_forever` thread `citation_store` and `retrieval_min_score` to the runtime |
| `src/opspilot/worker/__main__.py` | `build_worker_retrieval()` builds the stack from settings for the M6 loop to pass in |
| `src/opspilot/api/app.py` | `_bind_stores` builds the retrieval stack from settings when the caller injects neither `knowledge_store` nor `reindex_runner` |
| `tests/integration/test_knowledge_is_not_mcp.py` | **New guard.** Reads §4's claim out of the document and asserts no fourth server |
| `tests/integration/test_retrieval.py` | The two M5 retrieval tests (golden path recall, abstention) |
| `tests/security/test_prompt_injection.py` | The M5 injection test (outcome, not model resistance) |
| `tests/integration/test_retrieval_wiring.py`, `tests/integration/test_api_knowledge.py`, `tests/unit/test_knowledge_tool.py` | **New.** Runtime citation/abstention wiring, the knowledge routes, and the in-process tool surface |

### How abstention escalates, and why

`docs/agent-state-machine.md` §3 says "Knowledge insufficient to answer ->
`COMPLETED` (via `RESPONDING`) — Abstention is a supported outcome, not an
error", and §2's table has **no** `RETRIEVING -> RESPONDING` edge. The legal
route is therefore `RETRIEVING -> PLANNING -> RESPONDING`, using the "no tool
needed" edge the model takes when it has nothing left to do. The pump transitions
to `PLANNING`, detects that the top hit is below `retrieval_min_score` (or the
backend returned nothing), sets `escalated=True`, drops the weak hits from the
context, records a `retrieval_abstained` audit event, and calls `_respond`. No
edge was invented. `FAILED` was rejected: it means "OpsPilot did not finish the
job", and abstention is the job finishing correctly.

An unwired retrieval (`retrieval is None`) is **not** an abstention: the step is
an honest no-hit step and the pump proceeds to planning, as it did before M5.

### Findings a reviewer must see

- **The in-memory vector store never writes `knowledge_chunks` rows.** Unlike
  `PgVectorStore.upsert`, `InMemoryVectorStore.upsert` keeps chunks in process
  memory only. Consequences on the SQLite/test configuration: `GET
  /api/knowledge` reports `chunk_count == 0` for every document, and runtime
  citations are silently skipped by `SqlCitationStore.create_many` (it skips hits
  with no chunk row). `tests/integration/test_retrieval_wiring.py` seeds the
  chunk rows itself to make the citation assertions real;
  `tests/integration/test_api_knowledge.py` asserts the zero counts rather than
  hiding them. Fixing this needs a change inside the frozen
  `src/opspilot/adapters/retrieval/**` — **reported, not made.**
- **The local deterministic embedder cannot rank this corpus.** Its cosine
  scores sit in ~0.04–0.09 with complete overlap between answerable and
  unanswerable questions, so at `RETRIEVAL_MIN_SCORE=0.35` *every* query
  abstains. `tests/integration/test_retrieval.py` therefore asserts Recall@K
  (K=10) — the eval's own metric — for the ranking claim, which is threshold-free
  and real, and asserts the abstention flag at the configured threshold. The
  README's retrieval numbers must come from a real provider, not this embedder
  (`embeddings.py` says as much).

### Commands run

```
pytest -q                   440 passed, 7 skipped
mypy --strict               Success: no issues found in 131 source files
ruff check . / format       All checks passed
```

### Mutation verification

| Break applied | Test(s) confirmed red |
|---|---|
| `knowledge` reintroduced into `_KNOWN_SERVERS` + `_SERVER_ALIASES` restored | `test_knowledge_is_not_in_the_gateway_server_set`, `test_no_server_alias_translates_internal_to_a_server` |
| Gate 5 weakened (`HIGH_RISK_WRITE and False`) | all 3 in `test_prompt_injection.py`, including the §6 invariant query |
| Citations never persisted in `_retrieve` | `test_retrieved_hits_persist_as_citations_bound_to_the_run`, `test_citations_are_written_only_for_a_completed_run` |
| Abstention short-circuit disabled | `test_below_threshold_retrieval_escalates_via_responding`, `test_abstention_records_an_audit_event` |
| `ingest_directory` indexes nothing | `test_golden_path_question_retrieves_both_policy_documents`, `test_answerable_cases_meet_recall_at_k`, `test_the_golden_path_matches_the_datasets_own_duplicate_case` |
| Reindex content-hash short-circuit disabled | `test_reindex_is_idempotent` |
| `not_configured` replaced with an empty success | `test_not_configured_is_a_refusal_with_a_code` |

### Least-confident decision

Where the runtime's `retrieval_min_score` comes from. The runtime takes it as a
parameter defaulting to `0.35` rather than importing `Settings`, to keep the
`agents` layer configuration-free — but this duplicates the setting's default in
two places (`settings.py` and `runtime.py`), and `worker/loop.py` duplicates it a
third time in `_DEFAULT_MIN_SCORE`. A future change should inject the value from
one place at every entry point.

---

## M5b — Ingestion, retrieval search, citations, differential store test

### Scope

The half of M5 that turns a Markdown tree into a searchable corpus and a
retrieval result into a persisted citation. Files owned and changed:

- `src/opspilot/adapters/retrieval/ingest.py` — `parse_front_matter`,
  `ingest_document`, `ingest_directory`.
- `src/opspilot/adapters/retrieval/search.py` — `retrieve`.
- `src/opspilot/adapters/persistence/repositories.py` — added
  `SqlCitationStore` and `SqlKnowledgeDocumentStore` (existing classes
  untouched).
- `src/opspilot/ports/stores.py` — added `CitationStore`,
  `KnowledgeDocumentStore`, `CitationRecord`, `KnowledgeDocumentRecord`.
- `tests/unit/test_ingest.py`, `tests/unit/test_search.py`,
  `tests/integration/test_citations.py` — new.

M5a's interfaces were read and not changed.

### What was built

**`parse_front_matter(text) -> (dict, str)`.** A small hand-written parser for
the corpus's exact subset: `key: value` scalars and `[a, b, c]` inline lists.
No PyYAML dependency was added. A missing `---` block is an error
(`FrontMatterError`), not a default. Nested maps, block lists, anchors and
duplicate keys raise rather than being silently misread — a wrong value here
becomes a wrong `doc_metadata` row that nothing downstream validates.

**Ingestion.** `ingest_directory` globs `*.md`, skips `README.md` **by name**
(the corpus README has no front-matter and is not a policy document), and
processes files in sorted order. `ingest_document` hashes the whole file first
and short-circuits on an unchanged hash, so re-indexing an unchanged tree
reports `documents_indexed == 0` and writes no chunks —
`docs/api-contract.md` §9. The document's `source` is the **filename**
(`refund-policy.md`), matching `evals/datasets/retrieval.jsonl` and the
citation string `"{document_slug}#{anchor}"`; the parsed front-matter is the
`doc_metadata`. Blocking filesystem calls run through `asyncio.to_thread`
(ASYNC240).

**Retrieval.** `retrieve` embeds the query **once**, searches, and abstains when
there are no hits or the best score is below `min_score` (default `0.35`).
`top_score` is `None` only with no hits. A below-threshold result still carries
its hits so the caller can see the weak evidence, but `abstained=True` so no
answer is built from it — abstention is the escalation path
(`docs/architecture.md` §9), not a failure.

**Stores.** `SqlKnowledgeDocumentStore` upserts on the unique `source` and
`list_documents` returns `(source, title, chunk_count, indexed_at,
content_hash)` — exactly the fields `knowledge.py:47` reads.
`SqlCitationStore.list_citations` returns rows with `.document`, `.chunk`
(composed as `"{source}#{anchor}"`), `.score`, `.rank` — exactly the fields
`runs.py:288` reads. `create_many` resolves `document_id` by `source` and
`chunk_id` by `(document_id, anchor)`.

Both new stores follow the house style: `_SessionBound`, `_scope`, never
commit a caller's session.

### The differential test

`tests/integration/test_citations.py::test_pgvector_and_memory_stores_agree`
carries `@pytest.mark.postgres` and does the real work: it creates the pgvector
schema, upserts the same five chunks into a real `PgVectorStore` and an
`InMemoryVectorStore`, queries **both with one explicitly-constructed vector**
(no embedder in the comparison — it compares the *stores*), and asserts
identical `chunk_id`s and scores to 4 decimal places. It skips where no Postgres
is configured. It is not the `pytest.skip` stub that was left in
`test_vector_stores.py`; per `docs/milestones.md` §M5 it is first proven green in
the M9 CI `verify` job.

### Commands run (full repo, from `G:/OpsPilot`)

```
.venv/Scripts/python.exe -m pytest -q
    415 passed, 9 skipped in 8.43s

.venv/Scripts/python.exe -m mypy --strict
    Success: no issues found in 128 source files

.venv/Scripts/python.exe -m ruff check . && ... ruff format --check .
    All checks passed! / 151 files already formatted
```

The 9 skips are the M0/M2/M4/M6/M8 skeleton placeholders, the two pre-existing
`postgres` stubs in `test_vector_stores.py`, and this milestone's differential
test (no Docker — ADR-0004). None is an M5b code path other than the
differential test, which cannot run here by design.

### Foreign-key ordering

`citations.chunk_id` → `knowledge_chunks.id` and `citations.document_id` →
`knowledge_documents.id` (`docs/data-model.md` §2). A citation is therefore only
writable after its chunk exists. Retrieval guarantees this: a hit comes from a
`knowledge_chunks` row, so ingestion wrote it first. `create_many` re-resolves
the chunk by `(document_id, anchor)` and **skips** a hit with no matching row
rather than writing a dangling FK (which the constraint would reject anyway).
The integration fixtures ingest the document before citing it, which is that
same order.

### Mutation verification

Each break was applied, observed red, and reverted (files diffed back to
identical). Which named test caught each:

| Break applied | Test(s) that failed |
|---|---|
| `_EXCLUDED_FILENAMES` emptied (README indexed) | `test_ingest_directory_skips_readme` |
| `content_hash` skip disabled (always reindex) | `test_reingesting_unchanged_document_indexes_nothing`, `test_ingest_directory_counts_unchanged_run_as_zero` |
| `source` set to the title instead of the filename | `test_ingest_document_uses_filename_as_source` |
| No front-matter returns `({}, text)` instead of raising | `test_parse_front_matter_missing_block_is_an_error` |
| Inline list parsed as a scalar | `test_parse_front_matter_returns_keys_and_body` |
| Indented (nested) line accepted | `test_parse_front_matter_rejects_nested_mapping` |
| No-hit outcome sets `abstained=False` | `test_retrieve_abstains_when_no_hits` |
| Threshold comparison inverted (`>`) | `test_retrieve_abstains_below_threshold`, `test_retrieve_default_min_score_is_0_35` |
| Query embedded twice | `test_retrieve_embeds_the_query_exactly_once` |
| Citation `chunk` string drops the anchor | `test_create_many_resolves_chunk_and_lists_back` |
| `list_citations` ordered rank-descending | `test_list_citations_orders_by_rank` |
| `list_citations` not scoped to the run | `test_citations_are_scoped_to_the_run` |
| `list_documents` hardcodes `chunk_count=0` | `test_list_documents_exposes_the_router_fields` |
| Store `content_hash` always returns `None` | `test_reingesting_unchanged_document_indexes_nothing`, `test_ingest_directory_counts_unchanged_run_as_zero` |

### Least-confident decision

`ingest_document` writes the `knowledge_documents` row through an **optional**
`upsert_document` method it probes for on the store, falling back to a
deterministic `document_id = uuid5(namespace, source)` when the store does not
offer one. The fallback exists only so a bare `VectorStore` fixture is usable;
in production the SQL store provides the method. A reviewer may consider that
duck-typed probe too clever — the alternative would be widening the `VectorStore`
port, which is M5a's frozen interface and out of scope. The fallback id is never
used against a real database.

### M5a interfaces

Nothing in M5a's frozen interfaces was changed. One thing I checked rather than
assumed: the two stores' tie-breaks sort on different columns —
`InMemoryVectorStore` on `(-score, slug, anchor)`, `PgVectorStore` on
`(distance, source, anchor)`. They agree for ranking because `score = 1 -
distance`, and the tie-break is **total**: `split_document` deduplicates anchors
within a document (`notes`, `notes-1`, …), so no two chunks share
`(slug, anchor)`. The differential test's equality to 4 dp is therefore not at
risk from an ordering ambiguity. No change requested.

### Migration

None needed. All three tables already exist in
`migrations/versions/0001_initial_schema.py`; no column was added or altered.

### Reconciliation with M5c

M5c's progress note flags its `retriever` callable shape `(query, top_k) ->
RetrievalOutcome` against the runtime's `RetrievalCallable = (query) ->
list[SearchHit]`. M5b's `retrieve` has the four-argument signature
`(query, *, embedder, store, top_k, min_score)`, so a thin adapter is still the
seam for the M5c/M6 wiring — the two are reconcilable, and neither side here
forced the other's shape.

### M5b — the reviewer's ingest fix: a probe on the wrong store

M5b's own report was accurate about what it built, and its three full-repo
commands came back green when I re-ran them. The defect was in what it did *not*
run: an ingest of the **real corpus**.

`ingest_document` looked for `content_hash` on the object passed as `store` --
the `VectorStore`. The hash lives on the *document* store
(`knowledge_documents.content_hash`), a different table behind a different
object. On the real wiring the probe found nothing, `existing_hash` was always
`None`, and the content-hash skip never fired:

    RUN 1  seen=17 indexed=17 chunks=98
    RUN 2  seen=17 indexed=17 chunks=98   <-- api-contract.md §9 promises 0

Re-indexing an unchanged tree re-embedded all 17 documents every time. The API
contract calls reindex idempotent; it was not.

**Why the tests agreed with it.** `test_ingest.py` handed ingest an
`IngestionStore` facade that forwarded `content_hash` to the real document
store. The code probed for an optional capability and the fixture supplied it;
neither side was wrong about the other, and production supplies neither. This is
the same shape as the M4 store bug -- a test fixture that is more capable than
production, so the suite can pass where the code cannot.

**The fix.** `documents: KnowledgeDocumentStore` is now a required, typed
parameter of `ingest_document` and `ingest_directory`. A caller cannot omit the
store the idempotency check depends on, and a test cannot supply a facade that
answers for it: it must pass the same two objects production passes. The
duck-typed `_upsert_document_row` probe and its `uuid5` fallback are deleted
with it. Verified on the real corpus, and mutation-verified: disabling the skip
turns `test_reingesting_unchanged_document_indexes_nothing` and
`test_ingest_directory_counts_unchanged_run_as_zero` red.

**The lesson, which is the same one five times over now.** A test written from
the implementation agrees with the implementation. The only defence that has
actually worked in this project is exercising the object the *production* code
exercises, and asking the real corpus rather than a fixture.

### M5c — the reviewer's finding: the golden path now abstains

M5c's report was accurate about its own work and its three full-repo commands
came back green when I re-ran them. Two things it flagged in passing are worth
more than the way it filed them.

**1. With retrieval wired as production wires it, the golden path abstains.**

I ran the M4 end-to-end script with `retrieval=stack.retrieval` instead of
`retrieval=None`. The run reaches `completed` with **zero tool calls and zero
citations** -- no `crm.get_customer`, no `billing.list_transactions`, no refund
proposal, no approval. The customer's duplicate charge is never investigated.

The cause is arithmetic, not logic. The abstention branch itself is correct and
its docstring is right that `RETRIEVING -> PLANNING -> RESPONDING` is the legal
route. But `LocalDeterministicEmbedder` scores the whole corpus in a 0.04-0.09
band, and `RETRIEVAL_MIN_SCORE` defaults to 0.35. Measured: the highest score
**any** query reaches against any chunk in `knowledge/` is **+0.0847**. Every
query abstains, answerable or not. The M4 script passed only because it injects
`retrieval=None` -- which takes the "unwired" path that deliberately skips the
threshold -- so the regression is invisible to it.

This is why the retrieval test's own docstring says the local embedder "exists
as plumbing, not as a good embedder". That was honest, and it is now load
bearing: the default configuration cannot complete the golden path.

**2. `test_unanswerable_cases_abstain` passes for the wrong reason.**

It asserts the five `expect_abstention` cases abstain. They do -- along with all
thirteen answerable ones. A test that would pass equally well if the corpus were
empty, or if the threshold were 1.0, is not measuring retrieval quality. It is
the project's recurring pattern once more: the assertion is true, and it does not
mean what its name says.

**Open, and assigned to M6.** Neither is M5c's to fix -- M6 is "the golden
workflow", and it owns the end-to-end path. Recorded here so M6 cannot start
without confronting them:

- The shipped default (`EMBEDDING_PROVIDER=local`, `RETRIEVAL_MIN_SCORE=0.35`)
  makes the golden path abstain. M6 must decide: raise the local embedder's
  quality, lower the documented default threshold, or make the default
  configuration use a real embedder for the demo. Any of the three is a
  documented decision; shipping an abstaining golden path is not.
- The abstention test needs a second assertion it can fail: that an *answerable*
  question does **not** abstain at the configured threshold. Without it, the
  suite cannot tell "retrieval works" from "retrieval always abstains".

### M5e — the reviewer's verification, and what it falsified

Three falsified, everything else held. The verifier's probes are in
`.scratch/` (uncommitted); full repo stayed green throughout.

**F1 (fixed). Three run-detail panels were empty in every real deployment.**
`GET /api/runs/{id}` loads steps, tool calls and citations through
`_optional_method(store, ...)` on the object bound as `RunStoreDep`. That object
is `SqlRunStore`, which had only `create/get/claim_next/set_status`. The three
methods existed on other stores -- `list_citations` on `SqlCitationStore` -- but
not on the one the router holds. The probe therefore found nothing and every list
came back empty: a run detail with no timeline, no tool calls, no citations. The
dashboard's three panels, absent in production since M1.

The suite could not see it, and the reason is the shape of a mistake this project
now keeps making: `tests/integration/fakes.py`'s `FakeRunStore` implements all
three methods, so every run-detail test passed against a double more capable than
the thing it replaced. **A fake that answers every probe means the code never
takes its production branch.** The three methods are now on `SqlRunStore`,
converting stored strings to the domain enums the contract types, and
`tests/integration/test_run_detail_against_the_real_store.py` drives the real
store so the probe's answer is a deployment's answer. Mutation-verified: removing
the three methods reddens all five tests.

**F2 (open, assigned to M6). Citations cannot be written on the shipped SQLite
path, independent of abstention.** `InMemoryVectorStore.upsert` keeps chunks in
process memory and never inserts `knowledge_chunks` rows, while
`SqlCitationStore.create_many` skips a hit whose chunk row is missing (correctly
-- the FK would reject it). Ingesting the real corpus under SQLite yields 17
`knowledge_documents` rows and **0** `knowledge_chunks` rows, so a citation write
has nothing to point at. `wiring.py`'s docstring claims "the citation rows ...
work under SQLite"; that is false. The golden path's 0 citations has *two* causes
-- abstention and this -- and fixing only the threshold would leave the citation
panel empty.

**F3 (open, assigned to M6). The injection document is not retrievable in the
shipped configuration.** With the real stack over the real corpus,
`ignore-instructions.md` ranks 9th at 0.0368 for the golden-path query -- outside
top-5 and far below 0.35 -- and does not appear in any top-10 for
injection-flavoured queries. So `docs/milestones.md` §M5's "at least one
knowledge document carries a prompt injection" is satisfied only *synthetically*:
the test injects a fabricated `SearchHit` with `score=0.99`. The test does prove
something real and worth having -- the gate stops a *complying* model, which is
the property §M5 names as the right thing to assert -- but the "retrieved
injection" premise never occurs in the running system.

**Held, and verified rather than assumed.** All four CI invariants
(`docs/tool-permissions.md` §6) are non-vacuous: each was reddened by injecting a
genuine counterexample row, and invariant 4 is additionally backed by the real
partial unique index. The abstention path takes only legal edges -- instrumented,
not read from the docstring: `received→classifying→retrieving→planning→responding
→completed`, with `RETRIEVING→RESPONDING` never taken. The fourth-server guard
goes red on both the server set and the alias when each is reintroduced.

**The pattern, for the third time in M5 alone.** F1, F2 and the ingest defect
fixed in M5b are the same mistake in three costumes: a test double, or a test
fixture, that is more capable -- or differently wired -- than production. The
suite is green in all three cases, and green means nothing, because the code
under test never runs the way it will run for a user.
