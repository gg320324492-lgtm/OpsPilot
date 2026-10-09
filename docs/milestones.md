# Phase 1 Milestone Plan

Nine milestones. Each is a separate, reviewable unit with acceptance criteria
written before the code, and each ends in a commit. The order is not negotiable:
the golden workflow (M6) precedes the dashboard (M7) and the evals (M8) because a
dashboard that displays a workflow that does not work is a liability, and evals
written against an unbuilt workflow measure nothing.

**Working method, per milestone:**

1. State the acceptance criteria in the milestone's section of `docs/progress.md`.
2. Write the tests that express them.
3. Implement until those tests pass.
4. Run the focused suite, then the full suite.
5. Record what was learned and what is still wrong.
6. One commit, `feat|test|fix:` subject, explaining the *why*.

A milestone is not "done" because the code exists. It is done when its acceptance
criteria are checked and the full suite is green.

---

## M0 — Architecture and skeleton

**Deliverable.** This `docs/` set, a repository skeleton that installs and
imports with no business logic, and a published GitHub repository.

**Acceptance criteria**

- [ ] `docs/architecture.md`, `agent-state-machine.md`, `tool-permissions.md`,
      `data-model.md`, `api-contract.md`, `mcp-contracts.md`, `evals.md`,
      `risks.md`, `limitations.md`, `milestones.md`, `adr/*` exist and are
      internally consistent.
- [ ] The five core principles are stated with their enforcement mechanism, not
      as slogans.
- [ ] The repository tree exists with `py.typed`, and `pip install -e ".[dev]"`
      succeeds.
- [ ] `python -c "import opspilot"` works.
- [ ] `ruff check` and `ruff format --check` are clean.
- [ ] `pytest` runs and reports 0 tests collected (no tests yet) without error.
- [ ] `.env.example` has every setting the code reads, with an empty value.
- [ ] GitHub repository created, `main` pushed.
- [ ] **No business logic.** `domain/`, `agents/`, `adapters/` contain
      docstrings explaining their responsibility and nothing else.

**Explicitly not in M0.** No models, no endpoints, no MCP servers, no agent loop.

---

## M1 — Database and API skeleton

**Acceptance criteria**

- [ ] All eight tables from `data-model.md` exist as SQLAlchemy models.
- [ ] One Alembic migration creates them, including
      `CREATE EXTENSION IF NOT EXISTS vector`, the partial claim index and the
      partial unique index on `tool_calls.idempotency_key`.
- [ ] `alembic upgrade head` runs against a pgvector container in CI.
- [ ] `POST /api/tickets` inserts a ticket and a `RECEIVED` run in one
      transaction and returns 201 **without starting any agent work**.
- [ ] `GET /api/tickets`, `GET /api/runs`, `GET /api/runs/{id}`, `/health`,
      `/ready` exist and validate their inputs.
- [ ] Error responses use the single shape from `api-contract.md §6`.
- [ ] Auth rejects a missing or wrong bearer token on every endpoint but
      `/health`, and the API refuses to start with no token configured.
- [ ] Repository classes exist with no business logic — `ToolCall`, `Run`,
      `Approval` stores with CRUD only.
- [ ] Tests: `tests/unit/test_models.py`, `tests/integration/test_api_tickets.py`.

---

## M2 — MCP servers

**Acceptance criteria**

- [ ] Three servers run as real MCP servers over stdio, started by the test
      fixture as subprocesses.
- [ ] Every tool in `mcp-contracts.md` exists with its declared input and output
      schema.
- [ ] Seed data committed as JSON; a `--reset` flag and a pytest fixture load it.
- [ ] `billing.issue_refund` mutates state and is idempotent on
      `idempotency_key`, with a `UNIQUE` constraint in its own store.
- [ ] A second call with the same key returns the same `refund_id` with
      `replayed: true` and creates no second refund row.
- [ ] Every refusal case in `mcp-contracts.md §2` returns its documented error
      code.
- [ ] `crm.get_customer` with neither or both conflicting identifiers returns
      `validation_error`.
- [ ] Tests: `tests/integration/test_mcp_crm.py`, `test_mcp_billing.py`,
      `test_mcp_issues.py` — called directly, with no agent and no LLM.

---

## M3 — Tool gateway, policy and approval

**The critical milestone.** Everything the project claims rests on this one.

**Acceptance criteria**

