# OpsPilot Progress

The append-only development log. It grew past ten thousand lines, so on
2026-11-16 it was split by phase into [`docs/history/`](history/) — the
content is unchanged, only the file boundaries moved. This file is the index;
each archive keeps its own chronological entries.

## Status

| Milestone | Status |
|---|---|
| Phase 1 — M0–M12 (architecture, API, MCP, gateway, runtime, RAG, golden path, dashboard, evals, CI, closure) | done |
| Phase 2 — hardening, desktop delivery, documentation, internationalisation, closure | done |
| Phase 3 — real-model runs, installer, releases (v0.2.0, v0.2.1) | done |
| Direction B tidy-up (DBC-01–05, 07, 08) | done |
| DBC-06 — real-model demo clip | shelved with direction A |

## Archives

| File | Covers |
|---|---|
| [`history/phase1-overview.md`](history/phase1-overview.md) | Phase 1 foundations: architecture, database/API skeleton, MCP servers, tool gateway, agent runtime (M0–M4) |
| [`history/phase1-rag-golden-path.md`](history/phase1-rag-golden-path.md) | RAG pipeline, `knowledge.search`, fixture repair, the golden-path tests and their argument divergence (M5–M6) |
| [`history/phase1-dashboard-evals-ci.md`](history/phase1-dashboard-evals-ci.md) | Dashboard, the 70-case eval and security batch, CI, the failed tool call and the M11/M12 fixes (M7–M12) |
| [`history/phase1-closure-audits.md`](history/phase1-closure-audits.md) | Closure audits, the settings campaign, the twelve tool-selection failures classified |
| [`history/phase2-hardening-and-desktop.md`](history/phase2-hardening-and-desktop.md) | Deployment security, the Electron shell, documentation delivery, internationalisation, and the audit campaign that closed the Phase 2 gaps |
| [`history/phase2-closure-and-handover.md`](history/phase2-closure-and-handover.md) | Phase 2 closure, the new machine, and the handover document |
| [`history/phase3-evidence-and-release.md`](history/phase3-evidence-and-release.md) | The real-model runs (five families), the installer walks, the acceptance reviews, and the v0.2.0/v0.2.1 releases |

## Where the living documents are

* Current plan and direction: [`phase4-draft.md`](phase4-draft.md), [`direction-b-checklist.md`](direction-b-checklist.md)
* Release state: [`release-checklist.md`](release-checklist.md)
* Acceptance: the M3-06 acceptance review (the scorecard drafts were removed from the repository)

## Phase 4 CI incident: the silent web-e2e, root-caused (2026-11-16/17)

The HeroUI adoption's first CI runs failed with a 25-minute silent web-e2e. A
dedicated diagnosis session (e2e-root-cause) ruled out the image-prefix,
classic-builder, worker-polling and readiness-fetch hypotheses one by one and
found the real cause in `heroui-providers.tsx`: HeroUI's `ToastProvider`
renders `null` during SSR when no toast is queued, so wrapping `children`
inside it made the entire server-rendered page an empty React shell that
never hydrates -- every spec then waited for buttons that would never mount.
The fix renders the provider as a sibling of `children` (`4f67a99`), and both
follow-up CI runs are green, including the browser golden path. The slow
Docker image builds that initially masked this were addressed separately:
CI now pre-builds both image prefixes with buildx `--load` and the specs run
with `E2E_SKIP_BUILD=1` (`f0f9bf7`), with the e2e budget at 30 minutes as a
backstop (`ff6b7cf`). Full diagnosis: the e2e-root-cause session report.
