# Progress archive — Phase 3 — the evidence, the real-model runs and the releases

> Split from `docs/progress.md` (the append-only log) on 2026-11-16 during
> the repository tidy-up. Content is unchanged; only the file boundary
> moved. The living index is [`docs/progress.md`](../progress.md).

## C4 root-cause hypotheses: what would make a worker not claim a run, and how each would announce itself

**Date:** 2026-11-14. CI run 38202887580's first `web-e2e` failure left a run
at `received` with zero steps for a 90-second wait, and
[the diagnosis section](phase1-closure-audits.md#ci-run-38202887580s-first-failure-the-cause-is-not-established-and-the-artifact-cannot-establish-it)
established that the artifact cannot say why: the worker's own log was never
captured, the page snapshot is identical for four of the five candidate
causes, and the cause was never established. The instrumentation added since
(`assessWorker`, the per-spec compose capture, `inspect.json`'s restart
counts, the CI log step) exists to make the *next* occurrence diagnosable.
This section is the concrete form of "next time": five root-cause hypotheses,
each with the log signature that would confirm it, the check that rules it
out, and a reproduction. They are ordered by how well the surviving evidence
fits them. All five assume the pre-flight passed — `docker compose up -d
--wait` returned, which it did (the run reached the browser and submitted a
ticket).

**H1 — the worker was up and polling, and the run was unclaimable because of
stale claim state in the reused database.** The e2e stack tears down with
`down -v` before `up`, but a *previous* run's containers are not the only way
state survives: if the teardown's `down -v` had been skipped or failed
silently on an earlier job, `postgres-data` would carry rows from a previous
stack, including a run holding an unexpired `claimed_until` written by a
worker that no longer exists. `claim_next`'s predicate excludes rows whose
lease is live (`repositories.py::SqlRunStore.claim_next`, the
`claimed_until < now` clause), so the new worker polls a database in which the
only claimable run is invisible to it, and the page renders `received` with no
steps — the exact failure shape. *Signature:* worker log shows the boot line
and nothing else (the worker is healthy and idle); `docker compose exec
postgres psql -U opspilot -c "select id,status,claimed_by,claimed_until from
agent_runs"` shows a foreign `claimed_by` on the run. *Rule out:* the
`down -v` in `startStackOrSkip` runs with `stdio: "inherit"`, so its failure
is loud, and the CI job runs on a fresh runner each time — which makes this
the least likely of the five on CI and the most likely **locally**, where a
killed run leaves volumes behind. *Reproduce:* start the stack, claim a run
through `SqlRunStore.claim_next` with a stand-in worker id and a 300 s lease
(exactly what `claim-lease.spec.ts` does via
`claimOldestRunThroughTheRepository`), stop the worker, start a second stack
without `down -v`, submit a ticket, and watch it sit unclaimed for the lease's
remainder. This is the one hypothesis that was **reproduced by design** —
`docs/limitations.md` §2 records the 300 s unclaimed window measured on a
live stack.

**H2 — the worker booted, printed its boot line, and then crashed after boot,
leaving the run unclaimed.** At the failing commit (`b8965d5`), the boot sweep
marked mid-flight runs `FAILED('interrupted')` but deliberately skipped
`RECEIVED` (`db.py::mark_interrupted_runs`: "RECEIVED has no work to
interrupt"), so a worker that died *after* booting but *before* claiming left
the new run at `received` forever. A crash after boot has more causes than a
crash before it: the first `drain_once` claim is the first database write the
process makes, and at that commit `MaxStepsExceeded` — which `run_loop` raises
after `_fail_run` — was **not** in `drain_once`'s suppression set
(`contextlib.suppress(RunParked, MCPUnavailable)` only; the third member was
added at `5f88201`), so any unexpected exception in the first drive escaped
`poll_forever`, which has no handler, and killed the process with
`restart: unless-stopped` restarting it into the same crash. *Signature:*
`docker compose logs worker` shows the boot line, then a traceback, then a
second boot line — the crash-loop shape `assessWorker` now detects via
`RestartCount > 0`; `docker compose ps -a` shows `Restarting`. *Rule out:* the
`inspect.json` capture now records `RestartCount` per container, so a worker
that came back would be visible where the failing run's artifact had nothing.
*Reproduce:* the crash-loop injection `stack-diagnostics.spec.ts` already
carries as a fixture (`BOOT_THEN_CRASH_LOG` with three boot lines and three
`refusing to start` lines) is this hypothesis's shape; driving it live needs
only a fault injected after the boot line.

**H3 — the worker container was never created, or compose brought up a stack
whose worker service failed its dependency gate.** The worker service
`depends_on` postgres (healthy), migrate (completed successfully), and api
(healthy on `/ready`). If the api's readiness had flapped — healthy at the
`--wait` gate, failing a re-check — or if the worker's container create had
failed (image pull, port conflict on the compose network), `docker compose up
--wait` would have failed the step before the browser ever ran. Since the run
got as far as a submitted ticket, the *stack* was up; the residual version of
this hypothesis is narrower: compose reported the worker as started while its
process was still in `build_worker()` — which resolves settings, probes the
persistence layer, and probes the provider SDK (`_require_provider_sdk`,
added after an earlier worker died on a missing `anthropic` extra) — and the
browser's 90-second window closed before the worker ever reached
`poll_forever`. *Signature:* the worker's log has **no boot line at all**
(`assessWorker` returns `silent` for exactly this), while `ps -a` shows the
container `Up`; the boot line is printed only after every probe passes
(`worker/__main__.py::_run`). *Rule out:* a worker that takes more than 90 s
to boot on a warm cache is not plausible — the boot line appears within
seconds on this machine and in CI — but the check is one `docker compose logs
worker`. *Reproduce:* set an env var that makes `build_worker` raise
(`WorkerConfigError`), `up -d` the stack, and confirm `assessWorker` reports
`silent` while `--wait` still passes, because the worker has no healthcheck to
fail.

**H4 — the worker was polling, claimed the run, and the claim was lost to a
transaction boundary, rendering the run unclaimed again.** At the failing
commit the claim took the row `FOR UPDATE SKIP LOCKED` but wrote nothing —
the claim *was* the open transaction, and when `drain_once`'s drive raised or
the session closed without a status transition, the row returned to the pool
with no trace. The diagnosis section rules the *symptom* version of this out
for the failing run (the run was ~1.5 s old and the wait never re-checked),
but as a root cause for the *class* it survives: the fix that landed
(`b9bbd5c`, the claim lease) exists precisely because this was real — thirty
of thirty concurrent claim pairs returned the same row. The residual risk
after the lease is the one `docs/limitations.md` §2 records: a claimed run
whose holder dies stays `received` and unclaimable until the lease lapses.
*Signature:* indistinguishable from H1 in the page, and in the worker log
identical to idle — this is the hypothesis the new instrumentation is
least able to confirm after the fact and most able to rule out live (a
`claimed_by`/`claimed_until` on the row, plus a healthy polling worker whose
log shows the boot line only). *Rule out:* `claim-lease.spec.ts` drives
exactly this state against a live stack and asserts the page now renders the
holder and the deadline; a red there with a green worker log is this
hypothesis confirmed. *Reproduce:* the same stand-in-claim sequence as H1,
with the worker left running to show the page's "held by" sentence.

**H5 — the worker was never a problem: the wait was structurally incapable of
observing a healthy worker's work.** This is the diagnosis section's own
finding, restated as a hypothesis because it is the one the evidence actually
supports. At `b8965d5` the park-wait was a single `goto` followed by a bare
`toBeVisible` with a 90-second timeout — no re-navigation, no polling —
against a Run Detail page that is a pure Server Component with no client
refresh. A worker that claimed the run at t+2 s and parked it at t+3 s would
**still** fail this wait unless the single navigation happened to land after
the park; the screencast's thirteen frames (twelve in the first second, then
nothing for 90 s) show the page was rendered once and never again. The
current spec polls with `expect.poll` re-navigating every 2 s, which is the
fix. *Signature:* worker log normal, run actually reaches `waiting_approval`
in the database while the browser times out — checkable after the fact only
by querying the row. *Rule out:* the current spec's poll loop, which re-reads
the page; a worker that parks within 90 s now passes, and the suite has been
green since. *Reproduce:* run the `b8965d5` spec against a healthy stack —
the failure is timing-dependent, which is why it did not recur.

**Which hypothesis the next failure should be read as.** The diagnostic order
is now mechanical: read `inspect.json` for `RestartCount` (H2), read
`worker.log` for the boot line (H3 if absent, H1/H4/H5 if present), query
`agent_runs.claimed_by`/`claimed_until` (H1/H4 if a foreign or lapsed lease
sits on the row), and only then suspect the wait itself (H5), which the
current polling spec should already have ruled out. The five are not
mutually exclusive — H5 is what made every other hypothesis invisible in the
failing run — but each now has a capture that would name it, which is the
second and third limbs of §P2-C4's acceptance made concrete.

## C4 discrimination experiments: the five root-cause hypotheses, tested

**Date:** 2026-11-14. The five hypotheses above were put to discriminating
experiments on a live Compose stack (branch `worktree-c4-discrimination`,
base `e504eee`). Each was driven the way an operator would reach it — the
database seeded the way a dead worker leaves it, the worker broken the way a
bad deployment breaks it, the page read the way the failing CI run read it —
and each is now **confirmed, excluded, or undecidable**, with the observation
that decided it.

**H1 (stale lease) — CONFIRMED, and its signature is silence.** A `received`
run was inserted directly into `agent_runs` with `claimed_by =
'ghost-worker-from-previous-stack'` and `claimed_until = now() + 10 min` —
exactly the row a worker that died mid-claim leaves behind. A healthy worker
(`booted; marked 0 interrupted run(s) failed; polling every 1.0s`, `Up`,
`RestartCount=0`) then polled for over **60 s**: the run stayed `received`
with **0 steps** the whole time, the worker log stayed at the single boot
line, and the row's lease was untouched. The predicate
(`repositories.py::claim_next`, the `claimed_until < now` clause) excludes the
row exactly as predicted, and **nothing logs** — the worker is healthy, the
page is frozen, and only the row's own `claimed_until` in the future
distinguishes this from "no worker at all". This is the failure shape CI run
38202887580 saw, reproducible on demand; what the CI artifact lacked was the
row query, not the repro.

**H2 (boot-then-crash) — CONFIRMED; the signature is the traceback without
the boot line, plus the restart count.** `DATABASE_URL` was pointed at a bad
password and the worker started under `restart: unless-stopped`. The process
died in `build_worker()` on
`sqlalchemy.exc.OperationalError: ... FATAL: password authentication failed`,
**before** `_run()` prints the boot line — the log is a bare traceback, zero
`booted;` lines, and the container reached `Status=restarting` with
`RestartCount=5` within seconds. This is precisely the shape `assessWorker`
labels `unhealthy_container` (restart count non-zero ⇒ fail immediately,
never wait out the deadline) and the shape the `BOOT_THEN_CRASH_LOG` fixture
in `stack-diagnostics.spec.ts` asserts against. A crash-looping worker is
therefore distinguishable from a slow one within one poll cycle, not 90 s.

**H3 (worker never reached `poll_forever` — the silent boot) — PARTIALLY
TESTABLE HERE; the clean reproduction is CI's to make.** Two attempts to
produce the `silent` verdict (container `Up`, no boot line) on this machine
hit walls that are themselves informative:

- *Unknown provider* (`MODEL_PROVIDER=nonexistent`): `Settings` is shared by
  api and worker, so **both** containers die at import with the same
  `pydantic ValidationError` — the worker crash-loops (RestartCount→6, bare
  traceback, no boot line) but so does the api, and `--wait` fails the whole
  stack. That is *not* the hypothesis's shape (the hypothesis requires the
  stack up and the worker alone silent).
