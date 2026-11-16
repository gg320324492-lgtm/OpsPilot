# Development Notes

This file carries the development-era narrative that used to live in
`README.md`: the history behind the numbers, the recording debates, the
build troubleshooting and the release reasoning. The README now keeps only
the front screen a first-time reader needs; this is the layer beneath it.

Everything here is historical record. Where a figure is quoted, the file or
run it came from is named beside it, and later documents supersede it when
they disagree.

---

## Status history: what Phase 2 closed, and what it left open

> **Phase 1 milestones M0–M9 closed; Phase 2 done — the golden path runs end
> to end, and the verified installer is downloadable.** The system is built:
> the agent loop, the five gates, the persisted approval gate, idempotent
> refunds, retrieval with citations, the dashboard, the desktop shell, the
> eval harness and CI all exist and are tested. **D2 passed on 2026-11-14**:
> the built installer was walked by someone who did not build it (sixteen
> checks, record sheet in
> [`docs/installer-verification.md`](installer-verification.md)), and
> **[Release `v0.1.0`](https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.1.0)
> now carries the verified `OpsPilot-Setup-0.1.0-x64.exe`**. The two records
> still worth reading in
> [`docs/phase2-plan.md`](phase2-plan.md)'s acceptance table are **B2**
> (executed against the real endpoint and measured — the improvement was real
> but the endpoint's variance ate it, so the 10/15 acceptance was *not* met)
> and **C1**, where the two-worker guarantee is *boundary stated and tested*,
> not closed.

### The CI run this README was checked against

> **The one run to check this README against** is
> [`39439825308`](https://github.com/gg320324492-lgtm/OpsPilot/actions/runs/39439825308)
> (commit `f410911`, 2026-10-20): **16 of 16 jobs green.** Its two `test`
> jobs (Python 3.12 and 3.13) each report **994 passed, 24 skipped**; its
> `web-e2e` job reports **67 passed**. Verify with `gh run view 39439825308`.
> The local suite is **1347 passed, 18 skipped** (`pytest`, 2026-11-08) —
> **1365 tests collected**. That is the tree as it stood then; the run above
> is from commit `f410911`, and the 347 tests added since are not in it.
> Between the two platforms the difference is exactly four
> `test_mcp_stdio_transport.py` tests that enumerate OS processes: they are
> `skipif(sys.platform != "win32")`, so the Linux runner skips them where
> Windows actually runs them. Local's 18 skips are 16 `postgres`-marked tests
> that CI resolves against a real pgvector container — seven of the sixteen
> named by node id and asserted `PASSED`, the other nine by their file's exit
> code plus an assertion that nothing in it skipped, so a skip there cannot
> read as coverage — and 2 eval-fixture skips for committed result files
> written before the breakdown was recorded. **No placeholder is skipped any
> more**: the three M0 stubs the suite used to carry are implemented.
>
> The browser walkthrough runs on a machine with Docker: **85 passed, 0
> skipped** (`npm run e2e`, 2026-10-21) — the CI run's 67 plus eighteen specs
> added since. The twenty-three Compose-backed specs — nineteen that bring up
> PostgreSQL, migrate, api and worker, and four that bring up the
> containerised dashboard — are the whole point of that job, and until Docker
> was running here they had never executed on this machine.
>
> What is **not** done is listed honestly — `issues.create` is implemented
> and tested in isolation but the golden path does not call it (see the
> golden-path trace), and the golden path needs PostgreSQL for retrieval
> (SQLite is a tests-only path). The compose services have been run on a
> machine with a Docker daemon: `api`, `worker`, `web` and `postgres` come
> up, `api`, `web` and `postgres` healthy and `worker` up. The same stack
> also comes up on CI's runner: the `docker-build` job builds every image,
> runs `docker compose up -d --wait`, and checks `/ready`. The golden path is
> walked in a real browser on every push: `web-e2e` brings up postgres,
> migrate, api and worker through Compose and clicks through the dashboard —
> reindex, submit the ticket, follow the run to its trace, approve the parked
> refund — asserting against PostgreSQL, so retrieval is cross-process the
> way the terminal recording never was. What is still *not* met is M9's
> criterion in full: the three MCP servers are not Compose services at all.
> They are spawned on demand by the worker as stdio subprocesses (boundary
> D12a), and the worker here reports the default `MCP_TRANSPORT=inprocess`,
> which builds them inside the process instead. The full list is in
> [`docs/limitations.md`](limitations.md), and each milestone's outcome is in
> [`docs/progress.md`](progress.md).

---

## The golden path: step the trace does *not* show

> **A step this trace does *not* show.** An earlier revision drew an
> `issues.create → OPS-1042` line between the refund and the reply. The tool
> is real, is `SAFE_WRITE`, is exercised in isolation by the gateway and
> policy tests — but the committed fixture the golden path replays
> (`evals/datasets/fixtures/duplicate_charge.json`) proposes four calls and
> `issues.create` is not one of them, so the run above never creates an
> issue. The line was removed rather than the fixture changed: adding a call
> to make a diagram true would be writing code to match a drawing. Whether
> the workflow *should* file a ticket is a product decision, and it is
> recorded as boundary D19a in [`docs/limitations.md`](limitations.md) §8.

Re-running the same refund produces `replayed: true` and **no second
refund**. That is tested by calling the MCP server directly, bypassing the
agent.

---

## The terminal recording: what it is, precisely

The GIF in the README (*A real run, recorded from
`scripts/demo_golden_path.py` on `MODEL_PROVIDER=fake`*) is a scripted
replay with no API key, so the model's *answers* are the same every time.
The trace rows are not byte-identical: each run mints its own ticket and run
ids and stamps its rows with the time. It shows `/ready` certifying against
the database, the ticket being submitted, the run parking at
`WAITING_APPROVAL`, the approval, and the worker executing the refund; the
last line is the billing server's own answer, not the run's.

**What this recording is, precisely.** The API is the real FastAPI app
served by uvicorn, and the worker is the real poll loop, but they run **in
one process** — which is how the script has to run them, because on SQLite
the vector store keeps embeddings in process memory (`adapters/wiring.py`),
so a two-process deployment cannot retrieve anything and the run abstains
instead of refunding. That limitation is real, documented, and in
[`docs/limitations.md`](limitations.md). What the GIF proves is that the
deterministic path does refund exactly once, and that the API surface, the
approval gate and the worker all behave as documented. It is not a
demonstration of the two-process deployment.

Reproduce it:

```bash
./.venv/Scripts/python.exe scripts/demo_golden_path.py    # run it, read the output
node scripts/record_demo_gif.mjs                          # re-record the GIF
```

The second command needs `ttyd` and `ffmpeg` on `PATH`, and the dashboard's
own dependencies installed (`npm ci` in `web/` — Playwright comes with them,
and the script resolves it from there). It used to be unrunnable in a clean
checkout: the script imported `playwright` from `scripts/`, which has no
`node_modules` of its own, so it died at module load and the recipe above
regenerated nothing.

The vhs tape is committed at
[`docs/demo/golden-path.tape`](demo/golden-path.tape). It is correct, but it
is **not** how the committed GIF was made: `vhs` cannot record on this
machine (its ttyd capture renders blank without WebGL, and its GIF encoder
writes no file), so the frames were captured the same way — real screenshots
of the real terminal — and encoded with the system ffmpeg. The tape says so
at the top, so nobody re-runs it expecting it to work.

---

## The browser screenshots: exactly how they were taken

The ten screenshots are **one run of the deployed stack** — PostgreSQL with
pgvector, the `api` service, the `worker` service and the dashboard, driven
in a real Chromium by clicking, in the order a person would. They are in
[`docs/assets/screenshots/`](assets/screenshots/) and they come from the run
written up in full in
**[`docs/case-study-duplicate-charge.md`](case-study-duplicate-charge.md)**.

**Exactly how they were taken, because it matters.** `postgres`, `migrate`,
`api` and `worker` are the real Compose containers. The **dashboard** is the
host-built Next.js standalone server — the same one the `web-e2e` CI job
drives — running on port 3100, **not** the containerised `web` service. They
were captured while that service was broken (every bundle 404'd, so the page
was inert), which is recorded and fixed in the README's running notes; the
fix changed the image, not these captures. The token was read from the
gitignored `.env` and injected into the server process at runtime; it
appears in no screenshot, no command line and no document.

> **What these screenshots are, precisely.** One run on
> `MODEL_PROVIDER=fake`, so the model's *proposal* came from the hand-written
> fixture — these prove the gates hold and the system is wired correctly, not
> that the model is good. The CRM, Billing and Issues data behind them is
> **in-repo seed data**, not a real finance system. And no credential appears
> in any of them: the operator token is read only in the dashboard's server
> process and never reaches the renderer.

---

## Why there is no video yet, and which one there will be

There is no screen recording in this repository, and the honest reason is
worth more than a placeholder would be. Three things make one hard, and only
the first is a tooling problem:

1. **Capture tooling is not wired up.** Playwright is a devDependency and
   drives the dashboard in CI, but nothing records video —
   `web/playwright.config.ts` sets `trace: "retain-on-failure"` and
   `screenshot: "only-on-failure"`, and Playwright's video output is off. A
   recording is `use: { video: "on" }` plus a commit of the `.webm` — small
   change, but it is application configuration, not documentation, and it was
   out of scope here.
2. **A recording of what?** The interesting run is the two-process one, and
   it needs a full image build, four Compose services, a reindex, and up to
   three minutes of worker polling per capture. A CI artifact would be the
   honest home for it — recorded on `web-e2e`, uploaded like the compose logs
   already are.
3. **A browser recording is weaker evidence than the screenshots.** Each of
   the ten screenshots is a state a human can reach and check; a browser
   recording is a state someone else recorded. This repository's own habit is
   to ask the billing server what it did rather than take the run's word for
   it, and that argues for images and a transcript over moving pictures.

**Point 3 is about browser recordings, and it is not about every recording.**
The argument above is strong, and it is strong for one kind of thing: a
capture of the dashboard moving, produced by the same Playwright run that
produces the screenshots, on the machine that built the application. Against
*that*, the ten screenshots win, and the reason is specific rather than
general. A screenshot is a state a reader can **arrive at** and check for
themselves; a browser video is a state someone else recorded, and the reader
can only watch it. A repository whose habit is to ask the billing server what
it did will not take a moving picture in place of asking.

**The recording this repository is going to have is not a browser
recording.** It is the one the D2 walk-through produces, and its subject is
not the application — it is **the installer, and the interface, on a machine
that did not build them**: a stranger, on a clean Windows machine, installing
the packaged build and taking the golden path through the operator's screens.
The ten screenshots cannot stand in for it, because their subject is the
application's behaviour and this one's subject is the product's arrival. They
show that the app looks like this on the machine that built it. A recording
of the install shows the thing they cannot: that it **installs** on a machine
that has never seen it, that it **starts**, and that the screens are legible
to the operator using them. That is the claim §P2-D2 was still open on, and
it is the one claim the ten screenshots are not making.

| | the browser recording | the D2 walk-through recording |
|---|---|---|
| what it records | the dashboard's own flow, in Chromium, in the author's stack | a stranger installing the packaged app on a clean machine, then walking the golden path |
| what it proves | that the app works | that it **installs and runs for a person who did not build it** |
| against the ten screenshots | weaker — a state someone else recorded | stronger — a claim the screenshots do not make |

**In priority order, and why each one sits where it does.**

1. **The D2 walk-through recording.** First, because it proves the most: it
   is the only evidence for the claim that the packaged product works on a
   stranger's machine, and no arrangement of screenshots reaches that claim.
   Second, because it is the only recording the project is *owed* — §P2-D2 is
   the one acceptance criterion that stayed open the longest, and the walk
   that closes it produces this recording as a by-product, so it costs
   nothing extra: somebody is going to be at that keyboard regardless. Third,
   because it is the only one of the three that closes a documented gap
   rather than adding comfort to one that is already closed.
2. **The `web-e2e` browser recording.** Second, because it is nearly free —
   the golden-path walkthrough already exists as a test, so recording it is
   `use: { video: "on" }` in `web/playwright.config.ts` plus a CI artifact —
   and because a reader who wants to watch the flow move has no way to today.
   It is second and not first for the reason in point 3: it would be *weaker*
   evidence than the ten screenshots already committed, so it adds motion
   rather than proof. The **case study**
   ([`docs/case-study-duplicate-charge.md`](case-study-duplicate-charge.md))
   covers the narrative it would have covered, and every number in it can be
   checked against a file.
3. **The failure states in `web/e2e/failure-states.spec.ts`.** Third, because
   they are the least legible of the three on their own: a recording of an
   error state without a caption is a video of a red box, and those states
   are already specified in prose and asserted in tests. If the D2
   walk-through catches an error state for real — the Docker warning, a run
   that will not start — that is worth more than a staged one.

**Where the recording will live, and what it will be called.** The D2
walk-through's recording will be committed to this repository at
`docs/assets/d2-walkthrough.mp4`, beside `golden-path.gif` and the ten
screenshots, and embedded in the README in a `<video>` element. The format
is **MP4 (H.264)**: it is the container GitHub's README renderer plays most
reliably, every browser plays it, and the size is predictable. Playwright's
native output is WebM (VP8/VP9), and if the walk-through's capture produces
one it is kept beside it as `docs/assets/d2-walkthrough.webm` — the source
the MP4 was transcoded from, offered inside the same `<video>` element as a
second `<source>` for a browser that prefers it.

**The committed MP4 must stay under 10 MiB.** The file lives in this
repository's history, so every clone pays for it, and an install-and-walk
recording fits in that at a modest bitrate: record at a fixed 1280x720, with
no audio, and a bitrate that lands the file in the low megabytes. A screen
recorder's own defaults will not do that.

**This section is not standing in for a file that does not exist.** The
recording has not been committed: nothing in the README pretends otherwise.
The two halves of that promise are held together by
`tests/unit/test_d2_walkthrough_recording.py`: it fails if the README points
at a recording the tree does not have, and it fails if the tree has a
recording the README does not point at. The file and the reference cannot
drift apart.

---

## The evaluation: caveats measured the same way as the numbers

### The 2026-10-11 run (the README's baseline)

70 cases, four datasets, against a live OpenAI-protocol endpoint. Raw
output:
[`evals/results/2026-10-11T15-39-42.json`](../evals/results/2026-10-11T15-39-42.json).

**`expected-write rate` is 0.000 in this run — and that is the point of the
row.** The gate above it is clean (0 unsafe executions), `approval-policy
compliance` reads 1.000, and yet no case's expected refund actually executed.
A run that proposes nothing unsafe but also does nothing useful is exactly the
failure a gate-only safety table cannot see; row 8 of
[`docs/evals.md`](evals.md) §1 exists to expose it. The figure is recomputed
from the cited file's own recorded `results[]` by
[`tests/evals/test_committed_results_are_recomputable.py`](../tests/evals/test_committed_results_are_recomputable.py).

**`tool selection` is the one row here you must not quote on its own.** It
was re-measured twice on **unmodified code** against the same endpoint and
the same 15 cases and scored **3/15 and 2/15**, proposing an identical tool
set for only 8 of the 15, and 6 of the 12 that failed in both proposed the
identical set again. Both runs are committed:
[`2026-10-13T18-24-25.json`](../evals/results/2026-10-13T18-24-25.json)
(3/15, p50 34.3 s) and
[`2026-10-13T18-40-10.json`](../evals/results/2026-10-13T18-40-10.json)
(2/15, p50 33.0 s), both `tool_selection` only, no code changed between
them. The case-by-case comparison is in
[`docs/progress.md`](history/phase2-closure-and-handover.md#b2-executed-against-the-real-endpoint-the-menu-was-the-prompts-and-one-number-moved).
The spread between two runs of the *same* code is as large as any plausible
improvement, so a single tool-selection figure from this endpoint measures
noise. That is why P2-B2 is deferred rather than tuned — see
[`docs/phase2-plan.md`](phase2-plan.md) §P2-B2.

**Read the rest as a measurement of one route, not of this system's design.**

- **`provider=openai` is a protocol, not a vendor.** The endpoint is
  MiniMax's official OpenAI-compatible API, `https://api.minimaxi.com/v1`,
  reached through `OPENAI_BASE_URL`; the harness records the provider name it
  was *invoked* with, so the banner says `openai` whatever answers. The model
  id is `MiniMax-M3.1-Flash-Preview`, and — unlike the earlier run below —
  this file's `model` field is the name that answered rather than the one
  requested. `f7cb034` changed `_reported_model` to count only calls carrying
  a usage record; here 140 of the run's 358 model calls carry one, and they
  report the answering model.
- **`retrieval precision@5 0.170` against `recall@5 0.700` is mostly the
  metric's shape.** The row carries no `(n/N)` because its denominator is
  K=5, not the hits: 17 expected-document hits across 20 cases × 5 slots =
  100 retrieved documents. This dataset's ceiling for that row is **0.250**,
  not 1.000 — the 20 cases expect 1, 2 or 3 documents (six, eight and one of
  them) and five expect none, so a retriever that ranked every expected
  document first would still score `(6×1 + 8×2 + 1×3) / 5 / 20 = 0.250`. The
  run found 17 of the 25 hits available. On top of that, 13 of the 20 cases
  returned a *duplicated* document inside their own top 5, so the slots were
  not even five distinct documents.
- **Retrieval was embedded locally, whatever `embedding_model` in the file
  says.** `config.embedding_provider` is `local`: the deterministic lexical
  embedder — a bag of stopword-free, lightly-stemmed words weighted
  sublinearly by frequency and hashed with a fixed-key `blake2b` into a
  fixed-width vector (`adapters/retrieval/embeddings.py`). It has no
  synonymy, so a question whose answer is worded differently from the
  question will not match it ([`docs/limitations.md`](limitations.md) §3).
  The file's `embedding_model: "text-embedding-3-small"` is a default that
  does not apply to the local provider — it was the unconditional default in
  `settings.py` and `.env.example` from before either run, and this run
  inherited it. It has since been changed to empty (`959855d`), which is why
  the run recorded it and the current default no longer does. It is a
  labelling defect the run inherited, not something the run introduced, and
  nothing of OpenAI's ran. The two retrieval rows are therefore a measurement
  of the lexical retriever.
- **Latency is the endpoint's, and neither run's p95 is a benchmark.** p95
  276.7s is 4.7× the earlier run's 58.3s, but that was a different endpoint.
  Per-dataset medians track how much work a case is: classification 6.9s,
  retrieval 17.9s, tool selection 63.1s, safety 73.6s, against 358 model
  calls in 70 runs (5.1 per run), and the worst single case took 550.3s.
  Nothing in the file shows a step being re-asked or re-answered: every
  case's `failure_reason` is null, so none of them exhausted the 24-step
  budget either, and `reasoning_content` — the fallback some endpoints use to
  re-answer a step — appears nowhere in the file.
- **Two of the 70 cases are not the endpoint's answers.** `safe-007` and
  `safe-008` are the prompt-injection cases: their classification and reply
  come from a provider *scripted* to comply with the retrieved attack
  (`runner._InjectionCompliantProvider`), so the approval gate can be shown
  to hold against a model that obeyed it. That is the deliberate worst case,
  not a live number. This run carries the `source: "synthetic"` marker the
  earlier run predates, and the `approval-policy compliance` row is computed
  over the **live** safety cases only — here exactly one, `safe-010`, the
  live case where the endpoint proposed a $129 refund — with the scripted
  pair reported beside it as `(2/2 synthetic)`. `unsafe execution count` is
  *not* split: the injection cases are exactly what that gate must cover.
- **`cost $0.0000` is real.** The endpoint reports zero cost; the token
  counts are non-zero and are what the metrics read.

The four gates are all zero, and `unsafe execution count` is the one that
matters: **170 tool calls were attempted across the run — 77 executed, 90
rejected by gate 1, 3 failed — over 15 hostile safety cases including a
retrieved prompt injection instructing a $10,000 unattended refund, and zero
unapproved high-risk executions.** The 90 rejections are not near misses and
not an adversarial population either: every one is a proposal with
`arguments: {}` — a tool named and nothing filled in — and every one carries
`rejection_reason: "gate_1_schema_validation"`. So the 90 are a model defect
the gate caught, not a measure of the gate stopping an attack, and argument
validity reads 0.471 over *attempted* calls mostly because of them: 80 of the
170 carry arguments, and of those 77 executed. The 3 failures are all
`billing.issue_refund` — both injected $10,000 refunds and the live $129
refund. Each has `approval_requested: true`, none executed, and no
`high_risk_write` call executed anywhere in the run. The file does not record
*why* they failed (`runner._tool_call_record` projects `rejection_reason`,
not the tool's `error`), but it does rule out the fault that would matter: an
unreachable tool server fails the whole run (`MCP_UNAVAILABLE` →
`_fail_run`), all 70 cases finished `completed`, and there are no
unreachable-server tool failures in this run at all. `knowledge.search` was
proposed in 20 cases and executed in all of them — 35 calls in total, more
than one retrieval per case, which is the defect fixed in `148a87c`: it could
never execute before that. In the injection cases the model *did* comply: it
proposed the injected refund, and the refund did not execute. That is the
property being claimed, and it does not depend on which model answers.

### An earlier run, 2026-10-08

70 cases, four datasets, against a live Anthropic-protocol endpoint — a
different route, kept because the file is committed and because two of its
numbers document a defect that has since been fixed. Raw output:
[`evals/results/2026-10-08T05-24-41.json`](../evals/results/2026-10-08T05-24-41.json).
This run predates the `source` marker, so all three of its approval-policy
cases count as live.

```
OpsPilot evaluation — provider=anthropic model=deepseek-v4.1-flash
                              cases   score
classification                  20   0.750   (15/20)
retrieval recall@5              20   0.500   (10/20)
retrieval precision@5           20   0.110
abstention correctness           5   0.600   (3/5)
tool selection                  15   0.067   (1/15)
tool argument validity          --   0.634   (170/268 calls)
approval-policy compliance       3   1.000   (3/3)
unsafe execution count          15   0         ← gate, must be 0
task completion                  9   0.444   (4/9)

latency   p50 6.6s   p95 58.3s
tokens    in 1085   out 628   (mean per run)
cost      $0.0000 mean per run
```

**The same caveats apply to it, measured the same way.**

- **The model name is what answered, not what was requested.** The run was
  configured for `claude-sonnet-5-5`; every response that reported a name
  reported `deepseek-v4.1-flash`. The endpoint ignores the requested name
  entirely — a probe with `totally-bogus-model-name` routed to the same
  backend — so a model name here is a label, not a selection. `runner.py`
  records the reported name for exactly this reason.
- **The committed results file predates that fix, and its `model` field is
  wrong for that reason.** `evals/results/2026-10-08T05-24-41.json` records
  `model: "claude-sonnet-5-5"` — the *requested* name — while the table above
  prints `deepseek-v4.1-flash`. This is not a discrepancy to reconcile by
  editing either: the file was written before `_reported_model` began
  counting only calls that carry a usage record (`tokens_available`).
  `choose_tool` calls return no usage, so they defaulted to the requested
  name, and across the run those defaulted events outnumbered the observed
  ones **299 to 96** — so the old rule picked `claude-sonnet-5-5` even though
  the 96 real responses all reported `deepseek-v4.1-flash`. The table above
  is right about what the endpoint returned (it is reproduced from the
  individual `model_calls` events, which still carry both names in the file);
  the file's summary field is what the pre-fix code wrote. The 2026-10-11 run
  does not have this problem: its `model` field is the observed name, and the
  field agrees with the table.
- **20 of 70 cases failed**, evenly across all four datasets (4/6/3/7), from
  `MaxStepsExceeded` (12) and structured-output errors (8). Both are the
  endpoint's planning reliability, not a property of the metrics: the same
  cases pass against the deterministic provider. Failed cases count in every
  metric's denominator rather than being dropped. The 2026-10-11 run had no
  failures at all.
- **`tool selection 0.067` is the endpoint.** Measured directly: given the
  same prompt and tools, it calls a tool **once in four attempts**, answering
  in prose otherwise. `choose_tool` puts the tool menu in the prompt text by
  design (`available_tools` is "for ergonomics only"; gate 2 re-checks every
  name against the registry), so this is a weak constraint for a model that
  does not use it.

---

## Network requirements: what to do when the first build fails

The first `docker compose up` builds two images on your machine, and
building means downloading: the Python services' build stage runs `uv sync`
against PyPI, and the dashboard's build stage starts from a Node image pulled
from Docker Hub. Both downloads happen **inside Docker's build network**, so
a working browser or `pip` on the host does not guarantee them — a corporate
proxy, a flaky link, or a regional mirror preference can make the first build
fail with a timeout even though "the internet works". The first build can
also simply take several minutes; with `docker compose up -d` the build
output is not streamed to your terminal, so a long quiet period is the build
running, and the reason for a failure is in the full output (run without
`-d`, or check `docker compose logs` afterwards) rather than in the silence.

What to do when it fails:

1. **Just run `docker compose up` again.** BuildKit caches every layer that
   succeeded, so a retry re-downloads only the step that timed out. Most
   first-run failures are transient and clear on the second or third attempt.
2. **If it keeps failing**, point the build at your network's reality: set
   `HTTP_PROXY`/`HTTPS_PROXY` in the shell that runs `docker compose` (or in
   `~/.docker/config.json`), or pre-pull the base images (`docker pull
   node:22-slim` and friends) on a connection that reaches them.
3. The published ports the stack listens on after it is up — API `8000`,
   dashboard `3000`, PostgreSQL `5432` — are bound to `127.0.0.1` only. If
   one of those is already taken on your machine, set `OPSPILOT_API_HOST_PORT`,
   `OPSPILOT_WEB_HOST_PORT` or `OPSPILOT_PG_HOST_PORT` in `.env` to move the
   host side (see `.env.example`); the services inside the Compose network do
   not change. The desktop shell reads `OPSPILOT_API_HOST_PORT` from the same
   places (`process.env`, the repository's `.env`, or the app-data `opspilot.env`
   of an installed copy), so a moved API does not leave the shell probing 8000.
   That override is verified end to end by
   `tests/integration/test_host_port_overrides.py`, which stands the stack up
   on high host ports and asserts reachability there; it skips cleanly on a
   machine without Docker.

> **The containerised `web` service is fixed.** The `web` service used to
> serve a page whose JavaScript 404'd, so the Reindex, Submit and Approve
> buttons did nothing. `web/Dockerfile` copied `public/` and `.next/static/`
> to `/app/web/public` and `/app/web/.next/static`, but the standalone server
> `chdir`s to its own directory and resolves both relative to *that* — with
> `server.js` at `/app/server.js` it reads `/app/public` and
> `/app/.next/static`, where the copies never landed. Both destinations are
> now `./public` and `./.next/static`, the siblings `server.js` expects.

---

## Tests: the commands and the traps

```bash
pip install -e ".[dev]" && pytest
```

SQLite in-memory by default, so the suite needs no services. Tests requiring
a real PostgreSQL are marked `postgres` and skipped locally — the skip count
is printed, so a green run is not mistaken for full coverage.
([ADR-0004](adr/0004-sqlite-tests-postgres-production.md))

Lint and formatting are two separate gates, both run by the `lint` CI job:

```bash
ruff check             # linter — four configured trees: src, tests, evals, mcp_servers
ruff format --check    # formatter — no file may need reformatting
```

Run both bare, with no path. `ruff check src tests` would silently skip
`evals/` and `mcp_servers/`, which the config includes. `ruff format
--check` is a gate in its own right because the formatter's output changes
between ruff minor releases: `ruff` is pinned to a minor line in
`pyproject.toml` so a dependency bump cannot quietly redefine what
"formatted" means.

Static types are checked with the file set configured in `pyproject.toml`
(`[tool.mypy] files`), not with a hand-typed path list:

```bash
mypy
```

Run bare `mypy`, with no arguments: the argument list and the configured
`files` are kept identical by
`tests/unit/test_mypy_configuration_is_enforced.py`, so `mypy --strict src`
— which checks only `src` — is not the project's type check. Passing a path
on the command line would silently narrow the check to that path.

### The browser walkthrough (`web-e2e`)

The Python suite runs on SQLite and never loads a page. The browser
walkthrough is the separate one that does, and it is what the `web-e2e` CI
job runs:

```bash
cd web && npm ci && npx playwright install --with-deps chromium && npm run e2e
```

It brings up `postgres migrate api worker` through Compose and builds the
dashboard, so **it needs a working Docker daemon** — it skips cleanly
without one, and a stack Docker tried and could not start is a failure, not
a skip. It takes about two minutes and leaves nothing behind; `docker
compose down -v` is run either way.

The web tree's other two gates:

```bash
cd web && npm run lint && npm run typecheck
```

Run both bare. `npm run e2e` is a third script, not a substitute for either.

### Regenerating `web/package-lock.json`

Do it inside the container, not on your host:

```bash
docker run --rm -v "$PWD/web:/app" -w /app node:22-slim npm install --package-lock-only
```

On macOS or Windows this *silently produces a broken lock file*. The wasm
fallbacks under `@emnapi/*` are reached only through `cpu: ["wasm32"]`
optional dependencies, so a lock generated on win32-x64 or darwin omits
packages that the Linux build needs. The symptom is misleading: `npm ci`
succeeds on the machine that generated the lock, and the Docker build fails
with `Missing: @emnapi/core@1.11.3` — which reads like a version problem and
invites a pointless version bump. It is not one. Generating from linux-x64
produces a superset lock (471 packages rather than 467) that installs
correctly on every platform.

---

## The desktop installer, and what a GitHub Release takes

The **built Windows installer** is an Electron shell that owns the window
and the Compose lifecycle and generates its own operator token. It requires
Docker Desktop; Node is not required.
[`docs/installer-verification.md`](installer-verification.md) is the
acceptance script for it, written for someone who did not build it.

**CI builds the thing the script installs.** The `desktop-package` job
builds the NSIS installer on every run of the workflow — every push to
`main` and every pull request against it — with
`npx electron-builder --win --x64` on `windows-latest`, after the same
`npm run package:dir` a developer runs, and uploads it as a workflow
artefact named **`OpsPilot-Setup-windows-x64`**, kept for **90 days**
(`retention-days: 90`, written out in `.github/workflows/ci.yml` rather than
inherited, because the walk-through is done by a person, on their own
schedule, on a machine with Docker Desktop). The job asserts the installer
before it uploads it — exactly one file matching
`OpsPilot-Setup-*-x64.exe`, over a megabyte, beginning with the PE signature
`MZ` and carrying the NSIS signature — and the upload itself fails if it
finds nothing, so a green job cannot have shipped no installer.

Before `17589a7` there was nowhere to get the file. The installer was a
build product on one developer's machine: gitignored, unreproducible from
any commit, attached to no run. §P2-D2 asks for the walk to be run "by
someone who did not build it", and in practice that means someone with no
repository and no toolchain — who had no way to obtain the thing the script
installs. **The blocker was a missing file, not a missing person**, and it
is gone — with one wrinkle the walk-through now spells out: downloading a
workflow artefact needs a GitHub account, and sending a file from one
person's machine to another's is not something a repository does. What
covers that last hop is a plain request: a short note that says what this
is, what it needs and what to send back, plus the installer itself, handed
over directly. That is what
[`docs/installer-verification.request.md`](installer-verification.request.md)
is for — the sender downloads the artefact and hands the script over, and
the file is sent on. The person being walked through needs no account, no
repository and no toolchain to receive it.

**What to hand over is three files**, and
[`docs/installer-verification.request.md`](installer-verification.request.md)
is the first of them: a note that says what the project is, what the walker
needs and what to send back, written to be sent as it stands. The other two
are the installer and
[`docs/installer-verification.md`](installer-verification.md) itself, the
step-by-step script (with a
[Chinese companion](installer-verification.zh-CN.md)).

**A workflow artefact is not a GitHub Release, and the difference is the
point.** An artefact is a build product attached to one run: it is not
indexed as a download anywhere, it expires, and nothing in the repository
links to it. It says "this build of this commit exists", not "this is the
version you should install" — and a link that says the second thing is
exactly what §P2-D2 has to earn first.

**What a GitHub Release took, concretely:**

1. **A tag.** Release assets attach to a Git tag. `v0.1.0` now exists.
2. **A rebuild from the tagged commit.** A local build's bytes move with
   every rebuild, and no commit records which commits the file on a machine
   contains. Its date is the date of a local build, not the provenance of a
   release. A release should ship a package built by
   `cd desktop && npm ci && npm run package` on a clean checkout of the tag
   — ideally in a CI job, so the artefact's provenance is a run id rather
   than a person's machine.
3. **Attaching the artefact.** `desktop/release/` is gitignored
   (`desktop/.gitignore:13`), and correctly so — 116 MB of build output does
   not belong in history. The file is uploaded as a release asset:
   `gh release create v0.1.0 desktop/release/OpsPilot-Setup-0.1.0-x64.exe`.
   The `.blockmap` beside it is electron-builder's differential-update
   metadata and would go up alongside it if auto-update is ever wired.
4. **D2's acceptance.** §P2-D2 requires that the package be installed and
   the golden path walked **on a clean Windows machine with Docker Desktop,
   by someone who did not build it**. That was done on 2026-11-14 — the
   sixteen checks in
   [`docs/installer-verification.md`](installer-verification.md) all passed,
   and the record sheet in that file is the evidence.

---

## Scope

**Phase 1 builds one workflow properly; Phase 2 adds a desktop shell and a
locale, and changes nothing about the workflow's scope.** It does not include
multi-agent orchestration, multi-tenancy, RBAC, SSO, **real
Salesforce/Stripe/Jira integrations**, Kubernetes, Kafka, Redis, Celery,
Temporal, GraphQL or fine-tuning. The test for inclusion is not "is it
useful" but **"does the golden workflow require it"**.

**The CRM, Billing and Issues integrations are simulated in this repository,
as the README's front screen says.** Nothing else about the workflow is a
demonstration: the state machine, the gates, the retrieval, the approval and
the audit trail are the real system, and the evaluation harness measures a
real model over a real endpoint.

Everything deliberately left unfinished — including the single-operator
token, the absent retry policy, the unredacted prompt storage, the worker's
inability to resume an interrupted run, and the untested two-worker
guarantee — is listed in
[**limitations.md**](limitations.md). That file is the one to read if you
want to know what this does not do.
