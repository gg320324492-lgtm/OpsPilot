/**
 * The one place a failed approve/reject becomes words an operator can read.
 *
 * ## Why this is a module and not a branch inside one screen
 *
 * Approve appears on two screens -- the pending inbox and the run detail -- and
 * both reach the API through the same Route Handler. If each screen wrote its
 * own version of "that did not work", the 409 would have two chances to be
 * softened into `Something went wrong` and one chance to be right. The response
 * that prevents a double grant is not a case that can afford to be
 * reformatted inconsistently, so the copy lives here and both screens render
 * what this returns.
 *
 * ## What the 409 actually is
 *
 * `POST /api/approvals/{id}/approve` performs `pending -> approved` as a
 * *conditional* update -- `UPDATE ... WHERE id = ? AND status = 'pending'`
 * (contract §5). A second click matches no row, and the API answers 409
 * `approval_already_decided`. That answer is the thing standing between a
 * double-click and a double grant, and it is the reason a double-click is safe.
 *
 * A dashboard that catches the 409 and carries on as though the decision landed
 * is not handling an error: it is deleting the safety property and keeping the
 * button. So the 409 is **never** folded into the generic failure styling. It
 * gets its own tone -- amber, not red -- because nothing failed. The system did
 * what it is supposed to do and said so, and the operator needs to be able to
 * tell that apart from a broken request.
 *
 * ## Who decided, and when
 *
 * The contract's example (§6) is explicit that the message names the actor and
 * the moment, and `api/routers/approvals.py::_already_decided` builds it that
 * way -- `"Approval b7e2… was already approved by admin at
 * 2026-10-05T10:44:32Z."`.
 *
 * What `details` carries is narrower: only `approval_id` and `status`. **The
 * actor and the moment are therefore rendered verbatim out of `message`** rather
 * than recomposed from fields. That is not laziness. Contract §6 says clients
 * branch on `code` and never parse `message`; the only honest way to show a
 * sentence the server wrote for humans is to show it, and the only honest way to
 * show the id and status is to read them from `details` where they are
 * structured. Doing it the other way round -- scraping a name out of prose -- is
 * exactly the rule the contract exists to stop.
 *
 * Deliberately *not* here: anything that decides the request should be retried.
 * Whether a 409 is retryable is the operator's judgment about the other tab they
 * may have open, not something this module can know.
 */

import { humanise } from "@/lib/format";

/** The two decisions gate 5 offers a human. */
export type Decision = "approve" | "reject";

/**
 * The contract's error envelope (§6), narrowed by hand.
 *
 * Narrowed rather than cast: this runs in the browser, on a body a Route
 * Handler forwarded, and there is no compile-time tie from a client component to
 * the generated `ErrorResponse`. An unchecked cast here would be a second
 * hand-written copy of a schema the generator produces -- the exact drift the
 * project keeps refusing to introduce.
 */
export type DecisionFailure = {
  /** The API's status, forwarded verbatim. 409 is the interesting one. */
  status: number;
  /** The stable branch key. Never displayed as if it were a sentence. */
  code: string;
  /** The API's human-readable sentence. Shown, never parsed. */
  message: string;
  /** `details.approval_id`. */
  approvalId: string | null;
  /** `details.status` -- the status the approval already holds. */
  approvalStatus: string | null;
  /** `details.trace_id`, for joining a 500 to the API's log. */
  traceId: string | null;
};