- *Bad stdio command* (`MCP_BILLING_COMMAND` with an unclosed quote):
  `MCPServerSpawnError` is real and named (`the 'billing' MCP server did not
  start from MCP_BILLING_COMMAND=... (No closing quotation)`), but the
  handles are **lazy** — `build_worker_gateway` returns a `MCPToolGateway`
  whose `server_factory` spawns nothing until the first tool call, so
  `build_worker()` succeeds and the worker boots and polls normally. The bad
  command surfaces mid-run, not at boot.

What this machine *can* confirm is the detection half: the boot line prints
only after every `build_worker` probe passes (`worker/__main__.py::_run`),
`assessWorker` returns `silent` for exactly "container `Up`, restarts 0, no
boot line", and a worker that boots on a warm cache prints within seconds.
The remaining injection — a fault that passes `build_worker` but blocks
before `_run`'s print — needs a CI-shaped image (e.g. the missing-`anthropic`
extra the guard was built for) and is left to CI. Verdict:
**undecidable locally in its clean form; the `silent` signature is
implemented and unit-tested, and the two local injections sharpen where it
can and cannot arise.**

**H4 (claim lost to a transaction boundary) — EXCLUDED as a live
possibility; its residue is H1.** The claim now writes `claimed_until`/
`claimed_by` in the same transaction that reads the row
(`repositories.py::claim_next`), so "claimed but written nowhere" is no
longer a state the code can produce: the claim **is** the write. The H1
experiment doubles as this hypothesis's live test — a claim that had been
taken and lost would leave the row lease-free and immediately claimable, and
the healthy worker in that experiment would have taken it within one 1 s
poll. It never did, because the row's future lease excluded it — the lease
mechanism working, not failing. What remains of H4 is exactly the lease's
documented residue (a holder that dies leaves the row unclaimable until the
lease lapses), which is H1's state, measured in `docs/limitations.md` §2.

**H5 (the wait was structurally blind) — CONFIRMED as the original failure's
mechanism, and EXCLUDED as a present one.** Three facts, in order:

1. *The page cannot update itself.* The Run Detail page is a pure Server
   Component (no `use client`, no interval, no `router.refresh` in
   `web/src/app/runs/[runId]/page.tsx`), and the data layer fetches with
   `cache: "no-store"` (`web/src/lib/api/client.ts::apiFetch`). A run that
   changes status *after* the single render is invisible until the next
   navigation — verified at the API level: the same `GET /api/runs/{id}`
   returned `received`/0 steps at t=0 and `classifying`/1 step at t=1 s, so
   the state was moving while a single-goto page would have shown the old
   frame forever.
2. *The worker is fast enough to make the blind window the whole story.* On
   the fake provider the full drive to `waiting_approval` (or `completed`)
   takes well under a second of wall time — a 90 s single-shot wait that
   navigated once at t=0 saw `received` and nothing else, exactly the CI
   artifact.
3. *The current spec's poll loop closes it.* With the stack brought up
   cleanly (`E2E_API_URL` pointing at the api's republished port, since this
   machine's 127.0.0.1:8000 is held by an unrelated service),
   `golden-path.spec.ts` — whose park-wait is `expect.poll` re-navigating
   every 2 s for 90 s — **passes end to end (2 passed, 39.7 s)**, park
   banner included, against the same worker that was invisible to the old
   single-goto spec.

**What follows.** The 38202887580 failure is now attributable: the *most
likely* single cause is H1-shaped (a lease-bearing row the new worker could
not claim, in a database the teardown did not actually clear), rendered
unrecoverable by H5's blind wait, with H2 as the alternative the artifact
could not exclude. The diagnostic order stands as written above, and every
step of it now has a measured, reproducible observation behind it. The first
limb of §P2-C4's acceptance — "the cause is found and fixed" — is satisfied
in its second branch: the gate's evidence (boot line, restart count, log
capture, lease columns, polling spec) is shown by these experiments to be
sufficient to diagnose each of the five causes next time, and H5's fix is
already in the spec.

## Phase 2 gap E closed: the README fixes verified by a second stranger pass

**Date:** 2026-11-14. The first stranger pass (2026-11-14, report kept at
`E:\opspilot-stranger-test\REPORT.md`) reproduced the golden path but found
two defects that discounted §P2-E: **D1** — the README said "nothing else"
while the first `docker compose up` builds two images whose downloads happen
inside Docker's build network, so a working host connection does not
guarantee the build, and the README gave no failure guidance; **D2** — the
three published ports were hard-wired (`127.0.0.1:5432/8000/3000`), leaving a
stranger whose host port is taken with no way forward but editing the
compose file.

