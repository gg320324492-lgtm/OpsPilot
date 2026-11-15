# OpsPilot v0.2.0 — Release notes (draft)

> **Status of this file:** published. It is the body of the
> [`v0.2.0` GitHub Release](https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.2.0),
> created from [`docs/release-checklist.md`](release-checklist.md) §4 Plan A
> (new tag `v0.2.0`, old and new releases coexist). The asset's SHA-256 was
> verified on the release object after upload and matches the value below.

## What OpsPilot is

OpsPilot is a safety-gated AI operations agent for B2B support and billing.
It takes a support ticket, investigates it through MCP tools, retrieves the
applicable company policy, proposes an action — and when that action moves
money, **stops and waits for a human** before executing it exactly once per
idempotency key. Every model call, tool call and retrieval is recorded in an
append-only trace.

> The model proposes. Deterministic code decides. Tools execute.
> Humans approve risky actions. Everything is traced.

## What changed since v0.1.0

v0.1.0 closed Phase 2: the golden path (ticket → investigation → policy →
proposed action → human approval → exactly-once execution) ran end to end,
verified on a real Windows machine walkthrough. v0.2.0 covers Phase 3 —
hardening, demonstrable UX, and honest real-model measurement.

### Reliability and correctness

- **Duplicate-charge root cause fixed.** The one-process-per-idempotency-key
  guarantee now holds under retries, concurrent submissions and worker
  restarts; replaying the same refund request returns the original outcome
  instead of charging twice (409 on replay). Covered by Phase 2 concurrency
  and idempotency tests.
- **Run recovery.** A run interrupted by a model-provider failure can be
  resumed instead of being lost; the failure and recovery are visible in the
  trace.
- **Configurable ports and compose project name.** The bundled Docker stack
  no longer hard-codes `localhost` ports or the `opspilot` project name, so it
  can coexist with other stacks (this fixes the two deviations recorded in
  the v0.1.0 installer walkthrough). The desktop shell follows
  `OPSPILOT_API_HOST_PORT` too — from its environment, the repository `.env`,
  or an installed copy's app-data `opspilot.env` — so moving the API off a
  taken 8000 moves the shell's `/ready` probe with it. That last half was the
  one substantive gap the independent 2026-11-15 walkthrough found; it is
  fixed in the commit this release is built from.

### Interface and demonstration material

- **Dashboard and run detail.** The web dashboard shows ticket intake, the
  run's parked/approved/completed states, approval gating, and a run list.
- **Exportable evidence.** A run's full trace can be exported for audit.
- **Showcase material.** Three scripted demo videos (paths A/B/C, 93s/43s/74s)
  with English subtitles and 13 screenshots. **These are pipeline
  demonstrations using a scripted fake model provider** — each video carries
  the on-screen disclosure "scripted fake model provider — no external AI
  calls". See [`docs/showcase.md`](showcase.md).

### Real-model validation and safety acceptance (November 2026)

The following numbers are **measured, not estimated**. Full methodology,
frozen configurations and per-case details:
[`docs/m3-02-dev-two-runs-2026-11-15.md`](m3-02-dev-two-runs-2026-11-15.md),
[`docs/m3-03-holdout-safety-2026-11-15.md`](m3-03-holdout-safety-2026-11-15.md).

| Batch | Result | Gate |
|---|---|---|
| Tool-selection, development set (15 cases, four runs across two code revisions) | **3/15, 2/15** (first measurement) and **2/15, 3/15** (after the retrieval fix) exact-set hits | Below the ≥10/15 project bar — **not met** |
| Tool-selection, holdout set (32 cases, untouched during tuning) | **10/32** exact-set hits | Reported as measured |
| Tool-argument validity (every run) | **1.000** — development set 44/44 and 40/40 before the fix, 38/38 and 56/56 after; holdout 102/102 | Met |
| Safety batch (15 cases incl. prompt-injection attempts) | **Unsafe executions = 0**; every high-risk proposal stopped at the human approval gate | **Met** |

Read this honestly: the **safety architecture works** — across all live
batches there was not one unauthorized high-risk write, not one replayed
idempotency key, and every refund proposal was stopped for human approval.
The **model's tool-selection accuracy is the known weak point**: with the
tested model (DeepSeek V4.1-Flash via an OpenAI-compatible gateway), the
agent often proposes an incomplete or over-broad tool set, which the reports
attribute to the model's judgement rather than to prompt or schema problems.
We publish these numbers because an agent that touches money must be
judged on measured safety behaviour, not on a scripted demo.

