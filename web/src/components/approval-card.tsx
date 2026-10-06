/**
 * One approval, as a card an operator can decide from.
 *
 * This is the human half of gate 5 rendered, and the acceptance criterion is
 * specific about what must be on it: the tool, the arguments, the risk level,
 * the model's `reason`, and the deterministic `risk_explanation` -- with the
 * last two distinguishable at a glance. They are rendered through
 * `TrustedExplanation` / `UntrustedExplanation`, which carry the distinction in
 * four signals rather than in two headings, because an operator skimming a
 * refund at 3am will not read a caption. See that file's module comment.
 *
 * ## The risk level is derived, and the card says so
 *
 * `ApprovalSummary` has no risk field (contract §5). `deriveRisk` reads the
 * tool call's `permission` when the run detail is available and falls back to a
 * transcription of the tool registry otherwise, and `riskProvenance` returns the
 * sentence that states which. The provenance line is on the card rather than in
 * a tooltip: a level the API did not send, shown as though it had, is the
 * dashboard inventing evidence.
 *
 * ## Arguments are shown whole
 *
 * `arguments_snapshot` is the *exact* gate arguments, and the whole point of it
 * being exact is that the human approves what actually executes. A card showing
 * a summary of the arguments is asking for a decision about something other than
 * the call.
 *
 * `context` (company, ticket subject) is shown beside it when the API sent one,
 * because contract §5 exists so the approver can decide without opening a second
 * tab.
 *
 * ## When the duplicate notice appears
 *
 * `groupApprovals` groups whatever rows it is handed, so its `duplicated` flag is
 * set on a decided pair just as readily as on a pending one. The double-approval
 * window, though, is only a *hazard* while both cards are pending -- that is the
 * state in which an operator might approve both. A decided pair is history, and a
 * notice explaining a replay on it would be describing a state nobody can act on
 * any more. So the notice is gated on `pending` here.
 */

import Link from "next/link";
import { DecisionActions } from "@/components/decision-actions";
import { TrustedExplanation, UntrustedExplanation } from "@/components/explanation";
import { Pill, Time } from "@/components/ui";
import { formatRelative, formatTimestamp } from "@/lib/format";
import { deriveRisk, riskProvenance, type Permission } from "@/lib/risk";
import type { Approval, ToolCall } from "@/lib/data";

export function ApprovalCard({
  approval,
  toolCall,
  /** Set when this run holds two or more pending cards for the same proposal. */
  duplicated,
  identicalArguments,
  /** How many pending cards this run holds, for the duplicate banner's wording. */
  duplicateCount,
}: {
  approval: Approval;
  toolCall?: ToolCall;
  duplicated?: boolean;
  identicalArguments?: boolean;
  duplicateCount?: number;
}) {
  const risk = deriveRisk(toolCall?.permission, toolCall?.tool_name ?? null);
  const pending = approval.status === "pending";

  return (
    <li
      className={`rounded-lg border bg-white p-4 dark:bg-neutral-900 ${
        pending
          ? "border-amber-300 dark:border-amber-700"
          : "border-neutral-200 dark:border-neutral-800"
      }`}
    >
      {/* Gated on `pending` as well as on the group's flag. `groupApprovals`
          groups every row it is given, but the duplicate window is a property of
          the *inbox*: two decided approvals on one run are history and a notice
          about them would be explaining a state the operator can no longer act
          on. */}
      {duplicated && pending ? (
        <DuplicateNotice
          identicalArguments={identicalArguments ?? false}
          count={duplicateCount ?? 2}
          runId={approval.run_id}
        />
      ) : null}

      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <RiskPill level={risk.level} />
          {toolCall ? <Pill>{humanisePermission(toolCall.permission)}</Pill> : null}
          <Pill tone={pending ? "warn" : "neutral"}>
            {approval.status.replace(/_/g, " ")}
          </Pill>
        </div>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          parked{" "}
          <Time value={approval.created_at}>
            {formatTimestamp(approval.created_at)}
            {` (${formatRelative(approval.created_at)})`}
          </Time>
        </span>
      </div>

      <div className="mt-3">
        <p className="font-mono text-sm font-semibold">
          {toolCall?.tool_name ?? "Tool unknown"}
        </p>
        {!toolCall ? (
          <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
            The run&rsquo;s tool calls were not available to read this call&rsquo;s name from.
            The arguments below are the API&rsquo;s own snapshot and are still exact.
          </p>
        ) : null}
      </div>

      {/* Deterministic first, model's second: reading order is the order the
          reasoning is safe in. See explanation.tsx. */}
      <div className="mt-3 space-y-3">
        <TrustedExplanation text={approval.risk_explanation} />
        <UntrustedExplanation text={approval.reason} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            arguments_snapshot
          </p>
          <pre className="mt-1 overflow-x-auto rounded bg-neutral-50 p-2 font-mono text-xs dark:bg-neutral-950">
            {JSON.stringify(approval.arguments_snapshot, null, 2)}
          </pre>
          <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
            tool_call_id <span className="font-mono">{approval.tool_call_id}</span>
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            risk level
          </p>
          <p className="mt-1 text-sm">
            {risk.level ? humanisePermission(risk.level) : "Unknown"}
          </p>
          {/* The provenance is on the card, not behind a tooltip: a derived level
              presented as though the API had sent it is the dashboard inventing
              evidence. See lib/risk.ts. */}
          <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
            {riskProvenance(risk.source)}
          </p>
          {approval.context ? (
            <div className="mt-3">
              <p className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
                context
              </p>
              <p className="mt-1 text-sm">
                {approval.context.company ? (
                  <span className="font-medium">{approval.context.company}</span>
                ) : null}
                {approval.context.ticket_subject ? (
                  <span className="block text-neutral-600 dark:text-neutral-400">
                    {approval.context.ticket_subject}
                  </span>
                ) : null}
              </p>
            </div>
          ) : null}
        </div>
      </div>

      <div className="mt-4 border-t border-neutral-200 pt-3 dark:border-neutral-800">
        {pending ? (
          <DecisionActions approvalId={approval.id} />
        ) : (
          <DecisionActions
            approvalId={approval.id}
            alreadyDecided={{
              status: approval.status,
              decidedAt: approval.decided_at ?? null,
              decidedBy: approval.decided_by ?? null,
              runStatus: null,
            }}
          />
        )}
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          <Link href={`/runs/${approval.run_id}`} className="underline underline-offset-2">
            Open run {approval.run_id.slice(0, 8)}
          </Link>{" "}
          &middot; approval <span className="font-mono">{approval.id.slice(0, 8)}</span>
        </p>
      </div>
    </li>
  );
}

