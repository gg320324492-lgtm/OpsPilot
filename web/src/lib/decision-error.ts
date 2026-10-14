import { approvalStatusLabel } from "@/i18n/enums";
import type { TFunction } from "@/i18n/format";

/**
 * The one place a failed decision is turned into something an operator reads.
 *
 * ## WHAT IS LOCALISED HERE AND WHAT IS NOT
 *
 * Three different kinds of string arrive at this module, and they are handled
 * three different ways -- which is the whole content of §P2-F's "error `code`s
 * are machine identifiers" rule as it lands in practice:
 *
 * 1. **The sentences this file writes.** Localised. They are the dashboard
 *    explaining what it is telling the operator, and on a screen where someone
 *    is deciding whether money moves, that explanation is the product.
 * 2. **`failure.message`, which the API wrote.** Passed through **verbatim**, in
 *    every locale. `docs/phase2-plan.md` §P2-F puts it out of scope by name:
 *    `docs/api-contract.md` fixes several error `message`s verbatim and Python
 *    tests assert them, so a Chinese operator copying a failure into a bug
 *    report gets the API's own English sentence -- exactly as before -- wrapped
 *    in Chinese.
 * 3. **`failure.code`, and the `code:` line the panel prints.** Verbatim, in
 *    every locale, because `docs/api-contract.md` §6 fixes it and every consumer
 *    switches on it.
 *
 * That is also why this module takes a `t` and not a dictionary of pre-resolved
 * strings: `describeDecisionFailure` is called from a Client Component, which
 * cannot read the locale cookie, so the server hands it the `decision` section
 * of the catalogue and this builds the same `t` the server would.
 */

export type Decision = "approve" | "reject";

export type DecisionFailure = {
  /** The HTTP status the Route Handler forwarded, or `0` for a transport failure. */
  status: number;
  /** The envelope's `code`, or a dashboard-local one. Never translated. */
  code: string;
  /** The API's own `message`. Never translated -- see the module docstring. */
  message: string;
  /** `details.approval_id` from the envelope. An id, not copy. */
  approvalId: string | null;
  /** `details.status` from the envelope. Labelled, not translated. */
  approvalStatus: string | null;
  /** `details.trace_id` from the envelope. An id, not copy. */
  traceId: string | null;
};

export type DecisionFailureView = {
  tone: "conflict" | "failure";
  headline: string;
  detail: string;
  status: number;
  code: string;
  approvalId: string | null;
  approvalStatus: string | null;
  traceId: string | null;
};

/**
 * Read the envelope out of a failed decision response.
 *
 * The `message` this returns is the **API's own** sentence whenever the body
 * carried one, and is passed through untranslated everywhere it is rendered.
 * Only the fallback -- a body that was not an envelope -- is written here, and
 * only therefore localised; a dashboard that cannot read the API's error cannot
 * show the API's error either.
 */
export function parseDecisionFailure(
  payload: unknown,
  status: number,
  t: TFunction,
): DecisionFailure {
  const envelope = asEnvelope(payload);
  return {
    status,
    code: envelope?.code ?? "error",
    message: envelope?.message ?? t("common.apiUnreadable", { status }),
    approvalId: readString(detailsOf(envelope), "approval_id"),
    approvalStatus: readString(detailsOf(envelope), "status"),
    traceId: readString(detailsOf(envelope), "trace_id"),
  };
}

export function transportFailure(message: string): DecisionFailure {
  return {
    status: 0,
    code: "dashboard_unreachable",
    message,
    approvalId: null,
    approvalStatus: null,
    traceId: null,
  };
}

export function describeDecisionFailure(
  failure: DecisionFailure,
  t: TFunction,
): DecisionFailureView {
  switch (failure.code) {
    case "approval_already_decided":
      return alreadyDecided(failure, t);
    case "run_not_awaiting_approval":
      return runNotParked(failure, t);
    case "run_already_completed":
      return runMovedOn(failure, t);
    case "approval_not_found":
      return notFound(failure, t);
    case "unauthorized":
      return unauthorized(failure, t);
    default:
      return generic(failure, t);
  }
}

function alreadyDecided(failure: DecisionFailure, t: TFunction): DecisionFailureView {
  // The status is the API's own enum value; only its *label* is localised, and
  // an unlabelled one falls through to `humanise` unchanged.
  const state = failure.approvalStatus
    ? approvalStatusLabel(failure.approvalStatus, t)
    : t("decision.alreadyDecided_defaultState");
  return {
    tone: "conflict",
    headline: t("decision.alreadyDecided_headline"),
    detail:
      t("decision.alreadyDecided_before", { state }) +
      " " +
      failure.message +
      " " +
      t("decision.alreadyDecided_after"),
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

function runNotParked(failure: DecisionFailure, t: TFunction): DecisionFailureView {
  return {
    tone: "conflict",
    headline: t("decision.runNotParked_headline"),
    detail: failure.message + " " + t("decision.runNotParked_after"),
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

function runMovedOn(failure: DecisionFailure, t: TFunction): DecisionFailureView {
  return {
    tone: "conflict",
    headline: t("decision.runMovedOn_headline"),
    detail: failure.message + " " + t("decision.runMovedOn_after"),
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

function notFound(failure: DecisionFailure, t: TFunction): DecisionFailureView {
  return {
    tone: "failure",
    headline: t("decision.notFound_headline"),
    detail: failure.message + " " + t("decision.notFound_after"),
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

function unauthorized(failure: DecisionFailure, t: TFunction): DecisionFailureView {
  return {
    tone: "failure",
    headline: t("decision.unauthorized_headline"),
    detail: failure.message + " " + t("decision.unauthorized_after"),
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

function generic(failure: DecisionFailure, t: TFunction): DecisionFailureView {
  const conflict = failure.status === 409;
  return {
    tone: conflict ? "conflict" : "failure",
    headline: conflict
      ? t("decision.generic_conflict_headline", { code: failure.code })
      : t("decision.generic_failure_headline", { status: failure.status }),
    detail: failure.message,
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

function asEnvelope(payload: unknown): {
  code: string;
  message: string;
  details: Record<string, unknown> | null;
} | null {
  if (!payload || typeof payload !== "object" || !("error" in payload)) {
    return null;
  }
  const error = (payload as { error: unknown }).error;
  if (!error || typeof error !== "object") {
    return null;
  }
  const { code, message, details } = error as {
    code?: unknown;
    message?: unknown;
    details?: unknown;
  };
  if (typeof code !== "string" || typeof message !== "string") {
    return null;
  }
  return {
    code,
    message,
    details:
      details && typeof details === "object" && !Array.isArray(details)
        ? (details as Record<string, unknown>)
        : null,
  };
}

function detailsOf(
  envelope: { details: Record<string, unknown> | null } | null,
): Record<string, unknown> | null {
  return envelope?.details ?? null;
}

function readString(details: Record<string, unknown> | null, key: string): string | null {
  if (!details) {
    return null;
  }
  const value = details[key];
  return typeof value === "string" ? value : null;
}