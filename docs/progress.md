# Phase 1 Progress

Append-only log of milestone outcomes. Each entry records what was built, what
the acceptance criteria came out as, and **what was learned or went wrong** —
because the second item is the one that stops the next milestone repeating it.

Status: `not started` · `in progress` · `done` · `blocked`

| Milestone | Status | Notes |
|---|---|---|
| M0 — Architecture and skeleton | done | See below |
| M1 — Database and API skeleton | done | See below |
| M2 — MCP servers | not started | |
| M3 — Tool gateway, policy, approval | not started | |
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
