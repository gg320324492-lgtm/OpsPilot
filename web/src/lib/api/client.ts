/**
 * The HTTP client: bearer auth, the error envelope, and typed returns.
 *
 * Everything the dashboard knows about the API's shapes comes from
 * `types.ts`, which is generated from the running app's OpenAPI document. This
 * file is the part that is *not* generated, and it is thin on purpose: it adds
 * the two things a generated type cannot -- the bearer token and the translation
 * from a non-2xx response into a thrown `ApiError` -- and nothing else. There is
 * no response normalisation here, because any normalising this layer did would
 * be a second place for the client's idea of the API to live, and two places is
 * how they start to disagree.
 *
 * ## Where the token comes from, and why
 *
 * `getOperatorToken()` reads a **server-only** environment variable
 * (`OPSPILOT_OPERATOR_TOKEN`). It deliberately does *not* read a
 * `NEXT_PUBLIC_`-prefixed variable, and that is the important decision here.
 *
 * A `NEXT_PUBLIC_` token would be inlined into the JavaScript bundle at build
 * time and readable by anyone who opens the dashboard -- including any other
 * page the operator visits in the same browser, any browser extension with
 * access to the page, and anyone who can fetch the bundle. Phase 1 is a
 * single-operator local deployment, so the realistic blast radius is small. It
 * is not zero, and "it's only localhost" is the reasoning that turns a
 * development convenience into a credential in a repository's build output.
 *
 * The API already hands out refunds through these endpoints. A token in the
 * bundle is a refund-capable credential published to the browser; that is a
 * materially worse thing to hand out than the ergonomics cost.
 *
 * So the token stays on the server. Every request below is made from a React
 * **Server Component** or a **Route Handler** -- never from the browser -- which
 * is what lets the browser bundle never contain it. The cost is that a route
 * Handler proxy (or a server action) is the only way to reach the API; the
 * screens that need to poll use one. `docs/api-contract.md` §8 already notes
 * that CORS is a browser policy and the bearer token is the only authorisation;
 * keeping the token server-side means the CORS question does not even arise for
 * authenticated reads.
 *
 * If a future phase needs the token in the browser, the change is one function
 * (`getOperatorToken`) and one env var name -- but it should be a deliberate
 * decision, not an accident of a prefix.
 */

import type { components } from "./types";

/**
 * The error envelope, from the generated schema.
 *
 * This was hand-written in `error-envelope.ts` because `ErrorResponse` was
 * defined in `api/schemas.py` but declared by no route, so it never reached
 * `components.schemas` and no generator could see it. The backend now declares
 * it on every guarded route, so the type is generated like every other one.
 *
 * The hand-written copy is deleted rather than kept in sync: a hand-maintained
 * duplicate of a schema the generator can see is exactly the copy that drifts.
 */
export type ErrorResponse = components["schemas"]["ErrorResponse"];
export type ErrorBody = components["schemas"]["ErrorBody"];

/**
 * The stable `code` values a client branches on.
 *
 * Contract §6 fixes the vocabulary; `| string` at the point of use because the
 * backend may add a code without a frontend release, and an unrecognised code
 * must read as an unrecognised failure rather than crash a screen.
 */
export type ApiErrorCode =
  | "validation_error"
  | "ticket_not_found"
  | "run_not_found"
  | "approval_not_found"
  | "approval_already_decided"
  | "run_not_awaiting_approval"
  | "run_already_completed"
  | "ticket_exists"
  | "schema_invalid"
  | "internal_error"
  | "not_ready"
  // Framework-derived via `_code_for_status` in api/errors.py: a bare
  // HTTPException (a 401 from auth.py) is re-enveloped, so these are what the
  // API really returns even though contract §6's table does not list them.
  | "unauthorized"
  | "not_found"
  | "method_not_allowed"
  | "error";

/**
 * A failed API call, carrying the contract's envelope.
 *
 * Thrown rather than returned so a caller cannot forget to check: a dashboard
 * that renders `undefined` as if it were data is worse than one that shows the
 * failure. The `code` is the branch key (contract §6); `message` is for humans
 * and must never be parsed, which is why it is not a discriminator here.
 */
export class ApiError extends Error {
  readonly code: ApiErrorCode | string;
  readonly status: number;
  readonly details: Record<string, unknown> | null;

  constructor(
    status: number,
    body: ErrorResponse | null,
    fallbackMessage: string,
  ) {
    super(body?.error?.message ?? fallbackMessage);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code ?? "error";
    this.details = body?.error?.details ?? null;
  }

  /**
   * True when the failure is an authentication problem rather than a bug or a
   * missing resource.
   *
   * This is the case the dashboard must render visibly. A missing or wrong token
   * makes *every* request fail, and a dashboard that shows an empty list with a
   * console error reads as "no tickets yet" -- which is a plausible-looking lie
   * about the state of the system. Callers use this to swap in the
   * "token missing or wrong" panel instead of an empty list.
   */
  get isAuthFailure(): boolean {
    return this.status === 401 || this.code === "unauthorized";
  }

