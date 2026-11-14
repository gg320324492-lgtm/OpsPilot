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
I build AI ops agents: the model does the legwork, a human keeps the final say. My open-source OpsPilot (FastAPI, Next.js, pgvector, MCP) handles a duplicate-charge ticket end to end: policy retrieval, three MCP servers, five gates and a human-approval step before any money moves. The demo refund executes exactly once under an idempotency key, and CI enforces that every high-risk write has a matching human approval: zero violations across 1,365 tests. Systems are mocks; the refund is simulated. I take on such prototypes, cited RAG assistants, and MCP integrations with approval backends.
```

**Length: 593 characters.**

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
- The capability wording here follows the bounded claims fixed in
  [`docs/claims-audit-2026-11-15.md`](claims-audit-2026-11-15.md): trace is
  *recorded and auditable*, writes replay by idempotency key — do not reintroduce
  a bare "fully replayable" claim.
- Security posture is single-operator for now; roles/approval spheres are
  explicit future work ([`docs/limitations.md`](limitations.md) §1).
