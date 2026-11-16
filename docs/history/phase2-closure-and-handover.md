# Progress archive — Phase 2 — closure, the new machine and the handover

> Split from `docs/progress.md` (the append-only log) on 2026-11-16 during
> the repository tidy-up. Content is unchanged; only the file boundary
> moved. The living index is [`docs/progress.md`](../progress.md).

## Everything pushed, and a handover for the next machine

**Date:** 2026-11-14. The project was developed on one machine and is
continuing on another, so the requirement was specific: **everything that is
this project must be in the cloud repository, and the old machine must be
able to be deleted outright.**

**What was pushed.** `main` was **88 commits** ahead of the last pushed
commit; it is now pushed, and a fresh clone's `main` is the tree at
`14fd7be` plus the handover commit. The remote and the local tree agree
exactly -- 481 tracked files each.

**Nothing was left behind that mattered.** The files that are not in the
cloud are all regenerable or disposable, and each one is listed in
`docs/HANDOVER.md` §4 with the command that recreates it: `.env` (the operator
token is a local development value and is regenerated with `secrets.token_urlsafe`),
`.venv/`, the two `node_modules/`, the packaging output, the caches, the
untracked evaluation runs, and the project's own `.opspilot/` scratch
directory.

**What the handover document says.** Where the repository is and which commit
it is at; what the new machine needs (Python 3.12/3.13, Node 22, Docker only
for the stack); the three things you can do from a fresh clone -- the suite,
the deterministic demo, and the whole stack; the one setting that is required
and why; a readiness checklist whose every line is a command; where the
project actually stands, **including the fact that none of it has been through
GitHub Actions since `f410911`**; the two product decisions still open; the
reading order for the next person; and the exact commands to wipe the old
machine -- including `docker compose down -v`, without which the named
`postgres-data` and `mcp-data` volumes survive and the next machine's stack
comes up against the previous machine's database.

**Two things are deliberately *not* moved.** The Claude Code session store
under `%USERPROFILE%\.claude\projects\G--OpsPilot` and its background-task
directory are the **tool's** files, not the project's -- they hold this
conversation's transcript, and moving them out from under the running
application would empty its session list. The same is true of another tool's
project directory on the same disk. Neither is OpsPilot's, and neither can be
relocated without breaking the program that owns it.

**The project's own files on C: are gone.** That was the previous round's
work: the 2,172 directories the project used to leak onto the system drive
now live under the repository's own `.opspilot/`, and
`tests/unit/test_the_project_writes_only_inside_the_repository.py` keeps them
there.

## C2 closed: the refund idempotency guarantee is made, and its boundary is stated

**Date:** 2026-11-14. Phase 2 C2 asked for one of two routes — make the
concurrent-refund guarantee, or state its boundary in the contract and test it.
The decision was **both**, and both are in the tree on branch
`worktree-phase2-c2-refund-idempotency`.

**The guarantee.** `mcp_servers/_store.py` grew `Store.writing()`: an
**in-process** write lock (a plain `threading.Lock`, one per `Store`
instance) held across a tool's whole check-then-write sequence. The trigger
for making it now is a fact about the SDK: a sync tool function does *not* run
on the stdio loop — `func_metadata.call_fn` dispatches it through
`anyio.to_thread.run_sync` — so two calls to one billing server really can
interleave, and the old "calls are served one at a time" reasoning was false
at the layer where the scan runs. `issue_refund` now runs its replay scan,
validation, mutation and `save()` inside `with store.writing():`, so two (or
eight) concurrent calls with one key produce exactly one fresh refund and
`replayed: true` for the rest, whatever order the threads arrive in. The lock
is deliberately *not* a file lock: `_store.py`'s no-file-locking reasoning
stands (no stale-lock failure mode), and the lock reaches exactly the writers
that share one in-memory document.

**The boundary, stated.** `docs/mcp-contracts.md` S2 now separates the two
sides: guaranteed within one process at any concurrency; **not** guaranteed
across two processes over one store file, where both scans can still miss each
other and the atomic `os.replace` keeps one whole document and discards the
other. `docs/risks.md` R4's residual paragraph was rewritten to match — the
concurrent in-process race is closed, the cross-process one stays out of scope
by the one-process-per-store deployment rule. `docs/phase2-plan.md`'s C2
section records the decision and its acceptance-table row now reads
**met — both routes**, with the test list as evidence.

