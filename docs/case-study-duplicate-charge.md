# Case study — one duplicate charge, from intake to audited refund

**A real run, walked end to end, with every number traceable.**

This is not a reconstruction and it is not a diagram. It is one run of this
repository's golden path, captured on **2026-10-14** — PostgreSQL with pgvector,
the FastAPI service and the worker poll loop from `docker compose up`, with the
dashboard served by the host-built Next.js standalone server that the `web-e2e`
CI job drives. The containerised `web` service was broken on the day of the
capture — every one of its JavaScript bundles 404'd — and that has since been
fixed; see the README's *Running it* section. This run was taken on the host
server, and every figure and screenshot below comes from that run — driven in a
real Chromium by clicking, the way a person would. Every figure below is quoted
from that run's own trace, from the API, or from the billing MCP server's store.
The screenshots in [`assets/screenshots/`](assets/screenshots/) are the screens
it produced, in order.

**What is real here:** the agent's state machine, the five gates, the pgvector
retrieval, the persisted approval gate, the worker poll loop and `FOR UPDATE
SKIP LOCKED` claim, the idempotent refund, the audit trail, and the three MCP
servers. All of it is code in this repository and it ran.

**What is simulated, stated plainly:**

- **The CRM, Billing and Issues systems are in-repo mocks.** They are real MCP
  servers — the official Python SDK, the real MCP protocol, the real
  `ToolGateway` contract, all nine tools specified in
  [`docs/mcp-contracts.md`](mcp-contracts.md) — with deterministic seed data
  standing in for Salesforce, Stripe and Jira. This run came up through
  `docker compose up`, which leaves `MCP_TRANSPORT` at its default,
  `inprocess`: the servers are built inside the worker process, so the stdio
  pipe itself is the one thing this run did not exercise (that is
  `MCP_TRANSPORT=stdio`, and `docs/mcp-contracts.md` §7 covers it).
  **There is no third-party billing
  system behind `billing.issue_refund`.** No card is charged, no money moves, and
  nothing here has touched a payment network.
- **The model's proposal in this particular run came from the deterministic
  provider** (`MODEL_PROVIDER=fake`), which replays a hand-written fixture
  (`evals/datasets/fixtures/duplicate_charge.json`). That fixture is hand-written,
  not a recording of a live model — `docs/risks.md` R2 used to claim otherwise
  and was corrected. This run therefore proves the *gates* hold and the *system*
  is wired correctly. It does not measure model quality; the honest measurement
  is the evaluation run below.

Everything downstream of the proposal — the classification the harness asked for,
the retrieval, the gates, the park, the approval, the execution, the audit trail
— is the real system.

---

## 0. The starting position

The billing MCP server's own store, before anything ran. This is the server's
answer, not the agent's account of itself:

```
TX-88218   $129.00 USD   charged   refund_id: null    2026-10-01T02:14:07Z
TX-88219   $129.00 USD   charged   refund_id: null    2026-10-01T02:14:09Z
```

Two charges, two seconds apart, for one invoice. Invoice `INV-2026-384`:
`total 129.00`, `amount_paid 129.00`, `status paid`, `CUS-1001`.

## 1. Intake

One customer ticket, submitted through the dashboard's own form
([screenshot 3](assets/screenshots/03-ticket-submitted.png)):

> **Subject:** We were charged twice for invoice INV-2026-384
> **Customer email:** billing@acme.example

The API creates ticket `97e9bec1` and run **`011b6a81-ff5a-4532-972c-8d2e7d2b64b6`**
([screenshot 4](assets/screenshots/04-ticket-accepted.png)). The run starts
in `RECEIVED`, sitting in the `runs` table — which *is* the queue, with no broker
([ADR-0005](adr/0005-db-backed-worker-no-broker.md)). The worker's next poll
claims it with `FOR UPDATE SKIP LOCKED`.

## 2. Classification and retrieval

| Step | Result | Time |
|---|---|---|
| Classify | `duplicate_charge`, confidence **0.93** | 23:26:35 |
| Retrieve | **5 chunks**, 10 ms | 23:26:35 |

The five retrieved chunks, in rank order, as the Sources panel shows them:

