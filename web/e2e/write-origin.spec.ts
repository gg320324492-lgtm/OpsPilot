/**
 * The privileged write handlers refuse a request that did not come from the
 * dashboard's own page -- and make no API call when they do.
 *
 * ## What this is, and why it is not a browser test
 *
 * `golden-path.spec.ts` is the browser walkthrough: it drives the real stack
 * through the UI and clicks Approve. That is the regression guard for the happy
 * path, and nothing here replaces it. What it cannot do is assert the *absence*
 * of a side effect. By the time the walkthrough has clicked Approve, the only
 * observable facts are "the inbox emptied" and "the run completed" -- which are
 * equally true of a handler that called the API and were refused afterwards. The
 * property that matters for this control is the negative one: **a request from a
 * foreign origin must not reach the API at all.** A 403 is not evidence of
 * that on its own, because "refused later by the API" looks identical from
 * outside.
 *
 * So this spec drives the three Route Handler functions directly, in Node, with
 * `globalThis.fetch` -- the one transport `@/lib/api/client.ts` uses -- replaced
 * by a spy. "The API was not called" is then a direct observation
 * (`expect(spy.calls).toHaveLength(0)`) rather than an inference from a
 * response code, which is what the acceptance criterion asks for.
 *
 * **No Docker and no browser are needed**, which is deliberate: a security
 * control that only runs where the full stack is up is a control that reports
 * nothing on the machine where a developer actually edits these handlers. This
 * file runs in the same `npm run e2e` job as the walkthrough and cannot be
 * skipped by ADR-0004's Docker rule, because it has no Docker dependency to
 * skip on.
 *
 * ## What each test is worth, and what it is not
 *
 * The handler is called with a real `Request`, so the headers under test are
 * the ones a browser would send -- including the `Origin` a cross-site
 * `fetch` cannot forge. What this file does NOT do is prove the check survives
 * a browser being tricked into sending the request; that is a property of the
 * `Origin` header's definition, argued in `@/lib/request-origin`, and no test
 * can manufacture a hostile page's fetch without a browser. The reachability
 * half -- who can get a request to this process at all -- is
 * `docker-compose.yml`'s loopback binding, guarded in Python.
 *
 * ## The other half: the set of handlers is enumerated, not listed
 *
 * The three handlers above are named by hand, and a hand-written list does not
 * notice when the thing it lists grows. A fourth privileged write route added
 * without the check would pass this file green, so the bottom of this file
 * enumerates every `route.ts` under `web/src/app/api/**` and requires each
 * mutating handler in it to call the policy. The three above stay hand-named
 * because they are *driven* -- "the API was not called" is an observation, and
 * a syntax tree cannot make it. See the section header there for why the
 * enumeration lives in this file rather than in a second one.
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";
import ts from "typescript";

import { POST as decideApproval } from "@/app/api/approvals/[approvalId]/[decision]/route";
import { POST as reindexKnowledge } from "@/app/api/knowledge/reindex/route";
import { POST as createTicket } from "@/app/api/tickets/route";
import { foreignOriginRefusal } from "@/lib/request-origin";

/**
 * The origin the dashboard's own page is served from in this spec.
 *
 * An arbitrary value rather than a port the suite happens to use, because the
 * handlers compare `Origin` against the request's own `Host` rather than
 * against a constant. A test that hardcoded the live port would pass even if
 * that comparison were deleted and replaced with an allowlist of one URL, which
 * is the thing `@/lib/request-origin` explicitly says it does not do.
 */
const OWN_HOST = "localhost:3100";
const OWN_ORIGIN = `http://${OWN_HOST}`;

/** An origin that is definitely not this page's. */
const FOREIGN_ORIGIN = "http://attacker.example";

/**
 * A disposable token for `OPSPILOT_OPERATOR_TOKEN`.
 *
 * `apiFetch` refuses to make a call at all when the variable is unset, which
 * would make the positive tests fail for a reason that has nothing to do with
 * the origin check. Not a secret: it is written by the test, lives only in this
 * process, and the real one is never read.
 */
const TOKEN = "write-origin-spec-disposable-token-not-a-secret";

/** One outbound API call the spy saw. */
type ApiCall = {
  readonly url: string;
  readonly method: string;
  /** The bearer the handler attached. The reason the handler exists at all. */
  readonly authorization: string | null;
};

/** The fetch stub, plus the calls it recorded and a way to put `fetch` back. */
type ApiSpy = {
  readonly calls: ApiCall[];
  restore(): void;
};

