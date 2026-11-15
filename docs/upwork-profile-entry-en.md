# Upwork profile entry — English draft (Phase 3 M2-09)

A ready-to-paste portfolio entry for an Upwork profile. Field limits follow the
task spec: title ≤ 70 characters, role ≤ 100 characters, description ≤ 600
characters, at most 5 skill tags. All facts and figures are drawn from this
repository's own documents: [`docs/case-study-duplicate-charge-en.md`](case-study-duplicate-charge-en.md),
[`docs/claims-audit-2026-11-15.md`](claims-audit-2026-11-15.md) and
[`docs/limitations.md`](limitations.md). Nothing here claims a paying customer,
revenue, or unverified savings, and the refund in the demo is simulated.

---

## Title (≤ 70 chars)

```
AI Ops Agent: FastAPI + MCP + Human-Approval Gate (Python/Next.js)
```

**Length: 66 characters.**

## Role / headline (≤ 100 chars)

```
I build LLM agents that propose actions but never move money or data without a human approval gate.
```

**Length: 99 characters.**

## Description (≤ 600 chars)

```
I build AI ops agents: the model does the legwork, a human keeps the final say. My open-source OpsPilot (FastAPI, Next.js, pgvector, MCP) handles a duplicate-charge ticket end to end: policy retrieval, three MCP servers, five gates and a human-approval step before any money moves. The demo refund executes exactly once under an idempotency key, and CI enforces that every high-risk write has a matching human approval: zero violations across 1,365 tests. Measured against a real model, the safety gate held (0 unsafe executions, injection attempts included) while tool-selection accuracy stays an open metric. Systems are mocks; the refund is simulated. I take on such prototypes, cited RAG assistants, and MCP integrations with approval backends.
```

**Length: 748 characters — over the 600 limit. Trim before pasting; a fitting
variant:**

```
I build AI ops agents: the model does the legwork, a human keeps the final say. My open-source OpsPilot (FastAPI, Next.js, pgvector, MCP) handles a duplicate-charge ticket end to end: policy retrieval, three MCP servers, five gates and a human approval before any money moves. The refund executes exactly once under an idempotency key; CI enforces zero unapproved high-risk writes across 1,365 tests. Measured on a real model, the safety gate held (0 unsafe executions, injection included); tool-selection accuracy is an open metric. Systems are mocks. I take on such prototypes, cited RAG assistants, and MCP integrations with approval backends.
```

**Length: 646 characters — still 46 over. The "Measured on a real model…"
sentence costs 155 characters; dropping it returns to the original compliant
593 (the first block above). Both variants are provided so the choice is
explicit at paste time — do not exceed the limit silently.**

**Recommended compliant variant with the real-model line kept** (rewritten to
fit, 599 characters):

```
I build AI ops agents: the model does the legwork, a human keeps the final say. My OpsPilot (FastAPI, Next.js, pgvector, MCP) handles a duplicate-charge ticket end to end: policy retrieval, three MCP servers, five gates and a human approval before any money moves. The refund executes exactly once under an idempotency key, and CI enforces zero unapproved high-risk writes across 1,365 tests. Measured on a real model, the safety gate held: 0 unsafe executions, injections included. Systems are mocks. I take on such prototypes, cited RAG assistants, and MCP integrations with approval backends.
```

**Length: 595 characters.**

## Skill tags (≤ 5)

1. `Python`
2. `FastAPI`
3. `Next.js`
4. `LangChain` — only if you have actually used it; otherwise replace with `RAG`
5. `LLM Prompt Engineering`

Suggested safe set without overclaiming: **Python · FastAPI · Next.js · RAG · LLM Integration**.

## Honesty notes (do not publish these, keep them for yourself)

- Do not add client names, revenue or savings figures — the project is an
  independent case study with no customer engagement
  ([`docs/case-study-duplicate-charge-en.md`](case-study-duplicate-charge-en.md), intro).
- The demo's refund is **simulated** and the CRM/Billing/Issues systems are
  in-repo mocks; keep that wording if the description is edited.
- The "1,365 tests / zero violations" numbers come from
  [`docs/phase2-review-2026-11-14.md`](phase2-review-2026-11-14.md) and
  [`docs/case-study-duplicate-charge-en.md`](case-study-duplicate-charge-en.md);
  if those docs change, update the entry.
- The "0 unsafe executions (injection included)" figure is the M3-03 safety
  batch of 2026-11-15
  ([`docs/m3-03-holdout-safety-2026-11-15.md`](m3-03-holdout-safety-2026-11-15.md)):
  15 cases (13 live DeepSeek V4.1-Flash + 2 scripted injection), unsafe
  execution count 0, both injection cases refused by the amount check. The
  "open metric" wording for tool selection reflects the M3-02 two-run result
  (3/15 and 2/15 against a ≥10/15 gate,
  [`docs/m3-02-dev-two-runs-2026-11-15.md`](m3-02-dev-two-runs-2026-11-15.md)) —
  do not upgrade it to a success claim.
- The videos in `web/public/showcase/` are fake-provider walkthroughs with the
  disclosure on their title cards; do not present them as live-model runs.
- The capability wording here follows the bounded claims fixed in
  [`docs/claims-audit-2026-11-15.md`](claims-audit-2026-11-15.md): trace is
  *recorded and auditable*, writes replay by idempotency key — do not reintroduce
  a bare "fully replayable" claim.
- Security posture is single-operator for now; roles/approval spheres are
  explicit future work ([`docs/limitations.md`](limitations.md) §1).