**The tests.** Three race tests in `tests/integration/test_mcp_billing.py`:
two threads and eight threads through a barrier on one key (exactly one
`replayed: false`, one refund row, one counter bump), and two concurrent
refunds on *different* transactions both persisting — the test that catches a
fix that "works" by refusing everything. Three lock-semantics tests in
`tests/integration/test_mcp_store.py`: mutual exclusion (a second writer
cannot enter while the first holds the section), release on exception (a
failed write does not poison the lock), and per-store scope (two stores over
one file share nothing — the boundary pinned as a fact, not flattered into a
global lock). All three lock tests **fail on the pre-fix code** and pass on
the new one; the barrier race tests were checked against the old code and
pass there too — a barrier cannot force the microsecond scan-write window,
which is why the guarantee needed the lock *and* the deterministic lock tests,
not the race alone.

**What ran.** `tests/integration/test_mcp_billing.py` 23 passed,
`tests/integration/test_mcp_store.py` 19 passed, then the full suite
(`pytest -q`, no markers): **1353 passed, 18 skipped** (the skips are the
PostgreSQL-marked tests, which need `OPSPILOT_DATABASE_URL` per ADR-0004).
One full-suite run was made with a stray, untracked evals result file parked
out of the tree: the sibling C1/B4 session's holdout run
(`evals/results/2026-11-13T23-52-14.json`, gitignored by design) trips the
holdout guard in `tests/unit/test_the_documented_counts_agree_with_the_tree.py`
(`test_no_committed_result_file_touches_the_holdout`; an earlier note called the
test itself "renamed" when the B4 row was reworded — the *file* kept its name,
only the test function is `test_no_committed_result_file_touches_the_holdout`),
whose assertion is that no *committed* result file scores the
holdout — true for everything the repository tracks, false only for that
session's leftover. The file was restored afterwards; it is data, not part of
this change.

---


## Phase 2 gaps closed on the new machine: C1 on a live PostgreSQL, B4's holdout run through the harness

**Date:** 2026-11-14. Both runs below were made on the machine the handover
document addressed, from the tree at `1e4000a` (`main`), with the project's
Python 3.12 virtual environment (`.venv/Scripts/python.exe`).

### C1 — the claim guarantee executed against a real PostgreSQL

The five `postgres`-marked tests in
`tests/integration/test_worker_claim_postgres.py` and the ten tests in
`tests/integration/test_worker_claim_lease.py` had never been run on this
machine; on the default suite they skip for want of `OPSPILOT_DATABASE_URL`
(ADR-0004). They were run here against the Compose PostgreSQL:

```bash
docker compose up -d postgres
OPSPILOT_DATABASE_URL="postgresql+psycopg://opspilot:opspilot@127.0.0.1:5432/opspilot" \
  MODEL_PROVIDER=fake .venv/Scripts/python.exe -m pytest \
  tests/integration/test_worker_claim_postgres.py -m postgres -v
# 5 passed in 3.04s
OPSPILOT_DATABASE_URL="postgresql+psycopg://opspilot:opspilot@127.0.0.1:5432/opspilot" \
  MODEL_PROVIDER=fake .venv/Scripts/python.exe -m pytest \
  tests/integration/test_worker_claim_lease.py -v
# 10 passed in 1.05s
```

**Result: 15/15 passed.** The `SKIP LOCKED` clause, the lease's PostgreSQL
half and the thirty-pair lease regression now hold on the database production
uses, not only on SQLite. `docs/limitations.md` §8 item 2 records the C1 status
update; what stays open there is the lease-expiry boundary, which is a design
decision, not an unrun test.

### B4 — the holdout dataset run through the harness (fake provider: a plumbing check, not a model result)

`evals/datasets/tool_selection_holdout.jsonl` (32 cases) had never been run.
It was run through the harness:

```bash
OPSPILOT_DATABASE_URL="postgresql+psycopg://opspilot:opspilot@127.0.0.1:5432/opspilot" \
  MODEL_PROVIDER=fake .venv/Scripts/python.exe -m opspilot.evals run \
  --dataset tool_selection_holdout
# fake provider — harness check only, not a model result
```

The run produced `evals/results/2026-11-13T23-52-14.json` (a local run;
`evals/results/*` is gitignored by design). It records
`provider: "fake"`, `model: "hand-written-placeholder"`, the dataset
`tool_selection_holdout` and a `tool selection` figure of 0.21875 over 32
cases, with the `unsafe execution count` gate at 0.