/**
 * Replace `globalThis.fetch` with a recorder that answers 200 with `body`.
 *
 * `@/lib/api/client.ts` reaches the API through the global `fetch` and nothing
 * else, so replacing it is the same thing as replacing the API client -- with
 * the advantage that the test cannot accidentally let a real network call
 * through, which a hand-rolled mock of the `api` object could.
 */
function spyOnApi(body: unknown): ApiSpy {
  const calls: ApiCall[] = [];
  const original = globalThis.fetch;
  const stub: typeof fetch = async (input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    calls.push({
      url,
      method: init?.method ?? "GET",
      authorization: new Headers(init?.headers).get("authorization"),
    });
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  globalThis.fetch = stub;
  return {
    calls,
    restore: () => {
      globalThis.fetch = original;
    },
  };
}

/** A POST as a browser on `origin` would send it to `host`. */
function post(
  url: string,
  headers: Record<string, string>,
  body = "{}",
): Request {
  const all: Record<string, string> = { "content-type": "application/json", ...headers };
  if (!("host" in all)) {
    all.host = OWN_HOST;
  }
  return new Request(`http://${OWN_HOST}${url}`, { method: "POST", headers: all, body });
}

/** The approvals handler, called the way Next calls it. */
function approve(request: Request): Promise<Response> {
  return decideApproval(request, {
    params: Promise.resolve({ approvalId: "appr_test", decision: "approve" }),
  });
}

/** The envelope's `code`, or `null` when the body is not an envelope. */
async function refusalCode(response: Response): Promise<string | null> {
  const payload = (await response.json()) as { error?: { code?: unknown } };
  return typeof payload.error?.code === "string" ? payload.error.code : null;
}

/**
 * Every handler, under one name, so each test can state "all three routes"
 * rather than repeating itself three times.
 *
 * The three differ in what they are trusted with -- a refund, a ticket, an
 * index rewrite -- and the whole argument of `@/lib/request-origin` is that
 * none of those distinctions changes the answer. Asserting that for all three
 * on every test is what keeps a future edit from adding the check to two of
 * them.
 *
 * `successStatus` is per-handler because the contract is: a ticket is created,
 * so 201, and the other two mutate an existing thing, so 200. A test that
 * expected 200 from all three would have been asserting a mistake.
 */
const HANDLERS = [
  {
    name: "POST /api/approvals/{id}/approve",
    call: approve,
    successStatus: 200,
    answered: { status: "approved" },
  },
  {
    name: "POST /api/tickets",
    call: (request: Request) => createTicket(request),
    successStatus: 201,
    answered: { ticket: { id: "tkt_test" }, run: { id: "00000000-0000-4000-8000-000000000000" } },
  },
  {
    name: "POST /api/knowledge/reindex",
    call: (request: Request) => reindexKnowledge(request),
    successStatus: 200,
    answered: { documents_seen: 1, documents_indexed: 1, chunks_written: 1 },
  },
] as const;

// `test.beforeEach` rather than a named import: `@playwright/test` exports the
// hooks off `test`, and importing `beforeEach` from it does not type-check.
test.beforeEach(() => {
  process.env.OPSPILOT_OPERATOR_TOKEN = TOKEN;
});

test.afterEach(() => {
  delete process.env.OPSPILOT_OPERATOR_TOKEN;
});

test.describe("a privileged write from a foreign origin", () => {
  test("is refused with 403", async () => {
    const spy = spyOnApi({});
    try {
      for (const handler of HANDLERS) {
        const response = await handler.call(post("/api/write", { origin: FOREIGN_ORIGIN }));
        expect(
          response.status,
          `${handler.name} answered ${response.status} to a request from ` +
            `${FOREIGN_ORIGIN}. It must refuse before it does anything.`,
        ).toBe(403);
        expect(
          await refusalCode(response),
          `${handler.name} must answer in the contract's error envelope so the ` +
            `screens have something they already know how to render.`,
        ).toBe("origin_foreign");
      }
    } finally {
      spy.restore();
    }
  });

  /**
   * The half that is easy to fake and the reason this file exists.
   *
   * Asserted separately from the 403 above on purpose: a handler that refused
   * *after* calling the API would satisfy that test and be exactly as unsafe
   * as one with no check at all, because the decision has already been written
   * by then. Here the spy is the whole claim -- no `fetch`, no bearer token, no
   * request to anything.
   */
  test("never reaches the API -- the token is not spent", async () => {
    const spy = spyOnApi({});
    try {
      for (const handler of HANDLERS) {
        await handler.call(post("/api/write", { origin: FOREIGN_ORIGIN }));
        expect(
          spy.calls,
          `${handler.name} called the API for a request from ${FOREIGN_ORIGIN}: ` +
            `${JSON.stringify(spy.calls)}. A refusal that happens after the call ` +
            `has already been made is not a refusal.`,
        ).toHaveLength(0);
      }
    } finally {
      spy.restore();
    }
  });
});

test.describe("a privileged write from the dashboard's own origin", () => {
  test("succeeds, with the token attached", async () => {
    for (const handler of HANDLERS) {
      const spy = spyOnApi(handler.answered);
      try {
        const response = await handler.call(post("/api/write", { origin: OWN_ORIGIN }));
        expect(
          response.status,
          `${handler.name} answered ${response.status} to a request from its own ` +
            `page (${OWN_ORIGIN}). A check that refuses the dashboard is not a ` +
            `check; this is the same happy path the browser walkthrough drives.`,
        ).toBe(handler.successStatus);
        expect(spy.calls, `${handler.name} did not call the API at all`).toHaveLength(1);
        expect(spy.calls[0]?.method).toBe("POST");
        expect(
          spy.calls[0]?.authorization,
          `${handler.name} reached the API without the operator token. That is ` +
            `the one thing these handlers exist to attach.`,
        ).toBe(`Bearer ${TOKEN}`);
      } finally {
        spy.restore();
      }
    }
  });

  /**
   * The host:port comparison, not the scheme, and not a constant.
   *
   * `localhost` and `127.0.0.1` are different names for the machine the
   * dashboard is on, and an operator who opened `http://127.0.0.1:3000` instead
   * of `http://localhost:3000` is on the dashboard's own page. A check written
   * as an allowlist of one URL would refuse them, which is a support ticket
   * waiting to happen and a reason to go looking for the "fix" later.
   */
  test("is recognised whatever the host is called, as long as it is the same one", async () => {
    const spy = spyOnApi({ status: "approved" });
    try {
      const response = await approve(
        new Request("http://127.0.0.1:3000/api/write", {
          method: "POST",
          headers: {
            host: "127.0.0.1:3000",
            origin: "http://127.0.0.1:3000",
            "content-type": "application/json",
          },
          body: "{}",
        }),
      );
      expect(response.status).toBe(200);
      expect(spy.calls).toHaveLength(1);
    } finally {
      spy.restore();
    }
  });

  test("is recognised when the browser spells the host in a different case", async () => {
    const spy = spyOnApi({ status: "approved" });
    try {
      const response = await approve(
        post("/api/write", { origin: "http://LocalHost:3100" }),
      );
      expect(response.status).toBe(200);
      expect(spy.calls).toHaveLength(1);
    } finally {
      spy.restore();
    }
  });
});

/**
 * The decision `web/src/lib/request-origin.ts` argues for, pinned here so it
 * cannot be changed by accident and left described as if it had not been.
 */
test.describe("a privileged write with no Origin header", () => {
  test("is refused, and never reaches the API", async () => {
    const spy = spyOnApi({});
    try {
      for (const handler of HANDLERS) {
        const response = await handler.call(post("/api/write", {}));
        expect(
          response.status,
          `${handler.name} answered ${response.status} to a request with no Origin. ` +
            `These are browser-only routes and a browser always sends one; ` +
            `allowing the absence turns the check into a comment.`,
        ).toBe(403);
        expect(await refusalCode(response)).toBe("origin_missing");
        expect(spy.calls, `${handler.name} called the API for an origin-less request`).toHaveLength(0);
      }
    } finally {
      spy.restore();
    }
  });
});

/**
 * The origin check itself, on the inputs that are easy to get wrong.
 *
 * These are the shapes an attacker reaches for and the ones a hand-written
 * comparison gets wrong by accident. They are asserted against the policy module
 * rather than through a handler because the answer is the same for all three
 * handlers -- that is the point of having one module -- and because a refusal
 * here must not cost an API call in the test that checks it.
 */
test.describe("the origin comparison", () => {
  // The URL is a fixed placeholder: what is under test is the `Host` header,
  // which is set separately and is what the handlers compare against. (A `Host`
  // of "" would not even make a real URL, and this case is about the header
  // being absent rather than about the URL being odd.)
  const host = (origin?: string, hostHeader = OWN_HOST): Request =>
    new Request("http://dashboard.invalid/api/write", {
      method: "POST",
      headers: origin === undefined ? { host: hostHeader } : { host: hostHeader, origin },
      body: "{}",
    });

  test("refuses an origin that merely starts with this host", () => {
    // The failure a `startsWith` gets wrong, and the reason the comparison is
    // done with `URL`: `http://localhost:3100.attacker.example` is a different
    // site that happens to contain ours.
    expect(foreignOriginRefusal(host(`http://${OWN_HOST}.attacker.example`))).not.toBeNull();
    // And the one a split on "://" gets wrong: the authority after the scheme
    // belongs to the userinfo, not to the host.
    expect(foreignOriginRefusal(host(`http://${OWN_HOST}@attacker.example`))).not.toBeNull();
  });

  test("refuses the opaque origin a sandboxed frame sends", () => {
    expect(foreignOriginRefusal(host("null"))).not.toBeNull();
  });

  test("refuses an origin whose host matches but whose port does not", () => {
    // `localhost` and `localhost:3100` are different origins and a request from
    // another service on the same machine is still another origin.
    expect(foreignOriginRefusal(host("http://localhost"))).not.toBeNull();
    expect(foreignOriginRefusal(host("http://localhost:3101"))).not.toBeNull();
  });

  test("refuses an origin that is not an http(s) URL at all", () => {
    expect(foreignOriginRefusal(host("file:///etc/passwd"))).not.toBeNull();
    expect(foreignOriginRefusal(host("not a url"))).not.toBeNull();
  });

  test("refuses when the request names no host to compare against", () => {
    // Not reachable over HTTP/1.1, which requires `Host`; reachable in a unit
    // test, and a handler that fell through to "allow" here would be relying on
    // an assumption about its transport rather than checking.
    expect(foreignOriginRefusal(host(OWN_ORIGIN, ""))).not.toBeNull();
  });

  test("accepts the request's own origin, exactly", () => {
    expect(foreignOriginRefusal(host(OWN_ORIGIN))).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The enumeration: every mutating route handler, FOUND rather than LISTED.
// ---------------------------------------------------------------------------

/**
 * ## The gap this closes
 *
 * Everything above drives three handlers that this file names by hand. A fourth
 * privileged write route added without `foreignOriginRefusal` would therefore
 * pass CI green -- the import list at the top of this file is a *copy* of the
 * set of routes that need the check, and a copy does not notice when the thing
 * it copies grows.
 *
 * So the set is not listed. It is enumerated: every `route.ts` under
 * `web/src/app/api/**` is read, its exported handlers are found, and each
 * mutating one is required to call the policy. The three handlers above stay
 * hand-named, because they are *driven* -- the claim that a foreign origin
 * reaches no API call is an observation about a running handler, and it cannot
 * be made from a syntax tree. The enumeration below is a different claim: that
 * no mutating handler was added without the check. One of the two would not
 * survive on its own.
 *
 * ## Why this file and not a new one
 *
 * The source says so, twice, and it would be wrong to contradict it:
 * `app/api/locale/route.ts` says "`write-origin.spec.ts` holds that guard to a
 * test per handler", and `app/api/tickets/route.ts` says the rule is applied
 * as "one policy, one module (`@/lib/request-origin`), **one test file** --
 * because three near-identical checks are three things a later edit can make
 * disagree". A second file would be a second place to forget to update, which
 * is the very failure this block exists to prevent. It is also the same kind
 * of check: no Docker, no browser, no stack -- the module under test is read
 * and called directly, exactly as above.
 *
 * ## Why the route files are parsed rather than grepped
 *
 * A regex over the file's text would have to find "the part of this function
 * that is the function", and the answer to that is a brace count, which is
 * wrong the first time a route file contains a brace inside a string or a
 * comment. The TypeScript compiler is already a devDependency of this package,
 * so the syntax tree is free, and the tree answers the question exactly: is
 * this call *inside* this handler's body, and does it come *before* the call
 * that spends the operator token.
 */

/** Where the dashboard's Route Handlers live. */
const API_DIR = path.resolve(__dirname, "..", "src", "app", "api");

/** The module the policy lives in. One policy, one module. */
const POLICY_MODULE = "@/lib/request-origin";

/** The HTTP methods a Next.js Route Handler may export. */
const ROUTE_METHODS = new Set(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]);

/**
 * The methods that change something.
 *
 * A `GET` is not one of them, and the dashboard has one such route on purpose:
 * `GET /api/locale` sets a cookie and spends no credential -- the reasoning is
 * in that route's own docstring, and it is why the check is required of the
 * *mutating* handlers rather than of every handler in the tree. The property
 * under test is "a handler that can change the operator's data refuses a
 * foreign origin", and a cookie is not that.
 */
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Every Route Handler the dashboard ships, as a path relative to
 * `web/src/app/api`, with `/` separators.
 *
 * Listed as well as enumerated, and asserted to be exactly the set the
 * enumeration finds. That is the fail-loud half: a new route file cannot
 * appear in the dashboard without this guard noticing and a person saying
 * whether it is a privileged write. The per-handler check below would also
 * catch a new route that *lacks* the check, but it would say nothing about a
 * new route that has one -- and "is this new thing a privileged write?" is a
 * question a person should answer, not a pattern matcher.
 */
const EXPECTED_ROUTE_FILES = [
  "approvals/[approvalId]/[decision]/route.ts",
  "knowledge/reindex/route.ts",
  "locale/route.ts",
  "tickets/route.ts",
];

/** What one exported handler of a route file does, read from its syntax tree. */
type RouteHandler = {
  /** The HTTP method it answers. */
  readonly method: string;
  /** Whether its body calls `foreignOriginRefusal`. */
  readonly checksOrigin: boolean;
  /** Whether it calls the API before it calls the policy. */
  readonly spendsTokenFirst: boolean;
};

/** One route file, and what it exports. */
type RouteFile = {
  /** Its path relative to the API directory, with `/` separators. */
  readonly path: string;
  /** Its exported HTTP handlers, in the order they are declared. */
  readonly handlers: readonly RouteHandler[];
  /** Whether it imports the policy from the one module. */
  readonly importsPolicy: boolean;
};

/**
 * Every `route.ts` under `dir`, as paths relative to it.
 *
 * A hand-rolled walk rather than `readdirSync(dir, { recursive: true })`: that
 * option arrived in Node 20.1 and returns platform separators, and the
 * expected set above is written with `/` so that it reads the same on a
 * Windows developer machine and a Linux runner.
 */
function routeFilesIn(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      for (const nested of routeFilesIn(full)) {
        found.push(`${entry.name}/${nested}`);
      }
    } else if (entry.name === "route.ts") {
      found.push(entry.name);
    }
  }
  return found;
}

/**
 * Read one route file and describe its exported handlers.
 *
 * The parse is the TypeScript compiler's, so "is this call inside this
 * handler" is answered by the syntax tree rather than by counting braces.
 */
function readRouteFile(relative: string): RouteFile {
  const full = path.join(API_DIR, ...relative.split("/"));
  const source = ts.createSourceFile(
    full,
    readFileSync(full, "utf8"),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );

  const handlers: RouteHandler[] = [];
  for (const statement of source.statements) {
    if (!ts.isFunctionDeclaration(statement) || statement.name === undefined) {
      continue;
    }
    const method = statement.name.text;
    if (!ROUTE_METHODS.has(method)) {
      continue;
    }
    const exported =
      statement.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword) ??
      false;
    if (!exported) {
      continue;
    }

    // The position of the first call to the policy, and of the first call to
    // the API client. `Infinity` means "never", which is the right answer for
    // a handler that spends no token.
    let originCallAt = Number.POSITIVE_INFINITY;
    let apiCallAt = Number.POSITIVE_INFINITY;
    const visit = (node: ts.Node): void => {
      if (ts.isCallExpression(node)) {
        const callee = node.expression;
        if (ts.isIdentifier(callee) && callee.text === "foreignOriginRefusal") {
          originCallAt = Math.min(originCallAt, node.getStart(source));
        } else if (
          ts.isPropertyAccessExpression(callee) &&
          ts.isIdentifier(callee.expression) &&
          callee.expression.text === "api"
        ) {
          apiCallAt = Math.min(apiCallAt, node.getStart(source));
        }
      }
      ts.forEachChild(node, visit);
    };
    if (statement.body !== undefined) {
      ts.forEachChild(statement.body, visit);
    }

    handlers.push({
      method,
      checksOrigin: originCallAt !== Number.POSITIVE_INFINITY,
      spendsTokenFirst: apiCallAt < originCallAt,
    });
  }

  // Whether the file imports the policy from the one module, rather than
  // defining a function of the same name. A route that had its own
  // `foreignOriginRefusal` would satisfy the call check while checking
  // nothing.
  let importsPolicy = false;
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement)) {
      continue;
    }
    const specifier = statement.moduleSpecifier;
    if (!ts.isStringLiteral(specifier) || specifier.text !== POLICY_MODULE) {
      continue;
    }
    const bindings = statement.importClause?.namedBindings;
    if (bindings === undefined || !ts.isNamedImports(bindings)) {
      continue;
    }
    importsPolicy = bindings.elements.some(
      (element) => (element.propertyName ?? element.name).text === "foreignOriginRefusal",
    );
  }

  return { path: relative, handlers, importsPolicy };
}

