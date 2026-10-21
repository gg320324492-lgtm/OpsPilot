import Link from "next/link";
import { EmptyState, ErrorPanel, Page, Panel, StatusBadge, Time } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { isNotFound, loadRuns, loadRunsByTicket, type RunSummary } from "@/lib/data";
import { runStatusLabel } from "@/i18n/enums";
import { getT } from "@/i18n/server";
import type { TFunction } from "@/i18n/format";

/**
 * The Runs list, filterable by status through `GET /api/runs?status=`, and by
 * ticket through `?ticket=`.
 *
 * The filter is a **server** filter, in the query string, not a client-side
 * filter over a fetched page. That is the contract's shape (§1) and it matters
 * for correctness rather than taste: `GET /api/runs` is paginated (§10), so
 * filtering a page in the browser would hide runs the reader never received
 * while the header claimed the filter was applied to the whole set. Links
 * rather than a `<select onChange>` because the filtered view has to be a URL --
 * an operator needs to paste "the failed runs" into a colleague's chat and have
 * it mean the same thing.
 *
 * ## The ticket filter is one the API does not have
 *
 * `GET /api/runs` filters by status and by nothing else (contract §1), so
 * `?ticket=` is applied by reading the list and keeping the rows whose
 * `ticket_id` matches -- every page of it, so the filter cannot drop the
 * ticket's oldest run. `lib/data.ts` says what that costs.
 *
 * **The two filters compose, and the ticket one is the narrower.** A status
 * filter is applied by the API and narrows the set before the ticket filter
 * sees it; a ticket filter narrows it further. The Tickets screen links here
 * with `?ticket=` alone, and the Run Detail screen links to the *ticket*, not
 * to a run list, so a run's ticket id and a ticket's runs are two different
 * links on purpose.
 *
 * ## The filter labels are labels; the filter values are identifiers
 *
 * `?status=waiting_approval` is the contract's enum and is written into the
 * href untouched. The word on the button is `runStatusLabel` of that same value
 * -- the same lookup the row's own `StatusBadge` uses, so the filter and the
 * thing it filters can never disagree. `src/i18n/enums.ts` is where that rule is
 * written down; `e2e/i18n.spec.ts` holds it.
 */
export default async function RunsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; ticket?: string }>;
}) {
  // Async `searchParams` is required from Next.js 16: synchronous access was
  // removed with the rest of the request APIs.
  const [{ status, ticket }, t] = await Promise.all([searchParams, getT()]);
  const valid = isRunStatus(status) ? status : undefined;
  // An empty `?ticket=` is not a filter; it is a link with nothing in it, and
  // it is dropped rather than forwarded, the way an unknown status is. A
  // *wrong* id is forwarded, and the API's answer to it is what the screen
  // shows -- see `lib/data.ts`.
  const ticketId = typeof ticket === "string" && ticket.length > 0 ? ticket : undefined;

  const runs = ticketId
    ? await loadRunsByTicket(ticketId, valid ? { status: valid } : {})
    : await loadRuns(valid ? { status: valid } : {});
  const rows: RunSummary[] = runs.data ?? [];
  const noSuchTicket = ticketId !== undefined && isNotFound(runs.error);

  return (
    <Page
      title={t("runs.title")}
      description={t("runs.description")}
      actions={<StatusFilter current={valid} t={t} />}
    >
      <Panel
        title={t("runs.panelTitle")}
        aside={
          runs.data
            ? `${rows.length} ${t("runs.shownSuffix")}${
                valid ? ` ${t("runs.filteredTo", { status: runStatusLabel(valid, t) })}` : ""
              }${
                ticketId ? ` ${t("runs.filteredToTicket", { id: ticketId.slice(0, 8) })}` : ""
              }`
            : undefined
        }
      >
        {noSuchTicket ? (
          /* A ticket this deployment does not have has its own branch, rather
             than falling into the generic error panel: "there is no such
             ticket" and "the API is down" are different problems, and an empty
             table would say neither. */
          <div className="space-y-4">
            <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
              <p className="font-medium">{t("runs.notFoundTitle")}</p>
              <p className="mt-1">{t("runs.notFoundBody", { id: ticketId.slice(0, 8) })}</p>
            </div>
            <Link href="/runs" className="text-sm underline underline-offset-2">
              {t("runs.backToRuns")}
            </Link>
          </div>
        ) : runs.error ? (
          <ErrorPanel error={runs.error} title={t("runs.couldNotLoad")} />
        ) : rows.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
                  <th className="py-2 pr-4 font-medium">{t("runs.colRun")}</th>
                  <th className="py-2 pr-4 font-medium">{t("runs.colTicket")}</th>
                  <th className="py-2 pr-4 font-medium">{t("runs.colStatus")}</th>
                  <th className="py-2 pr-4 font-medium">{t("runs.colModel")}</th>
                  <th className="py-2 pr-4 font-medium">{t("runs.colCreated")}</th>
                  <th className="py-2 pr-4 font-medium">{t("runs.colStarted")}</th>
                  <th className="py-2 pr-4 font-medium">{t("runs.colCompleted")}</th>
                  <th className="py-2 font-medium">{t("runs.colFailureReason")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((run) => (
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
                      <StatusBadge status={run.status} t={t} />
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
                    {/* `failure_reason` verbatim: a contract enum. */}
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
            title={
              ticketId
                ? t("runs.emptyTicketTitle")
                : valid
                  ? t("runs.emptyFilteredTitle", { status: runStatusLabel(valid, t) })
                  : t("runs.emptyUnfilteredTitle")
            }
            hint={
              ticketId || valid ? t("runs.emptyFilteredHint") : t("runs.emptyUnfilteredHint")
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

/**
 * The filter control.
 *
 * **Two groups of statuses, three visual segments.** The segment boundaries are
 * the `|` separators: "All", then the seven statuses a run is parked in or
 * moving through, then the two it ends in.
 *
 * The order inside the first group puts `waiting_approval` first -- the one
 * status an operator is waiting on -- and the second group puts `failed`
 * before `completed`, because a broken run is the one worth reading and a
 * finished one is not. Grouping by urgency rather than alphabetically is the
 * same argument the Dashboard makes: the order is the answer to "what should I
 * look at".
 *
 * The `href`s carry the raw enum and the labels carry the locale, which is the
 * split this screen's docstring describes.
 */
function StatusFilter({ current, t }: { current?: string; t: TFunction }) {
  return (
    <nav aria-label={t("runs.filterAriaLabel")} className="flex flex-wrap items-center gap-1 text-xs">
      <FilterLink href="/runs" label={t("runs.filterAll")} active={current === undefined} />
      <span className="px-1 text-neutral-400" aria-hidden="true">
        |
      </span>
      {(
        [
          "waiting_approval",
          "executing",
          "planning",
          "retrieving",
          "classifying",
          "responding",
          "received",
        ] as const
      ).map((status) => (
        <FilterLink
          key={status}
          href={`/runs?status=${status}`}
          label={runStatusLabel(status, t)}
          active={current === status}
        />
      ))}
      <span className="px-1 text-neutral-400" aria-hidden="true">
        |
      </span>
      {(["failed", "completed"] as const).map((status) => (
        <FilterLink
          key={status}
          href={`/runs?status=${status}`}
          label={runStatusLabel(status, t)}
          active={current === status}
        />
      ))}
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