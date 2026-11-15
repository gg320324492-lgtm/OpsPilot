# Evals baseline — every committed results file, read and grouped

**Generated:** 2026-11-15, from the results files under
[`evals/results/`](../evals/results/) as they exist on `main` (`852ff60`).
Each file's full metrics are recomputable from the file itself; this page is
the index a reader can start from. The "how to recompute" commands live in
[`docs/evals.md`](evals.md), and the interpretation of the live batches is in
[`docs/progress.md`](progress.md) and the M3-02/M3-03 reports.

**Columns:** `ts` = tool-selection exact-set accuracy (value × cases = hits);
`av` = tool-argument validity; `unsafe` = unsafe-execution count. `ds`
abbreviates the datasets in the run's config (`C` classification, `R`
retrieval, `TS` tool_selection, `S` safety, `TS_holdout` the 32-case
holdout).

**Committed vs local-only.** `.gitignore` ignores `evals/results/*` except
`.gitkeep` and `README.md`; six result files were force-added and are in the
repository (marked **committed** below). The four DeepSeek development-set
run files are **local-only** by the repo's stated convention
([`docs/m3-02-dev-two-runs-2026-11-15.md`](m3-02-dev-two-runs-2026-11-15.md):
"按仓库约定 gitignored、仅本机存在") — their numbers are quoted in the
committed reports, which are the durable evidence a reader can check.

## Real-model batches — the ones the release notes quote

### DeepSeek V4.1-Flash (gateway id `workbuddy/deepseek-v4.1-flash`) — the v0.2.0 measurement

| Run | File | Committed? | Dataset | ts | av | unsafe |
|---|---|---|---|---|---|---|
| M3-03 holdout (32 cases, frozen) | `2026-11-15T04-57-30.json` | **yes** | TS_holdout (32) | 10/32 (0.3125) | 1.000 | 0 |
| S4 safety batch (15 cases, injections included) | `2026-11-15T05-08-05.json` | **yes** | S (15) | — | 1.000 | 0 |
| M3-02 run 1 | `2026-11-15T05-08-14.json` | local-only | TS (15) | 3/15 (0.200) | 1.000 | 0 |
| M3-02 run 2 | `2026-11-15T05-19-05.json` | local-only | TS (15) | 2/15 (0.133) | 1.000 | 0 |
| Re-run after retrieval fix, run 1 | `2026-11-15T08-56-37.json` | local-only | TS (15) | 2/15 (0.133) | 1.000 | 0 |
| Re-run after retrieval fix, run 2 | `2026-11-15T09-12-41.json` | local-only | TS (15) | 3/15 (0.200) | 1.000 | 0 |

Four development-set runs, one band: **2–3/15** — the ≥10/15 gate is not
met, and this is the number the README, the release notes, the one-pager and
the acceptance scorecard all quote. The holdout (10/32) and the safety batch
(unsafe = 0, approval-policy compliance 1.000, expected-write rate 0.250)
are the committed files anyone can re-read.

### MiniMax-M3.1-Flash — the Phase 2 / B2-era measurement

All committed.

| Run | File | ts | av | unsafe |
|---|---|---|---|---|
| B2 pre-fix full sweep (2026-10-11) | `2026-10-11T15-39-42.json` | 3/15 (0.200) | 0.471 | 0 |
| Repeat run 1 | `2026-10-13T18-24-25.json` | 3/15 (0.200) | 0.442 | 0 |
| Repeat run 2 | `2026-10-13T18-40-10.json` | 2/15 (0.133) | 0.462 | 0 |
| B2 era (2026-11-14, six development runs around the schema fix) | `2026-11-14T08-18` → `14-51` | 0–2/15 | 0.357 → 1.000 after B2 | 0 |

The B2 fix (tool catalogue + argument schemas into the planner prompt) lifted
argument validity from ~0.38–0.47 to 1.000 and held unsafe = 0; tool
selection did not move. That is exactly what the Phase 2 close-out recorded.

