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

**Model responses are stored unredacted by default.** The `response` step's
`output.body` holds the composed reply in full, which may include customer PII
echoed from the ticket and from CRM tool results. This is a deliberate debugging
affordance for Phase 1 and it is a genuine privacy liability. `STORE_FULL_PROMPTS=false`
now withholds it (the value is replaced with an explicit marker; every other field
survives), so a deployment that does not want it has a switch — but the *default*
is still to store it. Note that Phase 1 records the model's **response**, not its
prompt: no step writes the prompt text, so despite the setting's name there is
less here than that name suggests. Field-level redaction of what remains, and a
retention policy, is Phase 3.

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

**One worker, and the concurrency claim is tested at the predicate and at the
lock — but the end-to-end two-worker guarantee does not hold.** The claim query
uses `FOR UPDATE SKIP LOCKED` on Postgres
(`repositories.py::SqlRunStore.claim_next`; SQLite has no equivalent, so the
same statement runs there without the locking clause — ADR-0004). The claim
*predicate* is tested on SQLite: `tests/integration/test_worker_claim.py` drives
`claim_next` and `drain_once` and asserts a `RECEIVED` run is claimed, a
`WAITING_APPROVAL` run is **not**, and boot preserves the states it must. The
**locking clause** is now tested too, against a real PostgreSQL:
`tests/integration/test_worker_claim_postgres.py` (`@pytest.mark.postgres`, run
by the `verify` CI job and asserted *PASSED* there) asserts the two properties
the clause actually has — a claim whose transaction **overlaps** another's takes
a *different* row rather than blocking on the locked one, and a fully-overlapping
claim of a single locked run gets `None`. An earlier revision of this section
named a `test_two_workers_do_not_claim_the_same_run` that did not exist; the test
that does exist is named above.

What that test deliberately does **not** claim is the property ADR-0005 and
`docs/architecture.md` advertise — that two workers on one database cannot claim
the same run, "scaling out is adding replicas". They cannot, as implemented,
because `claim_next` holds its row lock only for the duration of its own
`SELECT`: it opens a transaction, reads the oldest claimable row, returns a
domain object, and commits. Nothing is held across the run's drive, and the
claim does not change the row's status. The lock therefore excludes another
claim only while a claim transaction is *open*; two workers whose polls do not
overlap both receive the same still-`RECEIVED` run, and both drive it. This is
not theoretical: thirty pairs of concurrent `claim_next` calls on one row
returned that row to both, thirty times out of thirty. It is characterised in
code (`test_a_released_claim_is_reclaimable_by_a_second_worker`) and recorded
here rather than papered over. The fix — holding the row (or marking it claimed)
across the drive, which is what the ADR describes — is a production change with
its own decision, and is **Phase 2 C1's open half**, not something a test can
assert into existence. The practical consequence: **run one worker per
database.**

**The claim lease fixes that, and in exchange widens an already-unreportable
window from about a second to five minutes.** The lease (`b9bbd5c`;
`migrations/versions/0002_claim_lease.py`) writes `claimed_until`/`claimed_by`
in the same transaction as the claim read, so a *live* worker's run is not
handed to a second worker — which is the 30/30 above, fixed. What the lease
buys that is not free is that it now decides **when an abandoned run becomes
claimable again**, and it does not change the run's status when it does
(`SqlRunStore.claim_next` sets `claimed_until` and leaves `status` alone). So a
run that has been claimed and not driven
stays `RECEIVED`, and from every surface this repository has, that is
indistinguishable from a run nothing ever claimed:

- the boot sweep deliberately skips `RECEIVED` (`db.py:150`,
  `mark_interrupted_runs`), so a worker restart does not clear the lease;
- only a worker *driving* the run renews it (`worker/loop.py::_renew_lease_while_driving`),
  so a claimer that died stops renewing and the run simply waits the lease out.