/**
 * The double-approval notice, on the card itself.
 *
 * `EXECUTING` is preserved across a restart (an M6 decision), so a run killed
 * between committing `EXECUTING` and committing the tool call's terminal status
 * resumes and re-proposes the same refund under a **new** `tool_call_id`. One
 * refund therefore parks two pending approvals, and this screen shows both.
 *
 * **Both cards stay.** Not deduplicated, not collapsed, not filtered to one.
 * The API's pending list contains two rows; a screen showing one would be the
 * dashboard asserting a state the system is not in, and it would do so precisely
 * where an operator is deciding whether to move money. The money moves once
 * either way -- the idempotency key `refund:{run_id}:{transaction_id}` blocks
 * the replay -- but the second card is real, is still pending, and still needs
 * its own decision.
 *
 * `identicalArguments` is what makes the wording honest. Two pending cards on one
 * run with *different* arguments are two genuinely different proposals (a
 * customer owed two refunds), and calling that a replay would be the dashboard
 * inventing a claim -- so the two cases get different sentences.
 */
function DuplicateNotice({
  identicalArguments,
  count,
  runId,
}: {
  identicalArguments: boolean;
  count: number;
  runId: string;
}) {
  return (
    <div className="mb-3 rounded-md border border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-950 dark:border-amber-500 dark:bg-amber-950/40 dark:text-amber-100">
      <p className="font-semibold">
        {identicalArguments
          ? "This run is showing the same proposal twice."
          : "This run is holding more than one pending approval."}
      </p>
      <p className="mt-1">
        {identicalArguments ? (
          <>
            A run resumed from <code>EXECUTING</code> re-proposes the same refund under a new
            tool call id, so one refund produced{" "}
            <strong>{count} pending cards with identical arguments</strong>. The money moves{" "}
            <strong>once</strong> &mdash; the idempotency key blocks the replay &mdash; but both
            cards are real and both are shown rather than collapsed into one, because hiding one
            would misreport what the API has pending. Approving this card does not decide the
            other; it is still waiting and will need its own decision.
          </>
        ) : (
          <>
            This run holds {count} pending approvals whose arguments differ, so these are{" "}
            <strong>{count} separate proposals</strong> rather than one proposal proposed twice.
            Each needs its own decision.
          </>
        )}
      </p>
      <p className="mt-1">
        <Link href={`/runs/${runId}`} className="underline underline-offset-2">
          Open run {runId.slice(0, 8)}
        </Link>{" "}
        to see the tool calls it proposed.
      </p>
    </div>
  );
}

function RiskPill({ level }: { level: Permission | null }) {
  if (!level) {
    return <Pill tone="warn">Risk unknown</Pill>;
  }
  if (level === "high_risk_write") {
    return <Pill tone="danger">High risk write</Pill>;
  }
  if (level === "safe_write") {
    return <Pill tone="warn">Safe write</Pill>;
  }
  return <Pill tone="neutral">Read</Pill>;
}

function humanisePermission(value: string): string {
  return value.replace(/_/g, " ");
}
