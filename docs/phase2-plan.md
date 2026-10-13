# Phase 2 — Enhancement & Desktop Delivery

**Baseline:** `2e0c064` (Phase 1 closed: 84 of 90 acceptance criteria ticked, DoD
18 of 19, CI 12 of 12 green).

This document turns the Phase 1 review into work with verifiable acceptance
conditions. Every risk below was checked against the repository at the baseline
commit rather than taken from the review; where the review and the repository
disagree, the repository wins and the disagreement is recorded.

Each work package carries the evidence it must produce. A package that cannot
produce that evidence is not finished, however complete the code looks — that
is the rule Phase 1 spent most of its audit re-learning.

---

## P2-A · Deployment security and baseline hardening

**Priority: P0. This gates everything else.** Two of the three review findings
below are verified; one is already partly true.

### A1 — Network binding is unrestricted (verified)

`docker-compose.yml` publishes `5432:5432`, `8000:8000` and `3000:3000`. Docker's
default binds `0.0.0.0`, so on a machine with more than loopback networking every
one of those is reachable from the LAN.

Fix: bind the published ports to loopback explicitly (`127.0.0.1:3000:3000` and
the others), so a second machine on the network cannot reach them. This is the
single change that makes the stack local-only without any application change.

**Acceptance:** `docker compose up` then `ss -ltnp` shows the three ports bound to
`127.0.0.1`, and a request to the host's LAN address is refused. A negative test
asserts the compose file's port mappings all name a loopback interface, so a
future edit cannot silently un-bind one.

### A2 — The dashboard proxies privileged writes with no source check (verified)

`web/src/app/api/approvals/[approvalId]/[decision]/route.ts` reads
`OPSPILOT_OPERATOR_TOKEN` from the server environment and approves a refund on
the browser's behalf. Its own docstring explains why the token must stay
server-side — a token in a browser bundle is a refund-capable credential — and
that reasoning is correct. What is missing is any check on **who** is calling:
the handler validates `decision`, never the request's origin.

Combined with A1 this is the P0: with the ports unbound, anyone who can reach
:3000 can approve a refund without knowing the token.

Fix: bind the write handlers to loopback via A1, and add an explicit
origin/host check on the privileged routes so a request that did not come from
the dashboard's own page is refused. A1 is the defence that does not depend on
getting the origin check right; the origin check is the defence that survives a
future A1 regression.

**Acceptance:** a negative test drives `POST /api/approvals/{id}/approve` with a
foreign `Origin`/`Host` and asserts it is refused without the API being called.
The same request from the dashboard's own origin still succeeds. Both are in CI,
because a security property no test exercises is a claim, not a control.

### A3 — Fixed database credentials (verified)

`docker-compose.yml` sets `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` to
`opspilot`/`opspilot`/`opspilot`. With A1 this is no longer network-exposed, so
it is no longer the P0 it looks like alone. It is still a default that a
deployment inherits silently.

Fix: make the password come from the environment with a documented development
fallback, so a real deployment cannot run on the example credential by accident.

**Acceptance:** the compose file refuses to render with an empty password outside
the documented local-dev case, or the README's run instructions state plainly that
the credential is local-only and what to change before exposing anything.

### A4 — Stale descriptions that a reviewer will read (verified)

`web/README.md:5` says the browser "talks to the API on `:8000` **directly from
the browser process**" — untrue since the server-side proxy landed, and the
`api-contract.md` documents the opposite. `web/README.md:106` says the six
screens are "routing stubs and the nav shell" — they are implemented and the
browser walkthrough drives three of them. Compose comments carry the same drift.

Fix: correct `web/README.md` and sweep the compose comments. These are the files
a hiring reviewer opens first.

**Acceptance:** no document in the repository describes a capability that
`limitations.md` §8 records as absent, and no document describes the browser as
calling the API directly.

---

## P2-B · Agent and RAG quality

**Baseline recorded:** classification 0.800 (16/20), retrieval recall@5 0.700
(14/20), precision@5 0.170, tool selection **0.200 (3/15)**, tool argument
validity 0.471 (80/170), `unsafe execution count` 0, from
`evals/results/2026-10-11T15-39-42.json` on MiniMax-M3.1-Flash-Preview.

### B1 — Diagnose the 12 tool-selection failures before changing anything

`tool selection 0.200` is the weakest metric and the one most likely to be the
model rather than the design — the 2026-10-08 run scored 0.067 on the same
criterion, so the endpoint's tool-calling reliability is the obvious suspect and
must be excluded before the harness is blamed.

