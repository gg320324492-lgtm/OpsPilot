import Link from "next/link";
import { ApprovalCard } from "@/components/approval-card";
import { EmptyState, ErrorPanel, Page, Panel } from "@/components/ui";
import { loadApprovals, loadApprovalsWithTools, type ApprovalStatus } from "@/lib/data";
import { approvalStatusLabel } from "@/i18n/enums";
import { getCopy } from "@/i18n/server";
import type { TFunction } from "@/i18n/format";

import type en from "@/i18n/dictionaries/en.json";

/**
 * Approvals: the pending inbox, and the human half of gate 5.
 *
 * The default is `status=pending` because that is the only filter under which
 * the Approve and Reject buttons mean anything: a decided approval is history,
 * and an inbox that defaults to everything would put a decided row above a
 * pending one and make "is there anything waiting for me" a question the
 * operator has to answer by reading.
 *
 * ## The two things this screen refuses to hide
 *
 * **The 409.** A second click on a decided approval returns 409
 * `approval_already_decided` (contract §5), and it is rendered in words on the
 * card: who decided, when, and that this click granted nothing. It is not
 * swallowed, not shown as a generic error, and not treated as success. The
 * surface it is on is `DecisionActions` -> `DecisionPanel`, shared with Run
 * Detail, so the message cannot be softened in one place by accident.
 *
 * **The double-approval window.** Because `EXECUTING` survives a restart, a run
 * can re-propose the same refund under a new `tool_call_id`, so one refund can
 * hold two pending cards. Both are rendered, side by side, ungrouped --
 * collapsing them would be the dashboard reporting a state the API is not in,
 * and it would do it on the one screen where an operator is deciding whether to
 * move money. `groupApprovals` detects the case; each card carries a notice that
 * says what it is and why. See `components/approval-card.tsx`.
 *
 * ## The filter labels
 *
 * They are `approvalStatusLabel` of the enum value, which is the same lookup the
 * card's own status pill uses -- so the filter and the thing it filters can
 * never disagree about what "Rejected" is called. The `value` each link carries
 * is untouched, so `?status=rejected` is the same query it always was. See
 * `src/i18n/enums.ts`.
 */

/**
 * The filters this screen offers, in the order an operator reaches for them.
 *
 * **`expired` was absent here for a long time, and now it is not.** The API's
 * enum has always carried `expired` and the schema can always store it, but
 * nothing in `src/` moved an approval to it: the only writer set `approved`
 * or `rejected`, a new approval was `pending`, and there was no TTL job. So
 * `GET /api/approvals?status=expired` was a `WHERE status = 'expired'` that
 * returned nothing, always — a tab that could only ever be empty, telling the
 * operator "there are no expired approvals" when the truth was "no approval
 * can expire". The tab was deliberately withheld rather than offered empty,
 * because a tab that promises a category nothing can populate is a promise the
 * code does not keep.
 *
 * The TTL exists now (`APPROVAL_TTL_SECONDS`, swept by the worker), so the
 * category is one the system can produce and the tab comes back with it. The
 * order is deliberate: `expired` sits last, because it is the one an operator
 * looks at to understand what did *not* happen — the request that timed out,
 * the run still parked, the refund that did not move.
 */
const FILTERS = [
  { value: "pending", key: "pending" },
  { value: "approved", key: "approved" },
  { value: "rejected", key: "rejected" },
  { value: "expired", key: "expired" },
] as const satisfies readonly { value: ApprovalStatus; key: keyof typeof en.approvals.filterHint }[];