- [ ] `TOOL_REGISTRY` with all nine tools and their static permissions.
- [ ] `tests/security/test_permission_immutability.py` asserts the registry
      equals a hard-coded literal.
- [ ] `ToolGateway` port; `MCPToolGateway` implements it; `StubGateway` for tests.
- [ ] The five gates are implemented in order with no bypass path:
      schema → registry → permission → policy → approval.
- [ ] `HIGH_RISK_WRITE` without an approved `ApprovalRequest` for that exact
      `tool_call_id` raises `RunParked` and executes nothing.
- [ ] Approving a call for `TX-88219` does not authorise a call for `TX-11111`.
- [ ] Arguments cannot be mutated between approval and execution
      (`ApprovalArgumentsChanged`).
- [ ] `issues.create` executes automatically **and** writes a `tool_executed`
      audit event before returning.
- [ ] Unknown tool name → `ToolCall` recorded as rejected, never dispatched.
- [ ] `OPSPILOT_TOOL_DENYLIST` can refuse a tool and **cannot** grant one.
- [ ] The four security invariant queries return 0 after the whole suite.
- [ ] `tests/security/` has an adversarial test per row of
      `tool-permissions.md §5`'s right-hand column.

**Why this is the milestone that matters.** If the gate has a hole, the rest of
the project is a demonstration of a system that does not do what its README says.
M9's CI gates depend on this being airtight, and M8's safety metrics measure it.

---

## M4 — Agent runtime

**Acceptance criteria**

- [ ] `RunStatus`, `ALLOWED_TRANSITIONS`, `AgentRun.transition_to()` in
      `domain/runs.py`, importing only `enum` and Pydantic.
- [ ] A test iterating `RunStatus` requires every state to have a transitions
      entry, so adding a state without deciding its edges is red.
- [ ] `COMPLETED → EXECUTING` raises `IllegalTransition`.
- [ ] `ModelProvider` protocol with `generate_structured`, `choose_tool`,
      `generate_text`.
- [ ] `FakeModelProvider` replays **recorded** responses from
      `evals/datasets/fixtures/`, not hand-invented scripts.
- [ ] `AnthropicProvider` and `OpenAIProvider`, sharing one prompt-assembly and
      structured-output path.
- [ ] `LinearOrchestrator` drives a run end to end with the fake provider.
- [ ] `LangGraphOrchestrator` produces the same transition sequence on the same
      fixture, asserted as a subsequence of the legal set.
- [ ] `MAX_STEPS` exceeded → `FAILED(max_steps_exceeded)`.
- [ ] Every transition writes an `AgentStep(step_type='state_change')` in the
      same transaction as the status update.
- [ ] `agent_steps.sequence` is unique per run.
- [ ] Tests: `tests/unit/test_run_state.py`, `tests/agent/test_runtime_fake.py`.

---

## M5 — RAG

**Acceptance criteria**

- [ ] 15–30 Markdown documents in `knowledge/` with front-matter, genuinely
      overlapping rules (the `refund-policy.md` / `enterprise-billing.md`
      \$100-approval pair is mandatory).
- [ ] `ingest.py`: front-matter → document row; heading-aware chunking with
      overlap; `content_hash` skips unchanged documents.
- [ ] `knowledge_chunks` populated with `anchor` and `heading_path`.
- [ ] `pgvector` search returns top-k with scores; the SQLite path uses the
      in-memory store behind the same port.
- [ ] **Differential test** (Postgres CI job): same chunks, same query vector,
      identical top-5 ids and scores to 4 dp in both stores.
- [ ] Retrieval results carry `(document, chunk, score, rank)` and are persisted
      as `Citation` rows.
- [ ] Below `RETRIEVAL_MIN_SCORE` → the run escalates; the retrieval dataset's
      `expect_abstention` cases assert this.
- [ ] `knowledge.search` is a registered `READ` tool reachable through the
      gateway.
- [ ] **At least one knowledge document carries a prompt injection** and a test
      asserts it cannot change a permission or cause an unapproved execution.
- [ ] Tests: `tests/integration/test_retrieval.py`,
      `tests/security/test_prompt_injection.py`.

---

## M6 — The golden workflow

**The milestone the project exists to reach.**

**Acceptance criteria**

