# OpsPilot v0.2.0 — Release notes (draft)

> **Status of this file:** draft for the maintainer, prepared per
> [`docs/release-checklist.md`](release-checklist.md) §4 Plan A (new tag
> `v0.2.0`, old and new releases coexist). Nothing here is published yet:
> tagging, the GitHub Release and the asset upload are all user actions.
> The "Download" section below assumes the steps in the checklist have been
> completed; verify the SHA-256 against your local build before publishing.

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
  the v0.1.0 installer walkthrough).

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
| Tool-selection, development set (15 cases, two runs) | **3/15 and 2/15** exact-set hits | Below the ≥10/15 project bar — **not met** |
| Tool-selection, holdout set (32 cases, untouched during tuning) | **10/32** exact-set hits | Reported as measured |
| Tool-argument validity (both batches) | **1.000** (142/142 proposals) | Met |
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

**Fake demo vs. real evidence.** The showcase videos prove the wiring (UI,
gates, trace, recovery) on scripted data. The real-model claims above rest
only on the two eval reports and their committed results files. Do not
describe the videos as live AI runs — that distinction is part of this
project's acceptance criteria
([`docs/portfolio-acceptance-2026-11-15.md`](portfolio-acceptance-2026-11-15.md)).

### New installer

- **`OpsPilot-Setup-0.2.0-x64.exe`** — rebuilt from the Phase 3 final state.
  The pre-bump candidate measured **126,045,189 bytes (~120 MB)**,
  SHA-256 `71ac5ac0adcb543fc96810c96085fbb66bbb73260c92d85325a6927997ee9a7b`
  (built from commit `81a0278`; the v0.2.0 tag adds only version-number and
  documentation commits — verify the SHA of the asset you upload, since
  builds are not byte-reproducible).
- Machine verification passed: packaged `docker-compose.yml` is byte-identical
  to the repo; no `.env`, no API-key literals, no private keys inside the
  package; all six packaged-posture gates green. Cold start of the bundled
  stack took 3.5–4 min on a cache-warm machine (budget **5–15 min** on a
  cold machine), warm restart 18 s, `/ready` healthy in both cases.
- The v0.1.0 installer and its 2026-11-14 walkthrough record remain valid;
  the old release stays up. Details:
  [`docs/m3-04-repackage-verification.md`](m3-04-repackage-verification.md).

## Download

- **v0.2.0:** `OpsPilot-Setup-0.2.0-x64.exe` — attached to this release
  (verify SHA-256 after upload; expected pre-bump candidate:
  `71ac5ac0adcb543fc96810c96085fbb66bbb73260c92d85325a6927997ee9a7b`).
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
  10/15 bar (3/15, 2/15 dev; 10/32 holdout). The safety gates held in all
  measured batches, but do not expect the agent to pick the right tool set
  unaided on unfamiliar workflows.
- The installer is not code-signed; SmartScreen may show a warning
  ("More info" → "Run anyway"), consistent with v0.1.0.
- The demo videos are scripted-provider recordings, disclosed as such; they
  are not evidence of live model performance.
- One-time walkthrough of the new installer on a clean machine by a person
  who did not build it is recorded as pending in
  [`docs/release-checklist.md`](release-checklist.md) §3 at the time this
  draft was written; update the status when it completes.

## Upgrade notes

No data migration is required between v0.1.0 and v0.2.0 (same schema
lineage). As with v0.1.0, quitting the app keeps Docker volumes; use
`docker compose down -v` on the `opspilot` project to discard local data.