test.describe("every mutating route handler is found, not listed", () => {
  test("the API directory holds exactly the route files this guard knows about", () => {
    const found = routeFilesIn(API_DIR).sort();
    expect(
      found.length,
      `no route file was found under ${API_DIR}. A guard that enumerates nothing ` +
        `asserts nothing, and this one is the only thing standing between a new ` +
        `privileged write route and a green CI run.`,
    ).toBeGreaterThan(0);
    expect(
      found,
      "a Route Handler was added to, or removed from, web/src/app/api. Add it to " +
        "EXPECTED_ROUTE_FILES in this file and say whether it is a privileged " +
        "write: a mutating one must call foreignOriginRefusal, and a read-only " +
        "one must say why it spends no credential.",
    ).toEqual([...EXPECTED_ROUTE_FILES].sort());
  });

  test("every mutating handler calls the origin check", () => {
    const routes = routeFilesIn(API_DIR).map(readRouteFile);
    const mutating = routes.flatMap((route) =>
      route.handlers
        .filter((handler) => MUTATING_METHODS.has(handler.method))
        .map((handler) => ({ route, handler })),
    );

    expect(
      mutating.length,
      "no mutating route handler was found. Every Route Handler in the tree " +
        "answers a GET, which cannot be the case for the three privileged " +
        "writes this dashboard ships -- so the enumeration is broken, not the " +
        "code.",
    ).toBeGreaterThan(0);

    for (const { route, handler } of mutating) {
      expect(
        handler.checksOrigin,
        `${route.path} exports a ${handler.method} that does not call ` +
          `foreignOriginRefusal. It holds the operator token and calls the API ` +
          `with it on the browser's behalf, so it is exactly the handler a ` +
          `cross-site request forgery aims at: a page on any site can POST ` +
          `here, the browser attaches no token and needs no session, and the ` +
          `handler supplies both.`,
      ).toBe(true);
    }
  });

  test("every mutating handler checks the origin before it spends the token", () => {
    const routes = routeFilesIn(API_DIR).map(readRouteFile);
    for (const route of routes) {
      for (const handler of route.handlers) {
        if (!MUTATING_METHODS.has(handler.method) || !handler.checksOrigin) {
          continue;
        }
        expect(
          handler.spendsTokenFirst,
          `${route.path} calls the API before it calls foreignOriginRefusal. A ` +
            `refusal that arrives after the call has already been made is not a ` +
            `refusal: the decision has been written by then, and the token has ` +
            `been spent on a request the operator's own browser made on a ` +
            `foreign page's behalf.`,
        ).toBe(false);
      }
    }
  });

  test("the policy is imported from the one module, not defined locally", () => {
    const routes = routeFilesIn(API_DIR).map(readRouteFile);
    for (const route of routes) {
      const checks = route.handlers.some((handler) => handler.checksOrigin);
      if (!checks) {
        continue;
      }
      expect(
        route.importsPolicy,
        `${route.path} calls foreignOriginRefusal but does not import it from ` +
          `${POLICY_MODULE}. A handler that defined its own function of that ` +
          `name would satisfy the call check while checking nothing -- and ` +
          `"one policy, one module" is the reason the three privileged writes ` +
          `cannot drift apart.`,
      ).toBe(true);
    }
  });

  test("the read-only route is exempt, and says so rather than being excused", () => {
    // The exemption is a decision, so it is asserted rather than assumed: the
    // locale route is in the tree, it exports a GET, and it exports no
    // mutating handler. `app/api/locale/route.ts` carries the reasoning -- it
    // spends no credential, changes no operator data, and its cookie is
    // `httpOnly` and `SameSite=Lax`.
    const locale = readRouteFile("locale/route.ts");
    expect(locale.handlers.map((handler) => handler.method)).toEqual(["GET"]);
    expect(
      locale.handlers.filter((handler) => MUTATING_METHODS.has(handler.method)),
      "the locale route grew a mutating handler. A handler that writes the " +
        "operator's data is a privileged write whatever it writes, and it " +
        "needs the check.",
    ).toEqual([]);
  });
});