  /** The `trace_id` the 500 handler returns, for joining to the API's log. */
  get traceId(): string | null {
    const id = this.details?.["trace_id"];
    return typeof id === "string" ? id : null;
  }
}

/** Where the API lives. Read server-side; see the module comment on `NEXT_PUBLIC_`. */
function getApiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  // Trailing slashes removed so joining a path never yields a double slash,
  // which some proxies normalise and some do not.
  return raw.replace(/\/+$/, "");
}

/** The operator token, read on the server only. */
function getOperatorToken(): string | null {
  return process.env.OPSPILOT_OPERATOR_TOKEN ?? null;
}

/**
 * Ask whether the API will accept our credentials, before showing any data.
 *
 * The dashboard calls this once on load so that a missing or wrong token shows a
 * clear instruction rather than a screen of empty lists. It hits `/health`,
 * which is token-exempt -- so a *correct* deployment answers even when the
 * token is wrong, and a `200` with an unreachable API still fails loudly rather
 * than looking like "no data".
 */
export async function checkApiReachability(): Promise<{
  reachable: boolean;
  authenticated: boolean;
  message?: string;
}> {
  const base = getApiBaseUrl();
  try {
    const health = await fetch(`${base}/health`, { cache: "no-store" });
    if (!health.ok) {
      return {
        reachable: false,
        authenticated: false,
        message: `The API answered ${health.status} on /health.`,
      };
    }
  } catch {
    // The caught error is deliberately not interpolated into the message: a
    // connection failure can carry the full URL and, on some runtimes, request
    // headers, and this string is rendered on the page. The operator gets the
    // base URL and an action; the detail stays in the server log, where it
    // belongs and where a token cannot leak into a screenshot.
    return {
      reachable: false,
      authenticated: false,
      message: `Could not reach the API at ${base}. Is it running on port 8000?`,
    };
  }

  // Now an authenticated call. Any 401 means the token is the problem.
  try {
    const response = await apiFetch<components["schemas"]["RunListResponse"]>(
      "/api/runs?limit=1",
      { cache: "no-store" },
    );
    return { reachable: true, authenticated: true, message: response ? "" : undefined };
  } catch (cause) {
    if (cause instanceof ApiError && cause.isAuthFailure) {
      return {
        reachable: true,
        authenticated: false,
        message:
          "The API rejected the operator token. Check OPSPILOT_OPERATOR_TOKEN " +
          "matches the value the API process was started with.",
      };
    }
    if (cause instanceof ApiError) {
      return {
        reachable: true,
        authenticated: true,
        message: `The API answered ${cause.status} (${cause.code}).`,
      };
    }
    return {
      reachable: true,
      authenticated: false,
      message: "An unexpected error occurred while checking the API.",
    };
  }
}

/**
 * Perform one authenticated request against the API.
 *
 * This is the whole client. Everything above it composes calls; nothing below it
 * re-implements a request. Adding a second path here is how a token starts
 * being sent on some requests and not others.
 *
 * @typeParam T - The generated response type for this route, taken from
 *   `types.ts`. It is the compile-time contract; the runtime payload is the
 *   API's to keep honest.
 *
 * @param path - Path beginning with `/api`, or `/health` / `/ready`.
 * @param init - Optional `RequestInit`. `Authorization` is added by this
 *   function and should not be supplied by callers.
 *
 * @returns The parsed JSON body.
 *
 * @throws {ApiError} For any non-2xx response. The thrown error carries the
 *   contract's `{error: {code, message, details}}` envelope.
 * @throws {Error} If no token is configured at all -- a *different* failure
 *   from a 401, because it means the dashboard process is misconfigured rather
 *   than the token being wrong, and the two want different instructions.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getOperatorToken();
  if (!token) {
    throw new Error(
      "OPSPILOT_OPERATOR_TOKEN is not set in the dashboard's environment. " +
        "The dashboard reads it on the server and never sends it to the browser; " +
        "set it in web/.env.local (gitignored) or the process environment.",
    );
  }

  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  // Only set Content-Type when there is a body: sending it on a GET triggers a
  // needless preflight, and CORS costs a round trip per check.
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers,
    // The dashboard polls a run while it moves, and a cached `/api/runs/{id}`
    // would make the timeline look frozen. Reads are therefore uncached by
    // default; a caller that wants the platform cache passes it explicitly.
    cache: init.cache ?? "no-store",
  });

  const body = await readBody(response);

  if (!response.ok) {
    throw new ApiError(response.status, body as ErrorResponse | null, response.statusText);
  }
  return body as T;
}

/**
 * Read a response body as JSON, tolerating an empty one.
 *
 * A 204 or an empty 500 body is legal HTTP; `response.json()` would throw a
 * `SyntaxError` and mask the real failure behind a parse error. Returns `null`
 * so `ApiError` can fall back to the status line.
 */
async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

// -- Endpoints ---------------------------------------------------------------
// Thin bindings, one per contract §1 endpoint. Each returns the generated type
// for its own `response_model`, so a backend field rename becomes a TypeScript
// error at every call site rather than an `undefined` at runtime.