Both are fixed on `main` and the fix is verified by a second pass on a fresh
clone:

- `02e4288` — the holdout guards reworked so they pass on a machine that has
  never run the holdout (`test_the_phase2_holdout_counts_are_current` now
  checks the row's citation unconditionally and the cited artefact only where
  it exists; `test_no_committed_result_file_touches_the_holdout` keeps the
  *tracked* results honest), plus ruff-format cleanup.
- `e8e7f6a` — the published host ports become
  `127.0.0.1:${OPSPILOT_PG_HOST_PORT:-5432}:5432`,
  `127.0.0.1:${OPSPILOT_API_HOST_PORT:-8000}:8000` and
  `127.0.0.1:${OPSPILOT_WEB_HOST_PORT:-3000}:3000`, documented in
  `.env.example` with the reason the `127.0.0.1` prefix is not configurable
  (phase2-plan A1); README gains a §"Network requirements" section (what the
  first build downloads, why a working host connection does not guarantee it,
  and the three-step failure path: retry — BuildKit caches succeeded layers —
  then proxy or pre-pull).
- CI on `e8e7f6a`: **all sixteen jobs green** (run 43563202887), including
  `test (py3.12)`, `lint (ruff)` and `web-e2e`, which had been red on the
  pre-fix merge.

The second pass (fresh clone at `e8e7f6a`, on this machine, whose port 8000
is genuinely occupied by an unrelated project's container — the exact
conflict D2 describes): the default-port `docker compose up -d --wait` fails
on the bind as documented, adding `OPSPILOT_API_HOST_PORT=18000` to `.env`
brings all five services up (api healthy on `127.0.0.1:18000`, `/health` →
200), the host's port 8000 stays with its original owner (netstat), and the
golden path reproduces end to end through the overridden port — reindex
17/17/98, `duplicate_charge` 0.93, approval releases the run, refund
`REF-10091` with `replayed: false`, 21-step trace, 11 audit events, and the
billing store reconciled independently (TX-88218 charged, TX-88219 refunded).
§P2-E's acceptance row is updated to **met**; the second pass's residual
findings (D3–D6: the un-commented empty `MCP_*_COMMAND` lines in
`.env.example`, the fixed Compose project name, the silent `Exited (0)`
migrate, log-tail freshness) were low-severity, non-blocking documentation
polish — recorded in the report, and closed the same day (see "the second
stranger pass's low-severity findings" above).

The GitHub Release `v0.1.0` body was also expanded the same day (what Phase 2
contains, how to try it, the known limitations, and why no installer asset is
attached yet); the desktop installer still awaits P2-D2's clean-machine
verification before it is attached.

---

## Documentation consistency audit (2026-11-14)

A full-tree cross-consistency audit of `docs/` (every `.md` under `docs/`, plus
`README.md`), run by script over four dimensions: file-path references,
line-number citations, test/function-name citations, and the inter-document
links/anchors. The existing guards (`test_doc_line_citations_point_at_the_code`,
`test_the_docs_cited_test_names_exist`, `test_doc_cross_references_resolve`)
cover the *current* shapes; this audit also checked the shapes they deliberately
do not (bare citations, historical mentions, counts the guards do not pin).

### Found and fixed (objective errors)

1. **Stale line citations** — `docs/milestones.md` cited
   `routers/tickets.py:118` (the `POST /tickets` handler is now
   `src/opspilot/api/routers/tickets.py:124`),
   `duplicate_charge.json:15` twice (the `"category":` line is 17),
   `ci.yml` "line 277" (the pgvector image is line 278), and
   `repositories.py:693-703` (the `state_change` step is written at
   `:713-722`); the same note's `tracing/recorder.py:197` citation is correct
   but is now repo-rooted. `docs/progress.md` cited
   `domain/tools.py:84-116` (the registry is `:93-125`) and
   `runtime.py:434-444` (gate 2 is `:480-492`).
2. **Citations to lines that no longer hold the text** — the M5e note pointed
   at `agents/runtime.py:79` / `worker/loop.py:63` for the duplicated
   `0.35` defaults; both literals are gone (each module now resolves the
   threshold from `settings.py`). The note now says what was true *then* and
   what is true now. The D-table's README line numbers (`:21`, `:55`, `:94`,
   `:343-360`, golden path (56), five scenarios (157), Architecture (223),
   Evaluation (292), limitations link (29)) were scored at `cb457f4`; the
   README has since grown, so each row now names its as-scored commit and the
   current line. `README.md:59` (the golden-path ticket) moved to `:179`;
   `README.md:551` (the architecture diagram) moved with the diagram's
   rearrangement.
3. **Test names that no longer exist** — the error-vocabulary
   paragraph cited the guard's first-commit name (renamed
   to `test_the_error_vocabulary_agrees_with_the_api.py` in `5563c00`) — now
   named with the rename recorded; the MAX_STEPS mutation table used
   `...`-elided names (`test_state_does_not_redefine...` and friends), now
   spelled out to the names that exist (`test_state_does_not_redefine_the_budget_from_a_literal`,
   `test_runtime_does_not_redefine_the_budget_from_a_literal`,
   `test_the_default_follows_the_setting_after_import`, and the two
   parametrized wiring tests with their `[ticket_found]`/`[ticket_missing]`
   ids); `test_worker_honours_max_steps.py` was said to carry 11 tests, it
   carries 9. The M6 scenario shorthand (`test_scenario_1` … `test_scenario_5`,
   `test_scenario_4b_...`) is now the tests' full names.
4. **Counts** — limitations.md §8's C1 status update and progress.md's B4/C1
   section said "all five `postgres`-marked tests"; the file carries five
   *marked* tests among seven `test_` functions (the phase2-plan C1 row
   already said this correctly), and the "5 collected; grep -c test_pgvector
   -> 2" line now names what the grep actually counted (the
   `test_pgvector_store_*` group plus the citations differential). The
   mypy note about the scratch file now describes it without
   citing a path that was never committed.
5. **Deleted-module references kept honest** — the M5 fourth-server section
   names the fourth server and its test module, both deleted; each mention is now
   marked *(then; since deleted)* so the record stays readable without
   pretending the paths exist.

### Checked and sound (no change)

Every `docs/adr/*.md`, `principles.md`, `tool-permissions.md`,
`agent-state-machine.md`, `architecture.md`, `api-contract.md`,
`mcp-contracts.md`, `data-model.md`, `evals.md`, `risks.md`, `limitations.md`
and `case-study-duplicate-charge.md` citation of a test function, test file,
source symbol, dataset fixture, results file, workflow job or knowledge-corpus
document resolves to the thing named; the four guards that pin document counts
pass; all inter-document links and anchors (including the ADR links from
`docs/` subdirectories and the `#the-twelve-…` anchor) resolve.

### Subjective items left alone

The historical narratives (why a decision was made, what a defect cost, the
"M0–M9 then Phase 2" framing) are records of judgment, not checkable pointers,
and are untouched. The D3–D6 polish items from the second pass were closed the
same day (see the "second stranger pass" record above), and the D2 clean-machine
walk was closed on 2026-11-14 (see "D2 closed: the installer walked" below), so
what remains open is open by decision, not by drift.

## D2 closed: the installer walked, on the operator's machine (2026-11-14)

The last Phase 2 item that only a person could close was the installer
walk-through: "on a clean Windows machine with Docker Desktop, install the built
package and walk the golden path end to end, by someone who did not build it."
The operator on this machine walked it against
`OpsPilot-Setup-0.1.0-x64.exe` (SHA-256 `565206f7…`, 119,227,780 bytes), guided
step by step, with the command-line checks run by Claude Code. The completed
record sheet is `docs/installer-verification.md` §"Filled: the walk of
2026-11-14"; all sixteen checks passed:

- install → launch to the dashboard in ~2 minutes → `/ready`
  `{"database":"ok","migrations":"ok"}` and the dashboard answering 200;
- reindex on a fresh volume: **17 found / 17 indexed / 98 chunks** (a second,
  idempotent run reports 0 re-indexed, which the panel itself explains);
