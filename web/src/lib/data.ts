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
export type TicketDetail = components["schemas"]["TicketDetailResponse"];
export type KnowledgeList = components["schemas"]["KnowledgeListResponse"];
export type RunSummary = components["schemas"]["RunSummary"];
export type Citation = components["schemas"]["CitationDetail"];
export type ToolCall = components["schemas"]["ToolCallDetail"];
export type RunStatus = components["schemas"]["RunStatus"];
export type ApprovalStatus = components["schemas"]["ApprovalStatus"];
export type ClaimLease = components["schemas"]["ClaimLease"];

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

/**
 * `GET /api/runs`, optionally filtered by status. Returns newest first, as the API does.
 *
 * The rows are the list's `items`, not the list envelope: the Runs screen
 * renders one row per run, and a `total` it does not show is not worth
 * carrying across the layer.
 */
export function loadRuns(params: { status?: string } = {}) {
  return load<RunSummary[]>(async () => {
    const { status, ...rest } = params;
    const list = await api.listRuns(status ? { ...rest, status } : rest);
    return list.items;
  });
}

/**
 * The runs of one ticket, for `/runs?ticket=`.
 *
 * **The API has no ticket filter.** Contract §1 gives `GET /api/runs` a status
 * and nothing else, so the runs of a ticket are found by reading the list and
 * keeping the rows whose `ticket_id` matches -- and by reading *all* of it,
 * because the list is paginated (contract §10) and a filter that quietly drops
 * the ticket's oldest run is not a filter. `total` is the count of the whole
 * matching set, so the pages are read until that many rows have been seen.
 *
 * The cost is one request per 200 runs in the deployment, which is a fair
 * price for a screen that shows every run of one ticket and says so. It is
 * worth stating plainly because the alternative -- filtering the first page
 * only -- is cheaper and wrong.
 *
 * `GET /api/tickets/{id}` is read first, for one reason: it is what tells
 * *no such ticket* apart from *a ticket with no runs*. Without it, a filter
 * for a ticket this deployment does not have renders as an empty list, which
 * reads as "this ticket has done nothing" rather than "this ticket does not
 * exist".
 */
export async function loadRunsByTicket(
  ticketId: string,
  params: { status?: string } = {},
): Promise<Loaded<RunSummary[]>> {
  const ticket = await load<TicketDetail>(async () => {
    try {
      return await api.getTicket(ticketId);
    } catch (error) {
      throw ticketIdError(ticketId, error);
    }
  });
  if (!ticket.data) {
    // The ticket itself could not be read: a 404 for a ticket this deployment
    // does not have, or a failure the operator needs to see. Either way the
    // screen must not fall back to showing every run as though the filter had
    // matched something.
    return { data: null, error: ticket.error };
  }
  const rows = await load<RunSummary[]>(() => listEveryRun(params.status));
  if (!rows.data) {
    return { data: null, error: rows.error };
  }
  const mine = rows.data.filter((row) => row.ticket_id === ticketId);
  return { data: params.status ? mine.filter((row) => row.status === params.status) : mine, error: null };
}

/**
 * Every run the API has, read page by page.
 *
 * `GET /api/runs` returns a page and a `total` (contract §10); the `total` is
 * the count of the whole matching set, so the pages are read until that many
 * rows have arrived. Reading the first page and stopping would be a filter
 * that drops rows, and the drops would be invisible.
 *
 * `limit: 200` is the API's own maximum (contract §10), so one request per
 * 200 runs is the fewest this can be done in.
 */
async function listEveryRun(status?: string): Promise<RunSummary[]> {
  const rows: RunSummary[] = [];
  let offset = 0;
  let total: number | null = null;
  while (total === null || rows.length < total) {
    const page = await api.listRuns({
      limit: 200,
      offset,
      ...(status ? { status } : {}),
    });
    rows.push(...page.items);
    total = page.total;
    // A page that comes back empty ends the walk: the API said there were
    // `total` rows and then ran out, and looping on that would never finish.
    if (page.items.length === 0) {
      break;
    }
    offset += page.items.length;
  }
  return rows;
}

/**
 * The Dashboard's per-status counts, and the failures behind the missing ones.
 *
 * `counts[status]` is `null` where the count could not be read. It is
 * deliberately not `0`: a status the API could not answer for is not a status
 * with nothing in it, and the Dashboard's first screen exists to say whether
 * something needs a human.
 */
export type StatusCounts = {
  counts: Record<RunStatus, number | null>;
  /** The failure behind each `null` count. */
  errors: Partial<Record<RunStatus, unknown>>;
};