Read the 12 failures out of the committed results file. Classify each: wrong
tool, no tool proposed, an unregistered name, or a tool that was right with wrong
arguments. Only the last is ours.

**Acceptance:** a written classification of all 12, in `docs/progress.md`, with
the count belonging to each category. No code change in this step.

### B2 — Then change what the classification justifies

Whatever B1 shows: if the model picks nothing, the constraint is the problem and
the fix is a workflow constraint or a stricter tool menu; if it picks wrong
tools, the menu is ambiguous; if it picks the right tool with bad arguments, that
is `argument validity` and belongs to C. Do not add prompt text "to see if it
helps" — Phase 1's audit history is largely the record of changes made without
this step.

**Acceptance:** the same 15 cases, same model, same configuration, **run at
least twice** so a single run's variance is not read as improvement. A change
that moves one run and not the other is not a change.

**Status: deferred, because the acceptance cannot be met as written.** Two runs
of the unmodified code against `MiniMax-M3.1-Flash-Preview` scored **3/15 and
2/15**, and proposed an identical tool set for only 8 of the 15 cases — every
case that failed in both failed *differently* the second time. The gap between
the two runs is as large as any plausible improvement, so "ten of fifteen"
cannot distinguish a change from noise against this endpoint. The measurement
and the case-by-case comparison are in `docs/progress.md` under "Two runs of
the same code". B2 resumes when the model under test answers consistently enough
for two runs to agree; the holdout from B4 is the instrument for judging it then.

### B3 — Correct what `tool argument validity` actually measures

Measured at the baseline commit, all 170 recorded tool calls split as **77
executed, 90 rejected, 3 failed**, and **all 90 rejections carry
`arguments: {}` — empty — with `rejection_reason: gate_1_schema_validation`**.

So `0.471 (80/170)` carries almost no signal about *argument quality*. `80` is
`170 − 90`, and the 90 are calls the model made with no arguments at all. The
gate caught a defect; it did not stop an attack. Reading those 90 as a security
population would misattribute a model failure to a control, and "improving" the
combined number could mean making the model fill arguments in while leaving
whatever actually exercises the gate untested.

**What this changes:** the metric's split is not *ordinary-wrong-arguments versus
gate-rejected*, as an earlier draft of this plan assumed. There is no
gate-rejected-malicious population in this run to separate. The first job is to
say what the metric should measure and make it measure that; a second population
has to be *created* — cases that deliberately propose a forbidden or
malformed call — before the security half of it can be asserted at zero for any
meaning.

**Acceptance:** the metric's name matches what it counts, the 90 empty-argument
calls are attributed to the model rather than to the gate, and any security
population is drawn from cases that exist for that purpose rather than inferred
from ordinary failures.

### B4 — A holdout set

Fifteen tool-selection cases is few enough that a change can fit them. Add a
holdout that is committed and not consulted while tuning.

**Acceptance:** ≥30 new cases committed; tuning decisions cite the 15; the
holdout is run once at the end and its result reported whether or not it
improves.

---

## P2-C · Reliability and the operator's view

### C1 — Two workers, one database

`limitations.md` §8 item 2 already records this as a gap: `claim_next` is
covered on SQLite, and nothing runs two workers against one PostgreSQL. The
`FOR UPDATE SKIP LOCKED` claim has never been exercised where it matters.

**Acceptance:** a test that starts two workers against one PostgreSQL and asserts
no run is claimed twice. Skipped cleanly without PostgreSQL, per ADR-0004.

### C2 — Refund idempotency under concurrency

The refund key is enforced by the handler's linear scan
(`mcp_servers/billing/server.py`), which `e74dc2e` corrected the documentation to
say is scoped to one process serving calls one at a time. Two callers can
therefore both pass the scan.

**Fix:** do not add a lock to the JSON store — `_store.py:41-45` records that its
no-locking decision is deliberate. The honest options are an atomic
check-and-write at the store boundary, or stating the boundary in the contract
and testing the documented single-writer case. Decide which, and say why.

**Acceptance:** either the guarantee is made and tested, or the contract states
precisely what it does and does not cover and the test pins that boundary. The
failure mode is the second one being left implicit.

### C3 — What an operator sees when something goes wrong

Browser e2e covers the success path only. The failure paths — worker not
claiming, approval conflict, a tool refused by a gate — are what an operator has
to debug, and they are untested in the browser.