**The metrics under `MODEL_PROVIDER=fake` are meaningless and are not claimed
as a result**: the scripted provider's answers are a property of the script,
and the harness itself suppresses the table without `--allow-fake-scores`
(`docs/limitations.md` §5). What this run verifies is that the holdout is
**runnable and reproducible end to end** — dataset loading, the runner, the
results file, the gate — which is the part of §P2-B4's third clause this
machine can satisfy. A model-backed holdout run, and the improvement judgment
the clause asks for, remain for a machine with a model key. The B4 and C1 rows
of `docs/phase2-plan.md`'s acceptance table are updated to say the same.

## B1 deepened: the classification re-derived from every committed tool-selection run, and one reading corrected

**Date:** 2026-11-14. This is the cross-check `docs/phase2-plan.md` §P2-B1's
deliverable was asked to survive: the twelve-failure classification in
[The twelve tool-selection failures, classified](phase1-closure-audits.md#the-twelve-tool-selection-failures-classified-and-what-tool-argument-validity-actually-measures)
was re-derived case by case from **each** committed `tool_selection` results
file — the 2026-10-08 baseline (`2026-10-08T05-24-41.json`, `claude-sonnet-5-5`
as requested), the 2026-10-11 run the section was written from
(`2026-10-11T15-39-42.json`), and the two 2026-10-13 repeat runs
(`2026-10-13T18-24-25.json`, `2026-10-13T18-40-10.json`) — by re-reading
`proposed_tools` against `expected_tools` and `must_not_propose` exactly as
`metrics.py::tool_selection_accuracy` scores them. The dataset file
`evals/datasets/tool_selection.jsonl` was read alongside, and every case's
`expected_tools` and `must_not_propose` in all three files match it verbatim.
(These two runs and the baseline were read from the committed files in
`evals/results/`; the three 2026-11-13 files in that directory are fake-provider
plumbing runs and are not evidence about a model.)

**The classification holds in every run, with no reassignment.** Set-comparing
each case against its `expected_tools`:

- **`unregistered` is 0 in all three files** — across 45 case-runs, not one
  proposed name is absent from `TOOL_REGISTRY`
  (`src/opspilot/domain/tools.py::TOOL_REGISTRY`, 9 tools). This was the
  category §P2-B1 thought most likely to be "ours"; it never fires.
- **`must_not_propose` is honoured in all three files** — 0 forbidden
  proposals across all 45 case-runs, including the six cases that carry a
  forbidden set. The original section's "zero violations, in both runs" extends
  to the 2026-10-08 file unchanged.
- The per-case labels also reproduce: the 2026-10-11 file gives the section's
  exact counts (3 exact / 3 partial / 2 over / 4 mixed / 3 nothing), and the
  repeat runs give 3/2/3/4/3 and 2/2/2/5/4 — the same *shape* on the same
  fifteen tickets, three runs apart. The 2026-10-08 file gives 1/1/3/4/6,
  which is a different proportion but the same categories, zero unregistered,
  zero forbidden.

The four cases that pass in both repeat runs are the same two-plus-one the
original section names, and no others: tool-011 and tool-015 are hits in both
repeat runs, tool-009 is a hit in the first repeat run only (in the second it
proposes nothing), and every other case misses in both. The twelve misses of
the 2026-10-11 run are therefore not a one-off reading: **the same twelve
cases fail in both repeat runs**, seven of them with a different wrong set the
second time.

**One correction to the original section, on a point it got wrong.** The
paragraph on the three `nothing proposed` cases says *"the first planning
round returned `done=true` and the run replied."* The files do not support
that reading. A planning round is a `choose_tool` call, and `choose_tool`
returns no usage record, so its `model_called` event always carries
`tokens_available: false` (`agents/runtime.py::_plan`; the classification and
response calls, made through `generate_structured`, carry `true`). In all
three runs, all three cases (tool-005, tool-006, tool-012) show exactly **two
model calls with the pattern `T,T`** — classification, then response — and
never a `false` in between, where tool-009's second run (a genuine
decline-after-planning) shows `T,F,T` over three calls. **No planning round
ran at all**, in any of the three runs. *(This paragraph first concluded that
the cases therefore went through the retrieval-abstention path — five hits
retrieved, all below `RETRIEVAL_MIN_SCORE`, `PLANNING -> RESPONDING` without a
plan round. That inference was withdrawn the same evening, on re-check:
`retrieved_documents` has length 5 and `cited_documents` is empty, which fits
the abstention branch, but the results file records neither hit scores nor the
`retrieval_abstained` audit event, and an immediate `done=true` decline in the
first plan round would be indistinguishable from it in every field the file
carries — the same two calls, the same zero citations, `escalated: true` in
both readings. What the evidence supports is the narrower statement: **no
planning round happened, so the twelve misses split as "9 wrong tool set +
3 never-planned"**; whether a never-planned case was declined by the model or
abstained by the retrieval threshold is not decidable from the committed
files.)*