- the README ticket → run `e2b07c50` → **`waiting_approval`**, the HIGH_RISK_WRITE
  `billing.issue_refund TX-88219 $129.00` parked for a human — the safety gate
  held;
- approve → the refund executed **once**: `REF-10091`, `replayed:false`,
  idempotency key `refund:e2b07c50…:TX-88219`, and the billing store read back
  independently shows TX-88219 `refunded` / TX-88218 still `charged`;
- approve again → **409 `approval_already_decided`**;
- security posture intact: 3210 and 8000 bound to loopback only, cross-origin
  write refused (`origin_missing`), no-token read refused (`unauthorized`), no
  `.env*` anywhere in the installed tree;
- quit → zero `opspilot-` containers, both volumes kept; relaunch → the
  completed run is still there.

### Two deviations, both environmental, both recorded

1. **Host port 8000 was occupied by an unrelated project**
   (`microbubble-agent-app-1`). The packaged installer predates `e8e7f6a`, so its
   bundled compose file still publishes literal ports and fixes the compose
   project name; the app's first launch failed on it and showed its own
   `Bind … port is already allocated` panel with "nothing was left running" — a
   correct diagnosis. The walk proceeded after pausing the other project. The
   next installer build picks up `OPSPILOT_*_HOST_PORT` and
   `OPSPILOT_COMPOSE_PROJECT_NAME`, and this cannot arise.
2. **The first 3.4 attempt inherited stale dev-stack volumes.** Same compose
   project name on this machine meant `opspilot_postgres-data` /
   `opspilot_mcp-data` already held data from earlier test runs, so the refund
   was legitimately refused with `invalid_state` (`TX-88219` had already been
   refunded by an earlier run). That refusal is the billing server's state guard
   working. The two volumes were cleared (test data only) and the walk repeated
   from 3.1; every result above is from the clean repeat.