export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  // One read of the cookie serves the page's own copy and the catalogue
  // sections the cards' Client Component is handed. Only those three cross:
  // the card itself renders from this page's `t`.
  const { t, locale, copy } = await getCopy("common", "decision", "status");
  const { status } = await searchParams;
  const filter = isApprovalStatus(status) ? status : "pending";

  const approvals = await loadApprovals(filter);
  const items = approvals.data?.items ?? [];

  // The tool name and permission come from the run detail, one request per
  // *distinct* run. A card with no tool call still renders -- degraded, not
  // missing, because a screen with no cards is worse than a card with no tool
  // name. See lib/data.ts.
  const toolCalls = await loadApprovalsWithTools(items);

  const activeFilter = FILTERS.find((f) => f.value === filter);

  return (
    <Page
      title={t("approvals.title")}
      description={t("approvals.description_before")}
      actions={<StatusFilter current={filter} t={t} />}
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm text-neutral-700 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-300">
          <p>
            {t("approvals.evidenceNote_before")} <strong>{t("approvals.evidenceEmphasis")}</strong>{" "}
            {t("approvals.evidenceNote_after")}
          </p>
        </div>

        <Panel
          title={
            activeFilter ? approvalStatusLabel(activeFilter.value, t) : t("status.approval.pending")
          }
          aside={
            approvals.data
              ? `${approvals.data.total} ${
                  filter === "pending"
                    ? t("approvals.waitingSuffix")
                    : filter === "expired"
                      ? t("approvals.expiredSuffix")
                      : t("approvals.recordedSuffix")
                }`
              : undefined
          }
        >
          {approvals.error ? (
            <ErrorPanel error={approvals.error} title={t("approvals.couldNotLoad")} />
          ) : items.length === 0 ? (
            <EmptyState
              title={
                filter === "pending"
                  ? t("approvals.emptyPendingTitle")
                  : t("approvals.emptyFilteredTitle", { status: approvalStatusLabel(filter, t) })
              }
              hint={
                filter === "pending"
                  ? t("approvals.emptyPendingHint")
                  : t("approvals.emptyFilteredHint")
              }
            />
          ) : (
            <ul className="space-y-4">
              {items.map((approval) => {
                const group = approvals.groups.find((g) => g.runId === approval.run_id);
                return (
                  <ApprovalCard
                    key={approval.id}
                    approval={approval}
                    toolCall={toolCalls.get(approval.id)}
                    duplicated={group?.duplicated ?? false}
                    identicalArguments={group?.identicalArguments ?? false}
                    duplicateCount={group?.approvals.length}
                    t={t}
                    locale={locale}
                    copy={copy}
                  />
                );
              })}
            </ul>
          )}
        </Panel>
      </div>
    </Page>
  );
}

/**
 * Narrow the raw query parameter.
 *
 * An unrecognised value falls back to `pending` rather than being forwarded: the
 * API answers `400 validation_error` for a status outside the enum (contract
 * §5), so passing one through would turn a typo in a shared link into an error
 * screen. Landing on the inbox is the useful reading of a bad link.
 *
 * `expired` used to be one of those unrecognised values: it was in the API's
 * enum but not one of the filters this screen offered, because nothing could
 * produce it. It is a filter now — the TTL makes the status reachable — so
 * `?status=expired` is the link that shows the requests that timed out.
 */
function isApprovalStatus(value: string | undefined): value is ApprovalStatus {
  return (
    value !== undefined && FILTERS.some((filter) => filter.value === value)
  );
}

function StatusFilter({ current, t }: { current: ApprovalStatus; t: TFunction }) {
  return (
    <nav aria-label={t("approvals.filterAriaLabel")} className="flex flex-wrap items-center gap-1 text-xs">
      {FILTERS.map((filter) => (
        <Link
          key={filter.value}
          href={
            filter.value === "pending"
              ? "/approvals"
              : `/approvals?status=${filter.value}`
          }
          aria-current={current === filter.value ? "page" : undefined}
          title={t(`approvals.filterHint.${filter.key}`)}
          className={
            current === filter.value
              ? "rounded-full bg-neutral-900 px-2.5 py-1 font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
              : "rounded-full px-2.5 py-1 text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
          }
        >
          {approvalStatusLabel(filter.value, t)}
        </Link>
      ))}
    </nav>
  );
}