- [ ] The exact ticket from the README drives the exact documented sequence:
      `billing_dispute` → retrieve → `crm.get_customer` → `billing.get_invoice`
      → `billing.list_transactions` → duplicate detected → propose refund →
      `HIGH_RISK_WRITE` → `WAITING_APPROVAL` → approve → refund executed once →
      customer reply → `COMPLETED`.
      (An earlier wording of this criterion ended "→ issue created →", but the
      committed `duplicate_charge` fixture proposes no `issues.create` call, so
      the workflow does not perform that step. The criterion was corrected to
      match what runs rather than the fixture changed to match the criterion --
      `issues.create` is a real `SAFE_WRITE` tool, exercised in isolation by the
      gateway and gate tests, and whether the golden path *should* file an issue
      is an open product decision recorded in `docs/limitations.md` §8.)
- [ ] The trace shows every step with latency, and the citations are the two
      expected documents.
- [ ] Reject path: `WAITING_APPROVAL` → `RESPONDING` → `COMPLETED` with an
      escalation reply and **no refund**.
- [ ] Already-refunded: detected, no refund proposed, no approval needed,
      `COMPLETED`.
- [ ] Re-run after interruption does not double-refund
      (`errors.md` C2 in `risks.md`).
- [ ] MCP server down → `FAILED(mcp_unavailable)` cleanly, with no partial write.
- [ ] The worker parks on approval **and remains alive**; approving later resumes
      the run under a worker that has restarted in between.
- [ ] `tests/agent/test_golden_path.py` and five scenario tests from the README.
- [ ] The five README scenarios all pass against the fake provider.

---

## M7 — Dashboard

**Acceptance criteria**

- [ ] Next.js dashboard with Dashboard, Tickets, Runs, Run Detail, Approvals,
      Knowledge.
- [ ] Run Detail renders the timeline from `GET /runs/{id}/trace` server-ordered.
- [ ] Approval card shows tool, arguments, the model's `reason`, the
      deterministic `risk_explanation` and the risk level, visually distinguishing
      trusted from untrusted text.
- [ ] Approve and Reject work and use the 409 semantics on a repeat click.
- [ ] The "Sources" panel renders citations from the run's `Citation` rows.
- [ ] `eslint` and `tsc --noEmit` clean in CI.
- [ ] The golden path is walkable in a browser start to finish.

---

## M8 — Evals and security

**Acceptance criteria**

- [ ] Four datasets, 50–80 cases total, in `evals/datasets/`.
- [ ] `runner.py` isolates each case in a fresh database.
- [ ] `runner.py` refuses to print scores for `--provider fake` without
      `--allow-fake-scores`.
- [ ] All twelve metrics from `evals.md §1` computed and printed, with counts.
- [ ] `unsafe execution count` exits non-zero when non-zero.
- [ ] Results written to `evals/results/<timestamp>.json` with the provider,
      model, date and config recorded.
- [ ] One live run performed; its real numbers, with the model and date, go in
      the README. **No fabricated scores.**
- [ ] The safety dataset's injection case passes with a provider scripted to
      comply with the injection.

---

## M9 — CI and documentation

**Acceptance criteria**

- [ ] GitHub Actions jobs: `lint`, `typecheck`, `test` (3.12/3.13), `verify`
      (pgvector container: migrations, vector differential, worker concurrency),
      `mcp-contract`, `security`, `eval-smoke`, `web-lint`, `web-typecheck`,
      `docker-build`.
- [ ] `eval-live` on `workflow_dispatch` only.
- [ ] `docker compose up` brings up api, worker, web, postgres, and the three
      MCP servers (as child processes of the worker under
      `MCP_TRANSPORT=stdio`; the default `inprocess` runs them inside it).
- [ ] README: the golden-path trace, the honest-numbers eval table, an
      architecture diagram, the five demo scenarios, a recorded GIF from the
      deterministic path, and a limitations section that links
      `docs/limitations.md`.
- [ ] `docs/progress.md` records each milestone's outcome, including what went
      wrong.
- [ ] The repository's own Definition of Done checked line by line, with any
      unmet item named rather than omitted.

### Definition of Done

The DoD the last criterion above refers to. It is **derived from claims the
repository already makes about itself**, not invented here: every line traces to
a document, a README statement, or a CI invariant that predates this audit. An
item is included only because something in the repository already asserts it —
there are no aspirational additions, because a DoD invented at the end describes
what got built rather than what was promised.

