/**
 * The screen-to-API layer: one function per screen's read, all server-side.
 *
 * Every function here is `async` and every one is called from a Server
 * Component. That is not a stylistic choice and it is load-bearing: the operator
 * token has no `NEXT_PUBLIC_` prefix precisely so it is never inlined into the
 * browser bundle, and a single `fetch` from a Client Component would either
 * fail (no token in the browser) or tempt someone into adding the prefix
 * (a refund-capable credential in a public artefact). See the module comment on
 * `lib/api/client.ts` for the full argument.
 *
 * The alternative -- letting each page call `api.*` directly -- was rejected
 * because it puts the three states every screen must handle (loading is a
 * Server Component's problem, failure, and "the API is down") at six call sites
 * instead of one. Here, a screen gets `{ data, error }` and renders; it never
 * has to remember that a throw is not an `ErrorBoundary`'s job in a server
 * component.
 */

import { ApiError, api, checkApiReachability } from "@/lib/api/client";
import type { components } from "@/lib/api/types";

export type RunList = components["schemas"]["RunListResponse"];
export type RunDetail = components["schemas"]["RunDetail"];
export type Trace = components["schemas"]["TraceResponse"];
export type ApprovalList = components["schemas"]["ApprovalListResponse"];
export type Approval = components["schemas"]["ApprovalSummary"];
export type TicketList = components["schemas"]["TicketListResponse"];
export type KnowledgeList = components["schemas"]["KnowledgeListResponse"];
export type RunSummary = components["schemas"]["RunSummary"];
export type Citation = components["schemas"]["CitationDetail"];
export type ToolCall = components["schemas"]["ToolCallDetail"];
export type RunStatus = components["schemas"]["RunStatus"];
export type ApprovalStatus = components["schemas"]["ApprovalStatus"];

/**
 * The outcome of a read: either the payload or the failure, never a throw.
 *
 * Returning both rather than throwing is what lets a screen render *both* the
 * data it got and the fact that part of it failed. On Run Detail specifically
 * that matters: the run itself and its trace are two requests, and if the
 * trace call fails there is still a run status worth showing. A throw would
 * discard the whole page for one failed sub-read.
 */
export type Loaded<T> = { data: T; error: null } | { data: null; error: unknown };

/**
 * Generic over the *value*, not over a schema name.
 *
 * An earlier version took `components["schemas"][K]` with `K` inferred from a
 * string, which TypeScript resolves to the union of every schema -- so `load`
 * returned `Loaded<the union of all 47 responses>` and every call site needed a
 * cast back. Naming the value type at the call site is what keeps the contract
 * the generated `types.ts` supplies, because the annotation *is* a reference to
 * `components["schemas"][...]`.
 */
async function load<T>(read: () => Promise<T>): Promise<Loaded<T>> {
  try {
    return { data: await read(), error: null };
  } catch (error) {
    return { data: null, error };
  }
}

/**
 * The banner state, fetched once per screen render.
 *
 * Re-checked per navigation rather than memoised in a module variable: the
 * interesting failure (token changed, API restarted) becomes visible on the
 * next page load instead of at the next process restart, which for a
 * development-time dashboard is the difference between a fixable report and a
 * mystery.
 */
export function apiStatus() {
  return checkApiReachability();
}

/** `GET /api/runs`, optionally filtered. Returns newest first, as the API does. */
export function loadRuns(params: { status?: string } = {}) {
  return load<RunList>(async () => {
    const { status, ...rest } = params;
    return api.listRuns(status ? { ...rest, status } : rest);
  });
}

/**
 * `GET /api/runs?status=` counts, for the Dashboard.
 *
 * One request per status rather than one per status plus a client-side tally,
 * because `GET /api/runs` returns a *page* (default limit 50, contract §10).
 * Counting a page would report the number of rows the operator happened to be
 * looking at, which is a different number wearing the same label.
 */