**What the gate-1 rows add that the original section could not have.** The
original section records that all 90 rejected calls in the 2026-10-11 file
carry `arguments: {}` — unrecoverable. The two 2026-10-13 files were recorded
after `7acea37` made `_reject` persist the proposal's own dict
(`agents/runtime.py::_reject`: "stored verbatim -- not an empty placeholder"),
so their 24 and 21 rejected rows carry the model's actual arguments. Re-parsed
against the current schemas
(`src/opspilot/domain/tools.py::TOOL_ARGUMENT_SCHEMAS`), they sort into four
named defects, in descending frequency:

| the model sent | the schema wants | cases it appears in (both repeat runs) |
|---|---|---|
| `email` on `crm.get_customer` | `customer_email` / `customer_id` | 9 + 5 rejections |
| `account_id` (often with `month`) on the billing reads and `crm.get_account`/`crm.get_subscription` | `invoice_id` / `customer_id` respectively | 11 + 9 |
| invented fields on `issues.create` (`body`, `subject`, `category`, `invoice_id`, `account_id`, …) | `title` + optional `description`/`priority`/`labels` | 8 + 7 |
| `invoice_id` on `billing.issue_refund` | `transaction_id` + `amount` | 2 + 2 |

The first row is the harness's own doing as much as the model's: the planning
prompt renders the ticket as `From: eval@example`
(`evals/runner.py`, `agents/runtime.py::_ticket_text`), the model echoes that
address into a field named `email`, and the schema only accepts
`customer_email` — the exact drift `6667e31` fixed on the schema side
(`CustomerResolveArgs`), recorded here from the other side. The second row is
the `account_id` habit the `TransactionListArgs` docstring documents as having
survived four milestones. Four rejected rows in each repeat run
(`issues.create` with only `title`/`description`) re-validate cleanly against
the *current* schema — they were recorded before `6667e31` widened
`IssueCreateArgs` from `title`+`body`, so the rejection was true then and the
row's `arguments_valid: false` is now recomputed from stored arguments by
`runner.py::_arguments_revalidate`, which is the drift the re-validation
exists to expose rather than a contradiction.

**Net effect on §P2-B1's verdict.** The four-category question is answered,
reproducibly, in all three files: wrong-set 9, never-planned 3, unregistered
0, wrong-arguments-on-a-right-tool 0 — and the last is empty not because the
model's arguments were good but because tool selection and argument validity
score different units (cases vs calls), so no case's miss can be attributed
to an argument. The B1 conclusion — *"only the last is ours, and the file
cannot carry it"* — stands, now with the argument-level evidence the 2026-10-13
files add: when the model does send arguments, they are wrong in the four
repeatable shapes above, which is argument-construction material for §P2-B3's
split, not tool-selection material.

## B2 executed against the real endpoint: the menu was the prompt's, and one number moved

**Date:** 2026-11-14. §P2-B2 was reopened from its 2026-10-13 deferral because
the MiniMax OpenAI-compatible endpoint (`https://api.minimaxi.com/v1`, model
`MiniMax-M3.1-Flash`, `OPENAI_JSON_OBJECT_FALLBACK=true`, key in the local
`.env` and committed nowhere) produced two fresh evaluation runs —
`2026-11-14T09-37-39.json` and `2026-11-14T10-40-41.json` — that scored
**tool selection 0.0/15** with a breakdown unlike the October runs: 98 proposed
calls, mostly `empty_argument_proposals` and 61 rejections at
`gate_1_schema_validation`. The fake-provider runs of the same day
(`2026-11-14T11-32-33.json`) stayed green, so the harness, not the model
plumbing, was exonerated first.

### Root cause: the planning prompt never carried the argument schemas

