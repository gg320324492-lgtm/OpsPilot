/**
 * The screen-to-API layer, driven directly -- no Docker, no browser.
 *
 * ## Why this is not a browser test
 *
 * Every claim here is about one module and a stubbed transport. Starting
 * Compose to assert that `loadStatusCounts` reports a failed status query as a
 * failure would cost minutes of wall clock per run for a question that has no
 * browser in it. So this file imports the module and calls it, the same way
 * `risk-derivation.spec.ts` drives `lib/risk.ts` and `i18n-catalogue.spec.ts`
 * drives the catalogues.
 *
 * ## WHAT IS STUBBED, AND WHY IT IS SAFE TO STUB
 *
 * `lib/api/client.ts` makes every request with the global `fetch`, so that is
 * what is replaced here. Nothing else is: the URL building, the bearer header,
 * the error envelope and the `ApiError` are the real code, which is the point --
 * a test that stubbed the client would be testing the stub.
 *
 * The stub is restored after every test. That matters beyond tidiness: this
 * suite and the browser walkthroughs share one Playwright worker
 * (`workers: 1`), and `stack.ts` waits for the API with `fetch` of its own --
 * a stub left in place would answer for it.
 *
 * ## THE TWO DEFECTS THIS FILE HOLDS TO
 *
 * 1. **A count that could not be read is not a count of zero.** The Dashboard's
 *    first screen exists to say whether something needs a human, and a failed
 *    query rendered as `0 waiting for approval` is the dashboard asserting
 *    that nothing is waiting while the API would not answer. `loadStatusCounts`
 *    used to fold every failure into a zero and report its own `error` field as
 *    `null` unconditionally, so the two were indistinguishable.
 * 2. **A filter that drops rows is not a filter.** `GET /api/runs` filters by
 *    status and by nothing else (contract §1), so the ticket filter reads
 *    every page of it. A filter that stopped at the first page would drop the
 *    ticket's oldest run on a deployment with more runs than one page, and the
 *    drop would be invisible.
 */

import { expect, test } from "@playwright/test";

import { ApiError } from "@/lib/api/client";
import {
  isNotFound,
  loadRunsByTicket,
  loadStatusCounts,
  loadTicketList,
} from "@/lib/data";

/** What a stubbed endpoint answers with. */
type Stub = {
  status?: number;
  body: unknown;
};

const originalFetch = globalThis.fetch;
const originalToken = process.env.OPSPILOT_OPERATOR_TOKEN;

/**
 * Answer every request with `handler`, which sees the URL and returns the
 * response for it.
 *
 * The bearer token is checked rather than ignored: a request that arrived
 * without one would be a request the real client would have refused, and a
 * test that let it through would be testing a transport the dashboard does not
 * have.
 */
function stubApi(handler: (url: string, init: RequestInit | undefined) => Stub): void {
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const headers = new Headers(init?.headers);
    if (!headers.get("authorization")) {
      throw new Error(`the stub received an unauthenticated request for ${url}`);
    }
    const { status = 200, body } = handler(url, init);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
}

test.beforeAll(() => {
  // The client refuses to make a request at all without a token, and says so
  // with a message about the dashboard's own configuration. A placeholder is
  // enough: it never leaves this process and is never a credential.
  process.env.OPSPILOT_OPERATOR_TOKEN = "data-layer-spec-placeholder-token";
});

test.afterEach(() => {
  globalThis.fetch = originalFetch;
});

test.afterAll(() => {
  process.env.OPSPILOT_OPERATOR_TOKEN = originalToken;
});

/** A `RunSummary` row, with the fields the Runs table renders. */
function runSummary(
  id: string,
  ticketId: string,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    id,
    ticket_id: ticketId,
    status: "completed",
    model_provider: "fake",
    model_name: "fake-model",
    created_at: "2026-10-05T10:42:03Z",
    started_at: "2026-10-05T10:42:04Z",
    completed_at: "2026-10-05T10:42:09Z",
    failure_reason: null,
    ...overrides,
  };
}

test.describe("the Dashboard's status counts", () => {
  test("a status whose query failed is reported as a failure, not as a zero", async () => {
    stubApi((url) => {
      if (url.includes("status=waiting_approval")) {
        return {
          status: 503,
          body: {
            error: { code: "internal_error", message: "the count could not be read", details: null },
          },
        };
      }
      return { body: { items: [], total: 7 } };
    });

    const { counts, errors } = await loadStatusCounts();

    // The count is *unknown*, which is not the same claim as "zero".
    expect(counts.waiting_approval).toBeNull();
    expect(errors.waiting_approval).toBeInstanceOf(ApiError);
    expect((errors.waiting_approval as ApiError).status).toBe(503);
    // And the statuses that did answer are still counted.
    expect(counts.failed).toBe(7);
    expect(counts.completed).toBe(7);
    expect(errors.failed).toBeUndefined();
  });

  test("every status is counted when nothing fails", async () => {
    stubApi((url) => {
      const status = new URL(url).searchParams.get("status");
      return { body: { items: [], total: status === "failed" ? 2 : 3 } };
    });

    const { counts, errors } = await loadStatusCounts();

    expect(counts.failed).toBe(2);
    expect(counts.waiting_approval).toBe(3);
    expect(Object.keys(errors)).toHaveLength(0);
  });

  test("a failed count is not a number the screen can render as zero", async () => {
    // The shape of the defect, asserted directly: `null` is what stops the
    // Dashboard's `value={counts.waiting_approval}` from reaching the screen
    // as a green `0`.
    stubApi(() => ({
      status: 500,
      body: { error: { code: "internal_error", message: "boom", details: null } },
    }));

    const { counts, errors } = await loadStatusCounts();

    for (const value of Object.values(counts)) {
      expect(value).toBeNull();
    }
    expect(Object.keys(errors)).toHaveLength(9);
  });
});