| # | Item | Derived from |
|---|---|---|
| D1 | `ruff check` is clean. | M0 acceptance criteria; README "Running it". |
| D2 | `ruff format --check` is clean. | M0 acceptance criteria. |
| D3 | Bare `mypy` (no path argument) reports no errors. | README "Tests"; `pyproject.toml [tool.mypy] files`. |
| D4 | `pytest` is green, with the skip count printed and non-zero. | README "Tests"; ADR-0004. |
| D5 | The four CI invariants each return 0 after the whole suite. | `tool-permissions.md` §6; README "Evaluation". |
| D6 | The four safety properties in `limitations.md` "What is *not* on this list" hold: the permission model is verified; the refund cannot execute twice; retrieved text cannot escalate a permission. | `limitations.md` §"What is not on this list". |
| D7 | Every documented tool call actually executes: `issues.create` runs automatically and writes a `tool_executed` audit event. **The call was deliberately not added to the golden path — see D19a below.** | `milestones.md` §M3; `architecture.md` §7. |
| D8 | Every setting the code reads appears in `.env.example` with an empty value. | M0 acceptance criteria. |
| D9 | `python -c "import opspilot"` works after `pip install -e ".[dev]"`. | M0 acceptance criteria. |
| D10 | The CI jobs §M9 names exist and run the commands the README documents. | `milestones.md` §M9; README "Tests". |
| D11 | `eval-live` runs on `workflow_dispatch` only. | `milestones.md` §M9; `limitations.md` §5. |
| D12 | `docker compose up` brings up api, worker, web, postgres, three MCP servers. | `milestones.md` §M9. |
| D12a | The three MCP servers exist and are reachable, but as **child processes of the worker**, not as Compose services. A stdio server has no port to expose, so a Compose service for one would be a container that starts, blocks on stdin and serves nothing. `MCP_TRANSPORT=stdio` is what brings them up as subprocesses; the default `inprocess` runs them inside the worker with no process boundary at all. Both are real; only the first exercises the transport. | `mcp-contracts.md` §"Why stdio and not HTTP/SSE"; `docker-compose.yml` footer. |
| D13 | The Compose stack has been run at least once. | `docker-compose.yml` header ("NOT VERIFIED"); `limitations.md` §6. |
| D14 | README carries the golden-path trace, the eval table, an architecture diagram, the five demo scenarios, a recorded GIF, and a limitations section linking `docs/limitations.md`. | `milestones.md` §M9. |
| D15 | The README's eval table matches the results file it cites. | README "One live run". |
| D16 | The tests `limitations.md` names as existing actually exist. | `limitations.md` §2. |
| D17 | `docs/progress.md` records every milestone, including what went wrong. | `milestones.md` §M9. |
| D18 | The README's status banner agrees with the milestone that is actually done. | README status banner; `milestones.md` working method. |
| D19 | No documented capability is unimplemented (`MCP_*_COMMAND` settings read by no code, etc.). **Its last strand is recorded as boundary D19a — an operator decision of 2026-10-09, a boundary rather than unfinished work.** | `architecture.md` §8; the pattern of §M3/§M5. |
| D19a | `issues.create` is real, is `SAFE_WRITE`, is covered in isolation, and is **deliberately not a step of the golden path**: the committed `duplicate_charge` fixture proposes four calls and none of them creates an issue, so the run creates nothing and the README trace no longer draws the line. Recorded rather than closed, for three reasons: README:89-98's own rule that adding a call to make a diagram true would be writing code to match a drawing; D7 and D12a, where the document was corrected to match the behaviour rather than the behaviour to match the document; and whether the workflow **should** file a ticket is a Phase 2 product question already recorded in `risks.md` C1. Recorded as a boundary rather than quietly ticked. | README:89-98; `limitations.md` §8; `risks.md` C1. |

The audit result for each line, with the unmet and unverifiable ones named, is
recorded in `docs/progress.md` under §M9.

---

## Risk to the schedule

Ordered by how likely each is to cost a milestone:

1. **M3 (gates)** is where correctness is won or lost. If the approval gate has a
   hole, everything after it is built on sand, and it is cheap to discover in M3
   and expensive in M8.
2. **M4's LangGraph adapter** can drift from the domain state machine; the
   subsequence test exists because this is expected, not hypothetical.
3. **M7 (dashboard)** has the worst ratio of time-to-visible-value. It is
   deliberately last among the functional milestones and is scoped to the six
   screens, because a dashboard is the easiest place to spend a week on
   everything except the workflow.
4. **M5's differential vector test** needs the Postgres CI job working before it
   can run at all, so it is written in M5 but first proven green in M9. That
   sequencing is a known gap and is why the test is written even though it cannot
   pass locally.
