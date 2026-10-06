import Link from "next/link";
import { EmptyState, ErrorPanel, Page, Panel, StatusBadge, Time } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { loadRuns } from "@/lib/data";

/**
 * The Runs list, filterable by status through `GET /api/runs?status=`.
 *
 * The filter is a **server** filter, in the query string, not a client-side
 * filter over a fetched page. That is the contract's shape (§1) and it matters
 * for correctness rather than taste: `GET /api/runs` is paginated (§10), so
 * filtering a page in the browser would hide runs the reader never received
 * while the header claimed the filter was applied to the whole set. Links
 * rather than a `<select onChange>` because the filtered view has to be a URL --
 * an operator needs to paste "the failed runs" into a colleague's chat and have
 * it mean the same thing.
 */
export default async function RunsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  // Async `searchParams` is required from Next.js 16: synchronous access was
  // removed with the rest of the request APIs.
  const { status } = await searchParams;
  const valid = isRunStatus(status) ? status : undefined;
  const runs = await loadRuns(valid ? { status: valid } : {});

  return (
    <Page
      title="Runs"
      description="Every agent run, newest first. Filter by status to see what is moving, what is parked, and what broke."
      actions={<StatusFilter current={valid} />}
    >
      <Panel
        title="Runs"
        aside={
          runs.data
            ? `${runs.data.total} shown${valid ? ` · filtered to ${label(valid)}` : ""}`
            : undefined
        }
      >
        {runs.error ? (
          <ErrorPanel error={runs.error} title="Could not load runs" />
        ) : runs.data && runs.data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
                  <th className="py-2 pr-4 font-medium">Run</th>
                  <th className="py-2 pr-4 font-medium">Ticket</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">Model</th>
                  <th className="py-2 pr-4 font-medium">Created</th>
                  <th className="py-2 pr-4 font-medium">Started</th>
                  <th className="py-2 pr-4 font-medium">Completed</th>
                  <th className="py-2 font-medium">Failure reason</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((run) => (
                  <tr
                    key={run.id}
                    className="border-b border-neutral-100 align-top last:border-0 dark:border-neutral-800"
                  >
                    <td className="py-2 pr-4">
                      <Link
                        href={`/runs/${run.id}`}
                        className="font-mono text-xs underline underline-offset-2"
                      >
                        {run.id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="py-2 pr-4">
                      <Link
                        href={`/tickets?ticket=${run.ticket_id}`}
                        className="font-mono text-xs underline underline-offset-2"
                      >
                        {run.ticket_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="py-2 pr-4">
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="py-2 pr-4 text-xs text-neutral-600 dark:text-neutral-400">
                      {run.model_provider}/{run.model_name}
                    </td>
                    <td className="py-2 pr-4 text-xs text-neutral-600 dark:text-neutral-400">
                      <Time value={run.created_at}>{formatTimestamp(run.created_at)}</Time>
                    </td>
                    <td className="py-2 pr-4 text-xs text-neutral-600 dark:text-neutral-400">
                      {run.started_at ? formatTimestamp(run.started_at) : "—"}
                    </td>
                    <td className="py-2 pr-4 text-xs text-neutral-600 dark:text-neutral-400">
                      {run.completed_at ? formatTimestamp(run.completed_at) : "—"}
                    </td>
                    <td className="py-2 text-xs">
                      {run.failure_reason ? (
                        <span className="text-red-700 dark:text-red-300">{run.failure_reason}</span>
                      ) : (
                        <span className="text-neutral-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title={valid ? `No runs are ${label(valid)}.` : "No runs yet."}
            hint={
              valid
                ? "That is a real answer from the API, not an error. Clear the filter to see every run."
                : "A run is created when a ticket is submitted. Nothing here means nothing has been submitted."
            }
          />
        )}
      </Panel>
    </Page>
  );
}

/** Every `RunStatus`, as a literal. Mirrors the generated union. */
const RUN_STATUSES = [
  "received",
  "classifying",
  "retrieving",
  "planning",
  "executing",
  "waiting_approval",
  "responding",
  "completed",
  "failed",
] as const;

/**
 * Narrow the raw query parameter to a real status.
 *
 * An unrecognised value is dropped rather than forwarded: the API answers
 * `400 validation_error` for an unknown status (contract §1), so passing one
 * through would turn a typo in the URL into an error screen. Dropping it shows
 * the unfiltered list instead, which is the useful reading of a bad link.
 */
function isRunStatus(value: string | undefined): value is (typeof RUN_STATUSES)[number] {
  return value !== undefined && (RUN_STATUSES as readonly string[]).includes(value);
}

function label(status: string): string {
  return status.replace(/_/g, " ");
}

/**
 * The filter control.
 *
 * The three groups are the three questions an operator actually asks: *what is
 * moving*, *what is stuck on me*, *what broke*. Grouping by urgency rather than
 * alphabetically is the same argument the Dashboard makes -- the order is the
 * answer to "what should I look at".
 */
function StatusFilter({ current }: { current?: string }) {
  return (
    <nav aria-label="Filter runs by status" className="flex flex-wrap items-center gap-1 text-xs">
      <FilterLink href="/runs" label="All" active={current === undefined} />
      <span className="px-1 text-neutral-400" aria-hidden="true">
        |
      </span>
      <FilterLink href="/runs?status=waiting_approval" label="Waiting approval" active={current === "waiting_approval"} />
      <FilterLink href="/runs?status=executing" label="Executing" active={current === "executing"} />
      <FilterLink href="/runs?status=planning" label="Planning" active={current === "planning"} />
      <FilterLink href="/runs?status=retrieving" label="Retrieving" active={current === "retrieving"} />
      <FilterLink href="/runs?status=classifying" label="Classifying" active={current === "classifying"} />
      <FilterLink href="/runs?status=responding" label="Responding" active={current === "responding"} />
      <FilterLink href="/runs?status=received" label="Received" active={current === "received"} />
      <span className="px-1 text-neutral-400" aria-hidden="true">
        |
      </span>
      <FilterLink href="/runs?status=failed" label="Failed" active={current === "failed"} />
      <FilterLink href="/runs?status=completed" label="Completed" active={current === "completed"} />
    </nav>
  );
}

function FilterLink({ href, label, active }: { href: string; label: string; active: boolean }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={
        active
          ? "rounded-full bg-neutral-900 px-2.5 py-1 font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
          : "rounded-full px-2.5 py-1 text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
      }
    >
      {label}
    </Link>
  );
}