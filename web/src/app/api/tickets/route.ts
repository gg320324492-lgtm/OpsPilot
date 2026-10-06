import { NextResponse } from "next/server";
import { ApiError, api } from "@/lib/api/client";

/**
 * `POST /api/tickets`, proxied from the browser to the API.
 *
 * **This is the only route handler in the dashboard, and it is the reason the
 * browser is allowed to submit anything at all.**
 *
 * The form is a Client Component, and a Client Component cannot call the API
 * directly without one of two things going wrong: it has no token (it is not
 * `NEXT_PUBLIC_`-prefixed, deliberately), or somebody adds that prefix and ships
 * a refund-capable credential into a JavaScript bundle. So the browser POSTs
 * *here*, same-origin, and this handler calls the API with the token it reads
 * from the server environment.
 *
 * The API's CORS policy exists for the reads a browser makes directly; this
 * write goes through the same origin as the page, so it needs no CORS at all.
 * That is also why `docs/api-contract.md` §8.1's argument about a rewrite proxy
 * not being chosen does not apply to *this* route: it is an explicit
 * application route, not a transparent config-level rewrite, so a misconfigured
 * one fails loudly here rather than silently returning "no data".
 *
 * The 201 body is passed through verbatim -- `{ticket, run}` -- so the client
 * has both ids and can link straight to the run without a second request.
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "validation_error",
          message: "The request body was not valid JSON.",
        },
      },
      { status: 400 },
    );
  }

  try {
    const created = await api.createTicket(body as Parameters<typeof api.createTicket>[0]);
    return NextResponse.json(created, { status: 201 });
  } catch (error) {
    // The error envelope is forwarded with its own status rather than being
    // re-wrapped. A 409 `ticket_exists` has to reach the form as a 409 with
    // the API's message, because that status is what tells the operator their
    // external_id is a duplicate -- and re-wrapping it as a 500 would turn a
    // correct, actionable answer into "something broke".
    if (error instanceof ApiError) {
      return NextResponse.json(
        {
          error: {
            code: error.code,
            message: error.message,
            details: error.details,
          },
        },
        { status: error.status },
      );
    }
    // A plain `Error` here means the token was not configured in the
    // dashboard's environment -- `apiFetch` raises its own message for that,
    // and it is a dashboard misconfiguration rather than an API failure, so it
    // says so rather than reporting a 500 from a server that was never reached.
    const message =
      error instanceof Error
        ? error.message
        : "The dashboard could not submit the ticket.";
    return NextResponse.json(
      { error: { code: "internal_error", message, details: null } },
      { status: 500 },
    );
  }
}