### Claude Sonnet 5.5 — the very first live sweep

| Run | File | ts | av | unsafe |
|---|---|---|---|---|
| 2026-10-08 full sweep | `2026-10-08T05-24-41.json` (committed) | 1/15 (0.067) | 0.634 | 0 |

## Fake-provider runs — pipeline verification, not model evidence

`model: hand-written-placeholder` in the results file is the fixture replay;
every one of these runs has unsafe = 0 and tokens = 0. They exist to prove
the harness plumbing (dataset loading, run execution, metrics computation,
results persistence) and are cited as such wherever they appear. These files
are local-only (gitignored), as the convention requires.

- **Single-case smoke runs** (n=1, ts=1/1): ~70 files from the Phase 2/3
  development loop (`2026-11-13T19-18-41` through `2026-11-15T07-10-29`).
- **Full development set on fake** (ts=2/15 — the fixture answers only 2 of
  the 15 real cases): `2026-11-14T11-32-XX`, plus the two
  `2026-11-14T16-37/16-39` runs.
- **Holdout fixture** (ts=7/32 = 0.219): `2026-11-13T23-52-14.json`.
- **S0 dry-run for the v0.3.0 re-test** (`--limit 2`): `2026-11-15T13-02-31.json`
  (ts=1/2 — one case beyond the fixture's coverage; the point was the
  pipeline, not the score).

## What to tell a reader

1. The **safety gate** is the reproduced result: **unsafe = 0 in every run
   audited here**, real model or fixture, injection cases included.
2. The **tool-selection numbers that gate the acceptance** are the four
   DeepSeek development-set runs in the first table — 2–3/15 against a 10/15
   bar, with the retrieval-fix re-run showing the gap is the model's
   tool-set judgement, not the harness.
3. The **holdout** (10/32, committed) and the **MiniMax/Claude sweeps**
   (committed) are context for how the number behaves across models; none
   meets the bar either.
4. Everything else on this page verifies plumbing and should never be quoted
   as model performance.

### MiniMax-M3.1-Flash — the deferred re-test (2026-11-15/16, local-only files)

| Run | File | Dataset | ts | av | unsafe |
|---|---|---|---|---|---|
| S1 pilot (4 cases) | `2026-11-15T14-37-04.json` | TS (4) | 0/4 | 1.000 | 0 |
| S2 run 1 | `2026-11-15T15-23-14.json` | TS (15) | 1/15 (0.067) | 0.968 | 0 |
| S2 run 2 | `2026-11-15T16-03-15.json` | TS (15) | 1/15 (0.067) | 1.000 | 0 |

Worst tool-selection measurement across all models; the safety gates held.
Interpretation: [`docs/m3-02-minimax-retest-2026-11-15.md`](m3-02-minimax-retest-2026-11-15.md).

### deepseek-v4.1-flash-max — route-A re-test via the gateway (2026-11-15/16, local-only files)

| Run | File | Dataset | ts | av | unsafe |
|---|---|---|---|---|---|
| S1 pilot (4 cases) | `2026-11-15T16-20-15.json` | TS (4) | 0/4 | 1.000 | 0 |
| S2 run 1 | `2026-11-15T16-37-26.json` | TS (15) | 2/15 (0.133) | 1.000 | 0 |
| S2 run 2 | `2026-11-15T16-54-28.json` | TS (15) | 1/15 (0.067) | 1.000 | 0 |

Same band as the flash tier; 30/30 completed, ~6× faster. The stronger tier
did not improve tool selection. Interpretation:
[`docs/m3-02-deepseek-v4.1-flash-max-retest-2026-11-15.md`](m3-02-deepseek-v4.1-flash-max-retest-2026-11-15.md).
(`deepseek/deepseek-v4-pro` was refused 402 Insufficient Balance before any
measurement.)
