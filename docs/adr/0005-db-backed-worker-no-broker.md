# ADR-0005 — A database-backed worker, no message broker

**Status:** Accepted (Phase 1)

## Context

The agent loop must not run inside a FastAPI request. A ticket submission must
return immediately, and a run that parks for two hours waiting on a human
approval cannot hold an HTTP connection or a process.

The conventional answer is a broker: Celery with Redis, RQ, Dramatiq, or a
workflow engine like Temporal. Each brings a queue, a result backend, retry and
scheduling semantics, and a second piece of infrastructure to run, monitor and
back up.

Phase 1 has exactly one job type (drive a run) and expects tens of runs, not
thousands. The specification explicitly forbids Redis, RabbitMQ, Celery, Kafka
and Temporal in this phase.

## Decision

The run table is the queue. A worker polls it.

```sql
SELECT * FROM agent_runs
WHERE status IN ('received','classifying','retrieving','planning','executing','responding')
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

The claim set is computed from the enum, not typed as string literals:

```python
CLAIMABLE = frozenset(RunStatus) - {WAITING_APPROVAL, COMPLETED, FAILED}
```

so a run parked on approval is never re-claimed while it waits — the property
that makes the approval gate actually stop work rather than loop on it.

The worker runs as a separate process (`apps/worker/main.py`), in its own
container in Compose. Approval is **edge-triggered**: `POST
/api/approvals/{id}/approve` transitions the run back to `EXECUTING`, which puts
it back inside the claim predicate, and the worker's next poll picks it up.

## Consequences

**Good.** No second datastore. The queue's state is the run's state, so there is
no possibility of "the job is in Redis but the run says completed" — a class of
bug that costs real debugging time. `SKIP LOCKED` lets two claims that overlap in
time take different rows without blocking. **Corrected:** this ADR said that made
"scaling out is adding replicas" true. It does not, as implemented — the lock
lasts only for the claim's own `SELECT`, and the claim does not change the run's
status, so two workers whose polls do not overlap both receive the same run
(measured: 30/30 concurrent pairs). The clause is tested
(`tests/integration/test_worker_claim_postgres.py`) and is real; the end-to-end
guarantee is not, and `docs/limitations.md` §2 records the boundary. Run one
worker per database until a claim that holds its row is written. The whole
mechanism is one SQL statement that a reviewer can read in five seconds, and it
works without any process running at all — a parked approval survives the worker
being
down for a day.

**Later decision, and what it costs.** The claim now holds its row for a worker
that is driving it, via a lease rather than a status change (`b9bbd5c`;
`migrations/versions/0002_claim_lease.py` — a status change would have had to
make `EXECUTING` unclaimable, which §5 of `docs/architecture.md` explains would
strand approved refunds). That closes the duplicate above and it changes what
this ADR's own advice ("run one worker per database") now means: one worker per
database is no longer a correctness requirement. The lease's own boundary is
recorded in `docs/limitations.md` §2 and is worth repeating here, because this
ADR is where a reader looks to decide whether the claim is safe — **a run that
is claimed and then abandoned stays `RECEIVED` for up to one lease period
(300 s), and nothing in the API, the boot sweep, the dashboard or the worker's
log distinguishes that from a run that was never claimed.**

**Cost.** Polling means up to `WORKER_POLL_INTERVAL` (default 1s) of added
latency, and an idle worker issues a query per second per replica. Both are
acceptable at this scale and both are honest: the README's latency figures
include the poll delay rather than measuring only the work.

**Cost.** No retry, no dead-letter queue, no scheduling. A run whose model call
fails fails. Phase 2's retry work will likely still not need a broker — a
`next_attempt_at` column covers it — but if a second job type appears (a nightly
reindex, a cost rollup), the decision should be revisited rather than
accumulated onto this one.

**Cost.** `FOR UPDATE SKIP LOCKED` does not exist in SQLite, so the locking
clause is not exercised locally (see ADR-0004). The worker's claim code is
dialect-aware and the test that covers the clause is Postgres-only
(`tests/integration/test_worker_claim_postgres.py`, run by the `verify` CI job).
It asserts the clause's properties — overlapping claims take different rows —
and not the end-to-end two-worker guarantee, which the implemented claim does not
provide (see the correction above).

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Celery + Redis | Two extra services to run and monitor for one job type. A second source of truth for job state. Explicitly forbidden by the phase scope. |
| FastAPI `BackgroundTasks` | The failure the specification names: the work is tied to the process that received the request, so a web-server restart kills in-flight runs, and a run parked on approval has no home. |
| Temporal | Correct for a workflow with long waits and complex retries. Far more machinery than one workflow with one wait needs, and it would make the state machine a Temporal concept rather than a domain one. |
| A separate `jobs` table alongside `agent_runs` | Two rows to keep consistent for one concept, and the consistency has to be maintained by hand. The run *is* the job. |
| An in-process scheduler loop with no persistence | Cannot survive a restart, and the approval wait would have to be held in memory. This is the design the whole ADR exists to avoid. |
