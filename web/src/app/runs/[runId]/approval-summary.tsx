import Link from "next/link";
import { DecisionActions } from "@/components/decision-actions";
import { TrustedExplanation, UntrustedExplanation } from "@/components/explanation";
import { Panel, Pill } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { deriveRisk, riskProvenance } from "@/lib/risk";
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
 */
export function ApprovalSummaryCard({
  approval,
  toolCall,
}: {
  approval: components["schemas"]["PendingApproval"];
  toolCall?: ToolCall;
}) {
  const risk = deriveRisk(toolCall?.permission, toolCall?.tool_name ?? null);

  return (
    <Panel
      title="Awaiting approval"
      aside={
        <span className="text-amber-700 dark:text-amber-300">Nothing has been sent</span>
      }
      className="border-amber-400 dark:border-amber-600"
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {risk.level === "high_risk_write" ? (
            <Pill tone="danger">High risk write</Pill>
          ) : risk.level === "safe_write" ? (
            <Pill tone="warn">Safe write</Pill>
          ) : risk.level === "read" ? (
            <Pill>Read</Pill>
          ) : (
            <Pill tone="warn">Risk unknown</Pill>
          )}
          {toolCall ? <Pill>{toolCall.permission.replace(/_/g, " ")}</Pill> : null}
          <span className="text-xs text-neutral-500 dark:text-neutral-400">
            parked {formatTimestamp(approval.created_at)}
          </span>
        </div>

        {toolCall ? (
          <ToolAndArguments toolName={toolCall.tool_name} arguments={toolCall.arguments} />
        ) : null}

        <TrustedExplanation text={approval.risk_explanation} />
        <UntrustedExplanation text={approval.reason} />

        <p className="text-xs text-neutral-600 dark:text-neutral-400">
          {riskProvenance(risk.source)}
        </p>

        {approval.status === "pending" ? (
          <DecisionActions approvalId={approval.id} />
        ) : (
          <DecisionActions
            approvalId={approval.id}
            alreadyDecided={{
              status: approval.status,
              decidedAt: null,
              decidedBy: null,
              runStatus: null,
            }}
          />
        )}

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Link href="/approvals" className="font-medium underline underline-offset-2">
            Open the Approvals inbox &rarr;
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
 */
export function ToolAndArguments({
  toolName,
  arguments: args,
}: {
  toolName?: string;
  arguments: Record<string, unknown>;
}) {
  return (
    <div>
      {toolName ? <p className="font-mono text-sm font-semibold">{toolName}</p> : null}
      <p className="mt-2 text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        arguments_snapshot
      </p>
      <pre className="mt-0.5 overflow-x-auto rounded bg-neutral-50 p-2 font-mono text-xs dark:bg-neutral-950">
        {JSON.stringify(args, null, 2)}
      </pre>
    </div>
  );
}
