# Demo paths — three browser walkthroughs on a disposable stack

Three paths a demonstrator can walk in a browser, end to end, on a stack whose
data is disposable by construction:

- **A — approve:** a duplicate charge is investigated, the policy is cited, the
  refund parks on the approval gate, a human approves, and the simulated refund
  executes exactly once, with the audit trail to show for it.
- **B — reject:** the same investigation parks, a human rejects it, and the run
  completes with an escalation reply and **no refund**. A rejection is the
  workflow working, not a failure — the terminal state is `COMPLETED`.
- **C — model failure:** the model provider answers 429, the run fails *loudly*
  with `model_unavailable` (no silent retry), and the queue keeps working: the
  next ticket is claimed and completes after the worker is restored.

## The isolation guarantee

The demo stack runs under the compose project **`opspilot-demo-test`**, with
its own containers and its own volumes, on ports the desktop stack and the e2e
suite never use:

| service | host address |
|---|---|
| dashboard (web) | `http://localhost:3101` |
| api | `http://localhost:18000` |
| postgres | `127.0.0.1:15432` (loopback only) |

`reset`/`down` run `docker compose -p opspilot-demo-test down -v`, which removes
exactly this project's containers and volumes. It cannot reach the `opspilot`
project the desktop application owns — the names are disjoint by design, and
`scripts/demo_paths.py` refuses a `--project` name of `opspilot` or
`opspilot-e2e` outright, so a typo cannot put the teardown on someone else's
data.

## Seed data

No seeding step exists, on purpose. The MCP servers seed themselves from the
committed `mcp_servers/*/seed.json` the first time they touch their store on a
fresh volume — the duplicate charge `TX-88219` on invoice `INV-2026-384`
($129.00, customer CUS-1001 / billing@acme.example), and every other row the
paths touch, is committed data. The model runs on `MODEL_PROVIDER=fake`, which
replays the committed fixtures (`evals/datasets/fixtures/`) in order: the same
classification, the same tool proposals, the same refund, every run. A reset
reproduces the demo exactly.

## Start

From the repository root:

```bash
.venv/Scripts/python.exe scripts/demo_paths.py up
```

This brings up postgres + migrate + api + worker + web, waits for `/ready`,
reindexes the knowledge base over the API, and prints the dashboard URL and the
demo token. Open `http://localhost:3101/tickets` in a browser when it returns.

(Alternatively, `docker compose -p opspilot-demo-test up` with the same
environment variables works; the script exists so the operator does not have to
spell them out. The operator token is a local demo value, not a secret.)

## Path A — approve

1. Open `http://localhost:3101/tickets`, click **Fill with the README's
   ticket**, then **Submit ticket**. The run link appears immediately.
2. Open the run. Wait for the **Parked. Nothing has been sent.** banner — the
   worker has proposed `billing.issue_refund` on `TX-88219` and stopped at the
   approval gate. The Timeline panel shows the investigation steps, and the
   Sources panel cites `refund-policy.md` and `duplicate-charge-sop.md`.
3. Open **Approvals** (`/approvals`). The parked refund is in the pending
   inbox; its card names the tool, the arguments and the risk. Click
   **Approve**. The card leaves the pending inbox.
4. Reload the run page until the status badge reads **Completed**. The tool
   call shows as `executed`, and the reply is a refund confirmation.
5. The server-side proof: exactly one transaction for `INV-2026-384` is
   refunded — `TX-88219`. (`docker compose -p opspilot-demo-test exec worker
   python -c "..."` can read the billing store; the e2e test
   `web/e2e/demo-paths.spec.ts` does exactly that.)

## Path B — reject

1. Reset first (`scripts/demo_paths.py reset`) or recreate the worker
   (`docker compose -p opspilot-demo-test up -d --force-recreate worker`):
   the fake provider's script is consumed in order, one full run per worker.
2. Submit the same ticket. Wait for the **Parked** banner as in path A.
3. Open **Approvals** and click **Reject**.
4. Reload the run page until the badge reads **Completed**. The run shows the
   **Escalated** banner — the run did not complete the request; it handed it to
   a human; no refund was issued. The decided card appears under the
   **Rejected** tab.
5. The server-side proof: the ledger still holds exactly path A's one refund —
   nothing moved.

## Path C — model failure

1. Start the failure injection and point the worker at it:

   ```bash
   .venv/Scripts/python.exe scripts/demo_paths.py inject-429
   docker compose -p opspilot-demo-test up -d --force-recreate worker \
     -e MODEL_PROVIDER=anthropic \
     -e ANTHROPIC_API_KEY=demo-throwaway-key \
     -e ANTHROPIC_BASE_URL=http://opspilot-demo-test-429:8000
   ```

   The injection container answers every model request with **429**. The
   provider SDK is configured `max_retries=0`, so the first model call of a run
   fails it — no retry loop, no silent swallow.

2. Submit a ticket. The run goes to **Failed**; the banner reads
   **This run failed: model_unavailable**. The timeline's model-failure event
   carries the structured diagnosis (`error_category=rate_limit`,
   `http_status=429`).
3. Restore the worker and submit the next ticket — it must be claimed and
   complete, proving the queue survived the failure:

   ```bash
   docker rm -f opspilot-demo-test-429
   docker compose -p opspilot-demo-test up -d --force-recreate worker
   ```

   The next run completes on the `already_refunded` script (honest here: path A
   really did refund `TX-88219`), with no approval needed.

## Reset / teardown

```bash
# Start over from the committed seed (same as `up`):
.venv/Scripts/python.exe scripts/demo_paths.py reset

# Stop and remove this project's containers AND volumes:
.venv/Scripts/python.exe scripts/demo_paths.py down

# What is running:
.venv/Scripts/python.exe scripts/demo_paths.py status
```

Both commands operate on `opspilot-demo-test` only. The desktop application's
`opspilot` volumes — the operator's runs, approvals and refund ledger — are
untouched.

## The tests

`web/e2e/demo-paths.spec.ts` walks all three paths in a real browser against
this exact stack (same project name, same ports), asserting on the UI's own
words — the parked banner, the escalation banner, the failure banner — and on
the billing server's ledger, not the run's self-report. Run it with:

```bash
cd web && npx playwright test demo-paths
```

Without Docker the file skips cleanly (the same ADR-0004 discipline as the
other browser specs); with Docker it starts and tears down its own stack.