**Acceptance:** e2e specs for the failure states, asserting the UI says something
true. A failure the UI renders as "something went wrong" is a defect even when
the code is right.

### C4 — The worker-startup gap that bit the e2e job

`web-e2e` failed once because the worker never claimed a run and nothing captured
why. `waitForWorkerPolling` now gates on the worker's boot line and
`RestartCount`, and compose captures per-service logs. **The cause was never
diagnosed** — it did not recur, but that is not a diagnosis.

**Acceptance:** either the cause is found and fixed, or the gate's evidence is
shown to be sufficient to diagnose it next time, and `docs/limitations.md` records
it as an open question rather than a closed one.

---

## P2-D · Desktop delivery

**Positioning, decided:** a Desktop **Demo**. It packages the dashboard and owns
the local UI process; the backend still requires Docker Desktop. Bundling
PostgreSQL, the Python environment and the worker into one installer is a
different project, and Phase 2 does not attempt it.

- **Electron** owns install, window, and the local service lifecycle.
- **Next.js standalone** stays the UI, unchanged — the app is Server
  Components and Route Handlers, and a static export would forfeit the design
  that keeps the token server-side.
- **Docker Compose** supplies api + worker + PostgreSQL, unchanged.

Security configuration is not optional: context isolation on, node integration
off, sandbox on, navigation and IPC restricted. This app has a refund-capable
credential in its server process, so the renderer is treated as untrusted.

### D1 — Shell and lifecycle

**Acceptance:** the app starts the backend if it is not running, surfaces a real
error when Docker is absent rather than a silent failure, and stops what it
started on quit.

### D2 — Installer

**Acceptance:** on a clean Windows machine with Docker Desktop, install the
built package and walk the golden path end to end. That is the whole test, and it
must be run by someone who did not build it.

### D3 — The desktop build does not widen the P0

Every control in P2-A must hold in the packaged app: loopback binding, origin
checks, no token in the renderer.

**Acceptance:** the packaged app is checked against A1–A3 the same way the dev
stack is.

---

## P2-E · Portfolio delivery

Not code. It is the part a reviewer judges first, and the part this repository
has already shown it can get wrong: the `6d79fbe` commit message claimed a CI job
that was never committed, and `8c558b1` miscounted documents. Every number here
must be reproducible from a file in the repository.

- README positioned as **OpsPilot — Safety-Gated AI Operations Agent**, with
  **simulated** billing stated plainly. No implication of Stripe or of a real
  finance system.
- A video, screenshots, and a case study that walk one duplicate charge from
  intake to the audited refund.
- A GitHub Release with the installer.
- Every acceptance number in this document traced to the run that produced it.

**Acceptance:** someone who has not seen the project can clone it, follow the
README, and reproduce the golden path without asking a question.

---

## Ordering

A → B → C → D → E, with the caveat that A must finish before anything is
exposed to a network, and D is deliberately last of the engineering work so the
installer ships the hardened system rather than the vulnerable one.

## What Phase 2 does not do

No multi-agent orchestration, no third-party platform integrations, no general
workflow builder, no multi-tenancy, no enterprise identity. None of these has a
user asking for them, and each is exactly the kind of scope growth that turns a
portfolio piece into an unfinishable one.

Phase 3 is stabilisation and freeze: serious defects, installer compatibility,
documentation consistency, release tags — then feature development stops until
real feedback justifies another round.

---

## Baseline measurements this plan is written against

Recorded at `2e0c064`, reproduced by `docs/progress.md` and the committed results
files. Any Phase 2 claim that moves one of these must say so with the run that
moved it.

| Metric | Value | Source |
|---|---|---|
| CI | 12 of 12 green | run `38263406872` |
| Python tests | 865 passed, 11 skipped | local |
| Browser walkthrough | 4 of 4 passed | local, against Compose + PostgreSQL |
| classification | 0.800 (16/20) | `2026-10-11T15-39-42.json` |
| retrieval recall@5 | 0.700 (14/20) | same |
| retrieval precision@5 | 0.170 | same |
| tool selection | **0.200 (3/15)** | same |
| tool argument validity | 0.471 (80/170) — 80 of the 170 calls carry arguments; the 90 that do not are empty (`{}`), see B3 | same |
| unsafe execution count | **0** | same — the security property |
| task completion | 1.000 (9/9) | same |
| Acceptance criteria | 84 ticked, 6 not | `docs/milestones.md` |
| Definition of Done | 18 of 19 (D19a is a recorded boundary) | `docs/milestones.md` |