| # | Document | Chunk heading | Score |
|---|---|---|---|
| 1 | `duplicate-charge-sop.md` | `duplicate-charge-sop.md#when-this-procedure-applies` | 0.3355 |
| 2 | `duplicate-charge-sop.md` | `duplicate-charge-sop.md#detection` | 0.2895 |
| 3 | `payment-methods.md` | `payment-methods.md#card-failures` | 0.2818 |
| 4 | `enterprise-billing.md` | `enterprise-billing.md#billing-cycle` | 0.2676 |
| 5 | `refund-policy.md` | `refund-policy.md#ineligible-reasons` | 0.2408 |

Both documents §M6 requires the golden path to cite are present — `refund-policy.md`
and `duplicate-charge-sop.md` — and the run **cited** them: the dashboard's Sources
panel renders the documents retrieval actually returned, and the customer's reply
is composed against them. Retrieval here is pgvector across two processes: the
reindex ran over HTTP in the **api** container and the search ran in the
**worker** container, sharing one database. That cross-process property is why
this run is against Compose and PostgreSQL rather than the single-process SQLite
demo.

> **This retriever is lexical, not semantic.** `config.embedding_provider` is
> `local` — a bag of stopword-free, lightly-stemmed words weighted sublinearly by
> frequency and hashed with a fixed-key `blake2b`. It has no synonymy. A question
> worded differently from its answer will not match
> ([limitations.md](limitations.md) §3). That is why the evaluation's retrieval
> `recall@5` is 0.700 and not higher — 0.700 being the 2026-10-11 run's figure,
> the one this repository's baseline quotes; the corpus has been revised since
> that run, and the same local embedder measures 13/20 (0.650) over it today.

## 3. Investigation — three reads

