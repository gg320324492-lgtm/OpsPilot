# Service offerings — what I can take on (Phase 3 M2-09)

Three kinds of engagements I offer, each with what you (the client) need to
provide, what I deliver, how we accept the work, and what is explicitly out of
scope. **No timelines or ROI figures are promised here** — those are quoted per
project after scoping. **Integration into your real production system is a
separate scoping exercise**, not part of the base offering below.

These offerings are backed by my open-source project
[OpsPilot](../README.md) — an AI operations agent with a human-approval gate,
documented in [`docs/case-study-duplicate-charge-en.md`](case-study-duplicate-charge-en.md)
with its limits stated plainly in [`docs/limitations.md`](limitations.md).

---

## 1. AI customer-service / operations workflow prototype

A working prototype of an LLM agent that handles a repetitive support or
operations workflow (ticket triage, policy lookup, draft decisions, structured
escalation) with deterministic gates between the model and any consequential
action — the pattern proven in OpsPilot's duplicate-charge run.

**You provide:**

- 2–5 representative real workflow cases (anonymised is fine) including the
  happy path and at least one exception case
- Policy/process documents the agent should consult (PDF/HTML/Markdown)
- Descriptions of the systems involved, ideally with API docs or sample
  responses; sandbox credentials if a real read-only integration is wanted
- The decision rule you want enforced (e.g. "nothing irreversible without a
  human sign-off")

**You get:**

- A running prototype: API service + minimal web UI, state-machine agent with
  step-by-step trace
- A gating layer: schema validation, tool allow-list, policy/risk checks, and a
  human-approval step before any sensitive action
- Seed data and a scripted demo path, plus a written handover doc

**Acceptance:**

- Your sample cases run end to end in the prototype; outputs match the
  decision rule on the agreed test cases
- Every sensitive action in the demo is blocked until an approval is recorded,
  verified by an automated test you can re-run
- A walkthrough session where you drive the demo yourself

**Excluded:** production deployment, SSO/enterprise auth, load testing,
migration of your live data, and guarantees about model-vendor pricing or
accuracy on data we have not tested against.

---

## 2. Knowledge-base / RAG assistant with source citations

A retrieval-augmented assistant over your documents that always answers with
citations to the source chunks it used, so your team can check every claim.
OpsPilot uses PostgreSQL + pgvector for this; the same pattern adapts to other
stores.

**You provide:**

- The document corpus (policy docs, manuals, FAQs, tickets) and permission to
  process them
- 10–20 real questions with expected answers or source documents, used as the
  acceptance set
- Which LLM provider/model you want to use (or want a provider-agnostic setup)

**You get:**

- Ingestion pipeline for your documents with chunking and metadata
- A retrieval + answer service (API + small chat UI) that returns answers with
  per-claim source references and an "insufficient evidence" path instead of
  guessing
- An evaluation report on your acceptance set: hit rate, citation accuracy,
  failure examples
- Handover doc covering how to re-ingest and re-evaluate

**Acceptance:**

- On your 10–20 question acceptance set, answers cite the sources you expect
  (agreed threshold, e.g. ≥ 80% correct citations)
- Questions outside the corpus get a refusal/escalation, not a fabricated
  answer — verified on agreed probe questions
- You can add a new document and re-run evaluation yourself

**Excluded:** content authoring for your domain, multilingual OCR of
poorly-scanned archives (quoted separately if feasible), fine-tuning of
foundation models, and any claim of "eliminates hallucination" — the design
goal is *cited, checkable* answers with honest refusal, not perfection.

---

## 3. MCP tool integration with a human-approval backend

Expose your internal tools/APIs to LLM agents safely using the Model Context
Protocol (MCP): I build the MCP server wrappers around your systems and the
approval/audit backend that keeps a human in command of every consequential
call — the architecture at the core of OpsPilot (three MCP servers, a
`ToolGateway` contract, and a persisted approval gate with a full audit trail).

**You provide:**

- API documentation or OpenAPI specs for the tools to expose
- Sandbox/test accounts and non-production data
- Your risk policy: which actions are read-only, which need approval, any
  amount/status limits
- A named approver (or approver group) for the demo workflow

**You get:**

- MCP servers for your tools following a written tool contract (names, schemas,
  error semantics, idempotency behaviour)
- An approval backend: approval requests are persisted, expire, and are the
  only path to execute high-risk calls; every call lands in an append-only
  audit log
- Integration tests proving: no high-risk call executes without a matching
  approval; replaying the same idempotency key does not duplicate effects
- Documentation of the tool contracts and the operator runbook

**Acceptance:**

- Automated tests demonstrate the invariant "every high-risk write has a
  matching approval" on your tool set
- A simulated end-to-end incident (e.g. duplicate transaction) flows
  investigate → propose → approve → execute exactly once → audit trail
- You can add one new tool to the registry yourself following the documented
  contract

**Excluded:** production go-live, penetration testing or formal security
certification, multi-role approval chains and spending-authority matrices
(designable later, quoted separately — see the single-operator caveat in
[`docs/limitations.md`](limitations.md) §1), and integration into live
customer-facing systems.

---

## Commercial notes

- **Timelines:** not promised here. Each engagement starts with a short scoping
  pass against your materials, then a written estimate.
- **Business outcomes:** I do not promise revenue, cost-saving or accuracy
  numbers in advance; the acceptance criteria above are the contract.
- **Real-system integration:** moving from sandbox/prototype to your production
  systems is **a separate project to be evaluated on its own** — it adds auth,
  data handling, deployment and compliance work that the base offerings above
  deliberately exclude.
- **Working model:** code is delivered in a repository you own, with tests and
  docs, so your team can take it over.
