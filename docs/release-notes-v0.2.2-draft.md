# v0.2.2 — the Phase 4 UI release

> **Status: released 2026-11-17.** This text is the body of the GitHub
> release. The version freeze (F4-E) bumped all five version locations to
> 0.2.2 in one commit; the installer beside this file is the one those
> sources built.
>
> Related planning documents: [`phase4-plan.md`](phase4-plan.md) (this is the
> F4-A/F4-B/F4-C slice), [`f4e-release-checklist.md`](f4e-release-checklist.md)
> (pre-release checks), [`model-retest-plan-v0.3.0.md`](model-retest-plan-v0.3.0.md)
> (the *separate*, later real-model retest iteration — this release contains
> **no** model, prompt or retrieval changes).

UI release: the dashboard is re-skinned on HeroUI 3.2.7 with one shared
semantic design-token palette. It is a presentation-layer change only — every
route, API call, permission check and state transition is untouched.

## Changed (web UI)

- **HeroUI 3.2.7 adopted** (`@heroui/react`, `@heroui/styles`, locked in
  `web/package.json`) on the existing Next.js 16 + React 19 + Tailwind CSS 4
  stack, via a client provider island. Icons stay Lucide; i18n stays `en` /
  `zh-CN`.
- **One semantic token set for every screen** ("audit-workbench" palette):
  graphite surfaces; accent colour reserved for the action that needs a
  person; amber = waiting / needs-human, red = failed or dangerous,
  green = completed. Replaces four ad-hoc Tailwind colour scales.
- **Dashboard** rebuilt on the tokens: the two headline cards (pending
  approvals, failed runs), the replay warning (now a HeroUI Alert), the
  recent-runs table and the status rows.
- **Run Detail**: banners and timeline state colours move to the same tokens.
- **Approvals**: approval card, risk summary and the confirm Dialog are on the
  same tokens; the decision buttons and their disabled/expired/decided states
  are unchanged in behaviour.
- **Tickets, Runs, Knowledge, locale switcher**: colour and border references
  unified — no copy, layout or behaviour changes in this slice.

## Explicitly unchanged

- **API contract** — no new, removed or reshaped endpoint;
  [`docs/api-contract.md`](api-contract.md) is the unchanged reference.
- **Permission model** — the tool registry and its `HIGH_RISK_WRITE` /
  read-only classification are byte-identical; the registry-equals-literal
  security test still pins it.
- **Run state machine** — states, legal transitions and their order are
  unchanged.
- **Approval semantics** — an approval still binds to the tool call's
  identity (not the run or customer); a second decision is still refused with
  `409 approval_already_decided`.
- **Idempotency** — the `(run_id, transaction_id)` key and the
  exactly-once-per-key guarantee are unchanged.
- **The five gates** — schema validation, registry lookup, static permission
  lookup, policy engine, approval gate: untouched.
- **Provider behaviour** — `MODEL_PROVIDER=fake` default, disclosure banner,
  and all real-model settings in `.env` are as before. No model, prompt or
  retrieval change ships in this release.

## Known-pending at draft time

- **README screenshots and the golden-path GIF still show the pre-HeroUI
  UI.** They are being re-captured on the new token set (same fixture, same
  viewport, same paths) per [`f4e-release-checklist.md`](f4e-release-checklist.md)
  §2; the README carries an explicit "screenshots pending update" note until
  then.
- **F4-D (visual QA, accessibility, responsive, bundle check) is running in
  parallel.** Its findings gate this release; this draft will be amended with
  the audit outcome before freeze.
- Release asset (installer) is rebuilt and re-verified only after the freeze
  commit exists; see the checklist for the walkthrough requirements.

## Notes

- The packaged run still uses the scripted fake provider by default; the
  dashboard carries the disclosure.
- Install / quick-start steps are unchanged: `docker compose up` with a
  non-empty `OPSPILOT_OPERATOR_TOKEN`, or the desktop installer (requires
  Docker Desktop).
