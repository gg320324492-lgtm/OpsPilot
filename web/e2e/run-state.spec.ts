/**
 * The Run Detail state predicates, checked without Docker or a browser.
 *
 * ## Why this is not a browser test
 *
 * M2-01's state distinctions -- "waiting approval" vs "approved but not
 * executing" vs "rejected" vs "failed" vs "replay blocked", and "stop
 * refreshing at a terminal state" -- are decided by pure functions in
 * `src/lib/run-state.ts` from contract fields the API already sends. Starting
 * Compose to drive a worker into each of those states costs minutes of wall
 * clock per state and needs a different deployment knob per branch (the
 * refund ceiling for the gate refusal, a stopped worker for the unclaimed
 * run, an expired TTL for the expired approval). The predicates, unlike the
 * rendering, need none of that: they are functions of a payload.
 *
 * `e2e/failure-states.spec.ts` still drives the *rendered* states through the
 * real stack; this file pins the decisions those renders are built on, so a
 * future edit to a predicate cannot quietly redefine what "approved but not
 * executing" means without a test noticing.
 *
 * The payloads below are shaped by the contract's schemas
 * (`components["schemas"]["RunDetail"]` and friends); they are cast through
 * `as` because the test builds only the fields each predicate reads, the same
 * way a unit test builds a partial fixture. What each predicate reads is the
 * contract's own field, so a field rename is still caught by
 * `npm run check:types` at the page, and by these tests' compile step here.
 */

import { expect, test } from "@playwright/test";

import type { components } from "@/lib/api/types";
import {
  REPLAY_BLOCKED_REASON,
  isApprovedAwaitingExecution,
  isReplayBlockedCall,
  isTerminalStatus,
  lastActivityAt,
  latestModelFailures,
  shouldAutoRefresh,
} from "@/lib/run-state";
import type { RunDetail, ToolCall } from "@/lib/data";

type RunDetailShape = components["schemas"]["RunDetail"];

const BASE_RUN: RunDetailShape = {
  id: "7c1b0000-0000-0000-0000-000000000000",
  ticket_id: "0f3a0000-0000-0000-0000-000000000000",
  status: "executing",
  model_provider: "anthropic",
  model_name: "claude-sonnet-5-5",
  started_at: "2026-11-15T10:42:03Z",
  completed_at: null,
  failure_reason: null,
  created_at: "2026-11-15T10:42:03Z",
  claim_lease: { state: "held", claimed_by: "worker-1", claimed_until: "2026-11-15T10:43:03Z" },
  steps: [],
  tool_calls: [],
  citations: [],
  pending_approval: null,
  customer_reply: null,
  failed_tool_calls: [],
};

function run(overrides: Partial<RunDetailShape> = {}): RunDetail {
  return { ...BASE_RUN, ...overrides } as unknown as RunDetail;
}

function call(overrides: Partial<ToolCall> = {}): ToolCall {
  return {
    id: "aa010000-0000-0000-0000-000000000000",
    tool_name: "billing.issue_refund",
    arguments: { transaction_id: "TX-88219", amount: 129 },
    permission: "high_risk_write",
    status: "executed",
    result: null,
    latency_ms: 12,
    idempotency_key: null,
    error: null,
    ...overrides,
  } as unknown as ToolCall;
}

test.describe("terminal states stop the refresh", () => {
  test("`completed` and `failed` are terminal; every in-flight and parked state is not", () => {
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
    // The parked state is not terminal: an approval resumes it. Treating it as
    // terminal would stop the page one decision away from the answer.
    for (const status of [
      "received",
      "classifying",
      "retrieving",
      "planning",
      "executing",
      "waiting_approval",
      "responding",
    ]) {
      expect(isTerminalStatus(status), status).toBe(false);
    }
    // An unknown status is not assumed terminal: refreshing a status this
    // build does not know is the conservative choice.
    expect(isTerminalStatus("some_future_status")).toBe(false);
  });

  test("shouldAutoRefresh is exactly the negation", () => {
    expect(shouldAutoRefresh("completed")).toBe(false);
    expect(shouldAutoRefresh("failed")).toBe(false);
    expect(shouldAutoRefresh("waiting_approval")).toBe(true);
    expect(shouldAutoRefresh("executing")).toBe(true);
  });
});

test.describe("approved but not executing", () => {
  test("an approval plus a non-parked, non-terminal run with an awaiting call qualifies", () => {
    const detail = run({
      status: "executing",
      pending_approval: null,
      tool_calls: [call({ status: "awaiting_approval" })],
    });
    expect(isApprovedAwaitingExecution(detail)).toBe(true);
  });

  test("waiting_approval is 'waiting', not 'approved-but-not-executing'", () => {
    // The parked banner owns this state; the approved banner would contradict
    // it ("a decision is owed" vs "a decision was given").
    const detail = run({
      status: "waiting_approval",
      tool_calls: [call({ status: "awaiting_approval" })],
    });
    expect(isApprovedAwaitingExecution(detail)).toBe(false);
  });

  test("a terminal run never qualifies, even with an awaiting call on record", () => {
    // A rejected decision sends the run through RESPONDING to COMPLETED with
    // the call still `awaiting_approval` (it was never executed) -- that is
    // "a human said no", not "the worker is on its way".
    const completed = run({
      status: "completed",
      tool_calls: [call({ status: "awaiting_approval" })],
    });
    const failed = run({
      status: "failed",
      failure_reason: "max_steps_exceeded",
      tool_calls: [call({ status: "awaiting_approval" })],
    });
    expect(isApprovedAwaitingExecution(completed)).toBe(false);
    expect(isApprovedAwaitingExecution(failed)).toBe(false);
  });

  test("no awaiting call means not qualified", () => {
    const detail = run({
      status: "executing",
      tool_calls: [call({ status: "executed" }), call({ status: "failed" })],
    });
    expect(isApprovedAwaitingExecution(detail)).toBe(false);
  });

  test("missing tool_calls (an older deployment's payload) is not qualified", () => {
    // The field is optional in the schema; absence is "unknown", and unknown
    // never renders as a confident claim.
    const detail = run({ status: "executing", tool_calls: undefined });
    expect(isApprovedAwaitingExecution(detail)).toBe(false);
  });
});

