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
 *
 * ## What crosses to the browser
 *
 * `t` renders this file, and `copy` + `locale` are handed to the one Client
 * Component below it (`DecisionActions`). Both are typed against the English
 * catalogue. The `arguments_snapshot` heading, the `tool_call_id` label and the
 * tool name are **not** in the catalogue: they are a contract field name, a
 * field name and a tool identifier, and an operator greps for all three.
 */

import Link from "next/link";
import { DecisionActions, type DecisionCopy } from "@/components/decision-actions";
import { TrustedExplanation, UntrustedExplanation } from "@/components/explanation";
import { Pill, Time } from "@/components/ui";
import { formatRelative, formatTimestamp } from "@/lib/format";
import { approvalStatusLabel, permissionLabel } from "@/i18n/enums";
import { deriveRisk, riskProvenance, type Permission } from "@/lib/risk";
import type { Approval, ToolCall } from "@/lib/data";
import type { TFunction } from "@/i18n/format";


export function ApprovalCard({
  approval,
  toolCall,
  /** Set when this run holds two or more pending cards for the same proposal. */
  duplicated,
  identicalArguments,
  /** How many pending cards this run holds, for the duplicate banner's wording. */
  duplicateCount,
  t,
  locale,
  copy,
}: {
  approval: Approval;
  toolCall?: ToolCall;
  duplicated?: boolean;
  identicalArguments?: boolean;
  duplicateCount?: number;
  /** The request's `t`, resolved by the page that renders this card. */
  t: TFunction;
  /** The request's locale, needed to build the Client Component's own `t`. */
  locale: string;
  /** The catalogue sections the card's Client Component renders, handed straight to `DecisionActions`. */
  copy: DecisionCopy;
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
          t={t}
        />
      ) : null}

      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <RiskPill level={risk.level} t={t} />
          {toolCall ? <Pill>{permissionLabel(toolCall.permission, t)}</Pill> : null}
          <Pill tone={pending ? "warn" : "neutral"}>
            {approvalStatusLabel(approval.status, t)}
          </Pill>
        </div>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {t("approvalCard.parked")}{" "}
          <Time value={approval.created_at}>
            {formatTimestamp(approval.created_at)}
            {` (${formatRelative(approval.created_at, t)})`}
          </Time>
        </span>
      </div>

      <div className="mt-3">
        <p className="font-mono text-sm font-semibold">
          {toolCall?.tool_name ?? t("approvalCard.toolUnknown")}
        </p>
        {!toolCall ? (
          <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
            {t("approvalCard.toolCallUnavailable")}
          </p>
        ) : null}
      </div>

      {/* Deterministic first, model's second: reading order is the order the
          reasoning is safe in. See explanation.tsx. */}
      <div className="mt-3 space-y-3">
        <TrustedExplanation text={approval.risk_explanation} t={t} />
        <UntrustedExplanation text={approval.reason} t={t} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          {/* A contract field name, verbatim. See the module docstring. */}
          <p className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            {t("approvalCard.argumentsLabel")}
          </p>
          <pre className="mt-1 overflow-x-auto rounded bg-neutral-50 p-2 font-mono text-xs dark:bg-neutral-950">
            {JSON.stringify(approval.arguments_snapshot, null, 2)}
          </pre>
          <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
            {t("approvalCard.toolCallIdLabel")}{" "}
            <span className="font-mono">{approval.tool_call_id}</span>
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            {t("approvalCard.riskLevelLabel")}
          </p>
          <p className="mt-1 text-sm">
            {risk.level ? permissionLabel(risk.level, t) : t("risk.levelUnknown")}
          </p>
          {/* The provenance is on the card, not behind a tooltip: a derived level
              presented as though the API had sent it is the dashboard inventing
              evidence. See lib/risk.ts. */}
          <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
            {riskProvenance(risk.source, t)}
          </p>
          {approval.context ? (
            <div className="mt-3">
              <p className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
                {t("approvalCard.contextLabel")}
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
          <DecisionActions
            approvalId={approval.id}
            copy={copy}
            locale={locale}
          />
        ) : (
          <DecisionActions
            approvalId={approval.id}
            alreadyDecided={{
              status: approval.status,
              decidedAt: approval.decided_at ?? null,
              decidedBy: approval.decided_by ?? null,
              runStatus: null,
            }}
            copy={copy}
            locale={locale}
          />
        )}
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          <Link href={`/runs/${approval.run_id}`} className="underline underline-offset-2">
            {t("approvalCard.openRun")} {approval.run_id.slice(0, 8)}
          </Link>{" "}
          &middot; {t("approvalCard.approvalSuffix")}{" "}
          <span className="font-mono">{approval.id.slice(0, 8)}</span>
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
 *
 * `EXECUTING` is rendered as a `<code>` element from the component rather than
 * interpolated from the catalogue. It is a `RunStatus` value, it is what the run
 * page's own timeline will show for the same run, and a translator has no reason
 * to be able to change it.
 */
function DuplicateNotice({
  identicalArguments,
  count,
  runId,
  t,
}: {
  identicalArguments: boolean;
  count: number;
  runId: string;
  t: TFunction;
}) {
  return (
    <div className="mb-3 rounded-md border border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-950 dark:border-amber-500 dark:bg-amber-950/40 dark:text-amber-100">
      <p className="font-semibold">
        {identicalArguments
          ? t("approvalCard.duplicateIdenticalTitle")
          : t("approvalCard.duplicateDistinctTitle")}
      </p>
      <p className="mt-1">
        {identicalArguments ? (
          <>
            {t("approvalCard.duplicateIdentical_before")} <code>EXECUTING</code>{" "}
            {t("approvalCard.duplicateIdentical_middle")}{" "}
            <strong>
              {t("approvalCard.duplicateIdentical_emphasis", { count })}
            </strong>
            . {t("approvalCard.duplicateIdentical_moves")}{" "}
            <strong>{t("approvalCard.duplicateIdentical_once")}</strong>{" "}
            {t("approvalCard.duplicateIdentical_after")}
          </>
        ) : (
          <>
            {t("approvalCard.duplicateDistinct_before")}{" "}
            <strong>{t("approvalCard.duplicateDistinct_emphasis", { count })}</strong>{" "}
            {t("approvalCard.duplicateDistinct_after")}
          </>
        )}
      </p>
      <p className="mt-1">
        <Link href={`/runs/${runId}`} className="underline underline-offset-2">
          {t("approvalCard.openRun")} {runId.slice(0, 8)}
        </Link>{" "}
        {t("approvalCard.duplicateOpenRunSuffix")}
      </p>
    </div>
  );
}

function RiskPill({ level, t }: { level: Permission | null; t: TFunction }) {
  if (!level) {
    return <Pill tone="warn">{t("risk.level_unknown")}</Pill>;
  }
  if (level === "high_risk_write") {
    return <Pill tone="danger">{t("risk.level_highRiskWrite")}</Pill>;
  }
  if (level === "safe_write") {
    return <Pill tone="warn">{t("risk.level_safeWrite")}</Pill>;
  }
  return <Pill tone="neutral">{t("risk.level_read")}</Pill>;
}