The run proposed four tool calls and executed the three `READ` ones
([screenshot 5](assets/screenshots/05-run-parked-for-approval.png), "Tool
calls" panel, with arguments and results):

| Tool | Permission | Outcome | What it returned |
|---|---|---|---|
| `crm.get_customer` | `READ` | executed, 32 ms | `CUS-1001`, ACME, enterprise, active, `billing@acme.example` |
| `billing.get_invoice` | `READ` | executed | `INV-2026-384`, total 129.00, `paid`, USD |
| `billing.list_transactions` | `READ` | executed | both charges, both `charged` |

**One property worth naming:** the agent is what composes this picture. No MCP
server reads another's data — `billing` cannot ask `crm` who the customer is
([`docs/mcp-contracts.md`](mcp-contracts.md) §5). The correlation between "one
invoice, two charges, two seconds apart" and "this customer is enterprise and
active" was assembled by the agent from three separate tool results.

## 4. The proposal, and where it stops

The fourth call — `billing.issue_refund`, with
`{amount: 129, transaction_id: "TX-88219"}` — **did not execute.**
It parked.

(The argument snapshot in screenshot 10 carries two more fields — `currency`
and `transaction_amount` — because the schema accepted them then. `RefundArgs`
no longer does: a proposal that names a currency is refused, because the
refund is denominated in the transaction's own currency and a model's guess at
it is not a fact. The two above are what the schema accepts today.)

```
billing.issue_refund   HIGH_RISK_WRITE   Awaiting approval
```

The run moved to `waiting_approval` at 23:26:35 and stopped. This is the property
the whole project exists to demonstrate, and it is worth being exact about *why*
it held:

- `billing.issue_refund` is `HIGH_RISK_WRITE` in `domain/tools.py`, compared
  against a hard-coded literal in a security test
  ([ADR-0003](adr/0003-permissions-are-static-code.md)). Not configuration. A
  refactor that reclassified it would fail a CI gate that prints the old and new
  permission side by side.
- The policy engine classified the call and required approval, **deriving the
  sentence deterministically** — no model wrote it. That run's own screenshot
  (screenshot 10) records:

  > This moves $129.00 USD out of the account, refunding transaction TX-88219,
  > and cannot be undone automatically.

  **That is what the run said, and it is no longer what the code says.**
  `296c89b` (2026-10-21) rewrote the sentence, because the explanation used
  to name a currency the money would not move in — the refund goes back the way
  it came, in the transaction's own currency, and "$129.00 USD" told the
  approver a currency the transaction might not have been charged in. The
  current code produces:

  > This moves 129.00 out of the account, refunding transaction TX-88219,
  > and cannot be undone automatically. The amount is in the currency the
  > transaction was charged in.

  The screenshot has not been re-taken since, which is the honest reason both
  sentences are quoted here.

- The model's own justification was recorded too, and **labelled untrusted**,
  because nothing verifies it before it reaches the operator:

  > "The duplicate is confirmed and policy supports a refund. This is
  > HIGH_RISK_WRITE and requires human approval."

  The dashboard says so in as many words: *"Written by the model … it is shown
  because an operator may find it useful, not because it has been verified —
  nothing checks it before it reaches this screen, and retrieved text can induce
  a model to write something reassuring."*

The approval row snapshotted the arguments **at park time**, so what the human
approved and what later executes are the same value.

## 5. The human decides

The operator opens Approvals, sees the deterministic explanation above, the
model's reasoning below it, and the snapshotted arguments, and clicks Approve
([screenshot 6](assets/screenshots/06-approval-pending.png),
[screenshot 10](assets/screenshots/10-approval-recorded.png)).

| Field | Value |
|---|---|
| Approval id | `03e35a1a-5e91-46df-934f-fbd44bc4bf91` |
| Tool call id | `e5766c64-58e6-490e-836b-ceb22ccd2e97` |
| Status | `approved` |
| Decided by | `operator` |
| Parked at | `2026-10-14T23:26:35.548912Z` |
| Decided at | `2026-10-14T23:26:37.334911Z` |

**The approval does not execute the refund.** It releases the run back to the
queue; the worker performs it. That distinction is why a worker restart between
approval and execution does not lose or duplicate anything — the decision is a
row in PostgreSQL, not a value in a process's memory.

> **What this approval row is not.** `decided_by` **cannot be forged** — the
> server assigns it from the bearer token, and the request body has no
> `decided_by` at all, so a client that tries to name itself is refused rather
> than believed (`ApprovalDecisionRequest` is `extra="forbid"`). What Phase 1
> does not have is any way to tell *which* operator: one bearer token
> (`OPSPILOT_OPERATOR_TOKEN`) creates tickets, approves refunds and rejects
> them, so the value is the single literal `"operator"`. There is no separation
> of duties and no approval sphere; the same operator who submitted the ticket
> approved the refund here. A real deployment needs roles, and an identity the
> token resolves to a person rather than to a role
> ([limitations.md](limitations.md) §1).
>
> (This paragraph used to say that `decided_by` "is free text and is *not
> authenticated*", and cited `limitations.md` §1 for it. §1 says the opposite:
> "The client cannot forge it". The claim was wrong, and it was wrong in the
> direction that makes the system look less careful than it is — which is a
> reason to fix it, not a reason to leave it.)

## 6. Execution — once

The worker claimed the released run and executed the approved call. Not a
re-plan: the *approved* tool call, with the approved arguments.

```json
{
  "code": null,
  "status": "refunded",
  "replayed": false,
  "refund_id": "REF-10091",
  "transaction_id": "TX-88219",
  "idempotency_key": "refund:011b6a81-ff5a-4532-972c-8d2e7d2b64b6:TX-88219"
}
```

`replayed: false` means this was the first time this key was seen. The key is
derived from `(run_id, transaction_id)`, so a second execution of the same
approved call returns `replayed: true` and the **same** `REF-10091` rather than
issuing a second refund. That is tested by calling the MCP server directly,
bypassing the agent entirely — and, on the concurrency boundary, stated
precisely in [`docs/mcp-contracts.md`](mcp-contracts.md) and pinned by a test
that pins what it does *not* guarantee ([limitations.md](limitations.md) §2).

## 7. The audited refund

**The billing server's own store, after the run** — the same query as §0, same
server, same call:

```
TX-88218   $129.00 USD   charged    refund_id: null       ← the legitimate charge, untouched
TX-88219   $129.00 USD   refunded   refund_id: REF-10091  ← the duplicate, refunded once
```

This is the check the repository is built around: **a run that says `COMPLETED`
proves nothing about whether a refund row exists**, so the assertion is made
against the thing that holds the money. **Seven of the nine tests** in
[`tests/agent/test_readme_scenarios.py`](../tests/agent/test_readme_scenarios.py)
assert against this server rather than against the run's own status — the two
that do not are `test_scenario_4b_boot_preserves_a_decision_and_sweeps_the_rest`,
which asserts on run *statuses*, and
`test_a_second_run_gets_a_different_idempotency_key`, which asserts on the key
derivation itself — and the browser walkthrough's final step asks it the same
question from a fresh browser context.

## 8. Completion

The run's trace, as the dashboard renders it from `GET /api/runs/{id}/trace`,
server-ordered ([screenshot 8](assets/screenshots/08-refund-executed-audited.png)):

| | |
|---|---|
| Run id | `011b6a81-ff5a-4532-972c-8d2e7d2b64b6` |
| Created | `2026-10-14T23:26:34Z` |
| Started | `2026-10-14T23:26:35Z` |
| Completed | `2026-10-14T23:26:37Z` |
| Steps | 21 |
| Terminal status | `completed` |

The reply the customer received, composed against the retrieved policy:

> We confirmed the duplicate charge of $129.00 on INV-2026-384 and have refunded
> the extra transaction (TX-88219). The refund should appear on your statement
> within a few business days.

**Three seconds, end to end**, because the model was the deterministic provider.
Against a live endpoint this is minutes — the evaluation run below has a p50 of
26.8 s and a p95 of 276.7 s for a *single case*.

---

## What this case study does not establish

- **It does not measure the model.** The proposal came from a hand-written
  fixture. Measured against a live endpoint, tool selection is the system's
  weakest metric and **does not hold still between runs** — see below.
- **It does not show two workers on one database.** One worker ran this. The
  claim query's locking clause is tested against a real PostgreSQL, but the
  end-to-end two-worker promise is **not unconditional and is not claimed as
  one**: it holds while the lease holds, and a lease that lapses makes the run
  claimable again ([limitations.md](limitations.md) §2, and §8 item 2).
- **It does not file a ticket.** `issues.create` is implemented and tested in
  isolation but the committed golden-path fixture does not propose it. Recorded
  boundary D19a ([limitations.md](limitations.md) §8).
- **No money moved anywhere.** The Billing MCP server is an in-repo mock, stated
  at the top of this document.

## The live measurement, for contrast

The honest numbers are in
[`evals/results/2026-10-11T15-39-42.json`](../evals/results/2026-10-11T15-39-42.json)
— 70 cases against a live endpoint — and the README's Evaluation section reads
them with their caveats rather than quoting the best one. The single most
important caveat belongs here too:

> **`tool selection` does not reproduce.** The 3/15 above was re-measured twice
> on unmodified code against the same endpoint and scored **3/15 and 2/15**, with
> an identical tool set for only 8 of the 15 cases, and **6 of the 12 that
> failed in both proposed the identical set again** (three of those proposed
> nothing at all in either run). Two runs are committed —
> [`2026-10-13T18-24-25.json`](../evals/results/2026-10-13T18-24-25.json) and
> [`2026-10-13T18-40-10.json`](../evals/results/2026-10-13T18-40-10.json) — and
> the case-by-case comparison is in
> [`docs/progress.md`](progress.md#two-runs-of-the-same-code-b2-is-not-measurable-against-this-endpoint).
> A tool-selection figure from a single run of this endpoint is not a result.

The security property, by contrast, **is** stable: `unsafe execution count` is
**0** in all four committed runs, and the run above had 170 attempted tool calls
with **zero** unapproved `high_risk_write` executions.

---

## Reproducing this

```bash
git clone https://github.com/gg320324492-lgtm/OpsPilot && cd OpsPilot
cp .env.example .env          # add OPSPILOT_OPERATOR_TOKEN; no model key needed
docker compose up
```

Then, in the dashboard: **Knowledge → Reindex knowledge/**, **Tickets → Fill
with the README's ticket → Submit**, **Approvals → Approve**.

Or run the same walkthrough as a test, which asserts every step above including
the billing server's final answer:

```bash
cd web && npm ci && npx playwright install --with-deps chromium && npm run e2e
```

Or run the same scenarios in Python — nine tests, one file:

```bash
pytest tests/agent/test_readme_scenarios.py
```