export async function loadStatusCounts(): Promise<{
  counts: Record<RunStatus, number>;
  error: unknown;
}> {
  const statuses: RunStatus[] = [
    "waiting_approval",
    "executing",
    "planning",
    "retrieving",
    "classifying",
    "responding",
    "received",
    "completed",
    "failed",
  ];
  const results = await Promise.all(
    statuses.map(async (status) => {
      const loaded = await load<RunList>(() =>
        // limit=200 is the contract's maximum (§10), so a full page of parked
        // runs still returns a count that is *at least* the truth. Anything
        // beyond 200 parked runs is a different milestone's problem, and the
        // Runs screen says the same limit in its own header.
        api.listRuns({ status, limit: 200 }),
      );
      return [status, loaded.data ? loaded.data.total : 0] as const;
    }),
  );
  const counts = Object.fromEntries(results) as Record<RunStatus, number>;
  return { counts, error: null };
}

/** `GET /api/runs/{id}` plus `GET /api/runs/{id}/trace`, fetched together. */
export async function loadRunDetail(runId: string): Promise<{
  run: Loaded<RunDetail>;
  trace: Loaded<Trace>;
}> {
  const [run, trace] = await Promise.all([
    load<RunDetail>(async () => {
      try {
        return await api.getRun(runId);
      } catch (error) {
        // A run id pasted from a colleague's chat may have a stray character or
        // be truncated. FastAPI answers 422 for an unparseable UUID, and the
        // dashboard's message for that should say so rather than showing the
        // raw "uuid is not a valid UUID" from the server's validation path.
        if (error instanceof ApiError && error.status === 422) {
          throw new Error(
            `"${runId}" is not a run id. A run id is the UUID from GET /api/runs, or the last path segment of a /runs/... URL.`,
          );
        }
        throw error;
      }
    }),
    // The trace is a separate read rather than reusing `run.steps`: it is the
    // endpoint the dashboard is meant to render (contract §4) and it is the
    // cheap one. Fetching it from `run.steps` would work today and quietly
    // break the moment the two diverge, which is the entire reason §4 exists.
    load<Trace>(async () => {
      try {
        return await api.getRunTrace(runId);
      } catch (error) {
        if (error instanceof ApiError && error.status === 422) {
          return { run_id: runId, steps: [] };
        }
        throw error;
      }
    }),
  ]);
  return { run, trace };
}

/**
 * `GET /api/approvals`, grouped so the double-approval window is visible.
 *
 * Grouping is the whole point. A run resumed from `EXECUTING` re-proposes the
 * same refund under a **new** `tool_call_id` and parks again, so one refund can
 * hold two pending cards. Showing them as two unrelated cards would let an
 * operator approve both, believing they were two real proposals -- so the
 * group carries the count and an explanation instead.
 *
 * Only *pending* rows are grouped. Once one is decided the pair is history, and
 * a decided card beside a pending one is a normal state that needs no
 * explanation.
 */
export async function loadApprovals(status: ApprovalStatus = "pending") {
  const loaded = await load<ApprovalList>(async () =>
    api.listApprovals({ status, limit: 200 }),
  );
  if (!loaded.data) {
    return { ...loaded, groups: [] as ApprovalGroup[] };
  }
  return { ...loaded, groups: groupApprovals(loaded.data.items) };
}

/** One run's pending approvals, plus a flag when there is more than one. */
export type ApprovalGroup = {
  runId: string;
  approvals: Approval[];
  /** True when two or more pending cards share this run. */
  duplicated: boolean;
  /** True when the duplicated cards also carry identical arguments. */
  identicalArguments: boolean;
};

/**
 * Group pending approvals by run.
 *
 * Two runs' cards are "the same proposal" only when they share a run *and*
 * their `arguments_snapshot` matches exactly. Comparing the snapshot rather
 * than just the run id is what keeps the warning honest: two genuinely
 * different refunds on one run (a customer owed two refunds) are two proposals
 * and must not be described as a replay.
 *
 * `arguments_snapshot` is compared through a canonical JSON form rather than
 * with `===`, because two snapshots of the same proposal are separately
 * deserialised objects and reference equality would report them as different.
 * The key order is sorted so a server that serialises the same map in a
 * different order still compares equal.
 */