export const api = {
  /** `POST /api/tickets` -- the only write that starts work (contract §2). */
  createTicket: (body: components["schemas"]["TicketCreateRequest"]) =>
    apiFetch<components["schemas"]["TicketCreateResponse"]>("/api/tickets", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** `GET /api/tickets` -- newest first (contract §10). */
  listTickets: (params: { limit?: number; offset?: number } = {}) =>
    apiFetch<components["schemas"]["TicketListResponse"]>(
      `/api/tickets${queryString(params)}`,
    ),

  /** `GET /api/tickets/{ticket_id}` -- the ticket and its runs. */
  getTicket: (ticketId: string) =>
    apiFetch<components["schemas"]["TicketDetailResponse"]>(
      `/api/tickets/${encodeURIComponent(ticketId)}`,
    ),

  /**
   * `POST /api/runs` -- re-run an existing ticket (contract §9).
   *
   * Note the asymmetry with `createTicket`, which is not a typo here: ticket
   * creation returns the `{ticket, run}` envelope because the client needs both
   * ids to begin polling, while a re-run already has the ticket id and returns a
   * bare `RunRef`. Both shapes are confirmed against the running schema, so the
   * two bindings return different types on purpose.
   */
  createRun: (body: components["schemas"]["RunCreateRequest"]) =>
    apiFetch<components["schemas"]["RunRef"]>("/api/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** `GET /api/runs` -- optionally filtered by status (contract §1). */
  listRuns: (params: { status?: string; limit?: number; offset?: number } = {}) =>
    apiFetch<components["schemas"]["RunListResponse"]>(`/api/runs${queryString(params)}`),

  /** `GET /api/runs/{run_id}` -- the run-detail payload (contract §3). */
  getRun: (runId: string) =>
    apiFetch<components["schemas"]["RunDetail"]>(
      `/api/runs/${encodeURIComponent(runId)}`,
    ),

  /**
   * `GET /api/runs/{run_id}/trace` -- the cheap timeline the dashboard polls.
   *
   * Rendered in the order the server sent it: the handler sorts by `sequence`
   * and assembles `label`/`detail` server-side, so re-sorting or re-deriving
   * them client-side would be the client disagreeing with the API about what
   * happened (M7 acceptance criterion).
   */
  getRunTrace: (runId: string) =>
    apiFetch<components["schemas"]["TraceResponse"]>(
      `/api/runs/${encodeURIComponent(runId)}/trace`,
    ),

  /** `GET /api/approvals` -- the pending inbox by default (contract §5). */
  listApprovals: (params: { status?: string; limit?: number; offset?: number } = {}) =>
    apiFetch<components["schemas"]["ApprovalListResponse"]>(
      `/api/approvals${queryString({ status: "pending", ...params })}`,
    ),

  /** `GET /api/approvals/{approval_id}` -- one approval, arguments as shown. */
  getApproval: (approvalId: string) =>
    apiFetch<components["schemas"]["ApprovalSummary"]>(
      `/api/approvals/${encodeURIComponent(approvalId)}`,
    ),

  /**
   * `POST /api/approvals/{id}/approve` -- a state change, not an execution.
   *
   * A second click returns 409 `approval_already_decided`, which surfaces as an
   * `ApiError`. Callers must show it rather than swallow it: the 409 is the
   * guard against a double grant, and a dashboard that hides it hides the thing
   * it exists to prevent (M7 acceptance criterion).
   */
  approve: (approvalId: string, body?: components["schemas"]["ApprovalDecisionRequest"]) =>
    apiFetch<components["schemas"]["ApprovalDecisionResponse"]>(
      `/api/approvals/${encodeURIComponent(approvalId)}/approve`,
      {
        method: "POST",
        // The body is optional server-side, but sending `{}` keeps the request
        // shape identical to the contract's example rather than relying on a
        // bodiless POST.
        body: JSON.stringify(body ?? {}),
      },
    ),

  /** `POST /api/approvals/{id}/reject` -- runs on to an escalation reply. */
  reject: (approvalId: string, body?: components["schemas"]["ApprovalDecisionRequest"]) =>
    apiFetch<components["schemas"]["ApprovalDecisionResponse"]>(
      `/api/approvals/${encodeURIComponent(approvalId)}/reject`,
      {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      },
    ),

  /** `GET /api/knowledge` -- indexed documents and chunk counts. */
  listKnowledge: () =>
    apiFetch<components["schemas"]["KnowledgeListResponse"]>("/api/knowledge"),

  /** `POST /api/knowledge/reindex` -- idempotent; unchanged docs are skipped. */
  reindex: () =>
    apiFetch<components["schemas"]["KnowledgeReindexResponse"]>(
      "/api/knowledge/reindex",
      { method: "POST", body: JSON.stringify({}) },
    ),
} as const;

/**
 * Build a query string from defined values only.
 *
 * `undefined` and `null` are dropped rather than stringified, so an unset
 * optional filter is absent from the URL instead of being `?status=undefined` --
 * which the API would correctly reject with a 400, turning a default into an
 * error the caller did not cause.
 */
function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      search.set(key, String(value));
    }
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}