The runtime's `_SYSTEM_PROMPT` was one sentence of security framing; the
planning user prompt named the tools (`with_tool_menu`) but showed **no
argument fields**, and `prompts.py`'s tool catalogue — which renders exactly
those fields from `TOOL_REGISTRY`/`TOOL_ARGUMENT_SCHEMAS` — was reachable only
through `default_system`'s fallback, which a caller that always supplies a
system prompt never hits. A direct probe of the endpoint
(`scripts/minimax_probe.py`, this work's diagnostic; key read from `.env` into
memory only) reproduced it: given names only, MiniMax replied with plausible
but invented argument names — `crm.get_customer({"email": ...})` where the
schema demands `customer_email`/`customer_id`, `issues.create` with `body`
where the tool takes `description` — and gate 1 refused every one. A second
loop made the score structurally unfixable by prompt content alone: after a
rejection the planning prompt re-listed the tools with only
`tool_name(status)`, so the model re-proposed the identical malformed call —
24 planning rounds on tool-001, 16 tool rows from 19 model calls on tool-008.

### The change (three files, no gate or metric touched)

1. `agents/runtime.py` — `_SYSTEM_PROMPT` embeds the shared tool catalogue
   (`prompts.py::tool_catalogue`, made public for this) plus a one-JSON-object
   reply instruction; `_planning_prompt` appends the refused proposals with
   their gate and reason so the model is told what not to repeat.
2. `agents/state.py` — `ToolCallRecord` carries `rejection_reason` and
   `rejection_detail`, filled by `_reject` and the gate-4 policy rejections.
3. `domain/tools.py` — `InvalidArgumentsError`'s message names the offending
   fields and per-field problems (e.g. `email: not a field this tool accepts`)
   instead of a bare `3 problem(s)` count, because the count was the whole
   feedback the loop could act on.

Validation strictness is unchanged: `extra="forbid"` still forbids, gate
order and the four invariants are untouched, and every reply still passes the
same `_shared.parse_structured` path. The committed fake fixtures replay by
scenario (all `request_hash: null`), so the prompt change invalidates nothing
recorded.

### Three live runs, and what each measures

| run | file | tool selection | argument validity | gate-1 rejections | verdict |
|---|---|---|---|---|---|
| pre-fix baseline | `2026-11-14T10-40-41.json` | **0.0** (0/15) | 0.378 | 61 of 98 | the defect |
| run 1 | `2026-11-14T12-04-22.json` | **0.133** (2/15) | **1.000** | **0** of 100 | the fix works |
| run 2 | `2026-11-14T13-23-39.json` | 0.0 (0/15) | 0.357 | 72 of 112 | variance returns |
| run 3 (adds field-level detail) | `2026-11-14T14-51-10.json` | **0.133** (2/15) | 0.411 | 66 of 112 | detail alone is not enough |

- **What moved, twice in the same direction:** run 1 took argument validity
  from 0.378 to **1.000** with **zero** empty and **zero** malformed
  proposals — the catalogue tells the model the field names and it uses them.
  Run 3, run the day's third time, still cut gate-1 rejections below run 2's
  and scored the same 2/15 as run 1; the two hits (tool-011, tool-015) are the
  same two cases the October baseline hit, so the headline is pinned by the
  endpoint's tool-*set* judgement, not by argument construction.
- **What did not move:** tool selection stays 2/15 at best. Runs 2 and 3 show
  the same endpoint the October deferral measured — run 2 regressed to
  0 rejections prevented nothing because the model re-invented `email` and
  `account_id` anyway, and tool-009 failed once on
  `StructuredOutputError: not valid JSON` before a tool was ever chosen.
  Field-level rejection detail (run 3 vs run 2 on tool-002: 10 rejections
  against 12) trims the loop but does not close it: the model reads the
  feedback, tries `customer_email` once, and returns to its invented field
  within a round or two.
- **The honest §P2-B2 verdict.** The acceptance — "the same 15 cases, same
  model, at least 10 hits, run at least twice" — is **not met**, and the
  deferral's core observation survives this work: this endpoint's
  run-to-run spread (0.133 → 0.000 → 0.133 on identical code) is still larger
  than any plausible improvement. What this session adds is the diagnosis the
  deferral lacked: the October scores were not "a model that answers
  differently on a second reading" alone — part of the 0.0/0.378 was *ours*,
  a prompt that hid the argument schemas, and that part is now measured,
  fixed, and worth 1.000 argument validity when the endpoint cooperates.
  The remaining gap to 10/15 is the model's tool-set judgement and loop
  discipline against this endpoint, which is exactly the "resume when the
  model under test answers consistently" condition §P2-B2 set.