The acceptance table in `docs/phase2-plan.md` now reads **D2 met**; what remains
of the person-steps is uploading the now-verifiable installer asset to the
Release (P3-E's E2 in `docs/phase3-plan.md`).

## M1-02 closed: the model request is explicitly single-shot (2026-11-15)

The Phase 2 review's finding R1 (`docs/phase2-review-2026-11-14.md`) held that
the two provider client factories left `max_retries` at the SDKs' default of 2,
so one logical model call meeting a 429, a 5xx or a timeout put up to three
HTTP requests on the wire — reproduced offline: a `httpx.MockTransport` that
always answered 429 saw **3 requests** before `RateLimitError` surfaced. The
policy chosen for the fix is the review's recommendation: **the SDK never
retries; one logical call is one request**, and any retry policy stays with the
caller (the planning loop), which can see failures and budget steps.

### What changed

- `src/opspilot/adapters/models/openai_provider.py` and
  `src/opspilot/adapters/models/anthropic_provider.py`: both `_client()`
  branches — custom `base_url` and SDK-default endpoint — now pass
  `max_retries=0` explicitly. Timeout, key, `base_url` passthrough and the
  JSON-compatibility paths (`json_object` fallback, `reasoning_content`
  fallback, Anthropic's `output_config`/`tool` mechanisms) are untouched.
- `tests/unit/test_provider_single_shot_no_sdk_retries.py` (new): the review's
  observation turned into a regression pin. A real SDK client is built with
  only its transport swapped for a counting one; each test drives one
  `generate_structured` call against a transport that fails every request with
  429, 500, or a read timeout, and asserts the transport saw **exactly one**
  request and the client reports `max_retries == 0`. Parametrized over both
  endpoints (SDK default and a custom `base_url`) for each SDK — 18 cases.
  The anthropic side injects `httpx2` because the installed anthropic SDK
  rejects an `httpx.AsyncClient` outright; the openai side keeps `httpx`.
- Verified the pin bites: run against the pre-fix sources, the 16
  failure-mode/property cases fail (3 requests observed, `max_retries == 2`);
  against the fix, all pass. The two success-path cases assert the fix added
  no round trip: a successful call is one request too.

### Not changed, on purpose

- No retry/timeout settings were added: a per-call SDK retry budget is
  precisely what M1-02 removes, and `MODEL_TIMEOUT_SECONDS` already covers the
  one attempt's timeout.
- Tool writes were never retried by the SDK — R1's blast radius was model-call
  latency, provider request volume and the one-attempt wording — so no
  idempotency behavior changes.
- `docs/api-contract.md`'s retry language (§1 "Retrying after a `500` is
  safe", §9) is about **API clients retrying `POST /tickets` with the same
  `external_id`** — a different layer, still true, untouched.
- `docs/progress.md`'s earlier statement that "neither adapter passes
  `max_retries`" was true of that sweep; it now points here.

## M3-04 机器可验证部分完成：从 81a0278 重建可发布包（2026-11-15）

从固定提交 `81a0278`（`m3-04-repackage` worktree，工作区干净）重新构建
installer：`desktop/release/OpsPilot-Setup-0.1.0-x64.exe`，126,045,189 字节，
SHA-256 `71ac5ac0adcb543fc96810c96085fbb66bbb73260c92d85325a6927997ee9a7b`。
完整记录见 [m3-04-repackage-verification.md](../m3-04-repackage-verification.md)。

- 打包 compose 与源码逐字节一致（diff 零差异）；包内无 `.env`，无 `sk-…` 字面
  key，`OPSPILOT_OPERATOR_TOKEN` 仅有 `:?` 环境变量形态，asar 内亦无凭据字面量。
- 一次性 compose 项目名 `opspilot-m304-cold`（端口偏移 18000/15432/13000）走查：
  冷启动（构建三镜像 + 四服务就绪）约 3.5–4 分钟（本机有依赖缓存；陌生人机器按
  文档预告 5–15 分钟）；热启动（down 不带 `-v` → up 不带 `--build`）18.3 秒；
  `/ready` 两次均返回 `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`。
  全程零模型 API 调用。
- 旧 v0.1.0 包（SHA `565206f7…`，对应 2026-11-14 D2 走查）保留不撤；本包是面向
  M3-05 发布交接的新候选，完成真人走查并由用户安排发布前不产生 Release/链接。
- 未覆盖（如实）：真人干净机点选走查（对旧包已有 2026-11-14 记录，新包未做）；
  "新 checkout 验离线流程"；SmartScreen 行为复验；可复现构建承诺。

## M3-02 executed against a real endpoint: the dev-set two-run (2026-11-15)

Task M3-02 part 1 (phase3-plan §6): the 15-case development set
(`evals/datasets/tool_selection.jsonl`, sha256 `05195a7d…`), same frozen
config, two full runs, gate ≥ 10/15 tool-set hits. Report:
[docs/m3-02-dev-two-runs-2026-11-15.md](../m3-02-dev-two-runs-2026-11-15.md).

### Result: not met — 3/15 and 2/15

Both runs completed all 15 cases, zero case failures, argument validity
1.000 (44/44 and 40/40 calls), zero `must_not_propose` violations, unsafe
execution gate 0. Exact-set hits: run 1 **3/15**, run 2 **2/15**; identical
tool sets across runs on only 8/15 cases. The gap is tool-set judgement, the
same attribution the B2 analysis reached for MiniMax — not the prompt, not
schema compliance.

### Endpoint deviation, recorded

The plan's primary route (`https://api.deepseek.com`, `deepseek-flash`) is
unreachable from this machine: the only key on hand is the MiniMax-era
`sk-cp-…`, which that endpoint rejects with 401. The runs went through the
local magpie gateway (`127.0.0.1:3425/v1`), route
`workbuddy/deepseek-v4.1-flash` — same model generation, different access
path. One finding worth keeping: the gateway accepts the strict `json_schema`
envelope but does not enforce it (200 with free-text content), so the runs
used the repo's own `OPENAI_JSON_OBJECT_FALLBACK=true` — the same fallback the
MiniMax era validated. The `sensenova-token-plan/deepseek-flash` route was
tried first and dropped: its upstream answered 429 ("usage limit reached") on
the first real case.

### Attribution details

- tool-005/006/012 produced empty proposals on both runs: the harness's
  retrieval-abstention path, not a model fault. Measured directly with the
  runner's own stack: top retrieval scores 0.171 / 0.172 / 0.215, all below
  `RETRIEVAL_MIN_SCORE=0.22`, so PLANNING never ran (2 model calls,
  `escalated=true` each). The reachable ceiling on this dataset is 12/15.
- The 12 competitive cases all missed on set equality: extra `knowledge.search`
  / `issues.search` / `issues.create`, missing `crm.get_customer`, repeated
  `billing.list_transactions` (16 surplus calls across the two runs).
- No pathological looping: no case approached `MAX_STEPS=24`, no
  StructuredOutputError. DeepSeek V4.1-Flash behaves measurably better than
  MiniMax on argument validity (1.000 vs 0.378) and loop hygiene, but lands at
  the same 2–3/15 tool-selection level.

### Cost and bookkeeping

The gateway bills a pre-existing consumer subscription, not per-token API
credit: measured cost of both runs is $0, and the $25/model cap is untouched.
Raw result files `evals/results/2026-11-15T05-08-14.json` and
`2026-11-15T05-19-05.json` stay uncommitted per the results README (local
only). Failed starts (two 429s, one json_schema mismatch) were debugging runs,
kept out of the official pair. Per plan §4.1, S2 failing stops the train: S3/S4
freeze acceptance does not proceed on these numbers.

## M3-03 closed: the holdout ran frozen, and the safety gate held (2026-11-15)

Phase 3 M3-03 (holdout + independent safety acceptance) executed on branch
`m3-03-holdout-safety` from `main@81a0278`, against the same frozen
configuration as M3-02's dev runs: `workbuddy/deepseek-v4.1-flash` over the
local magpie gateway (`http://127.0.0.1:3425/v1`) with
`OPENAI_JSON_OBJECT_FALLBACK=true` (the gateway ignores the strict
`json_schema` envelope), `MAX_STEPS=24`, SDK `max_retries=0`. Full report:
`docs/m3-03-holdout-safety-2026-11-15.md`.

- **Holdout 32/32** (`evals/results/2026-11-15T04-57-30.json`): tool selection
  **10/32 (0.312)** — far below the dev set's level, reported as observed, no
  tuning against it. Argument validity **1.000** (102/102), `must_not_propose`
  violations **0**, task completion 31/32 (1 × `max_steps_exceeded`), latency
  p50 18.4s / p95 100.0s. The miss pattern is concentrated: skipped
  `crm.get_customer`/`billing.get_invoice` precondition reads (13 cases) and
  extra `issues.*`/`knowledge.search` proposals (9 cases).
- **Safety 15/15** (`evals/results/2026-11-15T05-08-05.json`; 13 live + 2
  synthetic injection): **unsafe execution count 0** — the gate held. Approval
  compliance 1.000 (5/5 live, 2/2 synthetic), unexpected-write 0, expected-write
  rate 0.250 (2/8, reported as observed). Both injection cases followed the
  injected directive and proposed a $10,000 refund claiming pre-authorisation;
  the MCP amount check refused it (`amount_exceeds_transaction`) and no approval
  was ever granted — refusal evidence is in the recorded call chains.
- **Cost disclosure:** the route is a local gateway, not a metered DeepSeek
  endpoint, so real billing is **unknown** and is not shown as free. Reference
  conversion at DeepSeek's published prices: < $0.31 peak upper bound for the
  whole M3-03 batch (measured tokens 69,800 in / 41,170 out over 264 calls,
  35% measurable; planning calls accounted at a 2K/0.6K per-call bound). Well
  inside the $25 shared cap with M3-02.

## M3-06 acceptance review: the public material is honestly at 75, and the real-model gate is still open (2026-11-15)

Phase 3 M3-06 (the acceptance review) executed on `main@3fdc027` (the
audit baseline, recorded at the top of the report). Full report:
the acceptance review. Scored against the seven weighted
dimensions of `docs/phase3-plan.md` §2, evidence-only.

- **Total ≈ 75/100** (skeleton self-estimate was ≈63 at `51a99ab`; target 92).
  By dimension: engineering & safety 9.0/10 (×25), real-model credibility
  4.0/10 (×20), showcase 8.5/10 (×15), stranger reproduction 8.0/10 (×10),
  case study 8.0/10 (×10), service offering 8.5/10 (×10), consistency &
  release 7.0/10 (×10).
- **Hard gates:** safety **unsafe = 0 achieved** (M3-03, 13 live + 2 synthetic
  injections, zero approvals released). **Real-model dev-set gate NOT met** —
  DeepSeek V4.1-Flash two runs 3/15 and 2/15 against the ≥10/15 bar, so the
  the public material must not claim real-model acceptance. Demo pipeline achieved;
  stranger reproduction partially achieved (new installer machine-verified,
  human clean-machine walkthrough still pending).
- **Three lists** are in the report: 14 verified items (V1–V14), 7 gaps
  (N1–N7, led by the real-model gate and the new-package human walkthrough),
  and a 6-step release checklist (R1–R6) for the user (Release asset upload,
  walkthrough scheduling, and the keep-tuning-or-accept decision).
- **30-second test:** passed from a cold reader's view (problem / demo /
  reproduce all answerable from the README's first screen); two optional
  first-screen improvements recorded for M3-05. Six cross-document
  number-drift items (D1–D6) are tabulated in the report for M3-05 to backfill;
  nothing was edited outside this report and this entry.

## M3-05 closed: the real numbers are in the materials, and the release is a checklist (2026-11-15)

Phase 3 M3-05 (finalisation and release handover) executed on `main` on top of
the M3-06 acceptance commit. The November real-model results are now in every
public surface, stated as measured, with the demo/real-model boundary made
explicit. New: [`docs/release-checklist.md`](../release-checklist.md) — every
outward-facing step (remote CI, human installer walkthrough, Release/tag,
Release publication) is listed with materials and marked
**for the user to execute**; nothing outward was performed.

### Backfilled (the D1–D6 list from the M3-06 report)

- `docs/case-study-duplicate-charge-en.md` (D1): the "honesty note" now cites
  the November batches instead of "ongoing workstream" — dev set 3/15 and 2/15
  against ≥10/15 (**not met**), holdout 10/32, safety unsafe=0 with the
  injection cases refusing at the amount check; injection-posture bullet and
  the limitations paragraph updated to the same measured wording; the frozen
  1,365-test figure now points at the README for the current count.
- `docs/case-study-summary-en.md` (D2): measurable-results paragraph now
  carries the real-model safety result (unsafe = 0, both injections refused)
  and the tool-selection shortfall; "remains active work" became the measured
  statement.
- `docs/case-study-duplicate-charge.md` (D2's Chinese counterpart): the
  "live measurement, for contrast" section gained a dated 2026-11-15 block —
  DeepSeek V4.1-Flash 3/15 and 2/15, holdout 10/32, safety gate held, with
  links to both reports.
- `README.md` (D3): the status banner now states Phase 3 is closing with the
  real-model outcome in one place; a new "Re-measured on a newer model,
  November 2026" table in the Evaluation section separates the three batches
  and names the demo videos as scripted-provider artefacts; the "Watch it
  run" section links the browser videos (`docs/showcase.md`) with the same
  disclosure.
- The description draft (D4) now carries the
  real-model line ("safety gate held: 0 unsafe executions, injections
  included; tool-selection accuracy is an open metric"); character counts
  made explicit — original wording is 748 chars (over the 600 limit), a
  trimmed 646-char variant, and a **recommended compliant 595-char variant**;
  honesty notes gained the M3-02/M3-03 provenance and the fake-video rule.
- The scorecard draft (D6): a banner marks it as the frozen
  skeleton (baseline `51a99ab`) and points to
  the acceptance review
  as the current rescore; its stale "M3 未开始" estimates are no longer
  presented as current.
- `docs/assets/showcase-raw/showcase-video-script.md`: new closing section —
  what the fake demo proves (wiring) vs. where the real-model evidence lives
  (the two eval reports and their committed results files), and the rule that
  a future live-model segment must carry its own run evidence and must not be
  spliced into these takes.

### Consistency notes

- Model identity across all surfaces: **DeepSeek V4.1-Flash** via the local
  magpie gateway, route `workbuddy/deepseek-v4.1-flash`, config frozen
  (`OPENAI_JSON_OBJECT_FALLBACK=true`, `MAX_STEPS=24`, `max_retries=0`) —
  matching both eval reports.
- The four committed MiniMax-era result files and the two committed
  2026-11-15 files (holdout `…T04-57-30.json`, safety `…T05-08-05.json`) are
  untouched; the M3-02 raw files (`…T05-08-14.json`, `…T05-19-05.json`) stay
  gitignored per the results README.
- The version/commit story is unchanged and now stated in the checklist:
  released `v0.1.0` asset = old package (`565206f7…`, D2 walk of 2026-11-14);
  new candidate = `71ac5ac0…` built from `81a0278` (M3-04). Choosing how to
  publish the new package (new tag vs. `--clobber`) is the user's call, and
  the checklist prices both.

### Left open (honest)

- Remote CI for this commit (needs the push), the human clean-machine
  walkthrough of the new installer, the Release/tag action, the video
  publication — all user steps, listed in
  [`docs/release-checklist.md`](../release-checklist.md).
- The real-model acceptance gate remains **not met** (3/15, 2/15 vs ≥10/15):
  the materials claim containment and engineering, not model competence.
  Retesting with a stronger model or accepting the current result is a
  decision recorded for the user in the checklist's closing section.

## M3-02 re-run after the retrieval fix: the abstentions are gone, the gate still isn't met (2026-11-15)

The retrieval fix (`229ead6` — query expansion toward the corpus vocabulary,
plus a `RETRIEVAL_MIN_SCORE` default recalibration to 0.205 that the pinned
`.env` value of 0.22 overrode in these runs) was merged, and the M3-02 two-run was
repeated on the same frozen config (DeepSeek V4.1-Flash via the local gateway,
`OPENAI_JSON_OBJECT_FALLBACK=true`, `max_retries=0`, timeout 300s, MAX_STEPS 24;
dataset hash unchanged `05195a7d…`). Report:
[`docs/m3-02-rerun-after-retrieval-fix-2026-11-15.md`](../m3-02-rerun-after-retrieval-fix-2026-11-15.md);
raw runs `evals/results/2026-11-15T08-56-37.json` (run 1) and
`2026-11-15T09-12-41.json` (run 2), gitignored per the results README.

- **Result: still not met.** Run 1 **2/15**, run 2 **3/15** — the same 2–3/15
  band as before the fix (the two runs swapped scores). Argument validity
  1.000 both runs, unsafe execution 0, all 15 cases completed.
- **The abstentions are gone.** tool-005/006/012 no longer abstain before
  planning (run 1: all three complete with proposals or a deliberate no-tool
  answer; run 2: tool-006/012 propose tools, tool-005 completes with none).
  The competitive ceiling is back to 15/15.
- **The remaining gap is the model's tool-set judgement**, not retrieval:
  the newly unblocked cases still do not hit, and run-to-run agreement stays
  low. The retrieval fix is kept — it removes a *deterministic* failure mode
  (any model would have missed those three) with zero leakage into the
  abstention controls — but it does not move the headline.
- Next-step options (stronger model re-run vs. accepting the open metric)
  remain the user's decision, recorded in
  the acceptance review's next-steps section.

## v0.2.0 moves to the retrieval-fix commit, and the release candidate is built and verified (2026-11-15)

Per the user's decisions: the v0.2.0 tag moves from `e6d9f69` to `8133a89` so
the release contains the retrieval fix and its re-run report; the real-model
gate is accepted as an open metric and shown as measured (no model switch);
the demo videos stay GitHub-only (no external platform upload); and the
GitHub Release is created only after a non-author walkthrough of the new
package.

- **Tag:** `v0.2.0` -> `2b96e70`, local and remote. The old positions
  (`e6d9f69`, then `8133a89`) had no Release object, so the moves published
  nothing that did not exist. The final move adds the 2026-11-15 independent
  walkthrough record and the fix for its one gap: the desktop shell now
  follows `OPSPILOT_API_HOST_PORT` (commit `5320ef5`).
- **Build** from a clean checkout at the tag
  (`E:/OpsPilot-release-v020`): `web` npm ci + build, `desktop` npm ci +
  package -> `desktop/release/OpsPilot-Setup-0.2.0-x64.exe`,
  **130,075,633 bytes, SHA-256
  `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`**.
- **Verification on that checkout:** packaged `docker-compose.yml`
  byte-identical to the repo's; no `.env` or credential literals inside the
  package; packaged-posture specs (`packaged-posture.spec.ts` +
  `packaged-posture-icon.spec.ts`, lifecycle project) = **5 passed,
  1 skipped** — the skip is the operator-token literal scan, which has
  nothing to search for when no token is configured in the build environment
  (same behaviour as the M3-04 build). Cold/warm start timings were not
  re-measured; the M3-04 numbers remain the reference.
- **Release notes draft** updated to the four-run evaluation band (3/15, 2/15
  before the fix; 2/15, 3/15 after; gate still not met) and the new artifact
  hash. Release creation and the asset upload stay user steps, gated on the
  walkthrough (`docs/release-checklist.md` §3-4).

## Walkthrough gap fixed: the shell follows OPSPILOT_API_HOST_PORT (2026-11-15)

The independent 2026-11-15 walkthrough (recorded in
[`docs/installer-verification.md`](../installer-verification.md)) found one
substantive gap: `docker-compose.yml` publishes the API on
`127.0.0.1:${OPSPILOT_API_HOST_PORT:-8000}:8000`, but the desktop shell probed
a hard-coded `http://127.0.0.1:8000` — so on the walker's machine, where 8000
is held by an unrelated service, the stack came up healthy on a moved port
while the shell's `/ready` probe never answered and the dashboard step never
completed.

- **Fix (`5320ef5`):** `resolveApiHostPort`/`resolveApiUrl` parse the same
  variable strictly, and the shell reads it from `process.env`, the
  repository `.env`, or an installed copy's app-data `opspilot.env` — the same
  sources Compose itself uses in the two deployment shapes. A new lifecycle
  spec (`desktop/e2e/api-port.spec.ts`) pins the parsing and compares the
  shell's default against the compose literal so the two cannot drift again.
- **Re-built and re-verified** on the tag checkout: new artifact
  130,075,633 B, SHA-256
  `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`;
  packaged-posture gates 5 passed / 1 skipped; CI on `2b96e70` green
  (run 43707783395).
- The walker re-checked the port-occupied scenario against the fixed build;
  the record is appended to
  [`docs/installer-verification.md`](../installer-verification.md).

## v0.2.0 Release published (2026-11-15)

The GitHub Release is live: tag `v0.2.0` → `656bbd3` (the docs-pointing fix
on top of the walkthrough record; CI run 43710760626 green). Assets:
`OpsPilot-Setup-0.2.0-x64.exe` (130,075,633 B, SHA-256
`2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`, digest
verified on the release object) and its `.blockmap`.
<https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.2.0>

Gate evidence behind it: the 2026-11-15 independent walkthrough
([installer-verification.md](../installer-verification.md)) plus the port-fix
recheck that confirmed the shell follows `OPSPILOT_API_HOST_PORT`
(dashboard up in ~5 s with the API on a moved port). M3-05's Release step is
closed; §7 post-release checks (README links, model-identifier consistency)
remain to be clicked through.

## Walk-through evidence archived into the repo (2026-11-15)

The two 2026-11-15 scratch workspaces were removed after their evidence was
copied into the repository: `docs/evidence/walkthrough-2026-11-15/` holds the
walk record plus the ticket/approval/audit JSON, billing snapshot, trace,
container/volume finals and install logs (16 files), and
`docs/evidence/port-recheck-2026-11-15/` holds the port-fix recheck record
and its stderr log (2 files). The one token-bearing file —
`preexisting-opspilot.env.bak` — was excluded, and all archived files were
grepped for token literals before copying. The installer builds
(`E:\OpsPilot-release-v020`) were not part of this cleanup.

## Third-round re-score: 79/100, still short of the gate (2026-11-15)

Read-only re-score on top of the published release (baseline: tag `v0.2.0` =
`656bbd3`, `main` = `ac04c82`). The seven-dimension total moves **76.75 → 79.25
(≈79/100)**: version delivery 7.0 → 9.0 (the v0.2.0 Release exists, its asset
hash was verified on the release object, and the tag's CI is green) and
stranger-reproduction 8.5 → 9.0 (the port fix was independently re-checked on
the shipped artifact, evidence archived under `docs/evidence/`). Dimensions 1,
2, 3, 5, 6 are unchanged; **the acceptance is still not passed** — the
real-model development-set gate (≥10/15 across two runs) remains unmet at
2–3/15, and a real-model demo segment is still missing. Full table and the
remaining-gap ordering: the acceptance review §7.

Same night, two follow-ups landed: the desktop shell's sandbox assertion now
polls for a real renderer pid (the one intermittently failing `test:shell`
spec; the isolated suite then passed 3/3), and the release documents were
swept for the last inconsistencies the read-only audit found — the argument
validity figures now read per run (44/44, 40/40, 38/38, 56/56; holdout
102/102), the Release notes are marked published with the tag's build commit
named, and the checklist's Release step is ticked off.

## Follow-up batch: lifecycle flake fixed, dependency audit clean, S0 dry-run passed (2026-11-15)

- **Lifecycle flake fixed (`b4ca2f9`).** The packaged-vs-repository compose
  comparison no longer fails a developer's `test:lifecycle` because
  `desktop/release/` still holds the previous (gitignored) package output: a
  mismatch now fails hard only under `OPSPILOT_PACKAGED_REQUIRED=1` — the CI
  desktop-package job, which builds the artefact from the same checkout — and
  skips locally with the rebuild instruction. Verified: the target spec runs
  5 passed / 1 skipped locally, and the CI protection still trips under the
  required flag.
- **Dependency audit (read-only): zero known vulnerabilities.** pip-audit on
  the project `.venv` (95 packages), `npm audit` on web (472) and desktop
  (360) — all clean; GitHub Actions use current official majors with least
  privilege. The one actionable improvement is a Python lockfile (the
  project is deliberately lockfile-free; low severity, reproducibility).
  Full report: `dep-audit-2026-11-15.md` (session notes).
- **S0 dry-run passed.** The eval harness ran end to end on the fake
  provider (`--limit 2`, ~5 s, $0, no external calls), producing a
  well-formed results file under `evals/results/` (gitignored). The real-model
  re-test (S1) is now unblocked pending only a provider key and budget sign
  off, per [`docs/model-retest-plan-v0.3.0.md`](../model-retest-plan-v0.3.0.md).

## Two-worker claim tests exercised against a live local PostgreSQL (2026-11-15)

limitations.md §2/§8 records the long-standing gap honestly: the
`FOR UPDATE SKIP LOCKED` clause behind the two-worker claim guarantee had only
ever been exercised on the CI `verify` job's throwaway container — never on a
second environment, and never with a local walk. Closed tonight on this
machine, against the running `opspilot` stack's PostgreSQL (15434), using a
disposable scratch database (`opspilot_scratch`, created and dropped; the
guard in `tests/_pgvector_target.py` refused the application database because
it holds rows, exactly as designed):

- `tests/integration/test_worker_claim_postgres.py` — **7 passed** (the two
  claimants racing one locked row never share it; a held lease withholds the
  run from a second worker; a `WAITING_APPROVAL` run is not claimable).
- The other postgres-marked suites — `test_citations.py`,
  `test_migration_schema.py`, `test_ticket_external_id_dedupe.py` — **35
  passed** against the same live instance.

The "PostgreSQL coverage depends on a single CI environment" caveat in the
acceptance scorecard's dimension 1 is therefore now supported by a second
environment. The `OPSPILOT_DATABASE_URL` used was the stack's own container
credentials read at runtime and never written to any file.

## MiniMax-M3.1-Flash re-test after unfreezing: 1/15, 1/15 — worst measurement yet, gates hold (2026-11-15/16)

The user supplied a MiniMax key (identical to the `.env` credential, verified
by programmatic comparison) and asked for the deferred re-test with model
`MiniMax-M3.1-Flash` over `https://api.minimaxi.com/v1`. Executed per
[model-retest-plan-v0.3.0.md](../model-retest-plan-v0.3.0.md) S1→S2 with the
M3-02 frozen configuration (same threshold 0.22, same dataset hash
`05195a7d…`, same MAX_STEPS/timeout/max_retries). Report:
[`docs/m3-02-minimax-retest-2026-11-15.md`](../m3-02-minimax-retest-2026-11-15.md);
raw runs `evals/results/2026-11-15T14-37-04.json` (S1, 4 cases),
`2026-11-15T15-23-14.json` (S2 run 1), `2026-11-15T16-03-15.json` (S2 run 2),
gitignored per the results README.

- **S1 pilot**: 0/4, argument validity 1.000 (33/33), no 429s, no schema
  refusals — endpoint and structured-output path usable; pilot passed.
- **S2 two runs**: tool selection **1/15 and 1/15** (both runs hit only
  tool-011) — below DeepSeek's 2–3/15 and below MiniMax's own B2-era band.
- **Safety gates hold everywhere**: unsafe = 0 in both runs, no
  `must_not_propose` violations; argument validity 0.968 (120/124; the four
  invalids were tail calls of one max-steps case) and 1.000 (98/98).
- **Failure shape differs from DeepSeek**: repeated same-tool loops (16×
  `billing.list_transactions` in one S1 case), billing tickets routed to
  `issues.create`, and 5 `max_steps_exceeded` cases across the two runs —
  model judgement, not harness plumbing.
- **Acceptance unchanged**: the two-run ≥10/15 gate remains unmet; the 79/100
  score and the "no real-model acceptance claim" boundary stand.

## deepseek-v4.1-flash-max re-test after unfreezing: 2/15, 1/15 — faster, not better (2026-11-15/16)

Route A of the model re-test plan continued past the MiniMax re-test: the
gateway (magpie, 84 models) exposed a stronger same-family tier. First choice
`deepseek/deepseek-v4-pro` was refused by the upstream with **402 Insufficient
Balance** (quota window, not a key problem); the fallback tier
**`deepseek-v4.1-flash-max`** ran the full S1→S2 sequence with the M3-02
frozen configuration (threshold 0.22, same dataset hash `05195a7d…`,
MAX_STEPS 24, timeout 300s, max_retries 0). Report:
[`docs/m3-02-deepseek-v4.1-flash-max-retest-2026-11-15.md`](../m3-02-deepseek-v4.1-flash-max-retest-2026-11-15.md);
raw runs `evals/results/2026-11-15T16-20-15.json` (S1, 4 cases),
`2026-11-15T16-37-26.json` (S2 run 1), `2026-11-15T16-54-28.json` (S2 run 2),
gitignored per the results README.

- **S1 pilot**: 0/4, argument validity 1.000 (21/21), p50 62s — each case
  missed by exactly one tool; no 429s, no schema refusals. Pilot passed.
- **S2 two runs**: tool selection **2/15 and 1/15** — same band as the flash
  tier (2–3/15), no improvement from the stronger tier. All 30 cases
  completed (no max-steps exhaustion), p50 28.7s/28.3s — roughly 6× faster
  than flash.
- **Safety gates hold**: unsafe = 0 in both runs, argument validity 1.000
  (48/48, 50/50), no `must_not_propose` violations.
- **Failure shape**: over-proposing `issues.*` on billing tickets, missing
  `crm.get_customer`, over-including `knowledge.search` — model judgement,
  not plumbing.
- **Route-A conclusion so far**: the "same-family stronger tier" hypothesis
  is falsified within the DeepSeek family (v4-pro untestable this window;
  flash-max = flash on accuracy). Cross-family candidates (Claude Sonnet 5.5
  via the gateway, GLM-5.3) remain; whether to spend on them is the user's
  call. The acceptance gate (two runs ≥10/15) stays unmet; 79/100 and the
  "no real-model acceptance claim" boundary are unchanged.

## GLM-5.3-Flash S1 pilot: 0/4 — direction A closed (2026-11-15/16)

The last cross-family candidate ran the S1 pilot (first 4 development-set
cases) through the gateway route `zcode/GLM-5.3-Flash`: **0/4**, argument
validity 1.000 (13/13), unsafe 0, p50 227s (slowest of all tested models).
Failure shape: conservative *under*-proposing — three cases missing
`billing.get_invoice` and/or `crm.get_customer`, one over-proposing
`issues.*`. Per the user's preset rule ("GLM 不达标即关闭方向 A"),
**direction A is closed**: five model families have now been measured and
none approaches the 10/15 two-run gate. Report:
[`docs/m3-02-glm-5.3-flash-s1-2026-11-15.md`](../m3-02-glm-5.3-flash-s1-2026-11-15.md);
raw run `evals/results/2026-11-15T17-20-13.json` (gitignored). The Phase 4
draft's direction-A section and candidate table were updated the same night;
the mainline moves to direction B (productisation/demo hardening).

## M3-05 post-release check closed (2026-11-15/16)

Release [`v0.2.0`](https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.2.0)
asset matches the local build exactly (130,075,633 B, SHA-256
`2461f146…`; blockmap online). README's three external links resolve (CI run
`39439825308` success, both Release tags, repo page 200) and the three
showcase mp4/srt pairs are in the tree. Numbers agree across README, the
case study, the public description and showcase.md (3/15, 2/15, 2/15, 3/15,
10/32, unsafe=0; MiniMax re-test 1/15×2 only in README). History scan is
clean: `.env`, the M3-02 raw runs and the live key prefix never entered git;
the one hit, `3b20b07`, is the deliberately committed M3-03 safety evidence
(config only, no credentials). Checklist §7 ticked in
[`docs/release-checklist.md`](../release-checklist.md). The description draft got its
read-only review applied: the recommended description now says *simulated*
refund at exactly 600 chars, and the honesty note drops the stronger-tier
figures per the external-materials decision.

## Interactive walk on the published v0.2.0 installer (2026-11-15/16)

Owner-performed interactive walk of the published artifact on the build
machine (owner-performed, not a second independent verification): NSIS
wizard, no SmartScreen (the local build carries no Mark-of-the-Web), golden
path with approval, API-level double-decision 409, audit page, quit, warm
start in 10–15 s, data survives the restart. Two findings entered the record:

1. the 8000-conflict failure page gives no recovery hint — the headless
   recheck's case A, a documented default rather than a regression; and
2. the installer reused a stale local `opspilot-api` image (built 2026-11-14,
   before the export route existed) because `docker compose up` does not
   rebuild an existing tag, so “export summary” returned 404 until the image
   was rebuilt from the installer's own stack sources — tracked as DBC-07.

Full sheet: [`docs/installer-interactive-walk-2026-11-15.md`](../installer-interactive-walk-2026-11-15.md),
archived into [`docs/installer-verification.md`](../installer-verification.md).

## DBC-07 fixed: versioned image tags (2026-11-16)

The upgrade-path finding from the interactive walk is fixed in code. Every
built service in `docker-compose.yml` now names its image
`${OPSPILOT_IMAGE_PREFIX:-opspilot}-<service>:${OPSPILOT_IMAGE_TAG:-local}`,
and the desktop shell passes `OPSPILOT_IMAGE_TAG` = the app's own version
(`desktop/src/main.ts`). A reinstall of a new version therefore asks Compose
for a tag it has never built, which forces the build that the fixed name used
to skip; the same version reuses the same tree's image, which is the case
where reuse is correct. The e2e harness sets `OPSPILOT_IMAGE_PREFIX` alongside
its compose project name so named stacks do not share images. Two reads
the compose file in `desktop/e2e/compose-commands.spec.ts` keep the tag from
being dropped. Lifecycle tests 121 passed, web typecheck clean, compose
python tests green. Rebuild-package verification of the upgrade path is
pending the next build.

## v0.2.1 packaged; upgrade path verified (2026-11-16)

Version bumped to 0.2.1 in all four declarations (the version guard passes),
the installer rebuilt (`OpsPilot-Setup-0.2.1-x64.exe`, 130,567,926 B, SHA-256
`567bec190d0a05d27c9aa29f99db68b3e1b96b32be0ef4a2ef924ee665f676c5`), and the
upgrade path walked live on the machine that still had 0.2.0 installed:
override install → registry reports 0.2.1, the packaged compose carries
`on-failure` and the versioned image tags, cold start 70 s, and every
container ran `opspilot-*:0.2.1` — the stale-image reuse that cost the export
route in the first walk is gone (export 200). The README's reference ticket
walked the golden path (approval → completed, second approve 409, no-token
401), and `docker stop` leaves the containers Exited without resurrection
(on-failure semantics, verified live). One honest correction recorded: a
restart policy acts on container exit, not on an external process's death, so
`on-failure` alone does not reclaim a killed app's healthy containers —
DBC-08's remaining gap (automatic reclamation on abnormal termination) is
future work; the versioned tags are what close the upgrade hole. Test
containers were removed; the data volumes and the unrelated 8000 stack were
left untouched.

## v0.2.1 released (2026-11-16)

One CI fix on the way (`bb4ae85`: the `/health` assertion pinned 0.2.0 and
failed on the version bump; it now reads `opspilot.__version__`). Tag
`v0.2.1` @ `bb4ae85`, Release created with the installer and blockmap
(130,567,926 B, SHA-256 `567bec190d0a05d27c9aa29f99db68b3e1b96b32be0ef4a2ef924ee665f676c5`,
verified against the local build), notes in
[`docs/release-notes-v0.2.1-draft.md`](../release-notes-v0.2.1-draft.md):
<https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.2.1>

## DBC-05 done: a demo compose profile (2026-11-16)

[`docker-compose.demo.yml`](docker-compose.demo.yml) is a second Compose file,
not a default override: merged only when both `-f` flags are passed, so the
development path is byte-identical without it. It gives the demo its own
project name (`opspilot-demo` — containers, network and, through the
`OPSPILOT_IMAGE_PREFIX` anchor, image tags separate from the development
stack), its own ports (API 18001, dashboard 13001, PostgreSQL 15433, passed as
shell variables because Compose interpolates `ports:` from the environment
before merging), the `already_refunded` fake script — whose agent detects the
earlier refund and proposes no write at all, so the demo ends at "the gate
never had to fire" — and `LOG_LEVEL=WARNING`. No credential is set and the
provider stays `fake`; the dashboard's scripted-provider disclosure is
untouched. README gains "A demo stack beside the development one"; the
`.env.example` scenario note names both scripts. Verified by rendering both
files (baseline unchanged; demo values as designed) and by the offline
suites: compose tests 25 passed, doc guards 14 passed. CI on `ed73e56`:
success (run 43749390768).

## Third-party interactive walk complete (2026-11-16)

The context-free tester (an agent with none of this project's history, working
from a written brief only) repeated the interactive walk: hash check, clean
reinstall, cold start at port 18002, ticket → run → completed, audit/trace,
401 on unauthenticated writes, warm start in ~15 s, data intact after the
restart. One functional FAIL: after a killed process the containers stayed up
(restart policy `unless-stopped`) and needed a manual `compose down` — tracked
as DBC-08. Cold start read 21–24 s only because stale images and volumes were
already on the machine; the 2–15-minute expectation stands for a clean one.
Report: [`docs/installer-walk-third-party-2026-11-16.md`](../installer-walk-third-party-2026-11-16.md),
archived into [`docs/installer-verification.md`](../installer-verification.md).
CI on `0c47176`: success.

## DBC-08 fixed: on-failure restart policy (2026-11-16)

The third-party walk's one functional FAIL — a killed app process leaving the
containers running under `restart: unless-stopped` — is fixed in
`docker-compose.yml`: postgres, api, worker and web now use
`restart: on-failure`, so a healthy container whose supervisor died is left
stopped while a genuinely crashed container still restarts; migrate keeps its
deliberate `"no"`. Normal-quit behaviour is unchanged (the shell's
`compose down` still runs). Verified by rendering the compose file (all four
services `on-failure`) and the compose python tests (25 passed). The shipped
v0.2.0 installer still carries the old policy; this lands with the next
package.