/**
 * `GET /api/runs?status=` counts, for the Dashboard.
 *
 * One request per status rather than one per status plus a client-side tally,
 * because `GET /api/runs` returns a *page* (contract §10). Counting a page
 * would report the number of rows the operator happened to be looking at,
 * which is a different number wearing the same label.
 *
 * The API's `total` is the count of the rows that **match the filter**, not
 * the length of the page (contract §10), so the tally is exact however the
 * page is sized -- and `limit: 1` is enough, because only the total is read.
 * It used to ask for the maximum page of 200 rows per status and then trust
 * that `total` was the count; it was not, it was the page length, so a
 * deployment with more than 200 parked runs reported 200. Asking for one row
 * and reading a real count is both smaller and correct.
 *
 * ## A count that could not be read is not a count of zero
 *
 * **A failed query used to be folded into a zero.** Every status was read
 * through `load`, which catches the failure, and the row was then reported as
 * `0` -- and the function's own `error` field was returned as `null`
 * unconditionally, so nothing downstream could tell a real zero from a failed
 * read. The Dashboard's first screen then said "0 waiting for approval" in
 * green while the API was unreachable, which is the exact failure
 * `components/ui.tsx` names: an empty list and a console error read as "no
 * tickets yet", and a dashboard that says so is untrustworthy precisely when
 * it is needed.
 *
 * So a status whose query failed is reported as `null` with its failure
 * attached, and the screen renders that failure instead of a number.
 */
export async function loadStatusCounts(): Promise<StatusCounts> {
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
      const loaded = await load<RunList>(() => api.listRuns({ status, limit: 1 }));
      return { status, loaded };
    }),
  );
  const counts = {} as Record<RunStatus, number | null>;
  const errors: Partial<Record<RunStatus, unknown>> = {};
  for (const { status, loaded } of results) {
    if (loaded.data) {
      counts[status] = loaded.data.total;
    } else {
      counts[status] = null;
      errors[status] = loaded.error;
    }
  }
  return { counts, errors };
}

/**
 * Whether a failed read was a 404 -- a resource this deployment does not have.
 *
 * Duck-typed rather than an `instanceof ApiError` check, so a failure that
 * never reached the API is not mistaken for one that did: only a `status` of
 * 404 means "it is not there", and everything else is a different problem with
 * a different instruction.
 */
export function isNotFound(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    (error as { status: number }).status === 404
  );
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
 * Only the rows the filter returned are grouped. With the default `pending`
 * filter those are pending rows; with `?status=approved` they are decided ones,
 * and the grouping is the same either way -- two decided rows on one run are
 * grouped too. What keeps a decided pair from being *explained* is the card,
 * not this function: `components/approval-card.tsx` shows the duplicate notice
 * only while both cards are pending, because a decided pair is history and a
 * notice explaining a replay on it would be describing a state nobody can act
 * on any more.
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

/**
 * One ticket, as a one-row list, for `/tickets?ticket=`.
 *
 * `GET /api/tickets/{id}` is the endpoint that answers "this ticket", so the
 * filter reads it rather than paging the list and keeping the matching row:
 * the read is one request, and it cannot miss a ticket that the list's own
 * page does not reach.
 *
 * The shape is a list of one on purpose -- the screen that renders it draws
 * the same row either way, and a filtered view that drew a *different* row
 * would be a second rendering of the same thing.
 */
export async function loadTicketList(ticketId: string): Promise<Loaded<TicketList>> {
  const ticket = await load<TicketDetail>(async () => {
    try {
      return await api.getTicket(ticketId);
    } catch (error) {
      throw ticketIdError(ticketId, error);
    }
  });
  if (!ticket.data) {
    // A 404 for a ticket this deployment does not have, or a failure the
    // operator needs to see. Either way the screen must not fall back to the
    // whole list, which would read as "the filter matched everything".
    return { data: null, error: ticket.error };
  }
  return { data: { items: [ticket.data.ticket], total: 1 }, error: null };
}

/**
 * A ticket id the API rejected, said in words rather than as a validation dump.
 *
 * FastAPI answers 422 for an unparseable UUID in a path, and a path parameter
 * is not covered by the contract's error envelope -- so the raw answer is a
 * validation dump, not a sentence. `loadRunDetail` already says this in words
 * for a run id; the two ticket filters say it for a ticket id, in the same
 * place and for the same reason.
 */
function ticketIdError(ticketId: string, error: unknown): unknown {
  if (error instanceof ApiError && error.status === 422) {
    return new Error(
      `"${ticketId}" is not a ticket id. A ticket id is the UUID from GET /api/tickets, or the last path segment of a /tickets/... URL.`,
    );
  }
  return error;
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
