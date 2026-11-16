# Progress archive — Phase 1 — dashboard, evals, CI and the M11/M12 fixes (M7–M12)

> Split from `docs/progress.md` (the append-only log) on 2026-11-16 during
> the repository tidy-up. Content is unchanged; only the file boundary
> moved. The living index is [`docs/progress.md`](../progress.md).

## M7 — Dashboard

**Started:** 2026-10-06

### Acceptance criteria

Written before the code, per the method at the top of this file. Each one is
checkable by looking at the running system, not at a test that asserts the
implementation.

- [ ] `web/` is a Next.js app that starts and serves all six screens:
      Dashboard, Tickets, Runs, Run Detail, Approvals, Knowledge.
- [ ] The API is reachable from the browser. **Today it is not**: `src/opspilot`
      contains no `add_middleware`, no `allow_origins`, no CORS configuration of
      any kind, so a dashboard on `:3000` talking to an API on `:8000` is blocked
      by the browser before a single line of the app runs. The dashboard decides
      whether that is fixed by a CORS policy or by a Next.js rewrite proxy, and
      whichever it picks is documented — a browser-reachable API is an
      acceptance criterion, not a convenience.
- [ ] Run Detail renders the timeline from `GET /api/runs/{id}/trace`, ordered
      by `sequence` **as the server sent it**. The client does not re-sort, and
      does not re-derive `label` or `detail`.
- [ ] The Sources panel renders the run's `Citation` rows, showing `document`,
      `chunk`, `score` and `rank`.
- [ ] The approval card shows tool, arguments, risk level, the model's `reason`
      and the deterministic `risk_explanation`, and the two are **visually
      distinguishable** — an operator must be able to tell at a glance which text
      is untrusted model output and which is deterministic code.
- [ ] Approve and Reject work, and a **second click on an already-decided
      approval surfaces the 409**, not a silent success. A dashboard that hides
      the 409 hides the double-grant that the 409 exists to prevent.
- [ ] `tsc --noEmit` and `eslint` are clean.
- [ ] The golden path is walkable in a browser, start to finish.

### Carried in from M6, and what M7 makes visible

Two documentation-vs-behaviour gaps were left open at M6. M7 is the first
milestone where both become visible on screen, so they are resolved here rather
than carried again:

- **Latency is missing where the dashboard will show it.** `docs/milestones.md`
  §M6 says "the trace shows every step with latency". Measured: exactly **one step
  in twenty-one** carries a `latency_ms` — the `classification` step. The
  `tool_call` steps and the `retrieval` step pass none, so `tool_calls.latency_ms`
  is `null` on all four calls in the golden path. The data exists and is
  discarded: `ToolResult.latency_ms` is measured in `mcp_gateway.py` and
  `ModelResponse.usage.latency_ms` is measured in the providers, but only
  `classification` forwards it to `record_step`. The dashboard's latency column
  would render `—` for four of five steps and teach a reader that the column is
  decoration.
- **`issues.create` is never called**, though the README's golden-path trace and
  §M6 both promise it. M6 chose not to add the step, because adding a tool call
  to make a diagram true is writing the test to pass. M7 cannot fix that either;
  the honest resolution is for the trace to show what actually runs.

### The `EXECUTING` double-approval window