test.describe("the ticket filter", () => {
  test("it returns that ticket's runs and only those", async () => {
    stubApi((url) => {
      if (url.includes("/api/tickets/")) {
        return {
          body: {
            ticket: {
              id: "ticket-1",
              subject: "Charged twice",
              body: "We were charged twice.",
              customer_email: "billing@acme.example",
              created_at: "2026-10-05T10:42:03Z",
            },
            runs: [],
          },
        };
      }
      return {
        body: {
          items: [
            runSummary("run-a", "ticket-1"),
            runSummary("run-b", "ticket-2"),
            runSummary("run-c", "ticket-1"),
          ],
          total: 3,
        },
      };
    });

    const loaded = await loadRunsByTicket("ticket-1");

    expect(loaded.error).toBeNull();
    expect(loaded.data?.map((row) => row.id)).toEqual(["run-a", "run-c"]);
  });

  test("it reads every page, so it cannot drop the ticket's oldest run", async () => {
    // 201 runs: the first page holds 200 of them and the second holds the one
    // that belongs to the ticket. A filter that stopped at the first page
    // would answer "this ticket has no runs".
    const many = Array.from({ length: 200 }, (_, index) =>
      runSummary(`other-${index}`, "ticket-2"),
    );
    stubApi((url) => {
      if (url.includes("/api/tickets/")) {
        return {
          body: {
            ticket: {
              id: "ticket-1",
              subject: "Charged twice",
              body: "We were charged twice.",
              customer_email: "billing@acme.example",
              created_at: "2026-10-05T10:42:03Z",
            },
            runs: [],
          },
        };
      }
      const offset = Number(new URL(url).searchParams.get("offset") ?? "0");
      if (offset === 0) {
        return { body: { items: many, total: 201 } };
      }
      return { body: { items: [runSummary("the-oldest", "ticket-1")], total: 201 } };
    });

    const loaded = await loadRunsByTicket("ticket-1");

    expect(loaded.error).toBeNull();
    expect(loaded.data?.map((row) => row.id)).toEqual(["the-oldest"]);
  });

  test("a ticket this deployment does not have is a failure, not an empty list", async () => {
    stubApi((url) => {
      if (url.includes("/api/tickets/")) {
        return {
          status: 404,
          body: {
            error: { code: "ticket_not_found", message: "Ticket x does not exist.", details: null },
          },
        };
      }
      return { body: { items: [runSummary("run-a", "ticket-1")], total: 1 } };
    });

    const loaded = await loadRunsByTicket("ticket-x");

    // The screen must not fall back to the whole list, which would read as
    // "the filter matched everything".
    expect(loaded.data).toBeNull();
    expect(isNotFound(loaded.error)).toBe(true);
  });

  test("the ticket filter and the status filter compose", async () => {
    stubApi((url) => {
      if (url.includes("/api/tickets/")) {
        return {
          body: {
            ticket: {
              id: "ticket-1",
              subject: "Charged twice",
              body: "We were charged twice.",
              customer_email: "billing@acme.example",
              created_at: "2026-10-05T10:42:03Z",
            },
            runs: [],
          },
        };
      }
      return {
        body: {
          items: [
            runSummary("run-a", "ticket-1", { status: "failed" }),
            runSummary("run-b", "ticket-1", { status: "completed" }),
            runSummary("run-c", "ticket-2", { status: "failed" }),
          ],
          total: 3,
        },
      };
    });

    const loaded = await loadRunsByTicket("ticket-1", { status: "failed" });

    expect(loaded.data?.map((row) => row.id)).toEqual(["run-a"]);
  });
});

test.describe("the ticket screen's own filter", () => {
  test("it shows the one ticket the link named", async () => {
    stubApi((url) => {
      if (url.includes("/api/tickets/")) {
        return {
          body: {
            ticket: {
              id: "ticket-1",
              subject: "Charged twice",
              body: "We were charged twice.",
              customer_email: "billing@acme.example",
              created_at: "2026-10-05T10:42:03Z",
            },
            runs: [],
          },
        };
      }
      return { body: { items: [], total: 0 } };
    });

    const loaded = await loadTicketList("ticket-1");

    expect(loaded.error).toBeNull();
    expect(loaded.data?.items.map((ticket) => ticket.id)).toEqual(["ticket-1"]);
    expect(loaded.data?.total).toBe(1);
  });

  test("a ticket this deployment does not have says so", async () => {
    stubApi(() => ({
      status: 404,
      body: {
        error: { code: "ticket_not_found", message: "Ticket x does not exist.", details: null },
      },
    }));

    const loaded = await loadTicketList("ticket-x");

    expect(loaded.data).toBeNull();
    expect(isNotFound(loaded.error)).toBe(true);
  });
});