**A fix between the two measurements, and an honest non-improvement.** The
first pair of runs surfaced three cases (tool-005/006/012) that never reached
the planner at all: their retrieval scores sat just below the abstention
threshold — a deterministic harness failure any model would have hit. A
curated query-expansion fix plus a recalibrated threshold
(`RETRIEVAL_MIN_SCORE` 0.22 → 0.205, calibrated between the answerable floor
and the unanswerable ceiling) removed those abstentions, and the development
set was re-run on the same frozen configuration
([`docs/m3-02-rerun-after-retrieval-fix-2026-11-15.md`](m3-02-rerun-after-retrieval-fix-2026-11-15.md)).
The headline did not move: **2/15 and 3/15**. What the fix proves is where the
remaining gap is *not* — it is the model's tool-set judgement, not retrieval
or prompt plumbing. Both measurements are reported in full; neither is hidden.

**Fake demo vs. real evidence.** The showcase videos prove the wiring (UI,
gates, trace, recovery) on scripted data. The real-model claims above rest
only on the two eval reports and their committed results files. Do not
describe the videos as live AI runs — that distinction is part of this
project's acceptance criteria
([`docs/portfolio-acceptance-2026-11-15.md`](portfolio-acceptance-2026-11-15.md)).

### New installer

- **`OpsPilot-Setup-0.2.0-x64.exe`** — built from commit `2b96e70` (the
  v0.2.0 tag at build time; it includes the retrieval fix, its re-run report,
  the independent 2026-11-15 walkthrough record, and the fix for the one gap
  that walk found: the shell now follows `OPSPILOT_API_HOST_PORT`). The tag's
  final position, `656bbd3`, adds documentation commits only — the binary is
  not affected.
  The built artifact measures **130,075,633 bytes (~124 MB)**, SHA-256
  `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`.
  Verify the SHA of the asset actually uploaded — builds are not
  byte-reproducible across machines.
- Machine verification passed on this build: packaged `docker-compose.yml` is
  byte-identical to the repo; no `.env`, no API-key literals, no private keys
  inside the package; the packaged-posture gates ran **5 passed, 1 skipped**
  (the skipped one searches for the operator-token literal and has nothing
  to search for when no token is configured in the build environment —
  the same behaviour as the M3-04 build). Cold/warm start timings measured on
  the pre-bump candidate (3.5–4 min cache-warm, 18 s warm restart; budget
  **5–15 min** on a cold machine) were not re-timed for this artifact; the
  bundled stack is unchanged by the commits since `81a0278` except the
  retrieval code path, which does not run at container start.
- The v0.1.0 installer and its 2026-11-14 walkthrough record remain valid;
  the old release stays up. Details:
  [`docs/m3-04-repackage-verification.md`](m3-04-repackage-verification.md).

## Download

- **v0.2.0:** `OpsPilot-Setup-0.2.0-x64.exe` — attached to this release
  (verify SHA-256 after upload; expected:
  `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`).
- **v0.1.0:** remains available at the
  [v0.1.0 release](https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.1.0)
  (old installer, SHA-256 `565206f7…`, verified 2026-11-14).

## Requirements

- Windows 10/11 x64.
- Docker Desktop (the agent runs the local Postgres/pgvector + API + worker
  stack via docker compose; first launch builds images — budget 5–15 min).
- No model API key is required to install and start: the bundled stack runs
  without external AI calls, and evaluation with a real model requires your
  own provider credentials supplied via environment variables (never bundled).

## Known limitations

- Tool-selection accuracy with the evaluated model is below the project's own
  10/15 bar (2–3/15 across four development-set runs, before and after the
  retrieval fix; 10/32 holdout). The safety gates held in all measured
  batches, but do not expect the agent to pick the right tool set unaided on
  unfamiliar workflows.
- The installer is not code-signed; SmartScreen may show a warning
  ("More info" → "Run anyway"), consistent with v0.1.0.
- The demo videos are scripted-provider recordings, disclosed as such; they
  are not evidence of live model performance.
- The installer was walked on 2026-11-15 by a person who did not build it;
  the filled sixteen-item record is the last section of
  [`docs/installer-verification.md`](installer-verification.md). The walk
  passed every step it could drive headlessly and found one substantive gap:
  the shell hard-coded the API at `127.0.0.1:8000`, so on a machine where
  that port is taken the dashboard step never completed. The shell now
  follows `OPSPILOT_API_HOST_PORT`; the artifact this release attaches is
  rebuilt from that fix, and the port-occupied scenario was re-checked on it
  before publication.

## Upgrade notes

No data migration is required between v0.1.0 and v0.2.0 (same schema
lineage). As with v0.1.0, quitting the app keeps Docker volumes; use
`docker compose down -v` on the `opspilot` project to discard local data.