Measured on a live stack against a healthy worker (`Up 31 seconds`, boot line
printed, 0 restarts), a `RECEIVED` run carrying an unexpired lease went
unclaimed for the full **300 s** (`WORKER_CLAIM_LEASE_SECONDS`,
`docker-compose.yml`), and the page rendered `Status: Received`, `Started: —`,
`0 steps, as the server ordered them`, `No steps recorded yet.` — character for
character what a run nothing claimed renders as, and the exact snapshot CI run
38202887580 could not explain. Before the lease that window was about a second,
because a claim that wrote nothing left the row for the very next poll.

**The default is still defensible, and the default did not move.** Recovery is
automatic — the lease expires, `claim_next`'s predicate admits the row again
(its `claimed_until < now` clause), the next poll takes it — and that is why the
boundary ADR-0005 describes ("a worker that dies leaves a lease that expires,
and the run becomes claimable again") is true. It is also precisely what makes
the symptom unreportable meanwhile: recovery is silent, and it arrives up to one
lease period late. **Lowering the default is not available.** It has to exceed
a run's p95 drive duration, measured at 276.7 s on the live eval
(`docs/milestones.md` §M8), or a long run loses its lease mid-drive and a second
worker drives the same refund — which is a worse outcome than a slow one, and
276.7 s is already larger than any detection window an operator would accept.
The two requirements are incompatible, so the number is at its floor.

**What is now bounded is *detecting* it, which is what the paragraph above named
as the missing half.** `GET /api/runs/{id}` carries a `claim_lease` object
(`docs/api-contract.md` §3): `unclaimed`, `held` or `expired`, carrying the
holder's `WORKER_ID` and the deadline. The dashboard reads it, so the run-detail
page now says *"A worker (worker-compose) claimed this run and has not written a
step yet. The claim is held until … and is renewed while the worker keeps
driving; if it is not renewed, the run becomes claimable again then."* in place
of the sentence it used to print unconditionally — *"the worker has not claimed
it yet"* — which was false for five minutes at a time. The state is computed
server-side rather than left to the client, so a dashboard cannot forget to.

**What is still not bounded is the recovery.** Nothing announces that a lapsed
lease has been taken up again: the page says a run is *held* and that the hold
has a deadline, not when the run starts moving, because nothing will. So a run
at `RECEIVED` for longer than `WORKER_POLL_INTERVAL` is still not evidence of a
busy worker. What changed is that an operator can now rule the *dead-worker* and
*claimed-but-stalled* cases in or out from the page instead of guessing — a run
with no lease has not been claimed, and a run whose lease has expired says so.

**No health checking of MCP servers at startup.** A dead billing server is
discovered when a run tries to use it, not when the worker boots.

**A hard-killed worker orphans its MCP child processes.** Under the default
`MCP_TRANSPORT=inprocess` this section does not apply — there is no child
process. Under `MCP_TRANSPORT=stdio` the worker spawns up to three server
children, and it reaps them on a *clean* shutdown: `SIGINT`/`SIGTERM` set a
stop flag, the poll loop drains, and the `finally` that closes the gateway
terminates each child. `SIGKILL`, a container OOM-kill, or a host power loss
bypass all of that — the finally never runs, the children are reparented to the
init process, and they keep running, holding their pipes open with nobody
reading them. Nothing in this codebase detects that: there is no parent-death
guard (`PR_SET_PDEATHSIG` on Linux, a Job Object on Windows), and adding one is
a cross-platform design change rather than a fix that belongs in a limitations
entry.

*Operator mitigation:* treat an ungraceful worker death as a reason to restart
the whole container, not just the worker process — `docker compose restart
worker` reaps the orphans as a side effect of the runtime replacing the
container's process tree, whereas restarting the process in place does not. If
you need the guarantee rather than the habit, give the deployment a process
supervisor that kills the whole process group on worker exit
(`docker stop`, a systemd `KillMode=control-group`, or a `prctl(PR_SET_PDEATHSIG)`
wrapper). Phase 2, if it is done at all, should be a parent-death guard rather
than a scan, because a scan cannot distinguish this worker's children from an
operator running a server by hand.

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
than derived from a corpus. Every number quoted in the README comes from a run
whose *model* provider was a real endpoint but whose *embeddings* were this local
lexical embedder — `evals/results/2026-10-11T15-39-42.json` records
`embedding_provider: "local"` — so that run's retrieval rows measure the lexical
retriever, not a provider's. Each run records the config it ran under alongside
its results, which is what makes that distinction checkable instead of a claim
taken on trust.

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

**The SQLite vector store does not survive a second process, and SQLite is not a
supported runtime configuration.** On SQLite `build_vector_store` returns
`InMemoryVectorStore`, which holds the chunk *embeddings* in process memory and
writes the `knowledge_chunks` rows with `embedding` NULL. A real deployment runs
two processes — the API performs `POST /api/knowledge/reindex`, the worker
retrieves — so reindexing loads vectors into the API's memory, the worker's
memory is empty, and retrieval returns **zero hits on every SQLite deployment**.
Measured on a real worker and a real API over an indexed corpus (17 documents, 98
chunks): classification correct (`duplicate_charge`, 0.93), retrieval `count: 0`,
no tool called, no refund proposed, no approval requested — the run abstains,
escalates, and the reply claims a refund that never happened. This is *not* a bug
to fix: per ADR-0004, production is PostgreSQL + pgvector and SQLite is a
tests-only path. The test suite could not see it because the golden harness
builds the API and the worker in one process, sharing a store a deployment does
not share. **Use PostgreSQL for anything that runs as more than one process.**

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

**The fake provider's numbers are not results.** `__main__.py` refuses to print a
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

**One locale ships as a translation; there is no locale negotiation.** Corrected
2026-10-14: this bullet previously read "No i18n infrastructure. The dashboard is
English-only," which stopped being true at `651bcef`. What exists now: an
English source catalogue, a `zh-CN` catalogue of **330 strings** across 19
namespaces (`web/src/i18n/dictionaries/zh-CN.json`), an `httpOnly SameSite=Lax`
cookie read server-side, and a switcher in the nav. English remains what a fresh
clone renders, with no configuration (D8). What is still absent is the half of
internationalisation that is usually assumed by the phrase: **there is no
`Accept-Language` negotiation, by deliberate decision** — the guide recommends
it for a public website, and this decides whether money moves on a
loopback-bound dashboard, so it is an explicit operator choice rather than a
default. One translation is not a localisation programme; `docs/phase2-plan.md`
§P2-F scopes the remaining languages as a product decision this project has not
made. Covered by 22 `web-e2e` cases across `i18n.spec.ts` and
`i18n-catalogue.spec.ts`.

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

## 8. Documented but not implemented

The repository has repeatedly shipped a small, specific defect class: a
capability described in a document — a README diagram, a `.env.example` line, a
limitations claim — that no code performs or no test covers. Each instance was
found late (three in M2–M5, more in M9's Definition of Done audit) and each is
the same failure in a different costume: a claim that reads as evidence of
something it is not. They are listed here, in one place, because the pattern is
only visible when the instances are gathered rather than scattered through a
milestone log.

**Remaining — documented, not implemented:**

1. **`issues.create` is not on the golden path** — recorded boundary, operator
   decision 2026-10-09 (D19a in `docs/milestones.md`). The tool is real, is
   `SAFE_WRITE`, and is exercised in isolation (`test_mcp_gateway.py`,
   `test_gates.py`, `test_policies.py`). The committed `duplicate_charge` fixture
   the golden path replays proposes no `issues.create` call, so the run does not
   create a ticket; the README's golden-path trace and `docs/milestones.md` §M6
   no longer draw that step. The capability is **not done, by decision rather
   than by neglect**: whether the workflow **should** file an issue is still
   open, and adding it to the golden path remains a possible Phase 2 product
   decision.
2. **The two-worker guarantee is untested at the level it is claimed — now
   partly closed.** `tests/integration/test_worker_claim.py` covers the claim
   *predicate* on SQLite. `tests/integration/test_worker_claim_postgres.py` now
   covers the **locking clause** against a real PostgreSQL: two overlapping
   claimants on one locked run take different rows, and a claim of a single
   locked run is withheld. What remains open is the *end-to-end* promise — two
   workers, one database, no run driven twice — which the shipped `claim_next`
   does not provide, because its lock closes with its own `SELECT` and it does not
   change the run's status. See §2 for the measured statement (30/30 concurrent
   pairs both received the one row) and the fix that is C1's open half. The test
   that closes the clause half is named above; the guarantee the ADR advertises is
   still not what the code does.
3. **`LangGraphOrchestrator` is implemented by nothing, and no contract test can
   exist yet.** This item previously read "Neither orchestrator is implemented".
   `LinearOrchestrator` is now real: `_pump` builds a `StageDriver` (the stage
   logic extracted from the old inline loop into one function per `StepKind`)
   binds it to the orchestrator and loops `orchestrator.step`, so `run_loop` no
   longer discards its orchestrator. `tests/agent/test_linear_orchestrator_drives.py`
   proves it with a spy that records the stage sequence, and a raising
   orchestrator now stops the pump. What remains a capability gap, with no
   decision behind it, is `LangGraphOrchestrator`: both `LangGraphOrchestrator.step()`
   and `.build_graph()` raise `NotImplementedError`. No test imports
   `langgraph_adapter`, and no test compares the two executors' step results —
   such a test cannot be written while one of the two raises on every method it
   would compare. `langgraph` is declared in `pyproject.toml` and imported by no
   module in the project. The `LinearOrchestrator` row now sits `- [x]` in
   `docs/milestones.md` §M4 with that evidence; the `LangGraphOrchestrator` row
   stays `- [ ]`.

**Fixed, and no longer in this list** (kept as a record so the list's history is
legible):

- **Two gate-1 schemas rejected the arguments the contract documents.** Found on
  2026-10-15 while chasing the live recording's `crm.get_customer` failure.
  `docs/mcp-contracts.md` §1 offers `customer_email` **or** `customer_id` for
  `crm.get_customer`, and `mcp_servers/crm/server.py` implements both — but all
  three CRM lookups shared `CustomerLookupArgs`, which declared only
  `customer_id` under `extra="forbid"`, so the contract's own first example was
  refused at gate 1. `issues.create` had the same shape: the contract and server
  take `{title, description, priority, labels}`, gate 1 declared
  `{title, body}` — a field the tool has no parameter for, and three missing.
  This is the defect `TransactionListArgs` records having "survived four
  milestones" (*no argument set satisfied both sides*) in two more tools. It
  escaped the existing guard because
  `test_every_gate_1_schema_is_accepted_by_the_real_tool` only pushes schema
  fields *toward* the server; a contract parameter the schema omits is never
  sent, so nothing checks it. `tests/integration/test_mcp_contract_arguments.py`
  is the reverse direction, reading the parameters out of the contract document.
  Fixing it exposed a third leak: unset optional fields reached the MCP server as
  `null`, so an email-only lookup arrived as *both* identifiers — now
  `validate_tool_call` dumps with `exclude_none=True`. **It does not change the
  0.471 argument-validity figure**: the recorded call sent `email`, where the
  contract says `customer_email`, and that argument is wrong under either schema.
  What it fixes is the case where a model reads the contract correctly and is
  refused anyway.

- `MaxStepsExceeded` killing the worker — a run that spent its step budget
  raised out of `drain_once`, and `poll_forever`'s `while True` had no handler,
  so the exception took the worker down and every run queued behind it waited
  for a restart. It was invisible in the compose deployment because the worker
  carries `restart: unless-stopped`: the container came back and resumed
  polling, which hid the defect while charging a full worker restart per
  over-budget run — and made a browser test look flaky, since
  `web/e2e/failure-states.spec.ts` drives a run to `max_steps_exceeded` on
  purpose. Now suppressed alongside `MCPUnavailable`, which was already
  suppressed for the same reason: the runtime writes `FAILED` before raising, so
  the exception arrives with no work left to do. A programming error still
  escapes, by design, and `tests/agent/test_worker_survives_max_steps.py` pins
  both halves.
- `knowledge_injection` — the `safety.jsonl` injection cases are now covered by
  `tests/security/test_prompt_injection.py` against a real retrieved injection.
- `/ready` — the readiness probe now reports unhealthy when the database is
  unreachable, rather than healthy on any reachable process.
- `MCP_*_COMMAND` — the three command settings are read on the production path
  (`worker/__main__.py::build_worker_gateway` →
  `adapters/tools/mcp_gateway.py::build_stdio_servers_from_settings`) and spawn
  real child processes through `adapters/tools/mcp_stdio.py`. `MCP_TRANSPORT`
  selects between that and the existing in-process path, which stays the default.
  `tests/integration/test_mcp_stdio_transport.py` proves it by calling
  `crm.get_customer` through a live subprocess and by checking that a server
  process really appeared and was really reaped.
- `knowledge.search` — `architecture.md` §7 advertises it as an automatic `READ`
  tool, and `adapters/wiring.py::RetrievalStack` has bound a `KnowledgeTool`
  since retrieval landed, but no code on the dispatch site could reach it. The
  runtime's single `gateway.call_tool` call site was handed a bare
  `MCPToolGateway` at both assembly points, and `mcp_gateway.py::_server_for`
  returns `None` for any server outside `{crm, billing, issues}` — so a server of
  `"internal"`, as `domain/tools.py` registers it, came back
  `server_unavailable` every time. `adapters/tools/composite_gateway.py::CompositeToolGateway`
  now routes on `TOOL_REGISTRY[name].server` at call time, and
  `tests/integration/test_composite_gateway.py` proves it at both wiring points.
- The containerised `web` service's static assets — `web/Dockerfile` copied
  `public/` and `.next/static/` to `/app/web/public` and
  `/app/web/.next/static`, and the standalone server reads neither. It `chdir`s
  to its own directory and resolves both against *it* (`base-server.js`:
  `distDir = join(dir, config.distDir)`; `next-server.js`: `getPublicDir() =
  join(dir, 'public')`), so with `server.js` at `/app/server.js` it wanted
  `/app/public` and `/app/.next/static`. Every JavaScript bundle and stylesheet
  404'd.

  This is the entry that best explains why the list exists, because **the
  failure was shaped exactly like success at every level the suite could see.**
  `GET /` returned 200, so the container reported `Healthy` (its healthcheck is
  a `fetch` of the root document). The document rendered, so
  `web-service.spec.ts` passed — every assertion in it is about server-rendered
  HTML and server-side API reads. And `web-e2e` never exercised the container's
  assets at all: `golden-path.spec.ts` runs the dashboard on the host, where
  `e2e/stack.ts` assembles the standalone tree by hand (it copies those same two
  directories in before spawning `server.js`), so the host path had always been
  correct. Only the browser knew: no stylesheet, no hydration, and Reindex,
  Submit and Approve silently doing nothing.

  Both destinations are now `./public` and `./.next/static`, which is what Next's
  own documentation prescribes (`cp -r public .next/standalone/ && cp -r
  .next/static .next/standalone/.next/`). Verified against the running container
  rather than the HTML alone: all 8 scripts and stylesheets the page requests
  return 200 with real bodies, the Tailwind custom properties resolve, the
  Reindex click POSTs 200 and repaints the table, and Submit ticket returns 201.
  Rebuilding the same image with the old destinations reproduces the defect
  exactly — `GET /` 200, all 8 bundles 404 — which is what makes the fix a
  measurement rather than an assertion.

  **What changed beyond the two lines is that CI can now see this class of
  failure.** `web/e2e/web-service.spec.ts` drives the real container and asserts
  that every script and stylesheet the page requests is served. It is red on the
  old image and green on the new one. It is an asset check rather than a
  click-through on purpose: a click would duplicate the golden path's coverage
  and depend on a seeded knowledge directory, whereas the browser fetches every
  `<script src>` before any of it can run, so the assertion still holds on the
  inert page. The broader lesson is the one §8 keeps teaching — a claim about
  running belongs here until something actually runs it, and a check that only
  asks "did the server answer?" cannot tell a working dashboard from a blank one
  with the HTML still attached.
- `evals/datasets/fixtures/` — `docs/risks.md` R2 claimed the fake provider "is
  a *replay* of recorded real interactions, not invented scripts:
  `evals/datasets/fixtures/` holds recorded provider responses for the golden
  path, captured once from a real run and committed," and the same sentence
  stood in R8, `docs/agent-state-machine.md:248`, `README.md:551` and
  `evals/README.md`. Both committed fixtures carry `"recorded": false` and the
  note "HAND-WRITTEN PLACEHOLDER, not a recording. Regenerate with
  record_fixture() against a real provider"; `grep -c request_hash` returns **0**
  in each. They are hand-written, which is the "invented scripts" the sentence
  denied.

  The mechanism makes the claim worse rather than merely unmet.
  `FakeModelProvider._call_for` (`adapters/models/fake.py:148`) prefers scenario
  mode: given a scenario name it returns calls by `method`, in file order, off a
  per-method cursor, without ever inspecting the request. `request_hash` is read
  only on the no-scenario fallback path and only to name the
  `UnmatchedFixtureError` raised when the script runs out. So a fixture with no
  `request_hash` — which is both of them — is served strictly by position and
  never sees what the model actually sent: less protection than R2 claimed, not
  more. `record_fixture()` exists (`fake.py:275`) and has never been run against
  a real provider, so no recording is committed. All four documents now say so.

  **This entry is here because the claims were corrected, not because the
  fixtures became recordings.** They did not, and R2's underlying risk — a
  scripted provider proving nothing about a real model — stands closer to open
  than R2 claimed.

  **Update, 2026-10-15.** The half of this that needs no API key is now closed:
  `tests/unit/test_record_fixture_roundtrip.py` drives `record_fixture()` against
  a stub provider and replays its output through `FakeModelProvider`, proving the
  round trip — the recorded value returns, a prompt the recorder never saw is
  refused, and the written file carries a `request_hash` that matches
  `hash_request(system, prompt)`. Mutation-verified three ways: a wrong hash, a
  forced `recorded: false`, and an emptied provenance copy each break a test. So
  the recorder works and what it writes is replayable; **the responses are still
  hand-written**, and the only step left is the one run against a real provider
  that needs the key. One test in that file fails if anyone flips a committed
  fixture's `recorded` flag without having made a recording, which is the failure
  mode this entry exists to prevent.

  **The real recording was attempted on 2026-10-15 and could not be made.** The
  key is valid and the endpoint is reachable — the request is authenticated and
  answered — but every call returns `HTTP 429`, MiniMax error code `2056`:
  *"已达到 Token Plan 用量上限"* (Token Plan quota exhausted). Three attempts six
  seconds apart returned the same error, so it is a spent quota rather than a
  burst limit, and it is the same 429 that interrupted two tool calls in this
  session. Nothing about the recorder is implicated: it was never given a
  response to record. **A seventh test now pins what happens when a recording
  dies partway** — `test_a_recording_that_dies_partway_writes_no_file`, driven by
  a stub that answers once and then raises — because that is the shape this
  attempt would have taken against a metered endpoint. `record_fixture` collects
  every response before it opens the file, so a mid-run failure discards the
  answers already collected and writes nothing; mutation-verified by moving the
  write inside the loop, which fails the test. The fixtures therefore remain
  hand-written, and the M4 criterion stays unticked, for a reason that is now
  recorded rather than assumed.

The rule the list enforces: a claim about running belongs in this section until
something actually runs it.

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