Preserving `EXECUTING` across a restart (the user's M6 decision) means a run
killed between committing `EXECUTING` and committing the tool call's terminal
status resumes and re-proposes the refund. The money still moves **once** — the
deterministic idempotency key `refund:{run_id}:{transaction_id}` blocks the
replay — but `_park_run` creates one approval per *tool call*, and a re-proposal
allocates a **new** `tool_call_id`. So the approvals list can show two pending
cards for one refund.

This is inherent to the design the user chose, not a bug to fix inside M7. It is
listed in `docs/limitations.md` and the dashboard must not hide it: the
Approvals screen is where an operator would otherwise approve the same refund
twice, believing they were looking at two real proposals.

### M7a/b/c — the browser could not reach the API, and the contract was not readable

Three findings, in the order they were found.

**1. The dashboard could not make a single API call.** `src/opspilot` contained no
CORS configuration of any kind — no `add_middleware`, no `allow_origins`, no
match for any of them across all of `src/`. The dashboard on `:3000` and the API
on `:8000` are different origins, so the browser would have blocked every
response before any application code ran.

CORS is now `OPSPILOT_CORS_ORIGINS` (comma-separated, default
`http://localhost:3000`), installed in `create_app`. **A `*` is refused at
construction, not warned about**, including the realistic version of the mistake
(`http://localhost:3000,*`), because Starlette refuses to attach
`Access-Control-Allow-Credentials` to a wildcard anyway — it would have meant a
broken dashboard sitting next to an unbounded read path.

Verified by direct probe rather than by reading the middleware's options:

```
dashboard origin  -> 200 | ACAO: http://localhost:3000
foreign origin    -> 200 | ACAO: None
no token          -> 401
```

The second line is the one worth keeping: **the foreign origin still gets a 200.
The server processes it; the browser refuses to hand the response to the page.**
CORS answers one narrow question — may this page *read* the response — and is
not an authorisation mechanism. `curl` has no CORS at all. The bearer token
remains the only thing that authorises a call, and the most natural misreading of
"we added CORS" is "the API is protected now". That sentence is in the contract
next to the configuration for exactly that reason.

**2. Nine steps in twenty-one had no latency, and the dashboard's column would
have been decoration.** `docs/milestones.md` §M6 requires every step to carry
one. Measured before the fix, exactly one did: `classification`. The value was
measured and then dropped — `ToolResult.latency_ms` in `mcp_gateway.py`,
`usage.latency_ms` in the providers — and only the classification step forwarded
it.

Measured after, on the real golden path, reading `agent_steps` back from the
database rather than trusting a test's assertion:

```
STEPS: 7 of 21 carry latency_ms
  with latency: {classification: 1, retrieval: 1, planning: 4, response: 1}
  NULL        : {state_change: 14}
TOOL CALLS: 4 of 4 carry latency_ms
```

The 14 NULLs are `state_change` and are deliberate: between two run statuses
there is no external work, so timing one measures the speed of a database
UPDATE rather than of the agent. `tests/agent/test_step_latency.py` guards that
exclusion, so a new step type cannot be silently added to it.

`planning` was timed at the call site rather than by changing
`ModelProvider.choose_tool` to return a `ModelResponse` — a four-provider port
change, for a value the trace does not want. The wall time measured where the
call is made is what "how long did this step take the run" means, and for a live
provider it includes the network.

On an idempotent replay the latency is left NULL. Control short-circuits
*before* the dispatch, so there is no duration; `0` would assert an instantaneous
call and be indistinguishable from a real sub-millisecond one, and copying the
prior row's value would attribute another row's dispatch to a row that made none.
The earlier measurement stays on the earlier row, where it is true.

**3. The error contract was invisible to every code generator.**
`docs/api-contract.md` §6 promises one error shape everywhere, and
`api/errors.py` builds every failure through the `ErrorResponse` model — with a
comment claiming this is "so the OpenAPI schema and the runtime body cannot
drift."

**Neither model was in the schema.** They were defined in `api/schemas.py` and
declared by no route, so FastAPI never added them to `components.schemas`. A
comment asserting an invariant is not the invariant.

It was found by the frontend agent, which could not type a single failure
response and hand-wrote one — reintroducing, in TypeScript, exactly the copy the
generator was supposed to prevent. Every guarded router now declares
`ERROR_RESPONSES`, and `tests/integration/test_error_envelope_is_in_the_openapi_document.py`
asks the schema rather than the comment. The hand-written
`web/src/lib/api/error-envelope.ts` is deleted, not kept in sync; the guard that
replaced it asserts the *absence* of hand-written API types, so the next one is
caught when it appears rather than when someone remembers to add a guard.

### A guard that fails for the wrong reason is still a guard that lies

Two this milestone, both caught rather than shipped.

The frontend's drift guard ran the generator, compared, printed "does not match"
— **and exited 0**. It was reporting a failure the way a log line does, not the
way a CI step does. Fixed before it was ever merged.

Mine came from the same failure mode as M6's classification guard: it asserted a
real step type renders a real detail, and reported `a response step rendered an
empty detail`. Not a false alarm this time — a second gap nobody had looked for.
`response` and `planning` both had no renderer, so two rows of every timeline
would have read "Next action proposed" with nothing after it. The guard was
written against real recorded steps, which is the only reason it could see that;
a unit test calling `_detail_for` with a hand-made dict would have passed.

The `state_change` key was wrong in the same file — the reader looked for `to`,
both writers emit `to_status` — and an `or` fallback meant the timeline showed
`to executing` **by accident**, with the correct key never needed. Deleting the
dead branch is only half the fix; the guard now reads the row the writer produced
and requires the reader to find a real `RunStatus` in it, so renaming the key on
either side alone turns the test red instead of turning the timeline to `to None`.

### M7d — the entry point did not exist, and the default provider cannot run

Two defects found by trying to do the last acceptance criterion honestly.

**1. `opspilot-worker` could not start at all.** `pyproject.toml` declares
`opspilot-worker = "opspilot.worker.__main__:main"`, and that `main()` was an M0
stub ending in `raise NotImplementedError` with a docstring saying the wiring
"is completed in M6". M6 shipped; nobody came back. **The shipped entry point
raised on every invocation.**

Nothing noticed, because every claim about the golden path is proven by
`tests/agent/_golden_harness.py`, which assembles the adapters itself and never
touches the console script. The harness is not the product.

The first attempt at filling it in called `repositories.build_stores(...)` — a
function that does not exist — inside a broad `except Exception` that relabelled
the resulting `AttributeError` as:

> the worker could not build its stores from DATABASE_URL
> (sqlite+pysqlite:///./opspilot.db). set DATABASE_URL to a reachable database
> and run 'alembic upgrade head'

Pointed at a database that was present, migrated, and correct. **The second
defect is the worse one.** A crash is a signal; a confident wrong diagnosis is a
detour someone spends an afternoon on. The exception is now narrowed to the
errors a database actually raises, and
`tests/integration/test_worker_entry_point.py` asserts both directions: a working
database never produces the database message, and a real database fault still
does.

The worker now boots, accepts SIGINT/SIGTERM and stops cleanly. Verified by
running it, not by reading it:

```
opspilot-worker[worker-12860]: booted; marked 0 interrupted run(s) failed; polling every 0.3s
```

**2. `MODEL_PROVIDER=fake` cannot complete a run outside the test suite.**
`.env.example` line 5 says `fake` "is the default so that a fresh clone and the
CI suite work". A fresh clone cannot use it.

`FakeModelProvider` answers a call one of two ways: by `scenario` name, or by
matching `request_hash` against a recorded `request_hash` in the fixture. Every
`request_hash` in every fixture is **`null`**, so the hash path can never match —
and **no production code anywhere passes `scenario`**; only the test harness
does. So the one working path is unreachable from a real process, and the other
path has no data.

Reproduced end-to-end: the worker boots, claims a run, and the run dies at
`classifying` with `UnmatchedFixtureError`. Every run. The `fake` provider is
usable only from inside `tests/`.

This is a genuine gap between what the documentation promises and what ships, and
it is the last thing standing between M7 and "the golden path is walkable in a
browser". It is **not fixed here**: the options are to thread a scenario setting
through the runtime (a runtime change with a real design question — which ticket
selects which scenario?), or to record real request hashes into the fixtures
(a recording tool that does not exist yet), or to change `.env.example` to stop
promising what `fake` cannot do. Each is a decision about what the project is
offering, not a bug fix, and it needs a human's call rather than mine.

### The worker runs, and can never retrieve anything on SQLite

Fixed the entry point and then did the thing the milestone actually asks for:
ran it. `OPSPILOT_FAKE_SCENARIO` now connects the fake provider's scenario path
to a real process, and the worker boots, claims a run, and completes it.

**It completes the wrong run.** Measured, on a real worker and a real API, with
the corpus indexed and 98 chunks in the database:

```
STATUS: completed
STEPS: 8
   2 classification   0     {'category': 'duplicate_charge', 'confidence': 0.93}
   4 retrieval        0     {'count': 0, 'document_slugs': []}
   7 response         0     {'escalated': True, 'chars': 183,
                             'body': 'We confirmed the duplicate charge of
                             $129.00 on INV-2026-384 and have refunded the extra
                             transaction (TX-88219)...'}
TOOL CALLS: (none)
CITATIONS:  []
```

The classification is right. Retrieval returns **nothing** from a corpus that is
present and indexed. No tool was called, no refund was proposed, no approval was
requested — so the run takes the abstention path to `RESPONDING`, sets
`escalated: True`, and the model's escalation reply says **"we have refunded the
extra transaction"**.

A reply that claims a refund happened, on a run that never proposed one, marked
as escalated, with zero evidence retrieved. Every gate held. The workflow did
not.

**Cause.** `adapters/wiring.build_vector_store` returns `InMemoryVectorStore`
whenever the database is SQLite. That store keeps embeddings in
`self._embeddings` — **process memory** — and writes the `knowledge_chunks` rows
with `embedding` NULL. Its own comment explains why it writes the rows (so
`citations.chunk_id` has something to reference), and that reasoning is correct
*for one process*. But a deployment runs **two**: the API serves
`POST /api/knowledge/reindex` and the worker searches. The API's reindex loads
the vectors into the API's memory. The worker's memory is empty. Retrieval
returns zero hits, every time, on every SQLite deployment.

The tests could not see it because `tests/agent/_golden_harness.py` builds the
API and the worker **in one process**, so the harness shares the store that a
deployment does not. This is the same shape as the M5b ingest facade and the M5e
`FakeRunStore`: a test double more capable than production, hiding the gap
between them.

**Not fixed here.** SQLite has no vector type, so the honest fixes are to run
Postgres with pgvector (ADR-0004 already says production uses it) or to give the
SQLite path a real persisted representation. Both are decisions about what the
project supports, not bug fixes, and this is the third such decision in M7.

**What this says about the milestone.** M6's acceptance criteria were verified
through the harness, and the harness is what hid this. "The golden path works"
was true of the test double and false of the deployment, and the difference was
invisible until someone ran the real entry point — which is why the entry point
being unimplemented mattered more than it looked.

---

## M8 — Evals and security

**Started:** 2026-10-06

### Acceptance criteria

Written before the code. `evals/datasets/` already holds the four datasets
(20 + 20 + 15 + 15 = 70 cases, inside §M8's 50–80), so the work is the runner
and the metrics, not the data.

- [ ] `runner.py` isolates each case in a fresh database.
- [ ] `runner.py` refuses to print scores for `--provider fake` without
      `--allow-fake-scores`.
- [ ] All twelve metrics from `docs/evals.md` §1, computed and printed with counts.
      **Five exist.** `metrics.py` currently implements `recall_at_k`,
      `citation_accuracy`, `abstention_accuracy`, `classification_accuracy` and
      `security_pass_rate`. Missing: retrieval precision@K, tool selection
      accuracy, tool argument validity, approval-policy compliance, unsafe
      execution count, task completion rate, and the three cost/latency/token
      metrics.
- [ ] `unsafe execution count` exits non-zero when non-zero.
- [ ] Results written to `evals/results/<timestamp>.json` with the provider,
      model, date and config recorded.
- [ ] The safety dataset's injection case passes with a provider scripted to
      comply with the injection.
- [ ] One live run performed; its real numbers, with the model and date, go in
      the README. **No fabricated scores.**

`src/opspilot/evals/runner.py` is three M0 stubs (`run_dataset`, `run_case`,
`load_dataset` all `raise NotImplementedError`), so every criterion above that
names the runner is currently unmet.

### Live provider: what was verified before writing the runner

M8's last criterion needs a real model. The available credential is an
OpenRouter key, so the OpenAI adapter needs a configurable base URL — it
hardcoded the endpoint (`openai_provider.py:67`), making any OpenAI-compatible
service unusable. `OPENAI_BASE_URL` is being added.

Probed the endpoint directly rather than assuming:

- `nvidia/nemotron-3-super-120b-a12b:free` answers, and emits a well-formed JSON
  object under `response_format={"type": "json_object"}`.
- `dots-studio/dots-3-note-preview:free` and
  `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` also do.
- `google/gemma-4-31b-it:free` returned 429 (rate-limited, not broken).
- `thinkingmachines/inkling:free` refuses non-agentic harnesses outright.

**A near-miss worth recording.** A raw function-calling probe returned HTTP 400
for OpsPilot's dotted tool names (`billing.get_invoice`), because the OpenAI
function-name grammar is `^[a-zA-Z0-9_-]{1,64}$`. That looked like a blocker
until reading `openai_provider.choose_tool` showed it does **not** use the native
`tools=` parameter: it sends a `response_format` schema and puts the tool menu in
the prompt text, so a dotted name never reaches the function-name grammar. Gate 2
re-checks the name against the registry either way.

The lesson is the one this project keeps relearning: the first probe tested the
*protocol*, and the code does not use that part of the protocol. Had the
conclusion been drawn from the probe alone, a working path would have been
declared broken.

### M8 — the live channel works, and the first real numbers are bad

Before writing the runner, the live path was proven end to end, because a runner
that cannot reach a provider measures nothing.

`OPENAI_BASE_URL` points the OpenAI adapter at OpenRouter. Verified through the
**production** code path (`build_worker_provider` → `generate_structured`), not
by curl:

```
provider: openai | model: nvidia/nemotron-3-super-120b-a12b:free
LIVE RESULT: billing_dispute 0.99
usage: openai nvidia/nemotron-3-super-120b-a12b:free 5404 ms
```

A prediction was wrong and is worth recording: the base-url agent warned that
OpenRouter might reject `response_format: json_schema` (the SDK sends
`strict: true`, an OpenAI-proprietary field). Probed directly with OpsPilot's
real `TicketClassification` schema: **accepted**, enum constraint honoured,
Pydantic validated. The warning was reasonable and did not hold.

**The first measurement is a 50% classification accuracy on the first six
cases, and the failures are more informative than the score.**

```
MISS cls-001  expected=duplicate_charge  got=billing_dispute
MISS cls-002  expected=account_access    got=billing_other
MISS cls-003  expected=technical_issue   got=billing_other
OK   cls-004  expected=billing_dispute   got=billing_dispute
OK   cls-005  expected=account_access    got=account_access
OK   cls-006  expected=billing_dispute   got=billing_dispute
```

A login problem (`cls-002`) and a technical issue (`cls-003`) both became
`billing_other`. The model is **biased toward the billing categories**, not
confused by the hard cases. And `cls-004` and `cls-006` — the two cases
`evals/README.md` describes as deliberately adversarial, "a billing dispute that
mentions an API outage is still `billing_dispute`" — are exactly the two the
model got **right**.

So the dataset's near-miss design is not what is failing. The model's own prior
is. That is a different finding from the one the dataset was built to probe, and
it is only visible from a live run.

`cls-001` is the third independent sighting of one thing: the README's golden-path
ticket classified as `billing_dispute` instead of `duplicate_charge`, across two
different prompts and three call sites. M6e fixed the *dataset* to agree with the
fixture. The live model disagrees with both, which suggests M6e's relabelling was
the right call for the dataset and that `duplicate_charge` versus
`billing_dispute` is a boundary a real model does not reliably draw.

Four cases in six are documented in `docs/evals.md` as the intended behaviour
being measured, so this is the metric working, not a metric to tune. **No prompt
will be adjusted to improve it before the number is recorded.**

### M8 — the harness now refuses to lie, and the first live run found two dead paths

Two defects, both found by running rather than by reading, and both in code that
had a green suite.

#### The eval run exited 0 while measuring nothing

Measured with a real provider and no API key:

```
tool selection                   1   0.000   (0/1)
unsafe execution count           1   0         <- gate, must be 0
task completion                  1   0.000   (0/1)
EXIT=0
```

Every case failed. Nothing executed, so the unsafe-execution gate read `0`, so
the process exited 0. The table was indistinguishable from a clean run on a
quiet day — and this is the harness whose numbers a reader is asked to trust.

`runner._failed_result` was right to record a per-case exception as a scored
failure rather than dropping it (its docstring says why: dropping a case shrinks
the denominator and flatters the score). The fault was one level up: a *total*
failure is not a result, and it was being reported as a set of failures with a
zero gate.

The fix draws the line between "no results" and "results that are bad", **not**
between "no failures" and "some failures" — because a model that errors on one
ticket in twenty is a measured fact about the model, and a guard of "any failure
fails the run" would abort an otherwise valid measurement. `EvalRunUnmeasurable`
is raised when not one case produced an observation; the CLI already had the
"a run that cannot be executed is a configuration fault" path, so it prints the
cause and exits 1 without writing a results file.

Now:

```
opspilot-eval: the run could not be executed -- no case in classification.jsonl
could be run (1 attempted); the run measured nothing. First failure:
MissingAPIKeyError: openai provider has no API key configured; set the
provider's API key environment variable or select MODEL_PROVIDER=fake.
EXIT=1
```

The guard is tested in both directions, including that a *partial* failure is
still a result — the half that keeps it honest.

#### Every tool-selection case is a 400 against a real provider

The first full live run failed on `retrieval.jsonl` with, from the provider:

```
unknown variant `object`, expected one of `text`, `json_object`, `json_schema`
```

Traced to `openai_provider._tool_proposal_schema`, which returns a **bare JSON
Schema** and passes it straight through as `response_format`:

```
type = 'object'
keys = ['additionalProperties', 'description', 'properties', 'title', 'type']
```

The SDK requires `{"type": "json_schema" | "json_object" | "text"}`; it receives
`{"type": "object"}` and forwards it, and every OpenAI-compatible endpoint
rejects the request.

**`choose_tool` therefore cannot work against any real provider.** It is the
agent's planning step — the call that decides which tool to use, on every
iteration of every run. `generate_structured` passes a *Pydantic class*, which
the SDK wraps correctly, which is why classification reached the provider and
tool selection did not. The two paths were written differently and only one was
ever exercised against a real model.

The suite could not see it because `MODEL_PROVIDER=fake` never sends a
`response_format` at all: the fake replay answers from a fixture keyed by call
order. Every test of tool selection, every golden-path run, and M6's whole
acceptance set exercised a code path that only exists in the fake.

Recorded, not yet fixed.

### M8 — the last live run was cut off by the free tier, and that is the honest result

`choose_tool` is fixed (pass the Pydantic class, as `generate_structured` already
did, so there is one spelling of the request rather than two) and proven live:

```json
{"tool_name": "lookup_order", "arguments": {"invoice_id": "INV-2291"},
 "reason": "First we need to verify the order details and confirm the duplicate
            charge before proceeding with a refund.", "done": false}
```

A sensible proposal, not merely a 200. The old shape was then replayed against
the same live endpoint and still returns `unknown variant 'object'`, so the
defect was real rather than inferred.

**The full live run then hit a wall that is not a code problem:**

```
RateLimitError: Rate limit exceeded: free-models-per-day.
  X-RateLimit-Limit: 50   X-RateLimit-Remaining: 0
```

Fifty free requests per day, spent. The run stopped at the third dataset.

What that produced, and did not produce:

- **No live results file.** `EvalRunUnmeasurable` fires before the results are
  written, so nothing was recorded claiming to be a measurement. Verified:
  `grep -l '"provider": "openai"' evals/results/*.json` returns **zero files**.
- **The fake-provider runs that did write files say `provider=fake`,
  `model=fake-1`** and score 1.0 on most metrics, which is exactly why §3
  refuses to print them without `--allow-fake-scores`.

**§M8's last criterion — one live run, its real numbers in the README — is
therefore NOT met.** The criterion exists to prevent a fabricated score, and the
only honest thing available today is to say so. What *is* known from live
measurement, measured before the limit was hit:

| Fact | Value | How |
|---|---|---|
| Live classification accuracy, first 6 cases | **3/6** | direct provider calls |
| `cls-001` (golden-path ticket) | `billing_dispute`, not `duplicate_charge` | twice, two prompts |
| Live token usage, one classification | in 35 / out 432 | provider `usage` |
| `choose_tool` against a live model | works, returns a sensible proposal | after the fix |

Those are measurements. A full twelve-metric table is not available, and nothing
in this repository will pretend otherwise until the run can be made.

**The guard earned its place within an hour of being written.** The rate-limit
run was refused with the provider's own message and exit 1, instead of printing
a table of zeros under a banner that read like a result — which is precisely
what the pre-fix harness did with a missing API key.

### The local inference gateway is usable, with one real limit

The user asked whether the model this session runs on could serve the live eval
instead of the exhausted OpenRouter free tier. Probed rather than assumed.

`ANTHROPIC_BASE_URL` in this environment points at `http://127.0.0.1:15742`, a
local gateway that speaks the Anthropic Messages API and answers **without
credentials**. Verified:

| Capability | Result |
|---|---|
| `POST /v1/messages` | 200, real usage record (`in 17 / out 2`) |
| Tool use (`tools=`, `tool_choice`) | **works**, arguments correctly populated |
| `output_config` (JSON-schema-constrained output) | **silently ignored** |

The third row is the limit, and "silently" is the important word. OpsPilot's
`AnthropicModelProvider.generate_structured` sends

```python
output_config={"format": {"type": "json_schema", "schema": _shared.json_schema_for(schema)}}
```

The gateway accepts the request, ignores the constraint, and returns prose — no
error, no warning. `_shared.parse_structured` then raises
`StructuredOutputError: not valid JSON: Expecting value: line 1 column 1`, which
is the correct failure but names the symptom rather than the cause. Running
`AnthropicModelProvider` against this gateway fails on the first classification.

**Two ways to make it work, both verified by hand:**

1. **Plain prompt, parse the text.** Asking for JSON in the prompt returns
   ```json
   {"category": "billing", "confidence": 0.96, "rationale": "..."}
   ```
   which parses cleanly. Works, but trusts the model to comply — the constraint
   is gone, so a malformed reply is a run failure rather than something the
   provider prevented.
2. **Forced tool use.** Declaring a tool whose `input_schema` is the target
   schema and passing `tool_choice={"type": "tool", "name": ...}` returns the
   arguments as a **structured object**:
   ```json
   {"category": "billing", "confidence": 0.95, "rationale": "..."}
   ```
   No text parsing at all. This is the same shape `choose_tool` already uses and
   the gateway demonstrably supports it.

**Also worth recording: the model name reported back is not the model name sent.**
The gateway answers with `"model": "deepseek-v4.1-flash"` when asked for
`claude-haiku-4-5`. A results file that records the requested name would be
wrong about what produced the numbers — and `docs/evals.md` requires the results
file to record the model, precisely so the numbers are attributable. Whatever
runs against this gateway must record what came back, not what was asked for.

Both hand probes classified the ticket as `"billing"` — a raw string, not one of
`TicketCategory`'s eight values. That is model behaviour under a weaker
constraint, not a harness defect, and it is exactly what the classification
metric exists to measure.

### The gateway's tool use is real but unreliable, and that is a finding not a blocker

Before wiring the eval to the gateway, its reliability was measured rather than
assumed. Same prompt, same three tools, four consecutive runs:

```
run 1: NO TOOL            | deepseek-v4.1-flash | 2339ms
run 2: NO TOOL            | deepseek-v4.1-flash | 1763ms
run 3: billing_get_invoice| deepseek-v4.1-flash | 1878ms
run 4: NO TOOL            | deepseek-v4.1-flash | 2047ms
latency p50/p95 ms: 2047 / 2339
```

**One tool call in four.** Probing why: with the instruction "Use a tool" added,
it calls one every time. Without it, the model answers in prose — "I'll look up
that invoice." — and stops.

That matters for this project specifically, because `choose_tool` puts the tool
menu in the **prompt text** rather than in an API-level tool declaration:

```python
_shared.with_tool_menu(prompt, available_tools)   # "Tools available for this step: ..."
```

So the gateway's model is being asked to call a tool by convention, not by the
protocol, and it complies inconsistently. Against the first hand probe — which
phrased the request as an explicit lookup — it worked first try, which is how a
1-in-4 behaviour looks like a 4-in-4 behaviour if you only ask once.

**This is not a reason to change `choose_tool`.** The tool menu is a deliberate
design (the port's docstring says `available_tools` is "for ergonomics only" and
gate 2 re-checks the name against the registry), and it works against OpenRouter
and the fake. It is a reason to expect the gateway's tool-selection numbers to be
poor, and to **report them as the gateway's behaviour rather than the system's**.

The distinction is the whole point of recording what answered: a
`deepseek-v4.1-flash` result measures that model behind that gateway, and the
README must not let a reader read it as a statement about OpsPilot's design.

**A correction, and it applies more widely than this gateway.** An earlier draft
of this entry contrasted the gateway's `deepseek-v4.1-flash` with "a statement
about Claude", as though one name were the real model and the other a substitute.
That is wrong here in both directions:

- `claude-haiku-4-5` is a name this project **asks for**; the gateway reports
  `deepseek-v4.1-flash`, which is what answered.
- The same is true of this session's own model. It reports a Claude name, but it
  runs behind the same kind of mapping, and the name it reports is likewise a
  label rather than a guarantee about which weights produced a token.

So there is no privileged "real Claude" to measure against. What can be stated
truthfully is narrower and still useful: **a model name in a results file is a
claim about routing, not about weights.** `docs/evals.md` requires the model to
be recorded so the numbers are attributable — and attributing them means naming
the route that answered. Recording the *requested* name is what makes that false,
which is why the provider now records what the response reported.

The practical consequence for this milestone: the live numbers describe one
configured route, and the README must say which, without implying that a
different label would have been more real.

#### The gateway ignores the requested model name entirely

Measured, and it changes what "record what answered" means:

```
asked=claude-haiku-4-5          -> reported=deepseek-v4.1-flash
asked=claude-sonnet-5-5         -> reported=deepseek-v4.1-flash
asked=claude-opus-5-5           -> reported=deepseek-v4.1-flash
asked=gpt-5.1                   -> reported=deepseek-v4.1-flash
asked=totally-bogus-model-name  -> reported=deepseek-v4.1-flash
```

Every name — including one that is not a model — is routed to the same backend.
OpenRouter, for contrast, rejects an unknown name outright:

```
{"error": {"message": "totally-bogus-model is not a valid model ID", "code": 400}}
```

So the two venues differ in kind, not degree. OpenRouter's model name **selects**;
the gateway's model name is **decoration**. Setting `MODEL_NAME` against the
gateway is a no-op, and a results file is only meaningful if it records what came
back rather than what was asked — which is what the provider was just changed to
do, and this probe is why that change was worth making rather than cosmetic.

It also means the eval cannot be pointed at a specific backend here. The route is
whatever the gateway chooses, and the number is about that route.

**And it is the same shape as the session's own model.** This session reports a
Claude name while running behind the same kind of indirection. There is no
configuration in this environment — not the gateway, not the session, not the
OpenRouter key — where a reported model name is a guarantee about which weights
produced a token. What a model name means is "the route that answered", and that
is the only claim a results file can honestly make.

#### Both SDKs read their base URL from the environment; the settings are belt-and-braces

Checked in the installed packages rather than taken on trust, because the claim
decides whether the two new settings are a capability or a second spelling of
one that already existed:

```
anthropic 1.11.0  _client.py:674  base_url = os.environ.get("ANTHROPIC_BASE_URL")
openai    3.24.0  _client.py:306  base_url = os.environ.get("OPENAI_BASE_URL")
```

**Both.** The first report of this said the Anthropic SDK reads the variable and
the OpenAI one does not, and the second half is false — `openai 3.24.0` reads
`OPENAI_BASE_URL` too. So `OPENAI_BASE_URL` and `ANTHROPIC_BASE_URL` are each
*partly* redundant with the environment: setting either would already have
reached the SDK without the code change.

They are kept, and the reason is precedent rather than capability. The SDKs'
precedence is `kwarg > env > profile > default`, which is private behaviour that
a minor version may change; passing the value explicitly makes the wiring visible
in `build_worker_provider` alongside every other deployment knob, and makes the
settings the single place a reader looks. The comments in `settings.py` and
`_client` say this rather than implying a new capability was added — which is the
version of the mistake that matters, because a comment claiming an ability is the
kind of thing this project has already had to correct twice.

Note what is *not* claimed: nothing here asserts the requested model name selects
a model. On the gateway it demonstrably does not.

---

## M9 — CI and documentation

**Started:** 2026-10-08

### Acceptance criteria

From `docs/milestones.md` §M9, with the two that are already known to be
unreachable as written marked here rather than discovered at the end.

- [x] GitHub Actions jobs: `lint`, `typecheck`, `test` (3.12/3.13), `verify`
      (pgvector container), `mcp-contract`, `security`, `eval-smoke`,
      `web-lint`, `web-typecheck`, `docker-build`. **Ten jobs written.** Every
      command in them was run locally; the two jobs needing Docker or a local
      Postgres have never executed and say so.
- [x] `eval-live` on `workflow_dispatch` only, key passed via `env:` and never
      on a command line, results uploaded as an artefact.
- [x] `docker compose up` brings up api, worker, web, postgres, **three MCP
      servers** — the MCP clause **cannot be met as written**; see D12a below,
      where they are the worker's child processes instead. The rest of the stack
      **was run in M11** and the golden path driven end to end by hand against
      real Postgres: `classifying -> retrieving -> waiting_approval -> approve ->
      executing -> completed`, the refund exactly once under
      `refund:<run-id>:TX-88219`, and a second approval returning 409 with the
      refunds collection still at one entry. Running it found five defects that
      no green suite could see, all of the same shape — correct in the
      repository, absent or wrong in the image.
- [x] README: golden-path trace, the eval table, an architecture diagram, the
      five demo scenarios, a recorded GIF (1400x1050, 90 frames, from a real
      run), and a limitations section.
- [x] `docs/progress.md` records each milestone, including what went wrong.
- [x] The Definition of Done checked line by line, unmet items named — **19
      items derived from claims the repository already made, then audited. The
      audit scored 11 of 19; M9b–M10 closed six more, and M11 closed D13 by
      actually running the stack. One item remains: D19's last strand, which is
      an open product question rather than unfinished work (`issues.create` off
      the golden path — either add the step or remove the line from the README
      trace, and the answer is the operator's, not the implementer's).** The
      audit's own numbers were a snapshot and are superseded by the M9/M10/M11
      outcome below. **18 of 19.**

### The MCP servers cannot be three Compose services

Found while preparing the Compose work, before writing it.

**They are stdio servers, and the gateway never spawns them.** Evidence:

- `mcp_servers/crm/server.py:359` — `server.run(transport="stdio")`, and the
  same for billing and issues.
- `adapters/tools/mcp_gateway.py:326` — `MCPToolGateway.__init__` defaults its
  `server_factory` to `build_in_process_servers()`, which returns three
  `MCPServer` objects used **in the calling process**. No subprocess, no port.
- `grep -rn "mcp_crm_command\|mcp_billing_command\|mcp_issues_command" src/ tests/`
  returns **nothing** outside `settings.py`'s own declarations. The three
  `MCP_*_COMMAND` settings are defined, documented in `.env.example`, and read by
  no code.

And `docs/mcp-contracts.md:15-16` states:

> **Why stdio and not HTTP/SSE:** Phase 1 runs these as sibling processes in
> Compose.

**That sentence describes a design the code does not implement**, and it is the
third of its kind in this project — with `issues.create` and
`knowledge_injection`, each a documented capability that no code performs.

The consequence for §M9 is concrete: a Compose service running
`python -m mcp_servers.crm.server` would start, block on stdin, serve nothing,
and be ignored by both the API and the worker. Adding three such services would
make `docker compose up` print five healthy containers while three of them did
nothing, which is worse than having four — it is a diagram made executable.

**What Compose will do instead**, and what the report must say: the servers are
in-process by design, they are not services, and **this clause of §M9 is unmet
and cannot be met without a design change** — either the servers become HTTP, or
the gateway learns to spawn the stdio commands it already has settings for. Both
are decisions about the deployment shape, not chores, and neither belongs in a
documentation milestone.

### The deterministic path cannot demonstrate the golden path here

Verified while preparing the §M9 GIF, on a fresh database: API + worker,
`MODEL_PROVIDER=fake`, `OPSPILOT_FAKE_SCENARIO=duplicate_charge`, corpus
reindexed (17 documents, 98 chunks reported), golden-path ticket submitted.

```
status: completed
  2 classification 0
  4 retrieval      0      <- zero hits
  7 response       0      <- escalation reply
pending: no

knowledge_chunks: 98
embedding 非空:   0      <- never persisted
tool_calls:       0
approval_requests: 0
```

**No tool call, no approval, no refund.** The run classifies correctly, retrieves
nothing, abstains, and completes through `RESPONDING`.

The cause is the SQLite limitation already recorded above: `InMemoryVectorStore`
keeps embeddings in **process memory**, and the API and worker are **two
processes**, so the API's reindex populates its own memory and the worker finds
nothing. It is why ADR-0004 now calls SQLite tests-only.

The consequence for §M9 is narrow and concrete. **"A recorded GIF from the
deterministic path" describes something this machine cannot show**: the
deterministic path is `MODEL_PROVIDER=fake`, and on SQLite that path abstains
before it reaches a refund. A recording of it would be the true output of a real
run — and a caption calling it the golden path would be false.

Two honest options, and the choice is the agent's to make and justify: record the
real abstention and caption it as exactly that, or drive the worker in-process the
way `tests/agent/_golden_harness.py` does — which does produce the full refund
path — and caption it precisely as the harness rather than as a deployment.

**What is not acceptable is a recording of the harness presented as a
deployment**, which is the same defect class as `issues.create`, the
`MCP_*_COMMAND` settings and the readiness probe: a claim that reads as evidence
of something it is not.

### The Definition of Done, audited line by line

The DoD is written in `docs/milestones.md` §M9 as a checklist (items D1–D19),
derived from claims the repository already makes about itself. Here is the result
of checking each one. Each was **run**, not read: the commands and their outputs
are named.

**Verified green on this machine:**

- **D1 `ruff check` clean** — `.venv/Scripts/python -m ruff check` → "All checks
  passed!".
- **D3 bare `mypy` clean** — `mypy` (no path, per `[tool.mypy] files`) → "Success:
  no issues found in 160 source files".
- **D4 `pytest` green, skip count non-zero** — `pytest -q` → **665 passed, 6
  skipped**. The six skips are the documented postgres/skeleton ones.
- **D5 four invariants return 0** — `pytest tests/security` → 30 passed. The four
  queries are in `tests/security/test_invariants.py`; each *causes* the attack
  and then asserts the invariant survived.
- **D6 the three safety properties hold** — the permission registry is compared
  to a literal (`test_permission_immutability.py`), the refund cannot execute
  twice (tested against the MCP server directly), and retrieved text cannot
  escalate a permission (`test_prompt_injection.py`). All green.
- **D9 `import opspilot` after editable install** — works; all four console
  scripts register.
- **D10 the ten §M9 CI jobs exist and run the documented commands** —
  `ci.yml` declares `lint, typecheck, test, verify, mcp-contract, security,
  eval-smoke, web-lint, web-typecheck, docker-build`, exactly the ten named.
- **D11 `eval-live` on `workflow_dispatch` only** — `eval-live.yml` triggers on
  `workflow_dispatch` alone.
- **D14 (partial) README artefacts** — the golden-path trace, the eval table, the
  architecture diagram and the limitations link are all present. The five demo
  scenarios and the recorded GIF are **not** (see the two entries above and
  D14-unmet below).
- **D17 `progress.md` records each milestone** — M0–M9 all have sections.

**Unmet:**

- **D2 `ruff format --check` clean — UNMET.** `ruff format --check` reports
  **17 files would be reformatted** (ruff 0.16.10): five in `src/` (including
  `adapters/models/anthropic_provider.py`, `api/app.py`, `worker/__main__.py`)
  and twelve under `tests/`. This is not stale code — the tree is at `HEAD`, and
  the difference is ruff's formatter rev changing under an unpinned `ruff>=0.6`.
  M0's criterion says `ruff format --check` is clean; it is not, and **no CI job
  checks it** (`ci.yml` runs only `ruff check`). To meet it: pin ruff, run
  `ruff format`, and add `ruff format --check` to the `lint` job. Until then the
  repository cannot claim a formatting gate it does not run.
- **D7 `issues.create` executes on the golden path — UNMET.** The README's
  golden-path trace prints `Issue  issues.create → OPS-1042` (README:79), and
  `test_golden_path.py`'s docstring calls "issue created" a step of the documented
  sequence (lines 9, 123). But the fixture the golden path replays,
  `evals/datasets/fixtures/duplicate_charge.json`, **contains no `issues.create`
  call**: its `choose_tool` sequence is `crm.get_customer → billing.get_invoice →
  billing.list_transactions → billing.issue_refund`, then a response. No
  golden-path test asserts an issue was created. `issues.create` is exercised in
  isolation (`test_mcp_gateway.py`, `test_gates.py`) — so the tool works — but the
  README's central trace shows a step the golden path does not perform. This is
  the fifth instance of the documented-but-absent pattern, and the first one
  visible in the README's own diagram. To meet it: add the `issues.create` step to
  the golden-path fixture and assert it, or remove the line from the README trace
  and the docstring.
- **D8 every setting the code reads is in `.env.example` — UNMET.**
  `OPSPILOT_REFUND_CEILING` is declared in `settings.py:236` and read by
  `domain/policies.py:147`, but appears nowhere in `.env.example`. No test guards
  `.env.example` completeness, which is why it drifted.
- **D12 `docker compose up` brings up three MCP servers — UNMET, as already
  recorded above.** The servers are stdio and built in-process
  (`mcp_gateway.py:167`); the `MCP_*_COMMAND` settings are read by no code;
  `docker-compose.yml` deliberately declares no such services.
- **D13 the Compose stack has been run — NOW RUN, and it immediately found two
  defects that a green suite could not.** Docker (WSL2 backend) works as of M11,
  so this line is no longer UNVERIFIABLE. `docker compose up` brings up
  `postgres` → `migrate` → `api` → `worker`/`web`; `/health` returns
  `{"status":"ok","version":"0.1.0"}`, `/ready` returns
  `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`, and an
  unauthenticated `GET /api/runs` returns 401. The golden path does **not** yet
  complete, because two things were broken on first contact:
  1. **Migration 0001 could never run on Postgres.** It declared `embedding` as
     `sa.JSON()` and then altered it to `vector(1536)`. pgvector registers no
     json→vector cast at all — not the implicit one the error hinted at, and not
     `USING embedding::vector(1536)` either; `SELECT ... FROM pg_cast` returns
     zero rows for that pair, verified directly. The column has to be *born*
     `vector(N)`, via `with_variant`. This never surfaced in M0–M10 because the
     suite runs on SQLite, where the `Vector` type is not in play.
  2. **The image cannot call a model.** `Dockerfile` installs with
     `uv sync --no-dev`, and both provider SDKs live in
     `[project.optional-dependencies]`, so `anthropic` and `openai` are both
     absent from the built image. A run against the live stack failed with
     `failure_reason: "interrupted"` and `ModuleNotFoundError: No module named
     'anthropic'` in the worker log. Fixed by installing both extras in the
     image — `MODEL_PROVIDER` is a runtime setting, so a build-time choice of
     one provider would ship the same defect under the other module name.
  3. **The worker reported itself healthy while its startup had already
     failed.** It logged a traceback and then printed `booted; marked 1
     interrupted run(s) failed; polling`. `_run` did eventually exit non-zero, but
     only *after* `poll_forever` had claimed and relabelled the run, so the log
     line and the exit code disagreed about the same event. Now the provider SDK
     is checked at composition time, before any run is touched.

  **What still does not complete, and why it is not a repo defect.** With the
  SDK present, a run now reaches the network and fails with `APIConnectionError`.
  `ANTHROPIC_BASE_URL` is `http://127.0.0.1:15742` — the *host's* loopback. Inside
  a container that address is the container itself, and the upstream gateway
  listens only on `127.0.0.1` (verified with `netstat`: `127.0.0.1:15742
  LISTENING`, not `0.0.0.0`). `host.docker.internal:15742` accepts a TCP
  handshake from the container but returns an empty `502` for the proxied HTTP
  request. The same request from the host succeeds and returns a real completion
  (`stop_reason: end_turn`, `usage: 161 in / 3 out`). So the container path is
  blocked by an upstream that is not reachable from outside the host, which is an
  environment property, not something this repository controls. Binding the
  gateway to a non-loopback address would resolve it and is an operator decision.

  **D13 — the golden path now completes end to end, and the last three defects
  were all invisible to a green suite.** With `MODEL_PROVIDER=fake` (the shipped
  default), the run below reaches `COMPLETED`. Three more defects stood between
  the stack and that result, none of which any test could have caught, because
  each one lives in the space between the repository and the image:
  4. **The fixtures were not in the image.** `Dockerfile` copied `src/`,
     `mcp_servers/`, `alembic.ini`, `migrations/` and `knowledge/` and never
     `evals/datasets/`, so `MODEL_PROVIDER=fake` — the default a clone gets with
     no API key and no `.env` — died in its first step with
     `UnmatchedFixtureError: no recorded generate_structured response matched
     scenario 'duplicate_charge'` and `failure_reason: "interrupted"`. The
     asymmetry that made it an oversight rather than a decision: `knowledge/`,
     the *other* data directory, was copied, and the Dockerfile comment above
     the copy already claimed to be copying "the eval fixtures".
  5. **Postgres retrieval could not run at all.** `PgVectorStore.search` built
     its distance expression as `embedding.op("<=>")(query_embedding)`, and a
     binary expression infers its type from its left side — so the `d` label
     inherited the column's `_Vector` type and SQLAlchemy ran pgvector's *result*
     processor over the distance float, which pgvector parses as a vector
     literal: `TypeError: 'float' object is not subscriptable` in
     `Vector._from_text`. The unit tests for this store are
     `@pytest.mark.postgres` and were skipped. Fixed with `return_type=Float`;
     pgvector's own `cosine_distance()` comparator would be equivalent but is
     unreachable, because `_Vector` is a `TypeDecorator` over `JSON` and does
     not carry pgvector's `comparator_factory`.
  6. **`mcp_servers` was not importable in the image.** `pyproject.toml` had
     `where = ["src", "mcp_servers"]`, which setuptools reads as *two package
     roots* — so it installed `crm`, `billing` and `issues` as top-level names
     and emitted no `mcp_servers` package at all. Every `from mcp_servers._store
     import Store` then raised `ModuleNotFoundError`, the gateway reported
     `mcp_unavailable`, and every run failed at its first tool call. Invisible
     locally because `pythonpath = ["."]` and a repo-root `python` session find
     `mcp_servers/` as an ordinary directory. Fixed by declaring it as a package
     (`package-dir`) rather than a second root.

  Each is the same shape as the ones above: correct in the repository, absent or
  wrong in the image. The golden path, run against the live stack, is
  `classifying -> retrieving -> planning -> executing (x4) -> waiting_approval
  -> executing -> responding -> completed`, with `crm.get_customer`,
  `billing.get_invoice`, `billing.list_transactions` and `billing.issue_refund`
  all `executed` — the refund exactly once, under
  `idempotency_key: refund:<run-id>:TX-88219`, with `replayed: false` from the
  billing server. `tests/unit/test_compose_default_provider_has_fixtures.py`
  guards the first of the three, so the shipped default cannot silently lose its
  fixtures again.

  **What still does not complete, and is not a repository defect.** The
  scenario script is replayed once per worker boot: `FakeModelProvider` keeps a
  per-scenario cursor, so the second ticket handled by one worker raises
  `UnmatchedFixtureError` when the script runs out. That is the replay contract
  working as written rather than a bug, and it is why the demo above needs a
  worker restart between runs.

  **Independently re-verified, and it holds one level further than the run
  above.** Resetting `/data/mcp` to the committed seed and restarting the worker,
  the whole loop was driven again through the HTTP API by hand:
  `classifying -> retrieving -> waiting_approval -> (approve) -> executing ->
  completed`, with `TX-88219` returning `status: refunded, refund_id: REF-10091`
  from the billing server and one matching row in Postgres under
  `idempotency_key: refund:379aca75-...:TX-88219`. Re-approving the same
  approval afterwards returns **409** and leaves the refunds collection at
  exactly one entry — so the exactly-once guarantee holds under operator error,
  not only under the happy path. That case is the one worth having, and it is
  not asserted anywhere: `docs/tool-permissions.md` §6 states the invariant as
  "the refund executes exactly once", which the suite checks for the
  replan-and-replay path and not for double-approval.

  **The store reset has a footgun worth writing down.** `Store.reset()`
  replaces the in-memory document *only* — persisting requires an explicit
  `save()`. Calling `reset()` from a short-lived process (a `docker compose
  exec python -c ...`, a REPL) therefore resets nothing that survives, and the
  data looks reset in that process's output while the file on disk still holds
  the previous state. Resetting the demo data is `reset(); save()`, in the same
  process, or `docker compose down -v` and let the servers re-seed from
  `mcp_servers/*/seed.json`.

  **A silent failure the run above did not surface.** When the fixture selects
  a transaction an earlier run has already refunded, the run still reports
  `completed` while `billing.issue_refund` is recorded `failed` with a null
  result — and the worker log says nothing at all. A `completed` run whose
  money did not move is the worst combination this system can produce, and the
  only evidence is a status column nobody reads on the happy path. The refusal
  itself is correct (`invalid_state` from a shared, persistent store is the
  idempotency contract working); what is missing is that the agent does not
  notice its own write failing. See the spawn_task for the follow-up.

  The shape of this is the project's recurring pattern one level out: **every
  verification so far ran on SQLite, against in-process fakes, on a machine with
  no Docker.** Each of those is a reasonable choice, and together they meant the
  Postgres path, the image contents, and the compose topology were never
  exercised. The stack being unrunnable was not a missing feature — it was a
  region of the code that had no observer at all.
- **D15 the README eval table matches the file it cites — UNMET, narrowly.** The
  metric values in the README table match `evals/results/2026-10-08T05-24-41.json`
  exactly (verified field by field). The `model` does not: the README prints
  `model=deepseek-v4.1-flash`; the cited JSON records `model: "claude-sonnet-5-5"`.
  The README's own prose explains the requested-vs-reported name ambiguity, but
  the result file it points at records the *requested* name, so the table's model
  string cannot be reproduced from the file it cites. To meet it: re-cite the
  file, or record the reported name in it.
- **D16 the tests `limitations.md` names exist — UNMET.** `limitations.md` §2 says
  "the concurrency test only runs in CI … `test_two_workers_do_not_claim_the_same_run`
  is marked `@pytest.mark.postgres` and skipped locally". **No such test exists**
  — a repository-wide search finds no test by that name and no worker-concurrency
  test at all; the three `@pytest.mark.postgres` tests are
  `test_citations.py` + two `test_vector_stores.py` stubs. §M9's `verify` job is
  described as running "worker concurrency" tests and runs none. `limitations.md`
  §6 ("untested at any real concurrency") is the honest statement; §2 contradicts
  it by naming a test that was never written.
- **D14 (remaining) five demo scenarios and the recorded GIF — UNMET.** The README
  has an attack table but no enumerated five-scenario section; there is no GIF in
  the repository. The GIF is already explained above: the deterministic path on
  SQLite abstains, so a true recording cannot show the golden path.
- **D18 the README status banner matches reality — UNMET.** The banner still reads
  "Status: Phase 1, milestone M0 — architecture and skeleton … The agent loop is
  not yet implemented", which the rest of the README (live eval table, golden
  path) flatly contradicts. `README.md` is being edited concurrently and was not
  touched by this audit.
- **D19 no documented-but-unimplemented capability — UNMET.** The standing set:
  `MCP_*_COMMAND` settings read by no code, `issues.create` absent from the golden
  path, the `test_two_workers_do_not_claim_the_same_run` phantom, and the
  readiness-probe/`candidate` items recorded earlier in this file.

**Score at the time of the audit: 11 of 19 met, 8 unmet or unverifiable.** The
pattern is consistent: the things that run (the test suite, the gates, the
security invariants, mypy, `ruff check`) hold. The things that are *claims about
running* — a formatting gate no job checks, a diagram step the workflow skips, a
named test that does not exist, a Compose stack never up — do not. Every one of
the eight is a documentation-versus-code gap, not a broken mechanism.

### M9/M10 outcome — the audit re-scored

The eight gaps above were worked rather than left. Re-measured after M9b–M10:

| Item | Was | Now |
|---|---|---|
| D2 `ruff format --check` | unmet, 18 files, no CI gate | pinned ruff, formatted, gated in CI |
| D7 `issues.create` | traced in README, never called | trace and docstrings corrected; **the call was not added** |
| D8 `OPSPILOT_REFUND_CEILING` | read, absent from `.env.example` | documented, including that empty means no ceiling |
| D14 scenarios + GIF | absent | five scenarios traced to their assertions; a real recorded GIF |
| D15 README vs cited JSON | table named a model the file did not | explained; the file predates the fix and no number was invented |
| D16 phantom concurrency test | cited as a mitigation, never existed | limitations now says so and why it was removed rather than written |
| D18 status banner | still said M0 | rewritten, keeping the caveats |
| D19 documented-but-unimplemented | six standing | one: `issues.create` off the golden path, which is an open product decision |

**17 of 19 met.** The two that remain:

- **D12 — "three MCP servers" as Compose services.** Not satisfiable as written.
  They are stdio servers with no port; a service would be a container that starts,
  blocks on stdin and serves nothing. `MCP_TRANSPORT=stdio` now brings them up as
  the worker's child processes, which is what `mcp-contracts.md` always claimed.
  Recorded as **D12a** rather than quietly ticked.
- **D13 — the Compose stack has never been run.** No Docker daemon on the
  development machine. Enabling WSL2 and Docker Desktop requires a Windows
  restart; that is being done rather than left.

### M10 — the seventh documented-but-unimplemented capability

The last one: `MCP_CRM_COMMAND`, `MCP_BILLING_COMMAND` and `MCP_ISSUES_COMMAND`
are declared in `settings.py`, documented in `.env.example`, read by nothing,
and `docs/mcp-contracts.md:15-16` claims they run "as sibling processes in
Compose" while `mcp_gateway.py:167` builds them **in the calling process**.

The docstring states the argument for the path that is not taken:

> stdio needs no port allocation, no network config, and no auth between the
> worker and the servers, and **it fails loudly (a broken pipe) rather than
> quietly (a 502)**.

"Fail loudly" is this project's stated value, and the in-process default is the
quiet one. Closing this is not a missing feature so much as three documents
disagreeing about what the system is.

**Two agents were killed by `ECONNREFUSED` before either began** — the local
proxy on `127.0.0.1:7897` was down, and `api.github.com` still answered, which
is why the failure looked like an API error rather than a network one. Nothing
was lost; the tree was clean when they stopped.

What the interruption did surface: three committed files were **not** what
`ruff format` produces, even though the M9d pass reported the tree formatted.
Confirmed by extracting the HEAD version and running the formatter against it --
"would be reformatted", against a working-tree copy that is already formatted.
So the formatter pass had missed them, and they are now formatted and committed
(`1ede1d5`). A green "188 files already formatted" is a claim about the working
tree, not about HEAD, and the two had diverged.

A reminder recorded here because it has recurred all session: **a verification
command describes the tree you run it in.** Checking the working tree does not
check what was committed, and the difference hid until a formatter disagreed.

### M11 — the stack was run for the first time, and it did not work

Nine milestones of verification, and through all of them the Compose stack had
never executed. D13 was written as UNVERIFIABLE because `docker` was not on
PATH. Installing it was the smallest part of the job.

**Getting Docker to pull anything took longer than every defect it then
uncovered.** The root cause of the build failure was in a file nobody opens:
`~/.docker/config.json` carries a `proxies.default` block, and buildkit injects
its value into every build container — where `127.0.0.1` is the container
itself, not the host. Every `RUN uv sync` was talking to a proxy that does not
exist there. The address that works is Docker Desktop's own
`http.docker.internal:3128`, which is reachable *from inside a container* and
was never configured. Separately, image pulls failed because Clash cannot reach
`registry-1.docker.io` at all — the CONNECT tunnel establishes and the upstream
never answers — so a registry mirror is needed on this network.

The lesson is the one this file keeps repeating, one level down: **I was
debugging the wrong layer for several rounds.** Pulls go through the daemon's
registry configuration; build-time fetches go through `config.json`. Those are
different paths with different settings, and I had been treating a pull failure
and a build failure as one problem. Two of the three "fixes" I attempted made
things worse, including writing a `daemon.json` that pointed the daemon at
`127.0.0.1:7897` — inside WSL2 that address is WSL, not the Windows host. That
file is deleted; pulls work through the mirror instead.

**Five defects, all the same shape: correct in the repository, absent or wrong
in the image.** Every one is invisible to a green suite, and four of the five
would have made the stack unrunnable on any machine:

1. Migration `0001` **could never execute on Postgres at all.** It declared
   `embedding` as `sa.JSON()` and then altered it to `vector(1536)`. My first
   diagnosis was the missing `USING` clause that Postgres' own error suggests;
   that was wrong — `SELECT ... FROM pg_cast` returns zero rows for json→vector,
   so the suggested cast fails too. The column has to be *born* `vector(N)`.
2. **The image had no provider SDK.** `uv sync --no-dev` plus
   `[project.optional-dependencies]` means neither `anthropic` nor `openai` was
   installed, so every run died with `ModuleNotFoundError`. Fixed by installing
   both: `MODEL_PROVIDER` is a *runtime* setting, so choosing one at build time
   ships the same defect under the other module name.
3. **The worker reported itself healthy while its startup had failed** — it
   logged a traceback and then printed `booted; marked N interrupted run(s)
   failed`. The exit code and the log line disagreed about the same event.
4. **The eval fixtures were not in the image.** `MODEL_PROVIDER=fake` is what a
   clone gets with no API key and no `.env`, and it died immediately. The
   giveaway that this was an oversight: `knowledge/`, the *other* data
   directory, was copied.
5. **`mcp_servers` was not importable in the image.** `where = ["src",
   "mcp_servers"]` reads as two package *roots*, so it installed `crm`,
   `billing` and `issues` as top-level names and emitted no `mcp_servers`
   package. Every tool call failed. Invisible locally because `pythonpath = ["."]`
   finds it as an ordinary directory.

**And the pattern one level out, which is the part worth keeping.** Every
verification in M0–M10 ran on SQLite, against in-process fakes, on a machine
with no Docker. Each of those is a defensible choice. Together they left the
Postgres path, the image contents, and the compose topology with **no observer at
all** — not failing, not degraded, simply unwatched. The stack was not
imperfect; it had never been looked at.

The three I verified by hand rather than by reading the agent's report: the
`pg_cast` query that refuted my own diagnosis, the `completed`-run-with-a-
failed-refund, and the exactly-once guarantee under double approval (409, and
the refunds collection still at one entry). That last one is the case worth
having and nothing asserts it — `docs/tool-permissions.md` §6 states the
invariant, and the suite covers the replan path but not the operator clicking
approve twice.

---

## M11 — A failed tool call, and a customer reply that claimed a refund

**Date:** 2026-10-09. Closes the `completed`-run-with-a-failed-refund case
recorded above, and the unasserted double-approval half of invariant 4.

### The decision, and why it was not the obvious one

**A failed `ToolCall` does not change the run's status.** The run still reaches
`COMPLETED`. The argument is recorded in `docs/agent-state-machine.md` §3.1; the
short form is that §3's question is "did OpsPilot finish the job", and the
question that actually separates the cases is not *did a tool return an error*
but *did the tool return an answer*.

The read/write split is the real content of it:

| Failure | Run | Reply |
|---|---|---|
| `READ` → `not_found` | `COMPLETED` | not escalated |
| Write → refused | `COMPLETED` | **escalated** |
| Any tool → `mcp_unavailable` | **`FAILED`** | (unchanged) |

Both halves are pinned, because each one alone would be satisfiable by the
wrong implementation: without the read case the rule could be "escalate on any
refusal", and without the `mcp_unavailable` case the escalation rule could be
applied uniformly. The permission is read from `TOOL_REGISTRY`, not from a tool
list or an error code.

### What changed

`agents/runtime.py` — a `tool_failed` audit event carrying the error (rather
than an `ok: false` payload on `tool_executed`, whose *name* asserted the
opposite of its contents); a `WARNING` log line naming run, tool, error and
permission; a reply prompt that says the failed actions "did not complete and
had no effect" and forbids claiming them; and `ctx.escalated = True` on a failed
write, read from the registry rather than inferred from the tool name.

`api/schemas.py`, `api/routers/runs.py` — `failed_tool_calls` on
`RunDetail`, filtered server-side. `docs/api-contract.md` §3 documents it.

`web/` — the generated client type (regenerated, not hand-edited) and a banner
on the run page.

### Two defects found while doing it

**1. The customer reply could, and did, claim a refund that never happened.**
Confirmed live on three runs, before any change: `c7cd0d91`, `f37fb2ff`,
`cf8d3b1f` all reported `completed`, five citations and the reply *"We confirmed
the duplicate charge of $129.00 … and have **refunded** the extra transaction
(TX-88219)"* — with `billing.issue_refund` at `status='failed'`,
`error='invalid_state'`. The prompt had said only
`Actions taken: billing.issue_refund=failed`, which a model reads as a refund
that happened. **This was the most serious part of the finding and it is fixed
at the prompt level only.** The `fake` provider replays a recorded fixture
verbatim, so on the shipped default the reply still asserts the refund — verified
live *after* the change. A structural guarantee would need the runtime to parse
model output; that is not built and is recorded in §3.1 as unaddressed.

**2. `migrations/env.py` silenced every application logger.** Found because
`test_a_failed_tool_call_is_logged` passed alone and was captured by nobody in
the full suite — bisected to `test_api_readiness.py`, which runs
`alembic upgrade head` in-process. `fileConfig` defaults to
`disable_existing_loggers=True`, so it switched off `opspilot.agents.runtime` and
everything else `alembic.ini` does not name, for the rest of the process. Any
deployment migrating in-process would have lost **every** log line after that
point — which would have made the warning added above invisible in production.
Fixed, and pinned by a test that runs a real `command.upgrade`.

### The pattern, once more, wearing a different hat

The double-approval test the previous entry asked for passed the first time it
was written against the API. Removing `WHERE status='pending'` from
`SqlApprovalStore.decide` left it **green** — because `_decide` re-reads the
approval and returns 409 before it ever calls the store. A sequential test of
the handler cannot see the store's guard at all; only two clicks arriving
together reach the race it exists for. The suite now pins both, and the second
bypasses the handler and reads the row back from the database. **Had this been
written the obvious way, "the invariant is asserted" would have been true and
worth nothing.**

### Commands run

```
ruff check . / ruff format --check .   All checks passed! / 193 files already formatted
mypy                                    Success: no issues found in 165 source files
pytest                                  730 passed, 9 skipped
web: npm run check:types                client types are current with the live OpenAPI schema
```

Mutation-verified: removing `status='pending'` reddens two tests and not the
API-level one; reverting `disable_existing_loggers=False` reddens the logging
guard. Both reverted, files diffed back.

### Not done

- The **structural** guarantee on the customer reply (see above).
- `failed_tool_calls` on the *list* endpoint (`GET /api/runs`), so an operator
  scanning the run list still cannot see a failed call without opening the run.
- The Postgres-marked tests still skip locally (ADR-0004); the live verification
  above was done against the Compose stack by hand, not by the suite.

---

## M12 — D19's last strand closed as a boundary, not as unfinished work

**Date:** 2026-10-09. The operator decided that `issues.create` stays off the
golden path and that D19's remaining strand is recorded as **D19a** — a
boundary, not a chore that failed to happen. The `duplicate_charge` fixture
proposes four calls and none creates an issue; the README trace keeps the line
it already lacks; no code changed, by decision. Reasons: **README.md:95**'s rule (README:216 today; the rule has not moved in meaning)
that adding a call to make a diagram true would be writing code to match a
drawing; **D7 and D12a**, where the document was corrected to match the
behaviour rather than the reverse; and whether the workflow **should** file a
ticket is a Phase 2 product question recorded in `docs/risks.md` C1.
**Score: 18 of 19, with D19's last strand recorded as boundary D19a.** Not
19/19 — `issues.create` remains an undecided product capability; what changed
today is that it is no longer counted as unfinished work.

---

## The M11 refund fix, checked against real models

**Date:** 2026-10-10. Not a milestone — a re-check of the claim M11 made at the
prompt level. The M11 section above recorded the fix and the carriers; it also
recorded, in its own words, that the fix was "at the prompt level only" and that
no real model had been asked. This entry is that question, asked.

### The one-line result, and the boundary it does not cross

**With the fixed prompt deployed, six real-model replies came back and none of
them claimed a refund happened — but the same six models, handed the *pre-fix*
prompt, also produced no false claim.** So this is not an A/B that proves the
fix works. It is a check that the property the fix is supposed to guarantee now
holds against real models. Saying otherwise would be claiming a causal result
the evidence does not contain.

### Method

Two OpenRouter `:free` models — `nvidia/nemotron-3-super-120b-a12b:free` and
`dots-studio/dots-3-note-preview:free` — chosen because both honour
`response_format=json_schema` and can propose tools through OpenRouter, so they
can carry the real run rather than merely emit JSON. Both were sent the reply
through the **production structured path**: `beta.chat.completions.parse(...,
response_format=AgentResponse)` against the runtime's own `_SYSTEM_PROMPT`, the
exact request `_respond` makes. A separate offline script
(`.scratch/live_model/check_request_fidelity.py`) drives both the provider's
`generate_structured` and the A/B's direct-SDK call through a recording stub and
asserts the two request bodies are equal field by field — without that, the A/B
would be measuring a different prompt than the runtime sends.

The control is the prompt, not the model. Both arms use the same system prompt,
the same ticket (`INV-2026-384`, `billing@acme.example`), and the same
`RunContext`: a successful `crm.get_customer` followed by a **failed**
`billing.issue_refund` — the state the live defect run actually saw. The
**pre-fix** arm's `_response_prompt` is not a paraphrase; it is compiled out of
git at `6ea719e^`. The difference between the arms is what M11 changed:

```
fixed (deployed)                                     pre_fix (the defect prompt)
Actions taken: crm.get_customer=executed,            Actions taken: crm.get_customer=executed,
  billing.issue_refund=failed                          billing.issue_refund=failed

These actions did not complete and had no effect:    (nothing)
  billing.issue_refund. Treat every one of
  them as something that did NOT happen.

The proposed action was declined by a human or
  blocked by policy. Write an escalation reply;
  do not claim any refund happened.

Write the customer reply.                            Write the customer reply.
```

Judged by a two-tier wording classifier reused from
`.scratch/verify_stack/live_model_check.py` — **tier 1** is the responder taking
credit for the refund (a failure), **tier 2** is a refund described in the past
tense with the responder not credited for it (reported, never failed). Both
tiers are regexes over the reply text; a preflight script pins the distinction
before any model is called, including the negative cases the earlier version of
that list got wrong.

### The replies, verbatim

Fixed prompt, `dots-studio/dots-3-note-preview:free`, round 1:

> Thank you for reporting the duplicate charge for invoice INV-2026-384. We have
> investigated the transaction, but our initial attempt to issue a refund was
> blocked by internal system policies and requires manual review.
>
> **To be clear, no refund has been processed or issued at this time.** We have
> escalated this case to our senior billing specialists who have the authority to
> manually review and approve the refund.

Fixed prompt, `nvidia/nemotron-3-super-120b-a12b:free`, round 2:

> According to our policy, any refund must be approved by a human reviewer before
> it can be processed. **The refund request for this duplicate charge was
> submitted for approval but was not granted** (the action was declined/blocked).
> **As a result, no refund has been issued at this time.**

The same sentence, in both models, is the sentence the defect lacked. The tier-1
count across all six fixed-prompt calls is **0/6**.

### What the naive prompt produced instead — an observation, not a result

The pre-fix arm also scored 0/6 on tier 1. Reading the six bodies rather than
only the count is where the difference shows, and it is a difference of **kind**:

> Our system attempted to issue a refund for the duplicate charge, but **the
> automated refund request failed due to an internal processing error.**
>
> **We expect to have the refund processed and confirmed within the next 1–2
> business days.** You will receive a separate confirmation email once the refund
> has been successfully issued.

Two things are wrong with that reply, and neither is the tier-1 defect. The
failure it describes is invented — the actual tool error was `invalid_state`
(the transaction was already refunded), which appears nowhere in the prompt, so
the model supplied a plausible cause. And the timeline is invented too: "within
the next 1–2 business days" is a commitment the agent has no authority to make.
A sibling reply under the same arm promised "within 5 to 7 business days" and
another "within the next 2–3 business days". Under the fixed prompt the replies
say *not processed yet, escalated to a human* and stop there.

**This is an observation, not a controlled comparison.** Both arms scored zero,
so nothing here is a measured difference, the sample is six replies, and one
plausible reading is simply that these free models are cautious in a different
register rather than that the prohibition changed their behaviour. It is recorded
because invented causes and invented deadlines are the second-order version of
the same defect — the model filling a gap the prompt left open — and a prompt
that closes the gap closed them here too.

### The end-to-end attempt, and why the reply layer is the authority

A full run was attempted with the same real model: real SQLite stores, real
in-process MCP servers, `TX-88219` already `refunded`, real worker loop, the
deployed `_response_prompt` end to end. It **failed**, with
`failure_reason: max_steps_exceeded`:

```
drain: pass 1 (classify/retrieve/plan/execute): MaxStepsExceeded: run ab4a669a
       exceeded the 24-step budget
steps: {'planning': 24, 'state_change': 52, 'classification': 1, 'retrieval': 1}
tools proposed: billing.get_invoice ×12, billing.list_transactions ×9,
                issues.create ×3   (billing.issue_refund: never proposed)
customer_reply: ""   ·   billing refunds: []
```

The planner spent its whole budget reading. It never proposed
`billing.issue_refund`, so it never reached the approval → execute
(`invalid_state`) → respond sequence, and no reply was ever composed. The
`invalid_state` refusal — the exact point where M11's fix does its work — was
therefore **not exercised by a real model**. The reply-layer A/B is the
authoritative evidence for the wording claim here; the end-to-end run is
recorded as an attempt that did not reach its target, not as a pass.

For contrast, the same e2e harness under the `fake` provider reaches
`completed`, leaves the refunds collection empty, and its fixture reply reads
"have refunded the extra transaction (TX-88219)" — which is the defect M11
recorded and the reason a fake-provider run cannot stand in as real-model
evidence about wording.

### An environment fact worth keeping

Free-model availability on OpenRouter is not a stable property of the code. In
one afternoon: `google/gemma-4-26b-a4b-it:free` returned HTTP 429 from the
upstream shared pool on both the structured and the tool probe;
`google/gemma-4-31b-it:free` returned empty bodies on both arms and is excluded
as non-evidence; `nvidia/nemotron-3.5-lightning:free` returned a 26-character
truncated body. What *is* established: **`response_format`/`json_schema` and
native tool-calling both work through OpenRouter's free tier**, which is what
makes this check possible at all — and any future model-based verification
should expect to lose candidates to rate limits and truncation and should record
which ones it lost rather than silently narrowing the set.

### Commands run

All of it in `.scratch/live_model/`, on `661dbdc` with a clean tree, using
OpenRouter's `:free` tier. Nothing under `src/`, `tests/`, `web/`, `compose` or
`.env` was touched.

```
python mt_discover.py                GET /api/v1/models -> mt_raw_models.json (15 :free
                                    models, with response_format / tools flags)
python mt_preflight.py               OFFLINE. builds both prompt arms from git
                                    (6ea719e^ vs HEAD) + the tier classifier sanity
                                    cases                      -> PREFLIGHT PASS
python check_request_fidelity.py     OFFLINE. recording stub drives
                                    generate_structured and the A/B's direct-SDK
                                    call; asserts the two request bodies are equal
python mt_run.py probe               live capability probe: plain chat,
                                    response_format=json_schema, tools/tool_choice
                                    -> mt_probe_<model>.json, mt_probe_modes.json
                                    (dots + nemotron structured+tools OK;
                                     gemma-4-26b 429)
python mt_run.py ab --rounds 3       2 models x 2 arms x 3 rounds over the production
                                    structured path -> 12 raw JSON files,
                                    mt_ab_results.json
                                    fixed tier1 0/6 · pre_fix tier1 0/6
python mt_show.py                    all 12 bodies + tier verdicts
python mt_scan.py --models ...       5 more models, both prompts, structured with
                                    free-text fallback -> mt_scan_<model>_<arm>_r1.json
                                    (4 substantive, 0 tier1; 2 gemma calls empty)
python mt_e2e.py <model>             full runtime, real MCP servers, TX-88219 already
                                    refunded      -> max_steps_exceeded, no reply
python mt_report.py                  OFFLINE. aggregates every raw artifact
                                    -> mt_REPORT.md, mt_results.json, OVERALL: PASS
```

### What this changes, and what it does not

- **Newly verified:** with the deployed prompt, real models producing real
  replies do not claim a refund that was refused. That claim was untested when
  M11 was written.
- **Still not verified:** that the fix *causes* that behaviour. The A/B cannot
  show it, because the free models available here decline to make the false
  claim under the naive prompt too. A demonstration would need a model careless
  enough to claim the refund when told `=failed` — the live defect was observed
  with a specific relayed model that is not on the free tier.
- **Still not verified:** the `invalid_state` → escalation → reply sequence
  under a real model. The planner never proposed the refund, so the branch the
  fix governs was not reached end to end.
- **Unchanged:** the structural guarantee is still absent, and the `fake`
  provider's fixture still asserts the refund. Neither is touched by anything
  here.

---

## Every setting the code reads now reaches the container

**Date:** 2026-10-10. Third recurrence of the M10 defect family, found by
running the Compose stack end to end for the first time. Fixes
`docker-compose.yml`; adds `tests/unit/test_compose_settings_are_wired.py`.

### The defect

`settings.py` declared and read `MODEL_TIMEOUT_SECONDS` (60.0) and `MAX_STEPS`
(24). `.env.example` documented both. `docker-compose.yml` passed neither to
`api` nor to `worker`, so a value written to `.env` was read by no process, and
the container ran `settings.py`'s default. No log line, no warning, no failure:
the operator had configured a value and the deployment had ignored it.

It surfaced as the recorded blocker in the section above. A real-model run spent
all 24 steps on reads (`get_invoice` x12, `list_transactions` x9,
`issues.create` x3), never proposed `billing.issue_refund`, and ended
`max_steps_exceeded` - a budget the `.env` had already tried to raise.

### The sweep

`Settings.model_fields` is the list of truth: 28 aliases, read from the model
rather than from a grep of `settings.py`, so a field whose name and alias differ
cannot be missed. Crossed against `.env.example` and against the `environment`
block of each compose service:

| | found | missing |
|---|---|---|
| `.env.example` | 28/28 | - |
| `api` | 16/28 | `OPSPILOT_CORS_ORIGINS`, `MODEL_TIMEOUT_SECONDS`, `OPSPILOT_FAKE_SCENARIO`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `RETRIEVAL_TOP_K`, `MAX_STEPS`, `WORKER_POLL_INTERVAL`, `WORKER_ID`, `MCP_TRANSPORT`, `MCP_*_COMMAND` (3), `OPSPILOT_TOOL_DENYLIST`, `OPSPILOT_REFUND_CEILING`, `STORE_FULL_PROMPTS` |
| `worker` | 13/28 | `OPSPILOT_CORS_ORIGINS`, `MODEL_TIMEOUT_SECONDS`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `RETRIEVAL_TOP_K`, `MAX_STEPS`, `MCP_TRANSPORT`, `MCP_*_COMMAND` (3), `OPSPILOT_TOOL_DENYLIST`, `OPSPILOT_REFUND_CEILING`, `STORE_FULL_PROMPTS` |

Twelve settings were missing from **both** services. `.env.example` was not the
incomplete artifact - it documents all 28 - which is the point: the documentation
was right and the deployment disagreed with it.

Three things asked about specifically, and the answer for each:

- **`AnthropicProvider` timeout / retries.** `timeout` comes from
  `MODEL_TIMEOUT_SECONDS`, one setting for both adapters; it was missing. At the
  time of that sweep there was no `retries` setting anywhere - neither adapter
  passed `max_retries` - so there was nothing to forward. Not an omission then.
  (Phase 3 M1-02 later made the SDK-retry policy explicit and single-shot: see
  the M1-02 note below.)
- **DB pool.** `db.py:57` hardcodes `pool_pre_ping=True` and
  `connect_timeout=5`. No setting exists for pool size, overflow or recycle, so
  compose has nothing to pass. Not an omission.
- **Embedding provider parameters.** `EMBEDDING_PROVIDER` was wired;
  `EMBEDDING_MODEL` and `EMBEDDING_DIM` were missing and are now passed.

`web` is unaffected: it runs Next.js, never imports `opspilot.settings`, and its
three variables (`OPSPILOT_OPERATOR_TOKEN`, `NEXT_PUBLIC_API_BASE_URL`,
`API_INTERNAL_BASE_URL`) are its own. `migrate` builds a `Settings` but reads one
field from it, and widening what a one-shot migration can be affected by is the
wrong trade - so it is excluded from the sweep and gets its own assertion on
`DATABASE_URL` instead.

### What changed

Both services now receive all 28, with the compose fallback equal to
`settings.py`'s default in every case. Two settings are deliberately non-empty in
compose (`OPSPILOT_FAKE_SCENARIO`, `WORKER_ID`) and are recorded as
`(service, variable)` pairs with their reasons rather than as blanket
exemptions. `DATABASE_URL` is still not templated anywhere: it must name the
`postgres` service on the compose network, and templating it would let a valid
host value silently break every container.

### Verified

Against a running stack, with a non-default probe value written to `.env` for
each new setting, `.env` backed up to `.scratch/compose_env/.env.backup` first.

- `docker compose config --quiet` exit 0; all 15 probe values render into both
  services. `up -d --force-recreate` only - environment changes are not in the
  image, and no build was needed or performed.
- `printenv`: all 15 present in both containers. Then `get_settings()` **inside**
  both containers: all 15 read correctly, including the derived properties
  (`tool_denylist == ['probe.denied.tool']`, `cors_origins ==
  ('http://probe.example:3000',)`). The env var existing is not the claim being
  made; the settings layer reading it is.
- **Behaviour, actually observed.** `WORKER_ID`/`WORKER_POLL_INTERVAL` from the
  worker's boot line (`opspilot-worker[probe-worker-id]: ... polling every
  2.5s`). `MODEL_TIMEOUT_SECONDS` through the production builder with nothing
  hand-passed: `build_worker_provider()` gave an adapter whose
  `AsyncOpenAI(...).timeout` is 17.5, not the default 60.0; a call with a 1ms
  timeout raised `APITimeoutError`. `EMBEDDING_DIM=256` crashed the api with
  `DimensionMismatch ... dim=256 does not match ... 1536` - an error only
  constructible when `settings.embedding_dim` is 256, so the crash is itself
  the proof.
- `.env` restored, `--force-recreate`, four services healthy, worker back to
  `worker-compose` / `1.0s`, and the settings layer back to 60.0 / 24 / 1536.

### Two more of the same defect, and they are not in compose

The sweep found two settings that compose now passes correctly and that **nothing
reads anyway**. Same shape as M10, one layer further in: declared, documented,
and consumed by no code.

**`MAX_STEPS` is read by nothing.** The chain is intact right up to the settings
object - compose passes it, `get_settings().max_steps == 7` inside the container
- and then stops:

```
settings.max_steps = 7
RunContext.max_steps built by the worker = 24
```

`worker/loop.py::_build_context` constructs `RunContext(...)` without a
`max_steps=` argument, so the dataclass default in `agents/state.py:62` always
applies and `agents/runtime.py:985` reads `ctx.max_steps`. The budget is a
literal `24` in a second file - exactly the shape `RETRIEVAL_MIN_SCORE` was in
before M6a.

**This means the compose fix does not unblock the recorded end-to-end run.**
`MAX_STEPS=40` in `.env` still yields a 24-step budget. The fix is one argument
in `_build_context`, which is in `src/` and outside this task's scope; it is
recorded here rather than made.

**`STORE_FULL_PROMPTS` is read by nothing.** `grep -rn store_full_prompts
src/` returns only the declaration. `TraceRecorder.record_step` writes
`input=input_payload` unconditionally, so `STORE_FULL_PROMPTS=false` changes
nothing.

### The guard

`tests/unit/test_compose_settings_are_wired.py` makes "a setting is configured
but unreachable" a red build. Five assertions: every alias reaches `api` **and**
`worker`; every `${VAR:-x}` fallback equals `settings.py`'s default; the
per-process `MCP_*_COMMAND` fallbacks stay blank; the token keeps its `:?` form;
`migrate` still points at the compose Postgres. Failures name **every** missing
variable, because the twelve were missing together.

Both, not either, is deliberate. Each service constructs the same `Settings`, so
a variable present for one and absent for the other is a deployment whose halves
disagree - which is this defect running in reverse, and is how
`OPSPILOT_CORS_ORIGINS` reached the api alone until now. The worker's copy is
inert (it serves no CORS middleware) but its `Settings` runs the same
`WildcardCorsOrigin` validator, so a wildcard in `.env` now stops both
containers rather than only the api.

The YAML is parsed with a regex: `PyYAML` is installed in the venv and is **not**
declared in `pyproject.toml`, so importing it would pass here and fail in a clean
CI environment - the "a dependency only the author's machine happens to have is
a dependency the package does not really have" defect this repository has already
recorded once, on `pgvector`.

Six mutations were run against it and all six went red: deleting a variable from
the worker; changing a fallback to disagree with `settings.py`; replacing the
token's `:?` with a `:-`; dropping `OPSPILOT_CORS_ORIGINS` from one service
only; hardcoding a fallback for a per-process setting; and making a recorded
exemption stale (which fails on its own, so an exemption cannot outlive the
decision it describes).

### Quality gates

`ruff check` clean, `ruff format --check` clean (199 files), bare `mypy` clean
(171 files), `docker compose config --quiet` exit 0, **777 passed / 8 skipped**
(up from 756).

One existing test failed during this work and the fix belongs in compose, not in
the test: `test_compose_default_provider_has_fixtures.py` reads the *first*
`${OPSPILOT_FAKE_SCENARIO:-...}` in the file and fails on an empty one. An empty
default in the `api` - correct on its own terms, since the api builds no provider
- made that guard report a working stack as broken. It is the same class of lie
in the other direction, so both services now default to `duplicate_charge`.

---

## The two settings compose delivers and the code ignores

**Date:** 2026-10-10. Fourth and fifth recurrence of the M10 defect family, and
the two the previous section recorded as "found but not fixed" because they sit
in `src/` rather than in compose. Fixes `worker/loop.py`, `agents/state.py`,
`agents/runtime.py` and `tracing/recorder.py`; adds two test files.

### Why these two, and not the twelve

The compose sweep proved a value can reach the container and still change
nothing. Compose was fixed and made unfixable-in-silence. These two are the
remaining instances *below* that layer, and both had the same signature: the
setting is declared, documented, templated into both services, and consulted by
no code.

### `MAX_STEPS` was three copies of a number, and the worker read none of them

The brief described two copies of `24`. There were **three**:

```
settings.py:235          max_steps: int = Field(default=24, alias="MAX_STEPS")
agents/state.py:62       max_steps: int = 24                    <- dataclass default
agents/runtime.py:72     _DEFAULT_MAX_STEPS: Final[int] = 24
```

and the worker read none of them, because `_build_context` built a
`RunContext(...)` without `max_steps=`, so the dataclass default always applied.
That is the whole chain, and it is why compose being fixed did not unblock the
run recorded above: `MAX_STEPS=40` now reaches the container and is read by
nothing.

**The single definition, and why it is a function.** `settings.py` keeps the only
definition; `agents/state.py` exposes `default_max_steps()`, and both
`RunContext.max_steps` (via `field(default_factory=...)`) and
`runtime._DEFAULT_MAX_STEPS` reference it.

The obvious version of this fix — a module constant
`DEFAULT_MAX_STEPS: Final[int] = _settings_max_steps()` — is *wrong*, and the
first draft of this work was that version. It reads the environment once, at
import, and keeps that number for the life of the process: a deployment or a test
that sets `MAX_STEPS` after the module loads gets the earlier value. That is the
same stale-copy defect wearing a different hat, so `default_max_steps()` is a
function and the dataclass field uses a `default_factory`, resolving per
construction. `test_the_default_follows_the_setting_after_import` exists only to
pin that, and it fails against the constant version.

`worker/loop.py::_build_context` now passes `max_steps=` on **both** of its
return paths. The missing-ticket fallback constructs a second, different
`RunContext`; wiring only the happy path would leave the malformed-row branch on
the stale literal, which is this defect one branch in. Mutations 1a and 1b below
drop one branch each and each turns exactly the corresponding test red.

### `STORE_FULL_PROMPTS`: the docstring described an unimplemented idea

`tracing/recorder.py` claimed, in its module docstring, that "Phase 1 stores the
full prompt and response in `agent_steps.input`/`output` by design
(`STORE_FULL_PROMPTS`)". **No prompt was ever stored.** Every `record_step`
payload in the codebase is structured metadata:

```
{provider, model}  {category, confidence}  {count, document_slugs}
{tool_name, done}  {from_status, to_status}  {escalated, chars, body}
```

The single verbatim model string in the entire trace is the `response` step's
`body`. So the docstring was not describing a decision that had gone unimplemented
— it was asserting a behaviour that did not exist, and in doing so it
mis-described the present as well. `docs/data-model.md` and
`docs/limitations.md` repeated the same claim in the same way, and
`.env.example` documented "stores full prompts".

That judgment decided the fix. The alternatives were to implement prompt storage
so the sentence became true, or to wire the switch to what is actually stored and
correct the sentence. Storing prompts is a privacy regression invented to satisfy
a comment; the switch is wired to the reply, and the docs now say what they mean.

`record_step` is the single place the redaction is applied, so a caller added
later cannot start recording prompts without passing through it.

**Scope is a named list, not a heuristic.** `_FULL_TEXT_KEYS = {"body",
"prompt"}`. A blanket "strip every string" rule would empty exactly the fields
the trace exists for — `{from_status, to_status}` is the state machine,
`{tool_name, done}` is the plan — leaving a trace that cannot answer a question.
That is the same failure `limitations.md` describes from the other side: a trace
you cannot read is not a trace. Mutation B below does precisely this and is
caught.

**The key is replaced, not dropped.** `repositories.get_customer_reply` reads
`output["body"]` to serve `RunDetail.customer_reply` (`docs/api-contract.md`
§3). Dropping the key breaks a documented reader; replacing it with
`[not stored: STORE_FULL_PROMPTS=false]` says "a reply happened and was
withheld", which is the same reasoning `_record_model_call` already applies by
writing `None` rather than `0`. Every other field survives — `chars`,
`escalated`, `latency_ms`, `step_type` — so a withheld step is still a countable
step. Mutation C below drops the key and is caught.

**`record_audit` is deliberately outside the switch.** No audit payload carries
model text: `reason` and `risk_explanation` come from `domain/policies.py`, which
is static code by construction. The one free-text field is
`approval_requested`'s `arguments` — the exact payload a human approves
(`docs/tool-permissions.md` §3.2), mirrored into the ledger so the record of
*what was authorised* survives. Redacting the evidence of an approval is not a
privacy control; it is the loss of the control's audit trail.

### The third hole: a full sweep of all 28 aliases

Every alias was taken from `Settings.model_fields` (not from a grep of
`settings.py`, so a field whose name and alias differ cannot be missed) and
crossed against every read under `src/`, resolving property indirection
(`tool_denylist`, `cors_origins`) and dynamic `getattr` dispatch. A first pass
reported six zero-read settings; four were false positives reachable only through
a `@property` or a computed `getattr` key. **The true zero-read list was exactly
the two fixed here**, and is now empty.

The narrower moral is that the naive scan was wrong twice over — it missed
`STORE_FULL_PROMPTS` in plain sight would have been caught by grepping the name,
and it produced four false positives that a less careful pass would have
"reported" as defects. A sweep that has not resolved indirection is not a sweep.

### The guard

`tests/unit/test_worker_honours_max_steps.py` (9 tests) and
`tests/unit/test_store_full_prompts_is_wired.py` (9 tests).

The MAX_STEPS file pins four things: the worker *passes* the configured budget on
both paths; the default is still 24; exactly one module under `src/` holds a
literal for it (parsed with `ast`, so `0x18` is caught too); and `run_loop`
enforces the context's budget rather than the default, since it computes
`ctx.max_steps or _DEFAULT_MAX_STEPS()` and a stale context wins *silently*.

Recording what the worker hands the `RunContext` constructor, rather than only
reading `ctx.max_steps` afterwards, is not incidental. After the
`default_factory` fix both paths produce the correct value, so an assertion on
the result alone **passed with the argument removed** — the first mutation run
caught this, and it is why the test spies on the constructor call.

### Verified

`ruff check` clean, `ruff format --check` clean (164 files), bare `mypy` clean
(72 files), **797 passed / 8 skipped** (up from 777; the 20 new tests, no
regressions).

Seven mutations, all red, then green on restore:

| # | mutation | caught by |
|---|---|---|
| 1a | drop `max_steps=` from the missing-ticket branch | `test_worker_passes_the_configured_budget_to_the_context[ticket_missing]` + `test_the_budget_is_not_the_literal_twenty_four[ticket_missing]` |
| 1b | drop `max_steps=` from the happy-path branch | `test_worker_passes_the_configured_budget_to_the_context[ticket_found]` + `test_the_budget_is_not_the_literal_twenty_four[ticket_found]` + `test_the_default_is_still_twenty_four` |
| 2 | restore the literal `24` in `RunContext` | `test_state_does_not_redefine_the_budget_from_a_literal` |
| 3 | `runtime._DEFAULT_MAX_STEPS` re-freezes its own `24` | `test_runtime_does_not_redefine_the_budget_from_a_literal` |
| 4 | `default_max_steps()` resolves to a hardcoded `24` | `test_the_default_follows_the_setting_after_import` |
| A | remove the `STORE_FULL_PROMPTS` gate entirely | withheld + prompt (2) |
| B | blanket "strip every string" instead of named keys | non-text untouched + 2 |
| C | drop the `body` key instead of replacing it | withheld + complete + prompt (3) |

Mutation 4 is the one worth remembering: it is the only one that does not remove
a wiring, and it is what forced the `default_factory` design in the first place.

### Still not done

A fourth instance of this family is not proven absent — only that all 28 aliases
are read. A setting that is read but whose value is discarded on the way to its
effect (a parameter accepted and never consulted) is the same shape one level
further in, and this sweep does not look for it.

---

## The Definition of Done, re-scored after the settings defects

**Date:** 2026-10-10, at `472c09a`, on a clean tree. The previous score was
**18 of 19** (`c96c289`, D19a recorded as a boundary). This pass re-ran every
line rather than reading it, and the score is **18 of 19** again — but not for
the reason it was 18 last time, and with one new fact that changes how D10 must
be read.

**The new fact first, because it is the one that matters: CI has been red since
the day it was written, and every commit since has never been pushed.** `gh run
list` returns exactly two runs, both `failure`, both on 2026-10-08, the last at
`6ae3d94`. `main` is **17 commits ahead of `origin/main`** — so none of M11,
M12, D19a, or any of today's settings work has ever executed on a GitHub
runner. D10's jobs exist and the commands match the README; **no job has ever
passed.** That was true before today and was not written down.

### 18 of 19

| # | Verdict | This round's evidence | Changed? |
|---|---|---|---|
| D1 | met | `ruff check .` -> `All checks passed!` | no |
| D2 | met | `ruff format --check .` -> `201 files already formatted` | no |
| D3 | met **locally**, see below | bare `mypy` -> `Success: no issues found in 173 source files` | no |
| D4 | met | `pytest -q` -> **797 passed, 8 skipped** in 48.69s | yes, 756 -> 797 |
| D5 | met | `pytest tests/security` -> 30 passed; `test_invariants.py` -> 6 passed | no |
| D6 | met | `test_permission_immutability.py` + `test_prompt_injection.py` -> 9 passed | no |
| D7 | met | `-k "issues_create or audit_event or tool_executed"` -> 5 passed | no |
| D8 | met, wording is wrong | 28/28 `Settings.model_fields` aliases present; 28 keys in `.env.example` | no (see the note below) |
| D9 | met | `import opspilot` -> `G:\OpsPilot\src\opspilot\__init__.py`; `pip show` 0.1.0 | no |
| D10 | **met as written, with a standing caveat** | ten job ids in `ci.yml`: `lint typecheck test verify mcp-contract security eval-smoke web-lint web-typecheck docker-build`; README documents bare `ruff check` / `ruff format --check` / `mypy` / `pytest` and each job runs exactly that | **no — but see the CI section below** |
| D11 | met | `eval-live.yml` triggers on `workflow_dispatch` alone | no |
| D12 | unmet, as D12a | `docker-compose.yml` declares `postgres, migrate, api, worker, web` and no MCP services | no |
| D12a | recorded-as-boundary | 40 stdio-transport tests pass; `MCP_TRANSPORT` is settings-read (now `src/opspilot/settings.py:404`, then `worker/__main__.py:433`) | no |
| D13 | met, verified live today | stack up 8–31h; `/ready` from inside the api -> `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`; worker `booted; marked 0 interrupted run(s) failed; polling every 1.0s` | no |
| D14 | met | README sections (as of `cb457f4`, when this row was scored): golden path (56), five scenarios (157), Architecture (223), Evaluation (292), limitations link (29); `docs/assets/golden-path.gif` = 359,981 bytes. (The README has since grown; those sections now start at lines 176, 471, 537, 606, 109.) | no |
| D15 | met, divergence documented | every metric value matches `evals/results/2026-10-08T05-24-41.json` (0.750 / 0.500 / 0.110 / 0.600 / 0.067 / 0.634 / 1.000 / 0 / 0.444); the `model` field differs and the README's model-name note (at `cb457f4` README:343-360) explains why | no |
| D16 | met | `limitations.md` §2 now states that no worker-concurrency test exists and that the old citation "never did" | no |
| D17 | met | M0–M12 all have sections | no |
| D18 | met | README:21 (at `cb457f4`) -> `Status: Phase 1, milestones M0–M9 — the golden path runs end to end.` (the banner has since moved to README:55 with Phase 2 wording) | no |
| D19 | unmet (its last strand is D19a) | the fixture proposes four calls — `crm.get_customer`, `billing.get_invoice`, `billing.list_transactions`, `billing.issue_refund` — and none creates an issue | no |
| D19a | recorded-as-boundary | `README.md:94` (at `cb457f4`; now README:92) states the tool is implemented and tested in isolation but not proposed by the fixture; `limitations.md` §8 item 1 carries the same | no |

**D19a is not the nineteenth point.** It is a recorded boundary, and D19 stays
unmet because of it. That is unchanged from `c96c289`, and it is the whole
reason this file reads 18 of 19 rather than 19.

### Two verdicts that are narrower than "met"

**D3 is green on this machine and would be red on the CI runner.** Bare `mypy`
here says `Success: no issues found in 173 source files`. The same command with
`--platform linux` says:

```
tests\integration\test_worker_entry_point.py:289: error: Module has no
attribute "CREATE_NEW_PROCESS_GROUP"  [attr-defined]
Found 1 error in 1 file (checked 173 source files)
```

That is not a hypothetical. It is the exact error, the exact file, from both
real CI runs: `test_worker_entry_point.py:280: error: Module has no attribute
"CREATE_NEW_PROCESS_GROUP"` (run 37741225317) and `:278` (run 37815712700).
`subprocess.CREATE_NEW_PROCESS_GROUP` exists only in the Windows typeshed
branch, and the line is guarded by `sys.platform == "win32"` at runtime — which
is precisely why a Windows developer sees a clean `mypy` and a Linux runner does
not. The line arrived in `d6dac61` and is still at `tests/integration/
test_worker_entry_point.py:289`. **So D3 is met as a statement about this
machine and unmet as a statement about the gate the `typecheck` job runs.** It
is counted met because the DoD line is about the command, and the command is
clean here; but the honest reading is that the job is red and always has been.

**D8's wording is now wrong, and it has been for a while.** The item reads
"Every setting the code reads appears in `.env.example` with an empty value."
The completeness half holds exactly — 28 of 28 aliases present, checked against
`Settings.model_fields` rather than a grep of `settings.py`, so a field whose
name and alias differ cannot be missed. The **empty-value** half does not hold
and was never meant to: only **12 of 28** values are empty
(`OPSPILOT_OPERATOR_TOKEN`, `MODEL_NAME`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`, `WORKER_ID`, the three
`MCP_*_COMMAND`, `OPSPILOT_TOOL_DENYLIST`, `OPSPILOT_REFUND_CEILING`). The other
16 carry their shipped defaults — `MODEL_PROVIDER=fake`, `EMBEDDING_DIM=1536`,
`MAX_STEPS=24`, `STORE_FULL_PROMPTS=true`. That is the correct design, and
`.env.example` is explicit that it documents settings rather than
silently-everything. D8 is scored met on the half that was ever true.

### The CI standing that should have been in D10 a week ago

`gh run list` — the entire history:

```
completed  failure  fix: an exception escaped call_tool and killed the worker  37815712700  2026-10-08
completed  failure  feat: the five demo scenarios, and a GIF that is a real run 37741225317  2026-10-08
```

Both red, both a week old, both at or before `6ae3d94`. `git rev-list --count
origin/main..main` -> **17**. What each run failed on, read from the logs:

- **`typecheck` — still red at HEAD.** `CREATE_NEW_PROCESS_GROUP`, reproduced
  above with `--platform linux`.
- **`verify` — was red, cause since fixed.** `psycopg.errors.DatatypeMismatch:
  column "embedding" cannot be cast automatically to type vector`. That is the
  M11 migration defect, fixed in `531b9f0`. The fix has never been exercised by
  the job.
- **`web-lint` / `web-typecheck` — was red, cause since fixed.** `npm ci can
  only install packages when your package.json and package-lock.json ... are in
  sync`, naming `@emnapi/*`. Fixed in `cceec90`; `npm ci --dry-run` in `web/`
  now says `up to date`.
- **`lint`, `security`, `mcp-contract`, `test`, `eval-smoke`, `docker-build`** —
  green in the last run.

So: of ten jobs, **one is known-red at HEAD** (`typecheck`), two were red for
causes since fixed but never re-run, and the seven others passed. That is a
materially different statement from "the CI jobs exist and run the documented
commands", which is all D10 claims and all that is met.

**Today's `verify`-job edits have never been executed either.** `661dbdc`
rewrote that job to name each pgvector test by node id and assert it PASSED,
assert the marked selection is skip-free, and assert exactly two are collected.
None of those three assertions has run on GitHub. What *is* verified is that
they are locally correct against a real pgvector 16:

```
OPSPILOT_DATABASE_URL=postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot
pytest tests/integration/test_citations.py::test_pgvector_and_memory_stores_agree \
       tests/unit/test_vector_stores.py::test_pgvector_store_satisfies_shared_contract -rA -v
  -> 2 passed in 10.71s        (both PASSED, neither SKIPPED)
```

and that the guard's own shell logic does what it claims — replaying the two
`grep` conditions against that output gives *WOULD MATCH* and *WOULD PASS*.
Without the URL the same selection is `1 skipped, 14 deselected`, so the guards
are not vacuous: they turn a skip into a failure. **Local logic correct, CI
behaviour unverified** — the honest description of every assertion added to CI
in the last 17 commits.

### The recurrence count for today's defect family

The brief's framing was: M10 is 1, the compose gap is 2, `MAX_STEPS` is 3,
`STORE_FULL_PROMPTS` is 4, the two skip-stubs are 5. Re-checking the record,
**the count is 6, and the extra one is `LOG_LEVEL`.**

| # | Instance | Where it was found | Status |
|---|---|---|---|
| 1 | `MCP_*_COMMAND` declared, documented, read by nothing | M10 (`d19a6b8`) | fixed `22d7c27` |
| 2 | **`LOG_LEVEL` documented and templated into both services, read by no code** | M12 (`c8cc1b0`) | fixed `c8cc1b0` |
| 3 | twelve settings never reached the container | running the stack | fixed `1cc5e13` |
| 4 | `MAX_STEPS`: three literals, worker passed neither | same sweep | fixed `472c09a` |
| 5 | `STORE_FULL_PROMPTS`: read by nothing, three docs claimed otherwise | same sweep | fixed `472c09a` |
| 6 | two `postgres`-marked vector tests: unconditional `pytest.skip`, counted green everywhere | reading the suite | fixed `661dbdc` |

Number 2 is the one the count missed, and it belongs: the `c8cc1b0` message says
it outright — "LOG_LEVEL was a documented setting no code read, so changing it
in docker-compose.yml changed nothing: **a knob that turns nothing is worse than
a missing one because it looks like control**". Same sentence, same commit day,
one commit earlier than the compose sweep that found the other two. It is worth
counting separately because it was found by *reading* rather than by running,
which is the tell for what number 6 also was.

Number 6 is a different shape and arguably should not share the count. It is
not a setting that fails to reach its effect; it is a **test that cannot fail**,
and it was reported as a pass. The reason it belongs in the same tally is the
one `661dbdc` records: the failure ADR-0004's cost items exist to prevent
occurred *inside its own mitigation* — the CI comment said out loud that the
tests were stubs, and the step that followed existed only to count them.

**And the pattern is still not closed.** The last section above ends honestly:
"A fourth instance of this family is not proven absent — only that all 28 aliases
are read. A setting that is read but whose value is discarded on the way to its
effect … is the same shape one level further in, and this sweep does not look for
it." Six findings did not produce a rule that prevents a seventh.

### The gap: what the DoD could not have caught

**Yes — and it is the same gap three times over, which is the reason it is worth
naming rather than filing.**

D8 says "every setting the code reads appears in `.env.example`". It is a
completeness check on **one artifact**. It cannot see `docker-compose.yml`, so
instance 3 was invisible to it; it cannot see `RunContext`, so instance 4 was
invisible; it cannot see `TraceRecorder`, so instance 5 was invisible. Every
one of the three new families would have been caught by a D8 that had said
"…**and is read by the thing that acts on it**", because each was a setting
that existed, was documented, and had no reader at the far end. The `.env.example`
side of D8 was not the problem — it was right all along, which is what made the
compose divergence easy to miss: both files were self-consistent and the
deployment was the odd one out.

Two things DoD has **no row at all** for, which is why they survived six
instances:

1. **No row covers "a guard that cannot fail."** D4 asks that pytest be green
   with a non-zero skip count. A skip-stub is green with a non-zero skip count.
   Instance 6 satisfies D4 and violates the *intent* of D4 completely. Nothing
   in the 19 rows asks whether a test can fail.
2. **No row covers "the CI gate is red."** D10 asks whether the jobs exist and
   run the documented commands. All ten exist; all ten run those commands; the
   `typecheck` job has failed every time it has run. D10 is satisfied by a
   correct-looking YAML file over a codebase that does not type-check on Linux.

**Recommended — for the operator's decision, not taken here.** The DoD is
derived from claims the repository already makes about itself, and inventing rows
is a project decision. Three candidates, in the order I would rank them:

- **Strengthen D8 in place** rather than add a row: "Every setting the code
  reads appears in `.env.example` **and reaches the process that acts on it**,
  and is read by that process." One row, retargeted, and it would have caught
  3, 4 and 5 of the six. The guard for it already exists
  (`test_compose_settings_are_wired.py`, `test_worker_honours_max_steps.py`,
  `test_store_full_prompts_is_wired.py`) — the DoD would be catching up to tests
  that exist, not asking for new work.
- **Add a row: "Every marked test can fail."** A `skip` that is unconditional, or
  a stub with a body of `...`, is a green that means nothing. This is the only
  proposed row with no existing guard, and it is the one that would have caught
  instance 6.
- **Add a row: "The CI gates are green on the platform CI runs."** Distinct from
  D10 because D10 is about the file and this is about the run. It would have
  caught the `CREATE_NEW_PROCESS_GROUP` failure five days before it was found —
  and, honestly, it may not have: nobody has pushed in 17 commits, so the row
  cannot help until pushing is routine. **That is the real first fix and it is
  not a DoD row.**

### Commands run

All on `472c09a`, clean tree, `.venv\Scripts\python.exe`, PowerShell.

```
ruff check .                       All checks passed!
ruff format --check .              201 files already formatted
mypy  (bare)                       Success: no issues found in 173 source files
mypy --platform linux              1 error  <- CREATE_NEW_PROCESS_GROUP, reproduces CI
pytest -q                          797 passed, 8 skipped, 9 warnings in 48.69s
pytest tests/security              30 passed
pytest tests/security/test_invariants.py                     6 passed
pytest tests/security/test_permission_immutability.py
                       test_prompt_injection.py              9 passed
pytest tests/integration/test_mcp_stdio_transport.py        40 passed
pytest -k "issues_create or audit_event or tool_executed"    5 passed
pytest -m postgres --collect-only -q   5 collected (the postgres-marked tests then in the tree); `grep -c test_pgvector` over the two files carrying them -> 2 in `test_vector_stores.py` (its `test_pgvector_store_*` group) + 1 in `test_citations.py` (`test_pgvector_and_memory_stores_agree`)
  + the four new test files            41 passed
python -c "import opspilot"        G:\OpsPilot\src\opspilot\__init__.py
Settings.model_fields vs .env.example     28 aliases, 0 missing, 28 keys, 12 empty
docker compose config --quiet      exit 0
docker compose ps                  api healthy / postgres healthy / web healthy / worker Up 8h
  printenv MCP_TRANSPORT           inprocess
  get_settings() in worker         max_steps 24, timeout 60.0, store_full_prompts True
  /ready from inside api           {"status":"ready","checks":{"database":"ok","migrations":"ok"}}
OPSPILOT_DATABASE_URL=... pytest <both pgvector node ids> -rA -v   2 passed, both PASSED
(cd web) npm ci --dry-run          up to date
gh run list                        2 runs, both failure, both 2026-10-08
git rev-list --count origin/main..main    17
```

### What this changes, and what it does not

- **Unchanged:** the score. 18 of 19, D19's last strand a recorded boundary.
- **Newly established:** D13 was verified a second time, against a stack that
  has been up for eight hours, rather than once by hand.
- **Newly established:** the two `postgres`-marked tests are real and pass
  against a live pgvector 16. Before today they were two `pytest.skip` lines
  that reported green on every machine including CI's.
- **Newly established:** the `typecheck` job is red at HEAD, and D3 is green
  only because this machine is Windows.
- **Still not established:** that any CI job passes. It has never happened.

---

## The Definition of Done, re-scored after the guard commits

**Date:** 2026-10-10, at `584ac99`, on a clean tree. The previous pass was
`cb457f4` ("after the settings defects", measured at `472c09a`, 18 of 19). This
pass re-reads the two lines the four guard commits since then could move, and
records one recommendation from `cb457f4` that has since been *built*. The score
is unchanged: **18 of 19**, D19's last strand still the recorded boundary D19a.

### What moved, and only what moved

`cb457f4` scored **D3** as "green on this machine, red on the runner" — the
single narrowest verdict in the table, because bare `mypy` here said `Success`
while the same command with `--platform linux` reproduced the exact
`CREATE_NEW_PROCESS_GROUP` error both real CI runs had failed on. That verdict
is now obsolete, and the commit that made it so is `480882b`: the ternary in
`test_worker_entry_point.py` became a statement-level `if sys.platform ==
"win32"`, because mypy's platform narrowing only applies to statements, not to
expressions. Re-run here:

```
mypy                 (bare)         Success: no issues found in 177 source files
mypy --platform linux                Success: no issues found in 177 source files
```

**D3 is now met as a statement about the gate, not just about this machine.**
It is the one line in the table that was less than "met" for a reason other than
D8's wording, and the reason is gone. This is also the honest answer to
`cb457f4`'s third recommendation — "the CI gates are green on the platform CI
runs" — for the `typecheck` job specifically: that job's only known cause of
redness was this one error, and it is fixed. The recommendation is *not* retired
in general (see below); one job going green locally is not a green run.

### The recommendation that was built, not just recorded

`cb457f4` listed three candidate DoD rows "for the operator's decision, not
taken here." Its second candidate — **"Every marked test can fail"** — is the one
it flagged as having no existing guard and as the one that would have caught
instance 6 (the skip-stubs that satisfied D4 while violating its intent). That
guard now exists, in `584ac99`:

```
pytest tests/unit/test_marked_tests_can_fail.py     13 passed
```

It scans every marked test under `tests/` by AST and rejects an unconditional
`skip`, a `...` body or a `pass` body, while allowing a conditional skip
(`if not X: pytest.skip(...)`) as a legitimate environment gate. It was proved
able to fail before it was trusted: injecting one unconditional-skip stub and
one `...` stub turned it red on both `file:line`, and it returned to green when
they were removed. No real stub exists in the tree today — the 11 skips are all
M0 skeletons (`test_refund_idempotency.py`, `test_schemas.py`) or documented
environment gates (`test_vector_stores.py:418`, ADR-0004).

**This does not add a row to the DoD.** `cb457f4` was explicit that inventing
rows is the operator's decision, and that has not changed. What changed is that
the recommendation now points at a test that exists and passes, exactly as
`cb457f4` said its first candidate would ("the DoD would be catching up to tests
that exist"). The recommendation is promoted from "would have to be written" to
"already written, awaiting the row"; the row is still the operator's to add.

---

## CI is green: four root causes, one all-green run

**Date:** 2026-10-11, at `0144d83`. The run is stamped `2026-10-10T18:44:22Z`;
the commit's own timestamp is 02:44 +0800, which is why this section reads as
the 11th and the one above it as the 10th.

**The oldest standing limitation in this project is closed: "CI has never been
green" is no longer true.** `cb457f4` wrote the fact down ("CI has been red
since the day it was written, and every commit since has never been pushed";
"**no job has ever passed**"), `584ac99` carried it forward as the one thing
still not established, and `milestones.md` D10 had to narrow itself to a
statement about the workflow file rather than about a run. GitHub Actions run
**38076989911** on `main` at HEAD `0144d83` finished `completed` / `success`
with **11 of 11 jobs green**:

```
gh run list --limit 100                             5 runs in the repository's history
38076989911  success  0144d83  2026-10-10T18:44:22Z   <- the first one
38069532950  failure  5e3a373  2026-10-10T16:54:06Z
38067117974  failure  d6e1661  2026-10-10T16:18:22Z
37815712700  failure  6ae3d94  2026-10-08T17:19:50Z
37741225317  failure  c989ffc  2026-10-08T07:04:33Z

gh run view 38076989911 --json jobs --jq '.jobs | length'
11

gh run view 38076989911 --json jobs --jq '.jobs[].name'
lint (ruff)  typecheck (mypy)  test (py3.12)  test (py3.13)  verify (pgvector)
mcp-contract  security  eval-smoke  web-lint  web-typecheck  docker-build
```

Four of the five runs are `failure`; this is the only one that is not.

### The four root causes

Four commits, four independent defects — and not one of them could fail on this
machine. That is the part worth keeping: the suite here was green the whole time
CI was red, because three of the four were platform-specific and the fourth was
a job missing a dependency it needed.

| Commit | Root cause | Why every runner was red |
|---|---|---|
| `6f4b497` | the stdio spawn/reap tests enumerated processes with a PowerShell / `Win32_Process` helper | A Windows-only helper: those tests passed here and failed on every Linux runner. Now guarded by `skipif(sys.platform != "win32")`. |
| `732dd42` | `scripts/generate-types.mjs` was pointed at a hardcoded Windows venv interpreter | The cross-stack client-type guard ran the generator through a `python.exe` that exists on one checkout and nowhere else, so on Linux it could not run the guard at all. It now receives `OPSPILOT_PYTHON=sys.executable`, which resolves on any platform. |
| `5e3a373` | `web/src/app/layout.tsx` typed its props from the build-time-only `LayoutProps` global | That global is injected by the Next.js build, so on a fresh checkout it does not exist and `tsc` had nothing to check the root layout against. Replaced with an explicit `ReactNode` children signature. |
| `0144d83` | the `test` and `verify` jobs never installed the Node package their own guard needs | The type-drift guard shells out to `openapi-typescript`, which only `web-lint` and `web-typecheck` installed. Both jobs now run `setup-node` and `npm --prefix web ci`. |

`480882b` had already fixed the `typecheck` job's Windows-only
`CREATE_NEW_PROCESS_GROUP` error, and `584ac99` recorded the correct caveat:
one job green locally is not a green run. Three of these four were invisible
here for exactly that reason.

### Measured locally, and why CI's number is not the same number

`pytest` at `0144d83` on this machine: **814 passed, 11 skipped.** That replaces
the 797 passed / 8 skipped `cb457f4` measured at `472c09a`. The skip count is
still printed and still non-zero — all 11 are M0 skeletons or documented
environment gates, per `584ac99` — so a green run is not mistaken for full
coverage. The README status banner now carries these numbers.

CI's `test` job runs the same suite on 3.12 and 3.13 and is green, but its skip
count is not necessarily this one: `6f4b497` skips the Windows-only
process-enum tests on Linux, so the runner skips more than this machine does.
The two figures should not be quoted interchangeably.

### What this closes

- **Closed: "CI has never been green."** It is why D10 had to narrow itself to
  the workflow file, and why `cb457f4`'s third recommendation — "the CI gates
  are green on the platform CI runs" — stayed open. D10 loses its caveat and
  that recommendation is retired.
- **Closed: the 17-commit gap** as measured at `472c09a` (see the 2026-10-10
  audit above). The 10 commits from `472c09a` to `0144d83` are all on
  `origin/main`, so `0144d83` — the head that produced the green run — is
  published. They produced three runs rather than ten — `d6e1661`,
  `5e3a373` and `0144d83` — because they went out in batches, so `cb457f4`,
  `1de657e`, `480882b`, `bbf0dd7`, `584ac99`, `6f4b497` and `732dd42` were never
  the head of a run and CI has still not seen them individually.
- **Re-verified live: D13.** The Compose stack is up on this machine's WSL
  Docker daemon — `api` / `web` / `postgres` healthy, `worker` up, which carries
  no healthcheck because it serves no HTTP endpoint — and `MCP_TRANSPORT` in the
  running worker is `inprocess`.
- **Unchanged: the score, 18 of 19.** D19's last strand is still the recorded
  boundary D19a.
- **Unchanged, and outside this round's three files:**
  `docker-compose.yml`'s header still reads "NOT VERIFIED. Docker is not
  installed on the machine this was written on", which is now false in fact but
  is not a file this commit may touch; and M9's third criterion stays unchecked,
  because the three MCP servers are constructed inside the worker rather than
  brought up as Compose services. Both are recorded here rather than edited.

### What this does not establish

A green run is not a live-model run, and the two are easy to conflate, so the
difference is worth writing down. Every job in 38076989911 either ran a static
check or ran the suite on `MODEL_PROVIDER=fake`: `eval-smoke` is a plumbing check
by design, and `limitations.md` §5 already records that the fake provider's
numbers are not results. The twelve-metric table for a real provider is therefore
still not available — the OpenRouter free tier is exhausted, recorded earlier in
this log — and `eval-live`, the only thing that would produce it, is
`workflow_dispatch`-only and deliberately outside the automatic gate (D11).

What the run does establish is narrower and still real: the gates, the golden path
on recorded fixtures, and the cross-stack checks pass on the runner CI actually
uses, not only on this machine. That was the open question — three of the four
defects above could not fail here — and it is now answered for all eleven jobs.

---

## `knowledge.search` could never execute — found, fixed, and the score is unchanged

**Date:** 2026-10-12, at `148a87c` (committed 2026-10-11 +0800). Another
instance of §8's own defect class, and the first one found in the dispatch path
rather than in a settings line.

### What was wrong

`architecture.md` §7 lists `knowledge.search` in the `READ` row — automatic,
no approval — and `adapters/wiring.py::RetrievalStack` has bound a `KnowledgeTool`
since retrieval landed. But neither fact reaches the code that runs tools. The
runtime has exactly one dispatch call site (`agents/runtime.py:638`), and both
assembly points handed it a bare `MCPToolGateway` that never referenced
`KnowledgeTool`. `knowledge.search` is not an MCP server — `mcp-contracts.md` §4
says so, and `domain/tools.py` registers it as `server="internal"` — so
`mcp_gateway.py::_server_for` returned `None` for it, `_KNOWN_SERVERS` being
`{crm, billing, issues}` alone. Every proposal became
`ToolResult(ok=False, error="server_unavailable")`, on the eval path and in
production alike.

It was invisible because `tests/integration/test_knowledge_is_not_mcp.py`
asserts only that the MCP gateway *refuses* `knowledge.search` — correct, and
still true after the fix — and no test covered what happened when the model
actually proposed it. The golden-path fixture never does, being the D19a
boundary.

### What was measured

Live during the 2026-10-11 eval: four `server_unavailable` failures. In the
committed run `evals/results/2026-10-11T15-39-42.json`, `results[].executed_tool_calls[]`
filtered to `tool_name == "knowledge.search"` gives **35 proposed, 35 executed,
0 failed** — counted by `status`, not asserted by a test.

The fix is `adapters/tools/composite_gateway.py::CompositeToolGateway`, which
routes on `TOOL_REGISTRY[name].server` at call time,
covered by `tests/integration/test_composite_gateway.py` (9 tests; reverting the
two call sites to `148a87c^` fails exactly the eval and worker wiring tests).

### What this does not establish

**The Definition-of-Done score is unchanged at 18 of 19, and D19 stays unmet.**
`knowledge.search` was never recorded as a D19 strand. The score counts DoD rows,
not §8 entries; D19's only unmet strand remains D19a, the `issues.create` product
decision of 2026-10-09, which this work does not touch. Fixing an *unrecorded*
instance of the defect class closes a gap; it does not change a row's verdict.
There is no D19b.

This is the same conclusion `cb457f4` reached for a different fix: the
recommendation may point at work that exists, but adding a row is the operator's
decision, not the code's.

---

## A §8 instance no row names does not move the score

**Date:** 2026-10-12.

**The rule, now standing rather than re-derived per instance:** an instance of
`limitations.md` §8's defect class — a capability a document advertises that no
code performs — that was never recorded as a Definition-of-Done row does not
generate one **by itself**, and does not move the score. Adding a row to the DoD
remains an operator's decision; the rule only says the finding does not make that
decision for anyone. `docs/milestones.md` now states this in the paragraph under
the DoD table.

### Why it was written down

D19's cell ends "**Its last strand is recorded as boundary D19a**", and *last
strand* is a negative nobody re-derives: it asserts that no other documented
capability is unimplemented. That was true when it was written and is true now,
but §8 is precisely the class most likely to produce the exception, and it has
repeatedly produced instances that were never recorded as rows — three in
M2–M5, more in M9's Definition-of-Done audit, and `knowledge.search` at `148a87c`
most recently. A row that rests on a negative about that class is one discovery
away from being wrong, so the score's stability needed to be stated rather than
assumed.

The reasoning is short. §8 is a catalogue; the DoD is a scored checklist. Adding
an entry to one is not adding a line to the other. And a row exists because its
audit re-verifies it — a §8 instance that no row names has no audit to re-run, so
it has nothing to move. Stating that once means the next instance is recorded
rather than re-litigated.

### The worked example, and the D19a convention beside it

`knowledge.search` was found, fixed in `148a87c`, recorded in §8's Fixed list, and
measured in the committed run `evals/results/2026-10-11T15-39-42.json` at 35
proposed, 35 executed, 0 failed — the same run and the same counting as the
section above. The score was **18 of 19**, D19 stayed unmet, and there is no
D19b.

That is a different claim from the one already recorded at the 2026-10-10 audit,
and the two sit next to each other without merging. **D19a is not the nineteenth
point** is about *that* row: a recorded boundary that leaves D19 unmet and is the
whole reason the file reads 18 of 19. This rule is about the instances *no* row
names. Neither one moves the score, and they answer different questions — the
first says why an existing row is red, this one says why a red row does not also
acquire new ones.

## An error in commit `8c558b1`'s message that cannot be corrected

**Date:** 2026-10-12.

Commit `8c558b1` ("docs: stop describing the orchestrators and the fixtures as
what they are not") ends its message with:

> four other documents carried the same false sentence and are corrected.

**Four is wrong; five carried it.** `docs/risks.md` stated it twice — in R2's
mitigation and again in R8 — and once each in `docs/agent-state-machine.md:248`,
the README's architecture section (README:551 when this was written; the
architecture diagram has since been rearranged) and `evals/README.md`. The word "other" also miscounts: R2 was
not an "other" document, it was one of the places making the claim.

The commit is correct; only its message miscounts. Every file it names was
fixed, and `docs/limitations.md` §8 now records the instance properly.

**This cannot be corrected in place.** A commit message is content-addressed:
changing `8c558b1` would change its hash and rewrite every commit after it.
The only alternatives are history rewriting or a `git notes` attachment, and
both cost more than the error is worth. It stays, and it is recorded here
instead.

Two things made it persist. The agent doing the work reported the count as
"four documents", which was right, and the brief it worked from had said
"five documents", which was wrong — so the number that survived into the
message was the agent's count with my wording wrapped around it, and nobody
re-counted the files against the diff. The second is the ordinary failure:
a figure carried between two conversations is a claim, not a measurement, and
`06a57c6` set the opposite precedent for this file — every count there was
taken from the repository at the time of writing.

## Phase 1 closes: every milestone audited, four defects fixed, CI at twelve jobs

**Date:** 2026-10-12. Spans `98071c2` through `bf29b69`, 22 commits.

### What the audit found

`06a57c6` checked all 84 M0–M8 acceptance criteria against the repository and
ticked the 70 that held, each with its evidence, leaving 14 that did not. Four
of those turned out to be real defects rather than stale wording, and none was
findable from the test suite:

- **`knowledge.search` could never execute** — on the eval path or in
  production. `architecture.md` §7 advertises it as a `READ` tool and
  `RetrievalStack` had bound a `KnowledgeTool` since retrieval landed, but the
  runtime's only `gateway.call_tool` site was handed a bare `MCPToolGateway` at
  both assembly points. Found by a live eval run, not by a test. Fixed by
  `CompositeToolGateway` (`148a87c`); the committed run then proposes it 35
  times and executes 35, none failed.
- **`orchestrator` was discarded.** `run_loop` did `_ = orchestrator` and both
  implementations raised `NotImplementedError`. Four tests asserted the
  *opposite* — their docstrings said "the pump must not consult it" — so the
  port looked wired and was not. Fixed by extracting the stage functions into a
  `StageDriver` the orchestrator dispatches through (`fcc8446`), with a spy test
  mutation-verified to fail if the orchestrator is bypassed.
- **`EMBEDDING_MODEL` defaulted to an OpenAI model name** the local provider
  never calls, and an eval had recorded it beside `embedding_provider: "local"`
  (`959855d`).
- **The containerised dashboard could not reach the API** — `client.ts`
  resolved one base URL for both server and browser, and `localhost` inside the
  container is the container (`bf29b69`).

Six more were false claims about the fixtures, the idempotency constraint, the
gold citations and `/ready`'s exemption; each was corrected to what the code
does, and the two that mattered for safety said so in their new wording — the
refund key is the handler's linear scan, not a UNIQUE constraint, and it holds
for one process serving calls one at a time.

### What the second live run measured

`evals/results/2026-10-11T15-39-42.json`, MiniMax-M3.1-Flash-Preview over 70
cases:

```
classification                20   0.800  (16/20)      unsafe execution count  15   0
retrieval recall@5            20   0.700  (14/20)      task completion          9   1.000 (9/9)
retrieval precision@5         20   0.170                approval-policy          1   1.000 (1/1)
abstention correctness         5   1.000  (5/5)         tool argument validity   --   0.471 (80/170)
                                                       tool selection          15   0.200 (3/15)
```

`unsafe execution count 0` is the property being claimed. `tool selection 0.200`
is the model, not the design, and the README says so.

### The score did not move

**18 of 19, unchanged.** D19 stays unmet for the reason it states: D19a, the
2026-10-09 `issues.create` decision, which none of this touches. A §8 instance
no row names does not generate a row — that rule is now written down, because
`knowledge.search` was one and the D19 row's "last strand" rested on a negative
nobody had re-derived.

### What went wrong, since the rule is to record it

- **`8c558b1`'s message miscounts** — "four other documents" where five carried
  the claim. Unfixable in place; recorded at `f829897`.
- **`6d79fbe` claimed a CI job it did not commit.** The message says "A web-e2e
  job runs it on push"; `git add` had not taken and CI ran eleven jobs without
  it. Found by reading the job list rather than the message, and committed at
  `b8965d5`. A document describing a mechanism that does not exist, in a commit
  message instead of a file.
- **The first suggestion to fix the trace was wrong.** `state_change` latency is
  NULL by M7a's decision — recorded in `progress.md` and argued in two frontend
  comments, and neither was read before recommending `latency_ms=0`. The change
  would have rendered fourteen rows as `0` in the dashboard. Reverted at
  `c799464`.

### Where Phase 1 ends

Six criteria remain unticked, each with its reason recorded rather than dropped:
three are M0 assertions true only of a tree that no longer exists, one is the
hand-written fixtures (`record_fixture()` exists and has never run against a real
provider), one is `LangGraphOrchestrator`, and one is the D12a boundary. The
browser walkthrough M7 could not reach — `docker-build` proves the stack comes up
but drives no run, and `web/` had no test runner — is now guarded by a job that
clicks through the real UI against the real stack, green on every push.

---

