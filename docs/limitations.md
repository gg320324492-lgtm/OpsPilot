# Honest Limitations — Phase 1

This file exists because a portfolio project's credibility comes from what it
refuses to overclaim. Everything below is true of OpsPilot as shipped at the end
of Phase 1. Anything on this list that a reader would otherwise assume is
working is a bug in the README, not in this file.

---

## 1. Security

**Single operator, no separation of duties.** One bearer token
(`OPSPILOT_OPERATOR_TOKEN`) authenticates every endpoint. The same credential can
create a ticket, approve a refund and reject one. There is no notion of who
approved what beyond the free-text `decided_by` field, which is not
authenticated. A real deployment needs roles, an approval sphere ("this operator
may approve up to $500"), and separation between the actor proposing and the
actor approving. **Phase 3.**

**No prompt-injection red-team.** The structural defence — permission gates that
retrieved text cannot influence — is tested and holds. The *prompt-level* defence
is a system-prompt instruction not to follow instructions found in retrieved
documents, which is a mitigation, not a guarantee. A sufficiently clever
injection may well steer the model's *proposal*. It cannot make that proposal
execute without a human, which is the property the project actually claims.

**Approval arguments are snapshotted but not cryptographically bound.** The
approver sees `arguments_snapshot`; execution reads `tool_calls.arguments`. A
test asserts they are equal and that the row cannot be mutated between approval
and execution, but there is no signature or hash. A bug that mutated the column
would be caught by the test, not by a cryptographic guarantee.

**Secrets live in `.env`.** No vault, no rotation, no per-environment key
management. `.env` is gitignored and `.env.example` has empty values; CI uses the
fake provider and needs no keys at all.

**Prompt bodies are stored unredacted.** `agent_steps.input` and `output` hold
the full prompt and response, which may include customer PII from the ticket and
from CRM tool results. This is a deliberate debugging affordance for Phase 1 and
it is a genuine privacy liability. Phase 3 does field-level redaction with a
documented retention policy.

**Rate limiting and abuse controls: absent.** There is no limit on tickets per
minute, no cap on tokens per hour, and no circuit breaker on the model provider.
A malformed client could run up a bill.

## 2. Reliability

**Interrupted runs are not resumed.** A worker restart marks any run in a
mid-flight state as `FAILED(interrupted)`. Its work is lost. Runs in
`WAITING_APPROVAL` survive, because they are parked in the database rather than
held in the process. Automatic resume needs a step journal and needs every tool
call to be safely replayable, which is Phase 2 work.

**No retries, no fallback model, no timeouts with backoff.** A model call that
fails fails the run. An MCP server that is down fails the run. There is a single
attempt and the failure is recorded honestly. This makes the eval numbers clean
and the demo fragile — both are intentional at this stage.

**One worker, and the concurrency test only runs in CI.** The claim query uses
`FOR UPDATE SKIP LOCKED`, which is correct on Postgres, but SQLite does not
support it, so `test_two_workers_do_not_claim_the_same_run` is marked
`@pytest.mark.postgres` and skipped locally. If that test is red, it is red only
in CI.

**No health checking of MCP servers at startup.** A dead billing server is
discovered when a run tries to use it, not when the worker boots.

**The step budget is the only loop guard.** 24 plan/execute rounds. A run that
loops more cheaply than it calls tools (e.g. re-retrieving without acting) is not
otherwise bounded, and cost is not capped.

## 3. Retrieval

**Top-k cosine similarity, nothing more.** No reranker, no hybrid BM25, no
query rewriting, no MMR diversification. On a 15–30 document corpus this is
adequate; it is not adequate at 3,000 documents and the design does not pretend
it is.

**Chunking is heading-aware fixed-size, ~500 tokens with overlap.** Tables and
long procedures split awkwardly. Nothing in Phase 1 detects a bad split.

**The embedding model is whatever `EMBEDDING_PROVIDER` selects.** The default
`local` embedder is a **lexical** scorer, not a semantic one: a bag of
stopword-free, lightly-stemmed words, weighted sublinearly by frequency and
hashed into a fixed-width vector. It is a real retriever on this corpus —
measured over `evals/datasets/retrieval.jsonl` it reaches **Recall@5 = 14/15**
and **Recall@10 = 15/15**, and its scores separate answerable from unanswerable
questions — which is what lets a fresh clone and the CI suite run the golden
path with no API key. What it is not is semantic: **there is no synonymy**. A
question whose answer is worded entirely differently from the question will not
match it, and the stopword list and the two suffix rules are hand-written rather
than derived from a corpus. Any number quoted in the README comes from a run
that used a real embedding provider, and the run's config is recorded with the
result.

**`RETRIEVAL_MIN_SCORE` is calibrated against the default embedder, and only
against it.** The default is `0.22`, chosen by measuring
`evals/datasets/retrieval.jsonl`: the answerable cases score **≥ 0.2500** and
the `expect_abstention` cases **≤ 0.1844**, so any value in that gap separates
them and `0.22` sits inside it with margin on both sides. Three things follow,
and none of them is comfortable:

1. **The margin is narrow.** 0.1844 → 0.2500 is a band 0.066 wide, on 20 cases.
   A corpus of 20 questions is a smoke test for separation, not a calibration.
2. **The number is meaningless under a different embedder.** A provider's cosine
   and this lexical score are different quantities on different scales; `0.22`
   for one is not `0.22` for the other, and switching `EMBEDDING_PROVIDER`
   without recalibrating gives a threshold that either admits everything or
   refuses everything. `.env.example` says which default suits which provider.
3. **It is still a single global threshold.** Not tuned per query type, not
   calibrated per corpus, and not a probability — it is a number with a comment
   and a measurement behind it, which is better than the previous value (0.35,
   which nothing could reach) and is still a long way from a learned ranker.

**Knowledge is static Markdown re-indexed manually.** `POST /api/knowledge/reindex`
is the only update path; there is no watch, no incremental indexing, no
versioning of document revisions. Citations point at a document's current content,
so a citation made last week may no longer match the text quoted.

## 4. Agent behaviour

**The workflow is optimised for one ticket shape.** Duplicate-charge billing
disputes. Other categories (`account_access`, `technical_issue`) are classified
correctly and then escalate, because there is no workflow behind them. The
classification metric measures the classifier; it does not mean the system
handles those categories.

**No multi-turn conversation.** A ticket is processed once. A customer reply to
the agent's response is a new ticket with no memory of the prior run, beyond
what an operator links manually.

**Planning is single-step and reactive.** The model proposes one action per
round; there is no up-front plan the runtime follows, and no critique step. This
makes the trace legible, which is the Phase 1 goal, and makes the agent less
capable than a planner-based design, which is a Phase 2 question that now has
real traces to design against.

**The number of tool calls on the golden path is the model's choice within
bounds.** The eval's `tool_selection` cases assert the *set* of tools; the order
and the exact count are not asserted and can vary between runs.

## 5. Evaluation

**50–80 cases is a regression detector, not a measurement instrument.** A
reported `0.85` on 20 cases carries a confidence interval wide enough that a
meaningful real change could hide inside it. The README reports counts alongside
scores for this reason.

**No LLM-as-a-judge, so reply quality is unmeasured.** Faithfulness is partially
checked structurally (the reply's citations must be a subset of the run's
retrieved citations). Tone, helpfulness, and appropriateness are not measured at
all.

**Live evals are manual.** `eval-live` runs only on `workflow_dispatch`. There is
no scheduled regression tracking, no stored baseline, and no alert when a metric
moves. Phase 2.

**The fake provider's numbers are not results.** `runner.py` refuses to print a
metric table for `--provider fake` unless `--allow-fake-scores` is passed. The
smoke job in CI is a plumbing check.

**Latency and cost numbers are single-machine, single-region, from one run.**
They are order-of-magnitude figures, not benchmarks, and the README says so.

## 6. Engineering

**No production deployment.** Docker Compose on one host is the target. No
Kubernetes, no managed Postgres, no TLS termination, no reverse proxy, no
backups, no migration rollback story beyond `alembic downgrade`.

**No load testing.** The concurrency model is correct in principle (database-backed
claiming) and untested at any real concurrency.

**Alembic migrations are forward-only in practice.** `downgrade` exists where it
was easy to write and is not exercised by CI.

**No coverage gate on `apps/web`.** The dashboard has type checking and linting
in CI; its component behaviour is verified by hand against the golden path. This
is a real gap and is the reason M7 is scheduled after the workflow it displays.

**One language for code, English; Chinese only in conversation.** No i18n
infrastructure. The dashboard is English-only.

## 7. Product

**The dashboard is operational, not polished.** It is designed to make a trace
readable, which is what a reviewer needs. It is not a product.

**No notification when an approval is pending.** An operator must have the
Approvals page open. There is no email, no Slack, no webhook. Deliberate — email
and Slack integrations are explicitly out of scope — but the consequence is that
a run can sit parked indefinitely in a real deployment.

**Approvals do not expire in practice.** The `expired` status exists in the
schema and a `pending` approval will sit forever. No TTL job is implemented. The
schema is ahead of the behaviour here, and that is stated rather than quietly
assumed.

**One seeded demo dataset.** Ten customers, twenty-odd invoices. Real data has
messier shapes — missing fields, multiple currencies, partial refunds across
periods — and the fake servers return clean records. The agent's argument
validation is exercised against a schema, not against real-world messiness.

---

## What is *not* on this list, and why

Three things a reader might expect to find here, which are deliberately absent:

1. **"The permission model is unverified."** It is verified, by four CI gates
   that count violations and require zero. That is a claim the project can make.
2. **"The refund can be executed twice."** It cannot, through three independent
   layers, tested by calling the MCP server directly. Also a claim that holds.
3. **"Retrieved documents can escalate tool permissions."** They cannot.
   Permissions are static code, compared against a literal in a test.

These three are the project's actual thesis. Everything in sections 1–7 is what
was traded away to get them right in Phase 1 — and knowing *which* trade was made
is the point of writing them down.
