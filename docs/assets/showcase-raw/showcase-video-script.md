# Showcase video — storyboard, subtitle script and waiting-time record

**Deliverable of Phase 3 M2-05.** The videos are real browser walkthroughs of
the three demo paths against the disposable demo stack
(`scripts/demo_paths.py`, compose project `opspilot-demo-test`, fake model
provider). Recorded with Playwright Chromium via `scripts/record_showcase.mjs`;
assembled by `scripts/cut_showcase_video.mjs`; subtitled by
`scripts/make_showcase_subtitles.py`. The public-facing summary lives in
[`docs/showcase.md`](../../showcase.md); this file is the engineering script.

- **What the viewer sees:** the real Next.js dashboard driving the real
  two-process stack (api + worker + PostgreSQL/pgvector) — form submission,
  run progress, the approval gate, the human decision, the executed refund and
  its audit trail.
- **What is simulated:** the *model*. `MODEL_PROVIDER=fake` replays the
  committed `duplicate_charge` fixtures — no real model API is called. The
  billing/knowledge MCP servers are real code over seeded fixture data
  (`mcp_servers/*/seed.json`); the refund is a real write to a disposable
  store. The stack is a **live UI over a scripted scenario**, not a mock-up
  video.

## Deliverables and timings

| Video | Length | Assembly |
|---|---|---|
| `web/public/showcase/demo-path-a.mp4` | 93.4 s | title card (8 s) + raw path-A take (77.4 s) + end card (8 s) |
| `web/public/showcase/demo-path-b.mp4` | 43.4 s | disclosure card (6 s) + raw path-B take (37.4 s) |
| `web/public/showcase/demo-path-c.mp4` | 73.9 s | disclosure card (6 s) + raw path-C take (67.9 s) |

## Pacing — what the "waiting time" treatment actually is

On this machine the whole golden path completes in ~5 worker seconds, which is
too quick to read. The recording therefore **paces itself**: every beat (form
fill, submit, parked banner, approval card, approve click, completed run, runs
list) holds the page for 6–14 s so a viewer can read the screen, while the
worker polls in real time between states. The assembly adds only the title/end
cards. **No segment is cut, sped up or slowed down** — the waiting time is
present in the take, just made readable. The beat offsets are in
`record-manifest.json` (`paths[].beats`), and the subtitles are generated from
exactly those offsets (`scripts/make_showcase_subtitles.py`), so caption and
screen state always agree.

## Storyboard (path A — approve)

| # | Beat (manifest label) | On screen | Narration (English, see demo-path-a.srt) |
|---|---|---|---|
| 1 | dashboard | Dashboard, fresh stack | "This is OpsPilot — a live dashboard over a real stack; the model is a scripted replay, so no external AI service is called." |
| 2 | ticket-filled | Tickets form, one-click fill | "A customer reports a duplicate charge on invoice INV-2026-384. The form is pre-filled with the README's case." |
| 3 | ticket-submitted | Submit → run created | "Submitting creates the ticket and enqueues a run in the same transaction." |
| 4 | parked | Parked banner: WAITING_APPROVAL | "The agent classified the ticket (0.93), retrieved the policy, proposed a $129 refund on TX-88219 — then PARKED. Nothing has been sent." |
| 5 | approval-card | Approvals inbox card | "The card shows exactly what will execute: arguments, risk, the deterministic policy explanation — and the model's justification, labelled untrusted." |
| 6 | approved | Click Approve; card leaves the inbox | "The human approves. The click releases the run to the worker — it does not execute the refund in the request." |
| 7 | completed | Run Completed, audit trail | "The worker executes the refund exactly once, idempotency-keyed; the run completes with 21 recorded steps and 5 cited sources." |
| 8 | runs-list | Runs list | "One completed run, fully traceable — investigate, cite, gate, execute, audit." |

## Paths B and C (companion material)

- **B — reject** (`demo-path-b.mp4`): the same park, the human REJECTS, the
  run completes with the escalation banner and **no refund**. The ledger still
  holds exactly one refund (path A's).
- **C — model failure** (`demo-path-c.mp4`): a throwaway HTTP container
  answers every model call with **429**; the SDK has `max_retries=0`, so the
  run fails **loudly** with `model_unavailable` and the structured diagnosis
  (`error_category=rate_limit`) on the timeline. The 429 server is removed, a
  real worker is restored, and the next ticket completes — the queue survived
  the outage.

## Provenance and reproduction

- Raw takes: `docs/assets/showcase-raw/videos/{A-approve,B-reject,C-model-failure}.webm`
  (Playwright Chromium video, same browser and clicks as `web/e2e/demo-paths.spec.ts`).
- `record-manifest.json` — which take produced which path, the beat offsets,
  and the screenshot list. This is the frame/moment log.
- Reproduce (reset first: the fake provider's fixtures replay in order, one
  full run per worker process):

```bash
.venv/Scripts/python.exe scripts/demo_paths.py reset --api-port 18001 --web-port 3102 --pg-port 15434
cd web && node ../scripts/record_showcase.mjs --api 18001 --web 3102 --pg 15434
node ../scripts/cut_showcase_video.mjs
```

All material is recorded from the live dashboard UI; the model layer is a
committed fixture replay (fake provider) and the data is disposable demo seed
data. No real customer data, credentials, or external model calls appear in
any frame.
