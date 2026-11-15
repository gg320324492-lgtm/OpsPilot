# OpsPilot — Project One-Pager

**A safety-gated AI operations agent for B2B support and billing.**

OpsPilot takes a customer-support ticket — the working case is *"we were
charged twice"* — investigates it through MCP tools, retrieves the applicable
company policy, and proposes an action. When the proposed action moves money,
the run stops and waits for a human to approve it before anything executes.
The model does the legwork; deterministic code decides; humans keep sole,
verifiable authority over money-moving steps.

## Design

- **Five deterministic gates** sit between the model's proposal and any
  external write: schema validation, tool-registry lookup, static permission
  lookup, a policy engine (risk classification, amount ceilings, idempotency
  pre-check), and the approval gate. Neither the model nor retrieved text can
  influence them.
- **Human approval for money-moving actions.** A proposed refund parks the
  run; the arguments are snapshotted at park time, so the approver signs off on
  byte-for-byte what will execute; a second decision on the same request is
  refused.
- **Exactly-once execution.** Every high-risk write carries an idempotency
  key; retries, concurrent submissions and worker restarts replay the original
  outcome instead of charging twice.
- **Append-only audit trace.** Every model call, tool call and retrieval is
  recorded — auditable, not re-executable.

## Verified with evidence

- The golden path runs end to end: ticket → investigation → policy → proposed
  action → human approval → exactly-once execution.
- Under a real model (DeepSeek V4.1-Flash via an OpenAI-compatible gateway),
  the safety gate held across a 15-case batch that included prompt-injection
  attempts: **zero unsafe executions**. In the injection cases the model did
  follow the injected instruction — and the deterministic gates contained it;
  the claim is containment, not prompt immunity.
- Argument validity was **1.000**: every proposed tool call passed schema
  validation (102/102 on the held-out set). The gap, where it exists, is
  tool-set judgment, not malformed calls.
- Two independent install walkthroughs passed on the released artifacts (a
  16-point headless checklist plus a port-configuration recheck); evidence is
  archived in `docs/evidence/`.
- **Zero known vulnerabilities** across all three dependency surfaces
  (pip-audit on the Python runtime set, npm audit on web and desktop), per the
  2026-11-15 dependency audit.
- CI is green on the tagged release (16/16 jobs).

## Honest status

Tool-selection accuracy with a real model is the open gap: four
development-set runs with DeepSeek V4.1-Flash scored 3/15, 2/15, 2/15 and
3/15, and a MiniMax-M3.1-Flash re-test scored 1/15 twice — all against the
project's own ≥10/15 acceptance gate, none met; the project does not claim
real-model acceptance. The demo video and screenshots run a **scripted fake model
provider, disclosed on screen**; the dashboard, API, worker, database, approval
gate, idempotent execution and audit trail in that material are real. All
systems are in-repo mocks, the refund is simulated, and no money moves. The
project's own acceptance scorecard stands at 79/100; the remaining gap is the
real-model tool-selection gate.

## Who it is for

Teams evaluating how to engineer LLM agents for high-risk operational
workflows: anywhere an AI proposal must pass deterministic safety gates, wait
in a human approval queue, and execute exactly once under an audit trail. The
pattern generalizes beyond refunds to any money- or state-moving action.
OpsPilot is an open-source, independently built engineering case study — not a
customer deployment.

**Contact:** [name / email — placeholder]

**Evidence:**
[Repository](https://github.com/gg320324492-lgtm/OpsPilot) ·
[Release v0.2.0](https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.2.0) ·
[Case study](https://github.com/gg320324492-lgtm/OpsPilot/blob/main/docs/case-study-duplicate-charge-en.md) ·
[Real-model eval reports](https://github.com/gg320324492-lgtm/OpsPilot/blob/main/docs/m3-02-dev-two-runs-2026-11-15.md) ·
[Showcase (with disclosure)](https://github.com/gg320324492-lgtm/OpsPilot/blob/main/docs/showcase.md) ·
[Acceptance scorecard](https://github.com/gg320324492-lgtm/OpsPilot/blob/main/docs/portfolio-acceptance-2026-11-15.md)
