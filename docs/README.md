# OpsPilot documentation

The docs are grouped by role. Start with [`architecture.md`](architecture.md)
for the system shape, [`milestones.md`](milestones.md) for what was delivered,
and [`progress.md`](progress.md) for the index of the development log.

## Product and interface

| Document | Contents |
|---|---|
| [`api-contract.md`](api-contract.md) | The HTTP API: every route, request and response |
| [`mcp-contracts.md`](mcp-contracts.md) | The three MCP servers' tool contracts |
| [`data-model.md`](data-model.md) | Tables, states and the run/approval lifecycle |
| [`tool-permissions.md`](tool-permissions.md) | The permission model behind the gates |
| [`agent-state-machine.md`](agent-state-machine.md) | The run state machine |
| [`architecture.md`](architecture.md) | System architecture and the decisions behind it |
| [`limitations.md`](limitations.md) | What this build does not do, honestly |
| [`risks.md`](risks.md) | The risk register |
| [`principles.md`](principles.md) | The engineering principles the project holds itself to |

## Running and installing

| Document | Contents |
|---|---|
| [`README.md`](../README.md) | The project front page and quick start |
| [`installer-verification.md`](installer-verification.md) | How the installer is verified, and every walk's record |
| [`installer-walk-third-party-2026-11-16.md`](installer-walk-third-party-2026-11-16.md) | The context-free tester's walk of v0.2.0 |
| [`installer-interactive-walk-2026-11-15.md`](installer-interactive-walk-2026-11-15.md) | The owner-performed interactive walk sheet |
| [`installer-walk-detailed-checklist.md`](installer-walk-detailed-checklist.md) | The step-by-step walk checklist with failure modes |
| [`installer-verification-new-package-template.md`](installer-verification-new-package-template.md) | The blank form for the next package |
| [`installer-verification.request.md`](installer-verification.request.md) / [`installer-verification.request.zh-CN.md`](installer-verification.request.zh-CN.md) | The brief sent to a walker (EN / 中文) |
| [`installer-verification.zh-CN.md`](installer-verification.zh-CN.md) | The verification script, 中文对照 |
| [`demo-paths.md`](demo-paths.md) | The three demo paths and their resets |

## Evaluation and model results

| Document | Contents |
|---|---|
| [`evals.md`](evals.md) | The evaluation design: datasets and metrics |
| [`evals-baseline.md`](evals-baseline.md) | The committed baselines, grouped by model and dataset |
| [`m3-02-dev-two-runs-2026-11-15.md`](m3-02-dev-two-runs-2026-11-15.md) | The first real-endpoint two-run |
| [`m3-02-rerun-after-retrieval-fix-2026-11-15.md`](m3-02-rerun-after-retrieval-fix-2026-11-15.md) | The re-run after the retrieval fix |
| [`m3-02-minimax-retest-2026-11-15.md`](m3-02-minimax-retest-2026-11-15.md) | The MiniMax re-test |
| [`m3-02-deepseek-v4.1-flash-max-retest-2026-11-15.md`](m3-02-deepseek-v4.1-flash-max-retest-2026-11-15.md) | The stronger-tier re-test |
| [`m3-02-glm-5.3-flash-s1-2026-11-15.md`](m3-02-glm-5.3-flash-s1-2026-11-15.md) | The GLM-5.3 S1 pilot |
| [`m3-03-holdout-safety-2026-11-15.md`](m3-03-holdout-safety-2026-11-15.md) | The frozen holdout and the safety batch |
| [`eval-failure-classification-2026-11-15.md`](eval-failure-classification-2026-11-15.md) | Why each evaluation failure happened |
| [`retrieval-abstain-analysis.md`](retrieval-abstain-analysis.md) | The retrieval abstention root-cause analysis |
| [`model-validation-plan.md`](model-validation-plan.md) | The original model validation plan |
| [`model-retest-plan-v0.3.0.md`](model-retest-plan-v0.3.0.md) | The re-test plan and its frozen configuration |
| [`s1-preflight.md`](s1-preflight.md) | The S1 pre-flight checklist |

## Portfolio, releases and direction

| Document | Contents |
|---|---|
| [`portfolio-entry-en.md`](portfolio-entry-en.md) | The ready-to-paste portfolio entry |
| [`portfolio-publish-kit.md`](portfolio-publish-kit.md) | The publish walkthrough for a freelance platform |
| [`service-offerings-en.md`](service-offerings-en.md) | The three service offerings |
| [`one-pager-en.md`](one-pager-en.md) | The one-page summary |
| [`portfolio-scorecard.md`](portfolio-scorecard.md) | The acceptance scorecard |
| [`portfolio-acceptance-2026-11-15.md`](portfolio-acceptance-2026-11-15.md) | The acceptance review and its verdict |
| [`release-checklist.md`](release-checklist.md) | The release checklist and its state |
| [`release-notes-v0.2.0-draft.md`](release-notes-v0.2.0-draft.md) / [`release-notes-v0.2.1-draft.md`](release-notes-v0.2.1-draft.md) | The published release notes |
| [`phase4-draft.md`](phase4-draft.md) | The Phase 4 direction draft |
| [`phase4-plan.md`](phase4-plan.md) | The Phase 4 execution plan: UI selection, milestones, visual direction, non-goals |
| [`ui-selection-2026-11-16.md`](ui-selection-2026-11-16.md) | The UI library selection record and candidate comparison |
| [`direction-b-checklist.md`](direction-b-checklist.md) | The direction-B execution checklist |

## Process records

| Document | Contents |
|---|---|
| [`milestones.md`](milestones.md) | Every milestone, its criteria and its verdict |
| [`progress.md`](progress.md) | The development log index; the logs live in [`history/`](history/) |
| [`claims-audit-2026-11-15.md`](claims-audit-2026-11-15.md) | The claims audit |
| [`security-review-2026-11-14.md`](security-review-2026-11-14.md) | The security review |
| [`dependency-audit-2026-11-15.md`](dependency-audit-2026-11-15.md) | The dependency audit |
| [`phase2-plan.md`](phase2-plan.md) / [`phase2-review-2026-11-14.md`](phase2-review-2026-11-14.md) | The Phase 2 plan and its review |
| [`phase3-plan.md`](phase3-plan.md) | The Phase 3 plan |
| [`HANDOVER.md`](HANDOVER.md) | The machine-to-machine handover |
| [`walkthrough-kit.md`](walkthrough-kit.md) | The screen-recording kit |
| [`video-upload-kit.md`](video-upload-kit.md) | The video upload kit |
| [`dev-notes.md`](dev-notes.md) | The development-era narrative beneath the README |
| [`mcp-sdk-notes.md`](mcp-sdk-notes.md) | The MCP SDK 2.x notes |
| [`m3-04-repackage-verification.md`](m3-04-repackage-verification.md) | The repackage verification |
| [`case-study-duplicate-charge.md`](case-study-duplicate-charge.md) / [`case-study-duplicate-charge-en.md`](case-study-duplicate-charge-en.md) | The duplicate-charge case study (中文 / EN) |
| [`case-study-summary-en.md`](case-study-summary-en.md) | The case-study summary |
| [`showcase.md`](showcase.md) | The demo showcase index |

## Decision records and evidence

* [`adr/`](adr/) — the five architecture decision records
* [`evidence/`](evidence/) — the archived run evidence (JSON, logs, snapshots)
* [`history/`](history/) — the development log, split by phase
* [`assets/`](assets/) — artwork and the raw showcase material
* [`demo/`](demo/) — the demo script source
