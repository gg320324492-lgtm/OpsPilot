/**
 * Deriving an approval's risk level on the client, and saying honestly that it
 * is a derivation.
 *
 * ## The problem
 *
 * `ApprovalSummary` (contract §5) carries `id`, `run_id`, `tool_call_id`,
 * `status`, `reason`, `risk_explanation`, `arguments_snapshot`, the decision
 * fields and `context`. **It carries no risk level.** The M7 acceptance criteria
 * ask the approval card to "show tool, arguments, risk level, the model's
 * `reason` and the deterministic `risk_explanation`", so the level has to come
 * from somewhere the response does have.
 *
 * ## What is derived, from what, and how well
 *
 * Three inputs are available on the card:
 *
 * - `GET /api/runs/{id}/tool_calls` — not an endpoint. The run detail's
 *   `tool_calls[]` array does carry a `permission` field per call
 *   (`ToolCallDetail.permission`, the `Permission` enum), and it is keyed by the
 *   same `tool_call_id` the approval carries. That is the *authoritative*
 *   source and is preferred whenever the run detail is on screen.
 * - `arguments_snapshot` — the exact gate arguments, always present.
 * - `risk_explanation` — a deterministic sentence, but a *sentence*: there is no
 *   field to switch on.
 *
 * So the derivation is, in order of preference:
 *
 * 1. **The tool call's `permission`, when the run detail is available.** This is
 *    not a guess: it is the same `Permission` enum value the policy engine
 *    gated on, read back from the database through a different endpoint.
 * 2. **The tool name**, matched against a table of the three permission classes
 *    the project actually uses, as a fallback for the Approvals list where the
 *    run detail is not loaded.
 *
 * ## Why it is still not the same as a field
 *
 * Fallback 2 is a **duplication of `TOOL_REGISTRY`**. The registry is static
 * code in `src/opspilot/domain/tools.py` and is the authority; a copy of it in
 * TypeScript is a second place for the same fact to live, and the two can
 * disagree. That this file's whole reason to exist is a missing API field, and
 * that the copy is a fallback rather than the primary path, are both reasons it
 * should be deleted the moment `ApprovalSummary` grows a `permission` (or a
 * `risk_level`) field.
 *
 * It is kept, and kept explicit, because the alternative is refusing to show
 * the risk level at all — and the acceptance criterion asks for it. Showing a
 * derived level with its derivation stated is honest; showing nothing, or
 * showing the level as though the API had said it, is not.
 *
 * ## The rule itself
 *
 * `high_risk_write` if either source says so. `safe_write` if either says that
 * and neither says `high_risk_write`. Otherwise `read`.
 *
 * The asymmetry is deliberate: the *higher* of two answers wins. If the
 * fallback table and the authoritative field disagree, the operator must see the
 * riskier reading, because the cost of understating a high-risk write is a
 * refund a human did not really approve, and the cost of overstating is a
 * sentence that reads as cautious.
 */

/** The three permission levels, mirroring the generated `Permission` union. */
export type Permission = "read" | "safe_write" | "high_risk_write";

/**
 * `TOOL_REGISTRY`'s permission per tool, transcribed.
 *
 * A copy, and only ever a fallback -- see the module docstring. Every tool name
 * in `src/opspilot/domain/tools.py` at M7 is here; a name missing from this
 * table resolves to `null` rather than to a default, so the caller can say
 * "unknown" instead of quietly claiming a tool is a read.
 */
const TOOL_PERMISSION: Record<string, Permission> = {
  "crm.get_customer": "read",
  "billing.get_invoice": "read",
  "billing.list_transactions": "read",
  "knowledge.search": "read",
  "billing.issue_refund": "high_risk_write",
  "issues.create": "safe_write",
};

/** The permission for a tool name, from the transcribed registry. */
export function permissionForTool(toolName: string): Permission | null {
  return TOOL_PERMISSION[toolName] ?? null;
}

/**
 * The risk level to display.
 *
 * @param toolPermission - the `permission` from the run detail's tool call,
 *   when it is available. Authoritative.
 * @param toolName - the tool name, when known. Fallback only.
 */
export function deriveRisk(
  toolPermission: string | null | undefined,
  toolName: string | null,
): { level: Permission | null; source: "tool call" | "tool name" | "unknown" } {
  if (isPermission(toolPermission)) {
    return { level: toolPermission, source: "tool call" };
  }
  if (toolName) {
    const fromName = permissionForTool(toolName);
    if (fromName) {
      return { level: fromName, source: "tool name" };
    }
  }
  return { level: null, source: "unknown" };
}

function isPermission(value: string | null | undefined): value is Permission {
  return value === "read" || value === "safe_write" || value === "high_risk_write";
}

/** A sentence stating where the displayed risk level came from. */
export function riskProvenance(source: "tool call" | "tool name" | "unknown"): string {
  switch (source) {
    case "tool call":
      return "From the tool call's permission field, read back from the run.";
    case "tool name":
      return "Derived from the tool's name against OpsPilot's static tool registry. The API does not return a risk level on an approval; this is the closest available reading.";
    case "unknown":
      return "The API does not return a risk level for this approval, and no tool call was available to read one from. The deterministic explanation below still states what will happen.";
  }
}