export function groupApprovals(items: Approval[]): ApprovalGroup[] {
  const byRun = new Map<string, Approval[]>();
  for (const item of items) {
    const bucket = byRun.get(item.run_id);
    if (bucket) {
      bucket.push(item);
    } else {
      byRun.set(item.run_id, [item]);
    }
  }

  return Array.from(byRun.entries()).map(([runId, approvals]) => ({
    runId,
    approvals,
    duplicated: approvals.length > 1,
    identicalArguments:
      approvals.length > 1 &&
      approvals.every((a) => canonicalJson(a.arguments_snapshot) === canonicalJson(approvals[0].arguments_snapshot)),
  }));
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(value, (_key, inner) => {
    if (inner && typeof inner === "object" && !Array.isArray(inner)) {
      const sorted: Record<string, unknown> = {};
      for (const key of Object.keys(inner as Record<string, unknown>).sort()) {
        sorted[key] = (inner as Record<string, unknown>)[key];
      }
      return sorted;
    }
    return inner;
  });
}

/** `GET /api/tickets`, newest first. */
export function loadTickets() {
  return load<TicketList>(async () => api.listTickets({ limit: 200 }));
}

/** `GET /api/knowledge` -- the indexed documents and their chunk counts. */
export function loadKnowledge() {
  return load<KnowledgeList>(async () => api.listKnowledge());
}

/** `GET /api/runs` for the Dashboard's recent table. */
export function loadRecentRuns() {
  return load<RunList>(async () => api.listRuns({ limit: 10 }));
}

/**
 * The pending approval count for the nav.
 *
 * A separate small read rather than a prop threaded through six screens: the
 * number an operator needs most is "is anything waiting for me", and it must be
 * visible from any screen without navigating to Approvals to find out.
 */
export async function loadPendingApprovalCount(): Promise<number> {
  const loaded = await load<ApprovalList>(async () =>
    api.listApprovals({ status: "pending", limit: 1 }),
  );
  return loaded.data ? loaded.data.total : 0;
}
/**
 * The run detail, reduced to what an approval card needs.
 *
 * `ApprovalSummary` carries no `tool_name` and no risk level, and the run's
 * `tool_calls[]` carries both (`ToolCallDetail.tool_name` and `.permission`).
 * So each approval card fetches the run it belongs to and looks its
 * `tool_call_id` up. One request per *distinct* run, not per card, which is
 * also what makes the double-approval case cheap: the second card for a replayed
 * refund shares a run with the first, so it costs no extra request.
 *
 * The lookup fails softly -- a run that has moved on, or an approval whose run
 * the caller cannot read, leaves the card with no tool name rather than taking
 * the whole Approvals screen down. A card missing its tool name is degraded; a
 * screen with no cards is worse.
 */
export async function loadApprovalsWithTools(
  approvals: Approval[],
): Promise<Map<string, ToolCall>> {
  const byRun = new Map<string, Approval[]>();
  for (const approval of approvals) {
    const bucket = byRun.get(approval.run_id);
    if (bucket) {
      bucket.push(approval);
    } else {
      byRun.set(approval.run_id, [approval]);
    }
  }

  const found = new Map<string, ToolCall>();
  await Promise.all(
    Array.from(byRun.entries()).map(async ([runId, group]) => {
      const detail = await load<RunDetail>(async () => api.getRun(runId));
      if (!detail.data) {
        return;
      }
      const calls = detail.data.tool_calls ?? [];
      for (const approval of group) {
        const call = calls.find((c) => c.id === approval.tool_call_id);
        if (call) {
          found.set(approval.id, call);
        }
      }
    }),
  );
  return found;
}
