# Honest Limitations — Phase 1, updated for Phase 2

This file exists because a portfolio project's credibility comes from what it
refuses to overclaim. Everything below is true of OpsPilot as shipped. Anything
on this list that a reader would otherwise assume is working is a bug in the
README, not in this file.

---

## 1. Security

**Single operator, no separation of duties.** One bearer token
(`OPSPILOT_OPERATOR_TOKEN`) authenticates every endpoint. The same credential can
create a ticket, approve a refund and reject one. There is no notion of who
approved what beyond the free-text `decided_by` field, which is not
authenticated. A real deployment needs roles, an approval sphere ("this operator
may approve up to $500"), and separation between the actor proposing and the
actor approving. **Phase 3.**

**No prompt-injection red-team.** The structural defence — permission gates that
retrieved text cannot influence — is tested and holds. The *prompt-level* defence
is a system-prompt instruction not to follow instructions found in retrieved
documents, which is a mitigation, not a guarantee. A sufficiently clever
injection may well steer the model's *proposal*. It cannot make that proposal
execute without a human, which is the property the project actually claims.

**Approval arguments are snapshotted but not cryptographically bound.** The
approver sees `arguments_snapshot`; execution reads `tool_calls.arguments`. A
test asserts they are equal and that the row cannot be mutated between approval
and execution, but there is no signature or hash. A bug that mutated the column
would be caught by the test, not by a cryptographic guarantee.

**Secrets live in `.env`.** No vault, no rotation, no per-environment key
management. `.env` is gitignored and `.env.example` has empty values; CI uses the
fake provider and needs no keys at all.

**Model responses are stored unredacted by default.** The `response` step's
`output.body` holds the composed reply in full, which may include customer PII
echoed from the ticket and from CRM tool results. This is a deliberate debugging
affordance for Phase 1 and it is a genuine privacy liability. `STORE_FULL_PROMPTS=false`
now withholds it (the value is replaced with an explicit marker; every other field
survives), so a deployment that does not want it has a switch — but the *default*
is still to store it. Note that Phase 1 records the model's **response**, not its
prompt: no step writes the prompt text, so despite the setting's name there is
less here than that name suggests. Field-level redaction of what remains, and a
retention policy, is Phase 3.

**Rate limiting and abuse controls: absent.** There is no limit on tickets per
minute, no cap on tokens per hour, and no circuit breaker on the model provider.
A malformed client could run up a bill.

## 2. Reliability

**Interrupted runs are not resumed.** A worker restart marks any run in a
mid-flight state as `FAILED(interrupted)`. Its work is lost. Runs in
`WAITING_APPROVAL` survive, because they are parked in the database rather than
held in the process. Automatic resume needs a step journal and needs every tool
call to be safely replayable, which is Phase 2 work.

**No retries, no fallback model, no timeouts with backoff.** A model call that
fails fails the run. An MCP server that is down fails the run. There is a single
attempt and the failure is recorded honestly. This makes the eval numbers clean
and the demo fragile — both are intentional at this stage.

**The claim is tested at the predicate, at the lock and at the lease, and the
end-to-end two-worker promise is still not unconditional.** The claim query
uses `FOR UPDATE SKIP LOCKED` on Postgres
(`repositories.py::SqlRunStore.claim_next`; SQLite has no equivalent, so the
same statement runs there without the locking clause — ADR-0004). The claim
*predicate* is tested on SQLite: `tests/integration/test_worker_claim.py` drives
`claim_next` and `drain_once` and asserts a `RECEIVED` run is claimed, a
`WAITING_APPROVAL` run is **not**, and boot preserves the states it must. The
**locking clause** is now tested too, against a real PostgreSQL:
`tests/integration/test_worker_claim_postgres.py` (`@pytest.mark.postgres`, run
by the `verify` CI job and asserted *PASSED* there) asserts the two properties
the clause actually has — a claim whose transaction **overlaps** another's takes
a *different* row rather than blocking on the locked one, and a fully-overlapping
claim of a single locked run gets `None`. An earlier revision of this section
named a `test_two_workers_do_not_claim_the_same_run` that did not exist; the test
that does exist is named above.

What that test deliberately does **not** claim is the property ADR-0005 used to
advertise — that two workers on one database cannot claim the same run, "scaling
out is adding replicas". ADR-0005 and `docs/architecture.md` both carry a
correction of that claim now, and the correction is kept here because the
measurement is what the lease is measured against. **Before the lease, they
could not.** The claim as it stood at commit `23d3ffe` held its row lock only
for the duration of its own `SELECT`: it opened a transaction, read the oldest
claimable row, returned a domain object, and committed. Nothing was held across
the run's drive, and the claim did not change the row's status. The lock
therefore excluded another claim only while a claim transaction was *open*; two
workers whose polls did not overlap both received the same still-`RECEIVED` run,
and both drove it. This was not theoretical: thirty pairs of concurrent
`claim_next` calls on one row returned that row to both, thirty times out of
thirty. The test that characterised it was deleted when the lease fixed it — its
own docstring named the fix as the prompt to delete it — so the measurement is
recorded here rather than papered over. The practical consequence then was **run
one worker per database**; ADR-0005 says that is no longer a correctness
requirement.

**The claim lease fixes that, and in exchange widens an already-unreportable
window from about a second to five minutes.** The lease (`b9bbd5c`;
`migrations/versions/0002_claim_lease.py`) writes `claimed_until`/`claimed_by`
in the same transaction as the claim read, so a *live* worker's run is not
handed to a second worker — which is the 30/30 above, fixed. What the lease
buys that is not free is that it now decides **when an abandoned run becomes
claimable again**, and it does not change the run's status when it does
(`SqlRunStore.claim_next` sets `claimed_until` and leaves `status` alone). So a
run that has been claimed and not driven
stays `RECEIVED`, and from every surface this repository has, that is
indistinguishable from a run nothing ever claimed:

- the boot sweep deliberately skips `RECEIVED` (`db.py:150`,
  `mark_interrupted_runs`), so a worker restart does not clear the lease;
- only a worker *driving* the run renews it (`worker/loop.py::_renew_lease_while_driving`),
  so a claimer that died stops renewing and the run simply waits the lease out.

Measured on a live stack against a healthy worker (`Up 31 seconds`, boot line
printed, 0 restarts), a `RECEIVED` run carrying an unexpired lease went
unclaimed for the full **300 s** (`WORKER_CLAIM_LEASE_SECONDS`,
`docker-compose.yml`), and the page rendered `Status: Received`, `Started: —`,
`0 steps, as the server ordered them`, `No steps recorded yet.` — character for
character what a run nothing claimed renders as, and the exact snapshot CI run
38202887580 could not explain. Before the lease that window was about a second,
because a claim that wrote nothing left the row for the very next poll.

**The default is still defensible, and the default did not move.** Recovery is
automatic — the lease expires, `claim_next`'s predicate admits the row again
(its `claimed_until < now` clause), the next poll takes it — and that is why the
boundary ADR-0005 describes is true -- a run that is claimed and then abandoned
stays `RECEIVED` for up to one lease period, and is then claimable again. It is also precisely what makes
the symptom unreportable meanwhile: recovery is silent, and it arrives up to one
lease period late. **Lowering the default is not available.** It has to exceed
a run's p95 drive duration, measured at 276.7 s on the live eval
(`docs/milestones.md` §M8), or a long run loses its lease mid-drive and a second
worker drives the same refund — which is a worse outcome than a slow one, and
276.7 s is already larger than any detection window an operator would accept.
The two requirements are incompatible, so the number is at its floor.

**What is now bounded is *detecting* it, which is what the paragraph above named
as the missing half.** `GET /api/runs/{id}` carries a `claim_lease` object
(`docs/api-contract.md` §3): `unclaimed`, `held` or `expired`, carrying the
holder's `WORKER_ID` and the deadline. The dashboard reads it, so the run-detail
page now says *"A worker (worker-compose) claimed this run and has not written a
step yet. The claim is held until … and is renewed while the worker keeps
driving; if it is not renewed, the run becomes claimable again then."* in place
of the sentence it used to print unconditionally — *"the worker has not claimed
it yet"* — which was false for five minutes at a time. The state is computed
server-side rather than left to the client, so a dashboard cannot forget to.

**What is still not bounded is the recovery.** Nothing announces that a lapsed
lease has been taken up again: the page says a run is *held* and that the hold
has a deadline, not when the run starts moving, because nothing will. So a run
at `RECEIVED` for longer than `WORKER_POLL_INTERVAL` is still not evidence of a
busy worker. What changed is that an operator can now rule the *dead-worker* and
*claimed-but-stalled* cases in or out from the page instead of guessing — a run
with no lease has not been claimed, and a run whose lease has expired says so.

**No health checking of MCP servers at startup.** A dead billing server is
discovered when a run tries to use it, not when the worker boots.

**A hard-killed worker orphans its MCP child processes.** Under the default
`MCP_TRANSPORT=inprocess` this section does not apply — there is no child
process. Under `MCP_TRANSPORT=stdio` the worker spawns up to three server
children, and it reaps them on a *clean* shutdown: `SIGINT`/`SIGTERM` set a
stop flag, the poll loop drains, and the `finally` that closes the gateway
terminates each child. `SIGKILL`, a container OOM-kill, or a host power loss
bypass all of that — the finally never runs, the children are reparented to the
init process, and they keep running, holding their pipes open with nobody
reading them. Nothing in this codebase detects that: there is no parent-death
guard (`PR_SET_PDEATHSIG` on Linux, a Job Object on Windows), and adding one is
a cross-platform design change rather than a fix that belongs in a limitations
entry.

*Operator mitigation:* treat an ungraceful worker death as a reason to restart
the whole container, not just the worker process — `docker compose restart
worker` reaps the orphans as a side effect of the runtime replacing the
container's process tree, whereas restarting the process in place does not. If
you need the guarantee rather than the habit, give the deployment a process
supervisor that kills the whole process group on worker exit
(`docker stop`, a systemd `KillMode=control-group`, or a `prctl(PR_SET_PDEATHSIG)`
wrapper). Phase 2, if it is done at all, should be a parent-death guard rather
than a scan, because a scan cannot distinguish this worker's children from an
operator running a server by hand.

**The step budget is the only loop guard.** 24 plan/execute rounds. A run that
loops more cheaply than it calls tools (e.g. re-retrieving without acting) is not
otherwise bounded, and cost is not capped.

## 3. Retrieval

**Top-k cosine similarity, nothing more.** No reranker, no hybrid BM25, no
query rewriting, no MMR diversification. On a 15–30 document corpus this is
adequate; it is not adequate at 3,000 documents and the design does not pretend
it is.

**Chunking is heading-aware fixed-size, ~500 tokens with overlap.** Tables and
long procedures split awkwardly. Nothing in Phase 1 detects a bad split.

**The embedding model is whatever `EMBEDDING_PROVIDER` selects.** The default
`local` embedder is a **lexical** scorer, not a semantic one: a bag of
stopword-free, lightly-stemmed words, weighted sublinearly by frequency and
hashed into a fixed-width vector. It is a real retriever on this corpus —
measured over `evals/datasets/retrieval.jsonl` it reaches **Recall@5 = 14/15**
and **Recall@10 = 15/15**, and its scores separate answerable from unanswerable
questions — which is what lets a fresh clone and the CI suite run the golden
path with no API key. What it is not is semantic: **there is no synonymy**. A
question whose answer is worded entirely differently from the question will not
match it, and the stopword list and the two suffix rules are hand-written rather
than derived from a corpus. Every number quoted in the README comes from a run
whose *model* provider was a real endpoint but whose *embeddings* were this local
lexical embedder — `evals/results/2026-10-11T15-39-42.json` records
`embedding_provider: "local"` — so that run's retrieval rows measure the lexical
retriever, not a provider's. Each run records the config it ran under alongside
its results, which is what makes that distinction checkable instead of a claim
taken on trust.

**`RETRIEVAL_MIN_SCORE` is calibrated against the default embedder, and only
against it.** The default is `0.22`, chosen by measuring
`evals/datasets/retrieval.jsonl`: the answerable cases score **≥ 0.2500** and
the `expect_abstention` cases **≤ 0.1844**, so any value in that gap separates
them and `0.22` sits inside it with margin on both sides. Three things follow,
and none of them is comfortable:

1. **The margin is narrow.** 0.1844 → 0.2500 is a band 0.066 wide, on 20 cases.
   A corpus of 20 questions is a smoke test for separation, not a calibration.
2. **The number is meaningless under a different embedder.** A provider's cosine
   and this lexical score are different quantities on different scales; `0.22`
   for one is not `0.22` for the other, and switching `EMBEDDING_PROVIDER`
   without recalibrating gives a threshold that either admits everything or
   refuses everything. `.env.example` says which default suits which provider.
3. **It is still a single global threshold.** Not tuned per query type, not
   calibrated per corpus, and not a probability — it is a number with a comment
   and a measurement behind it, which is better than the previous value (0.35,
   which nothing could reach) and is still a long way from a learned ranker.

**The SQLite vector store does not survive a second process, and SQLite is not a
supported runtime configuration.** On SQLite `build_vector_store` returns
`InMemoryVectorStore`, which holds the chunk *embeddings* in process memory and
writes the `knowledge_chunks` rows with `embedding` NULL. A real deployment runs
two processes — the API performs `POST /api/knowledge/reindex`, the worker
retrieves — so reindexing loads vectors into the API's memory, the worker's
memory is empty, and retrieval returns **zero hits on every SQLite deployment**.
Measured on a real worker and a real API over an indexed corpus (17 documents, 98
chunks): classification correct (`duplicate_charge`, 0.93), retrieval `count: 0`,
no tool called, no refund proposed, no approval requested — the run abstains,
escalates, and the reply claims a refund that never happened. This is *not* a bug
to fix: per ADR-0004, production is PostgreSQL + pgvector and SQLite is a
tests-only path. The test suite could not see it because the golden harness
builds the API and the worker in one process, sharing a store a deployment does
not share. **Use PostgreSQL for anything that runs as more than one process.**

**Knowledge is static Markdown re-indexed manually.** `POST /api/knowledge/reindex`
is the only update path; there is no watch, no incremental indexing, no
versioning of document revisions. Citations point at a document's current content,
so a citation made last week may no longer match the text quoted.

## 4. Agent behaviour

**The workflow is optimised for one ticket shape.** Duplicate-charge billing
disputes. Other categories (`account_access`, `technical_issue`) are classified
correctly and then escalate, because there is no workflow behind them. The
classification metric measures the classifier; it does not mean the system
handles those categories.

**No multi-turn conversation.** A ticket is processed once. A customer reply to
the agent's response is a new ticket with no memory of the prior run, beyond
what an operator links manually.

**Planning is single-step and reactive.** The model proposes one action per
round; there is no up-front plan the runtime follows, and no critique step. This
makes the trace legible, which is the Phase 1 goal, and makes the agent less
capable than a planner-based design, which is a Phase 2 question that now has
real traces to design against.

**The number of tool calls on the golden path is the model's choice within
bounds.** The eval's `tool_selection` cases assert the *set* of tools; the order
and the exact count are not asserted and can vary between runs.

## 5. Evaluation

**50–80 cases is a regression detector, not a measurement instrument.** A
reported `0.85` on 20 cases carries a confidence interval wide enough that a
meaningful real change could hide inside it. The README reports counts alongside
scores for this reason.

**No LLM-as-a-judge, so reply quality is unmeasured.** Faithfulness is partially
checked structurally (the reply's citations must be a subset of the run's
retrieved citations). Tone, helpfulness, and appropriateness are not measured at
all.

**Live evals are manual.** `eval-live` runs only on `workflow_dispatch`. There is
no scheduled regression tracking, no stored baseline, and no alert when a metric
moves. Phase 2.

**The fake provider's numbers are not results.** `__main__.py` refuses to print a
metric table for `--provider fake` unless `--allow-fake-scores` is passed. The
smoke job in CI is a plumbing check.

**Latency and cost numbers are single-machine, single-region, from one run.**
They are order-of-magnitude figures, not benchmarks, and the README says so.

## 6. Engineering

**No production deployment.** Docker Compose on one host is the target. No
Kubernetes, no managed Postgres, no TLS termination, no reverse proxy, no
backups, no migration rollback story beyond `alembic downgrade`.

**No load testing.** The concurrency model is correct in principle (database-backed
claiming) and untested at any real concurrency.

**Alembic migrations are forward-only in practice.** `downgrade` exists where it
was easy to write and is not exercised by CI.

**No coverage gate on `apps/web`.** The dashboard has type checking and linting
in CI; its component behaviour is verified by hand against the golden path. This
is a real gap and is the reason M7 is scheduled after the workflow it displays.

**One locale ships as a translation; there is no locale negotiation.** Corrected
2026-10-14: this bullet previously read "No i18n infrastructure. The dashboard is
English-only," which stopped being true at `651bcef`. What exists now: an
English source catalogue, a `zh-CN` catalogue of **330 strings** across 18
namespaces (`web/src/i18n/dictionaries/zh-CN.json`), an `httpOnly SameSite=Lax`
cookie read server-side, and a switcher in the nav. English remains what a fresh
clone renders, with no configuration — which is `DEFAULT_LOCALE = "en"` in
`web/src/i18n/config.ts`, not a DoD item, and an earlier revision of this
sentence cited D8 for it. D8 (`docs/milestones.md`) is about `.env.example`.

What is still absent is the half of internationalisation that is usually
assumed by the phrase: **there is no
`Accept-Language` negotiation, by deliberate decision** — the guide recommends
it for a public website, and this decides whether money moves on a
loopback-bound dashboard, so it is an explicit operator choice rather than a
default. One translation is not a localisation programme; `docs/phase2-plan.md`
§P2-F scopes the remaining languages as a product decision this project has not
made. Covered by 22 `web-e2e` cases across
`web/e2e/i18n.spec.ts` and `web/e2e/i18n-catalogue.spec.ts`.

**The favicon's 256-pixel frame costs about 75 KB on the first visit, and the
browser pays it whether it wants that frame or not.** An `.ico` is fetched
whole: a browser does not request the one frame it is going to use, it
downloads the file and picks a frame out of it. So the 256 frame's 75,186
bytes of PNG arrive whether the browser wanted that size or not, and they
are 75,186 of `web/src/app/favicon.ico`'s 92,226 bytes — the same favicon
measured 17,508 bytes before the 256 frame was added, and 5.3 times as
large. The cost is about 75 KB of extra transfer on the first visit to the
domain, after which it comes out of the cache like any other asset. The
trade is deliberate: the 256 is the frame a reader that takes a single
picture out of an `.ico` is handed, and it is what makes the page announce
`sizes="256x256"` rather than `sizes="64x64"`. The cost is real, and it is
written down so the next reader weighs it instead of rediscovering it.

**The apple-touch icon's rounded corners are iOS's, and the four corners of
the picture are ours.** iOS puts the apple-touch icon into an opaque
container and masks the top of it with its own squircle, so the picture
itself has to be a completely filled square. The badge is a rounded square,
and cutting the export's white ground away leaves its four corners
transparent — which on iOS is four holes showing the container's black
through the badge. `web/src/app/apple-icon.png` is therefore 180×180 and
opaque everywhere, and the corners are filled by growing the badge's own
colour field outwards one ring at a time. **The cost is that the bright blue
stroke that bounds the badge is not in the four corners of the picture we
ship**: the corner carries the field continued to the square instead, so the
badge's rounded-square outline is lost from it and iOS's squircle supplies
the shape instead. The stroke is not smeared across the corner either, and
that is deliberate — the fill starts three pixels inside the artwork's edge
(`APPLE_EDGE_INSET` in `scripts/make_assets.py`), because a fill that took
the edge stroke for its source would drag it across the corner as a stripe,
the stroke and the ground differing by more than a hundred units of blue.
The result is 38,678 bytes of PNG that is not the same picture as the badge,
and that is the price of iOS's rule.

**The icons are committed binaries, and the guards prove that the committed
file is current, not that the artwork was changed on purpose.**
`desktop/assets/icon.ico`, `web/src/app/favicon.ico`,
`web/src/app/apple-icon.png` and `docs/assets/logo.png` are build products
that live in the repository: `scripts/make_assets.py` generates them from
`docs/assets/icon-source.png` and `docs/assets/logo-source.png`, and nothing
in the build regenerates them. The guard is the script's second mode,
`python scripts/make_assets.py --check`, which the `lint` job runs on every
push and `tests/unit/test_desktop_icon_is_current.py` asserts; it compares
picture against picture, it writes nothing, and it regenerates into a
temporary directory precisely so that a check cannot repair the tree it is
checking. What it establishes is that the file in the tree is what the
script produces today. **What it does not establish is that the artwork was
changed on purpose.** Replace `docs/assets/icon-source.png` with a different
drawing, run the script, commit the result, and every guard in the
repository is green: the guards are about drift, and a deliberate redraw is
not drift. The cost is a manual step no tool can perform — changing the
artwork means running the script and committing the artefacts by hand — and
it is invisible in a review, because a binary is not something a diff shows.

**The icon artefacts are produced on Windows and checked on Linux, and only
the Linux half of that pair is automatic.** The guard runs in the `lint`
job, on `ubuntu-latest`, and it runs nowhere else: the `desktop-package`
job that does run on `windows-latest` has no Python environment at all, and
the CI file says so. The committed artefacts were produced on a Windows
machine, so the combination is *produced on Windows, checked on Linux* — and
of that pair only the checking is automatic. The comparison is picture
against picture, and that is deliberate: a PNG's bytes depend on the zlib
that packed them, the Windows and manylinux wheels bundle different zlibs,
and a byte comparison would call an unchanged tree stale. So no job on any
platform re-runs the generator on Windows and compares the result with what
is committed. The two jobs do assert the two halves of the chain, each on
its own platform and neither together: the `lint` job, that the committed
file is what the script produces, on Linux; the `desktop-package` job, that
the executable carries *that* file's bytes — its group icon lists the ten
sizes the artwork is resampled to, each is backed by an icon resource
holding the committed bytes, and Windows itself renders the committed
32-pixel frame — on Windows.

## 7. Product

**The dashboard is operational, not polished.** It is designed to make a trace
readable, which is what a reviewer needs. It is not a product.

**No notification when an approval is pending.** An operator must have the
Approvals page open. There is no email, no Slack, no webhook. Deliberate — email
and Slack integrations are explicitly out of scope — but the consequence is that
a run can sit parked indefinitely in a real deployment.

**Approvals do not expire in practice.** The `expired` status exists in the
schema and a `pending` approval will sit forever. No TTL job is implemented. The
schema is ahead of the behaviour here, and that is stated rather than quietly
assumed.

**One seeded demo dataset.** Ten customers, twenty-odd invoices. Real data has
messier shapes — missing fields, multiple currencies, partial refunds across
periods — and the fake servers return clean records. The agent's argument
validation is exercised against a schema, not against real-world messiness.

**The native menu is localized; the operating system around it is not.** The
application's own menu bar is built in the operator's language and follows the
*same* cookie the dashboard renders from (`opspilot_locale`), so the menu and
the page cannot disagree. The shell reads that cookie out of the session's
cookie store rather than being told by the page (`desktop/src/menu.ts`), which
is what makes the menu correct at start-up — the operator's choice from last
time — and not only after a switch inside the running application. What is
**not** translated is everything Chromium and the OS draw for themselves: a
right-click context menu, a native file dialog, a system error bubble, the
window's own title bar. Those speak the platform's language on a platform
whose language the application cannot change. The menu's words are also the
shell's own copy of the dashboard's catalogue
(`desktop/src/menu-policy.ts`), because a native menu is drawn by the main
process, which has no Next.js runtime to read `web/`'s dictionaries from;
`desktop/e2e/menu.spec.ts` reads `web/src/i18n/config.ts` and fails if the
locale list or the cookie name drifts, so adding a language to the dashboard
cannot leave the menu behind without a red test.

**The menu is localized; the application's own *error copy* is not.** The
localization covers the dashboard's rendered pages and the native menu bar.
Two surfaces are not in it, and both are English:

- **The dashboard's own sentences about the transport.** `web/src/lib/api/client.ts`
  composes them — `The API answered {status} on /health.`, `Could not reach the
  API at {base}…`, `The API rejected the operator token…` — and
  `web/src/components/api-status.tsx:38` renders them in a banner that appears
  on every page. These are the *dashboard's* words, not the API's, so
  §P2-F's carve-out for "the API's own user-facing text" does not cover them.
- **The desktop shell's messages.** Every title and paragraph in
  `desktop/src/lifecycle/errors.ts` (the Docker states, the token states, the
  Compose states), the probe's failures, the session's shutdown messages and
  the bootstrap page's failure panel are English-only. §P2-F puts the shell in
  scope, and this is the part of that scope that is not done.

The reason this is recorded rather than fixed is that a half-localized
application is worse than a documented one: a Chinese operator reading a
Chinese page and an English error banner has to decide which half to believe.
Recording the boundary keeps the promise the repository already makes — that
`limitations.md` is the honest list — rather than leaving the gap to be found
by a user. The fix is mechanical (a second dictionary beside
`desktop/src/menu-policy.ts`'s, and six more keys in the dashboard's), and it
is a Phase 3 decision rather than a Phase 2 one.

---

## 8. Documented but not implemented

The repository has repeatedly shipped a small, specific defect class: a
capability described in a document — a README diagram, a `.env.example` line, a
limitations claim — that no code performs or no test covers. Each instance was
found late (three in M2–M5, more in M9's Definition of Done audit) and each is
the same failure in a different costume: a claim that reads as evidence of
something it is not. They are listed here, in one place, because the pattern is
only visible when the instances are gathered rather than scattered through a
milestone log.

**The list is not exhaustive, and does not pretend to be.** Two instances of the
same class are Phase 2's, and Phase 2 records them in its own acceptance table
rather than here: the **D2** installer walk-through, which nobody who did not
build the installer has run (`docs/phase2-plan.md` §D2 and the D2 row of its
acceptance table; `docs/progress.md`), and the **GitHub Release**, which is
held back on purpose until D2 passes (`docs/phase2-plan.md` §P2-E and the E
row). They are cross-referenced here rather than restated, because the title
describes what this section lists, and a wider title would be a claim this file
could not keep.

**Remaining — documented, not implemented:**

1. **`issues.create` is not on the golden path** — recorded boundary, operator
   decision 2026-10-09 (D19a in `docs/milestones.md`). The tool is real, is
   `SAFE_WRITE`, and is exercised in isolation (`test_mcp_gateway.py`,
   `test_gates.py`, `test_policies.py`). The committed `duplicate_charge` fixture
   the golden path replays proposes no `issues.create` call, so the run does not
   create a ticket; the README's golden-path trace and `docs/milestones.md` §M6
   no longer draw that step. The capability is **not done, by decision rather
   than by neglect**: whether the workflow **should** file an issue is still
   open, and adding it to the golden path remains a possible Phase 2 product
   decision.
2. **The two-worker guarantee is tested at the predicate, at the lock and at the
   lease; the end-to-end promise is still not unconditional.** Three files carry
   the claim. `tests/integration/test_worker_claim.py` covers the claim
   *predicate* on SQLite. `tests/integration/test_worker_claim_postgres.py`
   covers the **locking clause** against a real PostgreSQL — two overlapping
   claimants on one locked run take different rows, and a claim of a single
   locked run is withheld — and, in
   `test_a_leased_claim_is_withheld_from_a_second_worker`, the lease's
   PostgreSQL half. `tests/integration/test_worker_claim_lease.py` is the
   lease's **SQLite half**, and the regression for the measured defect: thirty
   pairs of non-overlapping claims on one run, asserted to hand that run to
   *none* of the second claimants.

   What remains open is the *end-to-end* promise — two workers, one database, no
   run driven twice — and the reason is not the one this item used to give. The
   lease, not the lock, is what a live worker's claim now holds across the
   drive: `SqlRunStore.claim_next` writes `claimed_until` and `claimed_by` in
   the claim's own transaction, and `renew_lease` is a conditional `UPDATE ...
   WHERE id = ? AND claimed_by = ?` that only the holder can extend. The 30/30
   duplicate is therefore closed, and §2 records the measurement it closed. But
   a lease is a deadline, not a lock. A worker that stalls past its lease, or
   dies so that nothing renews it, leaves a lease that expires, and an expired
   lease makes the run claimable again — a second worker then claims it. That
   is asserted rather than assumed:
   `test_worker_claim_lease.py::test_a_lease_that_lapses_mid_drive_is_the_tested_failure_mode`
   drives a run, lets an unrenewed lease expire, and asserts a second worker
   **does** claim it. The guarantee holds while the lease holds and is
   conditional once it lapses. ADR-0005 states the same boundary from the other
   side: "a run that is claimed and then abandoned stays `RECEIVED` for up to
   one lease period (300 s)" (`docs/adr/0005-db-backed-worker-no-broker.md`,
   the "Later decision, and what it costs" paragraph). The unconditional form
   of the promise is not what the code makes, and the boundary is stated here
   rather than papered over.
3. **`LangGraphOrchestrator` is implemented by nothing, and no contract test can
   exist yet.** This item previously read "Neither orchestrator is implemented".
   `LinearOrchestrator` is now real: `_pump` builds a `StageDriver` (the stage
   logic extracted from the old inline loop into one function per `StepKind`)
   binds it to the orchestrator and loops `orchestrator.step`, so `run_loop` no
   longer discards its orchestrator. `tests/agent/test_linear_orchestrator_drives.py`
   proves it with a spy that records the stage sequence, and a raising
   orchestrator now stops the pump. What remains a capability gap, with no
   decision behind it, is `LangGraphOrchestrator`: both `LangGraphOrchestrator.step()`
   and `.build_graph()` raise `NotImplementedError`. No test imports
   `langgraph_adapter`, and no test compares the two executors' step results —
   such a test cannot be written while one of the two raises on every method it
   would compare. `langgraph` is declared in `pyproject.toml` and imported by no
   module in the project. The `LinearOrchestrator` row now sits `- [x]` in
   `docs/milestones.md` §M4 with that evidence; the `LangGraphOrchestrator` row
   stays `- [ ]`.

**Fixed, and no longer in this list** (kept as a record so the list's history is
legible):

- **The packaged app refused every page-initiated navigation, so the language
  switcher did nothing.** Found on 2026-10-16 by the person running D2, who
  reported that clicking 中文 had no effect. The guard was not wrong about
  *which* origins to allow — it was holding a stale copy of them.
  `main.ts` installs `restrictWebContents` from
  `app.on("web-contents-created", …)`, which fires inside `createWindow()`;
  the dashboard's origin is assigned to the module-level `allowedOrigins` later,
  inside `launch()`. The guard took the **array**, so it captured
  `[BOOTSTRAP_ORIGIN]` — the pre-launch value — and `http://127.0.0.1:3210` was
  never in the list. Every `will-navigate` from the page was `preventDefault`-ed
  with only a `console.warn`.

  **It survived a whole phase because Next.js client-side routing does not fire
  `will-navigate`.** The five nav links are client transitions, so the app looked
  fully navigable. The first control that performed a real document navigation
  was the language switcher — which is why the symptom was "the 中文 button is
  dead" rather than "the app is broken". It was reproduced as a minimal Electron
  program before being fixed: the guard received `["opspilot://ui"]` while the
  module variable already held both origins. The fix takes a **getter**, so the
  list is read at navigation time, which is the only moment it is complete;
  `e2e/security.spec.ts` pins it with a fake `WebContents`, and a mutation that
  restores the array parameter fails that test.

  **The same session found a second, quieter defect in the packaging.** `npm run
  package` never built `web/` — it ran the desktop TypeScript build, then
  `stage-stack.mjs`, which copies `web/.next/standalone` if it exists and tells
  you to build web first if it does not. So a package could ship whatever the
  dashboard was last built from, silently, and did: an installer built during
  this session carried a `.next` from the previous day. `package` and
  `package:dir` now run `build:web` first.

- **Two gate-1 schemas rejected the arguments the contract documents.** Found on
  2026-10-15 while chasing the live recording's `crm.get_customer` failure.
  `docs/mcp-contracts.md` §1 offers `customer_email` **or** `customer_id` for
  `crm.get_customer`, and `mcp_servers/crm/server.py` implements both — but all
  three CRM lookups shared `CustomerLookupArgs`, which declared only
  `customer_id` under `extra="forbid"`, so the contract's own first example was
  refused at gate 1. `issues.create` had the same shape: the contract and server
  take `{title, description, priority, labels}`, gate 1 declared
  `{title, body}` — a field the tool has no parameter for, and three missing.
  This is the defect `TransactionListArgs` records having "survived four
  milestones" (*no argument set satisfied both sides*) in two more tools. It
  escaped the existing guard because
  `test_every_gate_1_schema_is_accepted_by_the_real_tool` only pushes schema
  fields *toward* the server; a contract parameter the schema omits is never
  sent, so nothing checks it. `tests/integration/test_mcp_contract_arguments.py`
  is the reverse direction, reading the parameters out of the contract document.
  Fixing it exposed a third leak: unset optional fields reached the MCP server as
  `null`, so an email-only lookup arrived as *both* identifiers — now
  `validate_tool_call` dumps with `exclude_none=True`. **It does not change the
  0.471 argument-validity figure**: the recorded call sent `email`, where the
  contract says `customer_email`, and that argument is wrong under either schema.
  What it fixes is the case where a model reads the contract correctly and is
  refused anyway.

- `MaxStepsExceeded` killing the worker — a run that spent its step budget
  raised out of `drain_once`, and `poll_forever`'s `while True` had no handler,
  so the exception took the worker down and every run queued behind it waited
  for a restart. It was invisible in the compose deployment because the worker
  carries `restart: unless-stopped`: the container came back and resumed
  polling, which hid the defect while charging a full worker restart per
  over-budget run — and made a browser test look flaky, since
  `web/e2e/failure-states.spec.ts` drives a run to `max_steps_exceeded` on
  purpose. Now suppressed alongside `MCPUnavailable`, which was already
  suppressed for the same reason: the runtime writes `FAILED` before raising, so
  the exception arrives with no work left to do. A programming error still
  escapes, by design, and `tests/agent/test_worker_survives_max_steps.py` pins
  both halves.
- `knowledge_injection` — the `safety.jsonl` injection cases are now covered by
  `tests/security/test_prompt_injection.py` against a real retrieved injection.
- `/ready` — the readiness probe now reports unhealthy when the database is
  unreachable, rather than healthy on any reachable process.
- `MCP_*_COMMAND` — the three command settings are read on the production path
  (`worker/__main__.py::build_worker_gateway` →
  `adapters/tools/mcp_gateway.py::build_stdio_servers_from_settings`) and spawn
  real child processes through `adapters/tools/mcp_stdio.py`. `MCP_TRANSPORT`
  selects between that and the existing in-process path, which stays the default.
  `tests/integration/test_mcp_stdio_transport.py` proves it by calling
  `crm.get_customer` through a live subprocess and by checking that a server
  process really appeared and was really reaped.
- `knowledge.search` — `architecture.md` §7 advertises it as an automatic `READ`
  tool, and `adapters/wiring.py::RetrievalStack` has bound a `KnowledgeTool`
  since retrieval landed, but no code on the dispatch site could reach it. The
  runtime's single `gateway.call_tool` call site was handed a bare
  `MCPToolGateway` at both assembly points, and `mcp_gateway.py::_server_for`
  returns `None` for any server outside `{crm, billing, issues}` — so a server of
  `"internal"`, as `domain/tools.py` registers it, came back
  `server_unavailable` every time. `adapters/tools/composite_gateway.py::CompositeToolGateway`
  now routes on `TOOL_REGISTRY[name].server` at call time, and
  `tests/integration/test_composite_gateway.py` proves it at both wiring points.

  **Re-verified 2026-10-15, against a running PostgreSQL.** The claim above was
  checked rather than re-read, because "fixed" is the word this list is least
  entitled to take on trust. A bare `MCPToolGateway` handed `knowledge.search`
  still answers `ok=False, error=server_unavailable` — the pre-fix behaviour,
  reproduced directly — while `build_worker()`'s `CompositeToolGateway` answers
  `ok=True` with five hits from the committed corpus
  (`duplicate-charge-sop.md`, `refund-policy.md`). Both assembly points are
  pinned: `test_the_worker_runs_on_a_gateway_that_serves_every_registered_tool`
  asserts `build_worker`'s gateway *is* a `CompositeToolGateway` and that the
  internal tool executes through it, and
  `test_the_eval_harness_wires_a_real_retriever_into_the_knowledge_half` drives
  a real case and dispatches through the gateway that case ran with, so
  "wired to this case's stack" is distinguished from "wired to something". Nine
  tests, all passing with the database up.
- The containerised `web` service's static assets — `web/Dockerfile` copied
  `public/` and `.next/static/` to `/app/web/public` and
  `/app/web/.next/static`, and the standalone server reads neither. It `chdir`s
  to its own directory and resolves both against *it* (`base-server.js`:
  `distDir = join(dir, config.distDir)`; `next-server.js`: `getPublicDir() =
  join(dir, 'public')`), so with `server.js` at `/app/server.js` it wanted
  `/app/public` and `/app/.next/static`. Every JavaScript bundle and stylesheet
  404'd.

  This is the entry that best explains why the list exists, because **the
  failure was shaped exactly like success at every level the suite could see.**
  `GET /` returned 200, so the container reported `Healthy` (its healthcheck is
  a `fetch` of the root document). The document rendered, so
  `web-service.spec.ts` passed — every assertion in it is about server-rendered
  HTML and server-side API reads. And `web-e2e` never exercised the container's
  assets at all: `golden-path.spec.ts` runs the dashboard on the host, where
  `e2e/stack.ts` assembles the standalone tree by hand (it copies those same two
  directories in before spawning `server.js`), so the host path had always been
  correct. Only the browser knew: no stylesheet, no hydration, and Reindex,
  Submit and Approve silently doing nothing.

  Both destinations are now `./public` and `./.next/static`, which is what Next's
  own documentation prescribes (`cp -r public .next/standalone/ && cp -r
  .next/static .next/standalone/.next/`). Verified against the running container
  rather than the HTML alone: all 8 scripts and stylesheets the page requests
  return 200 with real bodies, the Tailwind custom properties resolve, the
  Reindex click POSTs 200 and repaints the table, and Submit ticket returns 201.
  Rebuilding the same image with the old destinations reproduces the defect
  exactly — `GET /` 200, all 8 bundles 404 — which is what makes the fix a
  measurement rather than an assertion.

  **What changed beyond the two lines is that CI can now see this class of
  failure.** `web/e2e/web-service.spec.ts` drives the real container and asserts
  that every script and stylesheet the page requests is served. It is red on the
  old image and green on the new one. It is an asset check rather than a
  click-through on purpose: a click would duplicate the golden path's coverage
  and depend on a seeded knowledge directory, whereas the browser fetches every
  `<script src>` before any of it can run, so the assertion still holds on the
  inert page. The broader lesson is the one §8 keeps teaching — a claim about
  running belongs here until something actually runs it, and a check that only
  asks "did the server answer?" cannot tell a working dashboard from a blank one
  with the HTML still attached.
- `evals/datasets/fixtures/` — `docs/risks.md` R2 claimed the fake provider "is
  a *replay* of recorded real interactions, not invented scripts:
  `evals/datasets/fixtures/` holds recorded provider responses for the golden
  path, captured once from a real run and committed," and the same sentence
  stood in R8, `docs/agent-state-machine.md`'s note on the deterministic
  provider, `README.md`'s "Watch it run" section and its
  `MODEL_PROVIDER=fake` note, and `evals/README.md`. (The three line numbers this entry used to carry are gone:
  a number drifts on the next edit, so sections are named instead — the
  convention this repository adopted on 2026-10-15.) Both committed fixtures carry `"recorded": false` and the
  note "HAND-WRITTEN PLACEHOLDER, not a recording. Regenerate with
  record_fixture() against a real provider"; `grep -c request_hash` returns **0**
  in each. They are hand-written, which is the "invented scripts" the sentence
  denied.

  The mechanism makes the claim worse rather than merely unmet.
  `FakeModelProvider._call_for` (`adapters/models/fake.py:148`) prefers scenario
  mode: given a scenario name it returns calls by `method`, in file order, off a
  per-method cursor, without ever inspecting the request. `request_hash` is read
  only on the no-scenario fallback path and only to name the
  `UnmatchedFixtureError` raised when the script runs out. So a fixture with no
  `request_hash` — which is both of them — is served strictly by position and
  never sees what the model actually sent: less protection than R2 claimed, not
  more. `record_fixture()` exists (`fake.py:275`) and has never been run against
  a real provider, so no recording is committed. All four documents now say so.

  **This entry is here because the claims were corrected, not because the
  fixtures became recordings.** They did not, and R2's underlying risk — a
  scripted provider proving nothing about a real model — stands closer to open
  than R2 claimed.

  **Update, 2026-10-15.** The half of this that needs no API key is now closed:
  `tests/unit/test_record_fixture_roundtrip.py` drives `record_fixture()` against
  a stub provider and replays its output through `FakeModelProvider`, proving the
  round trip — the recorded value returns, a prompt the recorder never saw is
  refused, and the written file carries a `request_hash` that matches
  `hash_request(system, prompt)`. Mutation-verified three ways: a wrong hash, a
  forced `recorded: false`, and an emptied provenance copy each break a test. So
  the recorder works and what it writes is replayable; **the responses are still
  hand-written**, and the only step left is the one run against a real provider
  that needs the key. One test in that file fails if anyone flips a committed
  fixture's `recorded` flag without having made a recording, which is the failure
  mode this entry exists to prevent.

  **The real recording was attempted on 2026-10-15 and could not be made.** The
  key is valid and the endpoint is reachable — the request is authenticated and
  answered — but every call returns `HTTP 429`, MiniMax error code `2056`:
  *"已达到 Token Plan 用量上限"* (Token Plan quota exhausted). Three attempts six
  seconds apart returned the same error, so it is a spent quota rather than a
  burst limit, and it is the same 429 that interrupted two tool calls in this
  session. Nothing about the recorder is implicated: it was never given a
  response to record. **A seventh test now pins what happens when a recording
  dies partway** — `test_a_recording_that_dies_partway_writes_no_file`, driven by
  a stub that answers once and then raises — because that is the shape this
  attempt would have taken against a metered endpoint. `record_fixture` collects
  every response before it opens the file, so a mid-run failure discards the
  answers already collected and writes nothing; mutation-verified by moving the
  write inside the loop, which fails the test. The fixtures therefore remain
  hand-written, and the M4 criterion stays unticked, for a reason that is now
  recorded rather than assumed.

The rule the list enforces: a claim about running belongs in this section until
something actually runs it.

---

## What is *not* on this list, and why

Four things a reader might expect to find here, which are deliberately absent:

1. **"The permission model is unverified."** It is verified, by four CI gates
   that count violations and require zero. That is a claim the project can make.
2. **"The refund can be executed twice."** It cannot, through three independent
   layers, tested by calling the MCP server directly. Also a claim that holds.
3. **"Retrieved documents can escalate tool permissions."** They cannot.
   Permissions are static code, compared against a literal in a test.
4. **"The dashboard is not installable, and its icon set is incomplete."**
   There is no PWA manifest, no `site.webmanifest` and no Android theme icon,
   and their absence is a position rather than an oversight. OpsPilot is an
   internal tool for an operator on a Windows machine: there is no public
   domain to serve a manifest from, no offline use to justify one — the stack
   it drives is a local Docker Compose deployment with a real database behind
   it, not a page to cache — and no mobile target. A manifest would advertise
   an "installable" property the product does not have, and an Android theme
   icon a second one. The one mobile case that actually occurs is covered:
   iOS Safari's "Add to Home Screen" on a dashboard URL, which is what
   `web/src/app/apple-icon.png` is for. The project's actual claim is the
   narrower one it keeps — the dashboard is served at `127.0.0.1:3000` to an
   operator, and the icons it does ship (the Windows shell's, the browser
   tab's, the iOS home screen's) are there so that all three show the same
   badge.

The first three are the project's actual thesis. Everything in sections 1–7 is
what was traded away to get them right in Phase 1 — and knowing *which* trade
was made is the point of writing them down. The fourth is a different kind of
entry: not a claim the project cannot back, but a capability it has decided not
to offer, and a capability deliberately not offered is a limitation's other
side.
