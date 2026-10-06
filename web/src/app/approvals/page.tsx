import Link from "next/link";
import { ApprovalCard } from "@/components/approval-card";
import { EmptyState, ErrorPanel, Page, Panel } from "@/components/ui";
import { loadApprovals, loadApprovalsWithTools, type ApprovalStatus } from "@/lib/data";

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
 */

/** The filters the API accepts, in the order an operator reaches for them. */
const FILTERS: { value: ApprovalStatus; label: string; hint: string }[] = [
  {
    value: "pending",
    label: "Pending",
    hint: "Waiting for a decision. This is the inbox.",
  },
  { value: "approved", label: "Approved", hint: "Decided. The run was released to the worker." },
  { value: "rejected", label: "Rejected", hint: "Decided. The run went on to an escalation reply." },
  { value: "expired", label: "Expired", hint: "No longer decidable." },
];

export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status } = await searchParams;
  const filter = isApprovalStatus(status) ? status : "pending";

  const approvals = await loadApprovals(filter);
  const items = approvals.data?.items ?? [];

  // The tool name and permission come from the run detail, one request per
  // *distinct* run. A card with no tool call still renders -- degraded, not
  // missing, because a screen with no cards is worse than a card with no tool
  // name. See lib/data.ts.
  const toolCalls = await loadApprovalsWithTools(items);

  return (
    <Page
      title="Approvals"
      description={
        <>
          Gate 5. A tool call that can move money stops here until a person decides. Approving
          releases the run to the worker &mdash; it does not execute the refund in the request.
        </>
      }
      actions={<StatusFilter current={filter} />}
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm text-neutral-700 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-300">
          <p>
            Each card shows the deterministic explanation from the policy engine and the
            model&rsquo;s own reason, labelled differently.{" "}
            <strong>Only the deterministic one is evidence</strong> &mdash; the model&rsquo;s text
            is untrusted and nothing verifies it before it reaches this screen.
          </p>
        </div>

        <Panel
          title={FILTERS.find((f) => f.value === filter)?.label ?? "Pending"}
          aside={
            approvals.data
              ? `${approvals.data.total} ${filter === "pending" ? "waiting" : "recorded"}`
              : undefined
          }
        >
          {approvals.error ? (
            <ErrorPanel error={approvals.error} title="Could not load approvals" />
          ) : items.length === 0 ? (
            <EmptyState
              title={
                filter === "pending"
                  ? "Nothing is waiting for a decision."
                  : `No approvals are ${filter}.`
              }
              hint={
                filter === "pending"
                  ? "That is a real answer from the API, not an error. A refund proposal parks here; a run that reaches an answer without a risky write never does."
                  : "Clear the filter to see the pending inbox."
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
 */
function isApprovalStatus(value: string | undefined): value is ApprovalStatus {
  return (
    value !== undefined && FILTERS.some((filter) => filter.value === value)
  );
}

function StatusFilter({ current }: { current: ApprovalStatus }) {
  return (
    <nav aria-label="Filter approvals by status" className="flex flex-wrap items-center gap-1 text-xs">
      {FILTERS.map((filter) => (
        <Link
          key={filter.value}
          href={
            filter.value === "pending"
              ? "/approvals"
              : `/approvals?status=${filter.value}`
          }
          aria-current={current === filter.value ? "page" : undefined}
          title={filter.hint}
          className={
            current === filter.value
              ? "rounded-full bg-neutral-900 px-2.5 py-1 font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
              : "rounded-full px-2.5 py-1 text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
          }
        >
          {filter.label}
        </Link>
      ))}
    </nav>
  );
}