test.describe("replay blocked", () => {
  test("rejected + the idempotency reason is the replay marker", () => {
    expect(
      isReplayBlockedCall(
        call({ status: "rejected", error: REPLAY_BLOCKED_REASON }),
      ),
    ).toBe(true);
  });

  test("a gate refusal is not a replay", () => {
    // `refund_above_ceiling` is the failure-states run's own rejection: same
    // `rejected` status, different reason, different story.
    expect(isReplayBlockedCall(call({ status: "rejected", error: "refund_above_ceiling" }))).toBe(
      false,
    );
  });

  test("other statuses with the same error string are not the marker", () => {
    expect(isReplayBlockedCall(call({ status: "failed", error: REPLAY_BLOCKED_REASON }))).toBe(
      false,
    );
    expect(isReplayBlockedCall(call({ status: "executed", error: REPLAY_BLOCKED_REASON }))).toBe(
      false,
    );
  });
});

test.describe("last activity", () => {
  const detail = run({
    created_at: "2026-11-15T10:00:00Z",
    started_at: "2026-11-15T10:00:05Z",
    completed_at: null,
  });

  test("a completed run's completion time wins", () => {
    const completed = run({
      created_at: "2026-11-15T10:00:00Z",
      started_at: "2026-11-15T10:00:05Z",
      completed_at: "2026-11-15T10:05:00Z",
    });
    expect(
      lastActivityAt(completed, {
        run_id: completed.id,
        steps: [{ sequence: 9, step_type: "response", label: "Reply composed", detail: "x", latency_ms: 5, at: "2026-11-15T10:04:00Z" }],
      }),
    ).toBe("2026-11-15T10:05:00Z");
  });

  test("an in-flight run uses the last trace step", () => {
    expect(
      lastActivityAt(detail, {
        run_id: detail.id,
        steps: [
          { sequence: 1, step_type: "state_change", label: "State changed", detail: "to classifying", latency_ms: 0, at: "2026-11-15T10:00:10Z" },
          { sequence: 2, step_type: "classification", label: "Ticket classified", detail: "duplicate_charge (0.93)", latency_ms: 612, at: "2026-11-15T10:00:20Z" },
        ],
      }),
    ).toBe("2026-11-15T10:00:20Z");
  });

  test("no steps yet falls back to started_at, then created_at", () => {
    expect(lastActivityAt(detail, { run_id: detail.id, steps: [] })).toBe("2026-11-15T10:00:05Z");
    // `created_at` is overridden too, because the base fixture's created_at
    // (10:42:03) differs from this describe's clock (10:00:00) -- the fallback
    // under test is "the run's own created_at", not the base fixture's.
    const neverStarted = run({ started_at: null, created_at: "2026-11-15T10:00:00Z" });
    expect(lastActivityAt(neverStarted, null)).toBe("2026-11-15T10:00:00Z");
  });

  test("a failed trace read (null) does not erase the field", () => {
    expect(lastActivityAt(detail, null)).toBe("2026-11-15T10:00:05Z");
  });
});

test.describe("model failures from the ledger", () => {
  const event = (overrides: {
    event_type?: string;
    created_at?: string;
    payload?: Record<string, unknown>;
  }) => ({
    id: "e91a0000-0000-0000-0000-000000000000",
    actor: "runtime",
    created_at: overrides.created_at ?? "2026-11-15T10:01:00Z",
    event_type: overrides.event_type ?? "model_failed",
    payload: overrides.payload ?? {},
  });

  test("only `model_failed` events are extracted, newest first", () => {
    const failures = latestModelFailures([
      event({ event_type: "model_called", payload: {} }),
      event({ created_at: "2026-11-15T10:01:00Z", payload: { error_category: "rate_limit", stage: "planning" } }),
      event({ event_type: "tool_failed", payload: { error: "invalid_state" } }),
      event({ created_at: "2026-11-15T10:02:00Z", payload: { error_category: "timeout", stage: "classifying" } }),
    ]);
    expect(failures).toHaveLength(2);
    expect(failures[0].category).toBe("timeout");
    expect(failures[1].category).toBe("rate_limit");
  });

  test("missing payload fields degrade to unknown, never throw", () => {
    const [failure] = latestModelFailures([event({ payload: {} })]);
    expect(failure).toBeTruthy();
    expect(failure!.category).toBe("unknown");
    expect(failure!.stage).toBe("unknown");
    expect(failure!.httpStatus).toBeNull();
    expect(failure!.attempt).toBeNull();
    expect(failure!.summary).toBe("");
  });

  test("null/undefined event lists read as 'no failures', not as a crash", () => {
    expect(latestModelFailures(null)).toEqual([]);
    expect(latestModelFailures(undefined)).toEqual([]);
  });
});
