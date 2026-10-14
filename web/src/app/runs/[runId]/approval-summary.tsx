import Link from "next/link";
import { DecisionActions } from "@/components/decision-actions";
import { TrustedExplanation, UntrustedExplanation } from "@/components/explanation";
import { Panel, Pill } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { deriveRisk, riskProvenance } from "@/lib/risk";
import { permissionLabel } from "@/i18n/enums";
import { getCopy } from "@/i18n/server";
import type { TFunction } from "@/i18n/format";
import type { ToolCall } from "@/lib/data";
import type { components } from "@/lib/api/types";

/**
 * The approver card nested in a parked run (contract §3).
 *
 * **Buttons are here now, and they are the shared ones.** An earlier version had
 * none and linked to the Approvals screen, on the argument that a second button
 * doubles the number of places a double-click can happen. That argument no
 * longer holds once the 409 is handled in one module: the cost of a second call
 * site was the *copy*, and `DecisionActions` -> `DecisionPanel` means there is
 * only one copy. What is left is the benefit -- an operator who arrives at a
 * parked run from a pasted link can decide without a detour.
 *
 * The explanations are rendered here too, through the same components the
 * Approvals screen uses. Gate 5 is one gate; a reader who meets it on two
 * screens and sees the model's text styled as evidence on one of them has been
 * told two different things about the same sentence.
 *
 * ## Why this component resolves the locale itself
 *
 * `RunDetailPage` has a `t` already and could pass one down, but this card is
 * also a `"use client"` boundary's neighbour: it needs `copy` and `locale` for
 * `DecisionActions`, and the two have to agree. Reading them here with
 * `getCopy` is one cookie read in the component that owns both, rather than a
 * second prop pair threaded through the page. The page itself never renders a
 * decision control.
 */
export async function ApprovalSummaryCard({
  approval,
  toolCall,
}: {
  approval: components["schemas"]["PendingApproval"];
  toolCall?: ToolCall;
}) {
  const {
    t,
    locale,
    copy: { decision },
  } = await getCopy("decision", "explanation", "risk", "approvalSummary");
  const risk = deriveRisk(toolCall?.permission, toolCall?.tool_name ?? null);

  return (
    <Panel
      title={t("approvalSummary.title")}
      aside={<span className="text-amber-700 dark:text-amber-300">{t("approvalSummary.aside")}</span>}
      className="border-amber-400 dark:border-amber-600"
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {risk.level === "high_risk_write" ? (
            <Pill tone="danger">{t("risk.level_highRiskWrite")}</Pill>
          ) : risk.level === "safe_write" ? (
            <Pill tone="warn">{t("risk.level_safeWrite")}</Pill>
          ) : risk.level === "read" ? (
            <Pill>{t("risk.level_read")}</Pill>
          ) : (
            <Pill tone="warn">{t("risk.level_unknown")}</Pill>
          )}
          {toolCall ? <Pill>{permissionLabel(toolCall.permission, t)}</Pill> : null}
          <span className="text-xs text-neutral-500 dark:text-neutral-400">
            {t("approvalSummary.parked")} {formatTimestamp(approval.created_at)}
          </span>
        </div>

        {toolCall ? (
          <ToolAndArguments
            toolName={toolCall.tool_name}
            arguments={toolCall.arguments}
            t={t}
          />
        ) : null}

        {/* The policy engine's sentence and the model's, verbatim. */}
        <TrustedExplanation text={approval.risk_explanation} t={t} />
        <UntrustedExplanation text={approval.reason} t={t} />

        <p className="text-xs text-neutral-600 dark:text-neutral-400">
          {riskProvenance(risk.source, t)}
        </p>

        {approval.status === "pending" ? (
          <DecisionActions
            approvalId={approval.id}
            copy={decision}
            locale={locale}
          />
        ) : (
          <DecisionActions
            approvalId={approval.id}
            alreadyDecided={{
              status: approval.status,
              decidedAt: null,
              decidedBy: null,
              runStatus: null,
            }}
            copy={decision}
            locale={locale}
          />
        )}

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Link href="/approvals" className="font-medium underline underline-offset-2">
            {t("approvalSummary.openInbox")}
          </Link>
        </div>
      </div>
    </Panel>
  );
}

/**
 * The tool name and its full argument snapshot.
 *
 * Both are shown because the operator is approving *this call*: the tool decides
 * what happens, and the arguments decide to what. A snapshot with one field
 * omitted is not the call that will run, and the whole point of
 * `arguments_snapshot` being the *exact* gate arguments is that the human
 * approves what actually executes.
 *
 * The heading is the contract's field name in both locales, for the reason
 * `approval-card.tsx` gives: it is what a reader greps the API schema for.
 */
export function ToolAndArguments({
  toolName,
  arguments: args,
  t,
}: {
  toolName?: string;
  arguments: Record<string, unknown>;
  t: TFunction;
}) {
  return (
    <div>
      {toolName ? <p className="font-mono text-sm font-semibold">{toolName}</p> : null}
      <p className="mt-2 text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        {t("approvalSummary.argumentsLabel")}
      </p>
      <pre className="mt-0.5 overflow-x-auto rounded bg-neutral-50 p-2 font-mono text-xs dark:bg-neutral-950">
        {JSON.stringify(args, null, 2)}
      </pre>
    </div>
  );
}