/** What a screen renders: one headline, one explanation, and the identifiers. */
export type DecisionFailureView = {
  /**
   * `conflict` for a 409, `failure` for everything else.
   *
   * The two are not two shades of "bad". A conflict is the guard working and is
   * coloured amber; a failure is a request that did not do its job and is
   * coloured red. An operator who reads both as "an error" loses the only
   * signal that distinguishes them.
   */
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
 * Read the forwarded envelope into a `DecisionFailure`.
 *
 * Every field degrades to a neutral value rather than throwing, because the
 * worst outcome here is a screen that crashes while trying to explain a crash.
 */
export function parseDecisionFailure(payload: unknown, status: number): DecisionFailure {
  const envelope = asEnvelope(payload);
  return {
    status,
    code: envelope?.code ?? "error",
    message:
      envelope?.message ??
      `The API answered ${status} with a body this screen could not read.`,
    approvalId: readString(detailsOf(envelope), "approval_id"),
    approvalStatus: readString(detailsOf(envelope), "status"),
    traceId: readString(detailsOf(envelope), "trace_id"),
  };
}

/**
 * The failure the dashboard itself produces: the browser could not reach its
 * own Route Handler.
 *
 * A distinct code rather than a blank `code`, so the panel can say what is
 * actually broken -- the Next.js server, not the API. Conflating the two sends
 * an operator to restart the wrong process.
 */
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

/** The sentence for a parsed failure. */
export function describeDecisionFailure(failure: DecisionFailure): DecisionFailureView {
  switch (failure.code) {
    case "approval_already_decided":
      return alreadyDecided(failure);
    case "run_not_awaiting_approval":
      return runNotParked(failure);
    case "run_already_completed":
      return runMovedOn(failure);
    case "approval_not_found":
      return notFound(failure);
    case "unauthorized":
      return unauthorized(failure);
    default:
      return generic(failure);
  }
}

/**
 * The 409, stated as the guard working.
 *
 * The headline says *nothing was granted twice* rather than "conflict", because
 * "conflict" is a category and the operator needs the consequence. The API's own
 * message follows, verbatim, which is where the actor and the moment come from
 * (see the module comment).
 */
function alreadyDecided(failure: DecisionFailure): DecisionFailureView {
  const state = failure.approvalStatus ? humanise(failure.approvalStatus) : "already decided";
  return {
    tone: "conflict",
    headline: "Already decided — this click granted nothing.",
    detail:
      `The API returned 409 approval_already_decided: this approval is ${state}. ` +
      failure.message +
      " " +
      "This is the guard working, not a failure. The decision is a conditional update " +
      "(`WHERE id = ? AND status = 'pending'`), so the second click found no row to move " +
      "and no second grant was written. If you have two cards for the same refund, the " +
      "other one is still pending and still needs its own decision — approving one does " +
      "not decide the other.",
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

/**
 * 409 `run_not_awaiting_approval`: the approval is pending but the run has
 * moved, so there is nothing for a decision to release.
 */
function runNotParked(failure: DecisionFailure): DecisionFailureView {
  return {
    tone: "conflict",
    headline: "The run is not parked any more, so nothing was decided.",
    detail:
      failure.message +
      " " +
      "A decision only transitions a run that is `waiting_approval`. The run reached this " +
      "state after the approval was written -- most often because a second card for the " +
      "same run was decided first, or because the worker reclaimed it.",
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

/** 409 `run_already_completed`: the run is somewhere it cannot be moved from. */
function runMovedOn(failure: DecisionFailure): DecisionFailureView {
  return {
    tone: "conflict",
    headline: "The run had already moved on.",
    detail:
      failure.message +
      " " +
      "The decision was refused before anything was written, because the run was not in " +
      "the state the transition expects. Open the run to see where it is.",
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

/** 404: the id is gone from this deployment. */
function notFound(failure: DecisionFailure): DecisionFailureView {
  return {
    tone: "failure",
    headline: "That approval no longer exists.",
    detail:
      failure.message +
      " " +
      "Reload the inbox. An approval decided in another tab leaves the pending filter, so " +
      "a card left open for a while can point at a row that has moved.",
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

/** 401: the token is the problem, and it is every screen's problem. */
function unauthorized(failure: DecisionFailure): DecisionFailureView {
  return {
    tone: "failure",
    headline: "The API rejected the operator token, so nothing was decided.",
    detail:
      failure.message +
      " " +
      "Every request from the dashboard fails while this is true. Set " +
      "OPSPILOT_OPERATOR_TOKEN in the dashboard's environment to the value the API " +
      "process was started with, then restart the dev server.",
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

/**
 * Everything else, with the 409-ness still respected.
 *
 * The tone is derived from the status rather than being hardcoded to `failure`,
 * so a code the backend adds later under a 409 is still rendered as the guard
 * working instead of as a crash. An unrecognised code must read as an
 * unrecognised *conflict*, not as an unrecognised error.
 */
function generic(failure: DecisionFailure): DecisionFailureView {
  const conflict = failure.status === 409;
  return {
    tone: conflict ? "conflict" : "failure",
    headline: conflict
      ? `The API refused this decision with 409 (${failure.code}).`
      : `The decision did not go through (${failure.status}).`,
    detail: failure.message,
    status: failure.status,
    code: failure.code,
    approvalId: failure.approvalId,
    approvalStatus: failure.approvalStatus,
    traceId: failure.traceId,
  };
}

/**
 * Narrow an unknown body to the contract's `{error: {code, message, details}}`.
 *
 * Returns `null` rather than throwing so `parseDecisionFailure` can still say
 * something true about a response that is not an envelope.
 */
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

/** `details` on a parsed envelope, with an absent envelope folded into `null`. */
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
