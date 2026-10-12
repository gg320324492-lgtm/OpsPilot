/**
 * The source check on the dashboard's privileged writes.
 *
 * ## The gap this closes
 *
 * Every Route Handler under `web/src/app/api/` holds the operator token
 * (`OPSPILOT_OPERATOR_TOKEN`) in the server environment and calls the API with
 * it on the browser's behalf. That is the right design and it is deliberate --
 * `lib/api/client.ts` explains why a `NEXT_PUBLIC_`-prefixed token must never
 * exist, and a refund-capable credential in a JavaScript bundle is not a
 * trade-off worth making. The token staying server-side is *also* what makes
 * these routes dangerous: the handler attaches the credential to any request
 * that reaches it, so the only question left is **who** is asking.
 *
 * Until this module existed, nothing answered that question. A request to
 * `POST /api/approvals/{id}/approve` was checked for a valid `decision` value
 * and a parseable body, and was otherwise served. That is a cross-site request
 * forgery: a page on any site can `fetch("http://<this host>:3000/api/
 * approvals/<id>/approve", {method: "POST"})`. The browser attaches no token
 * and needs no session -- the handler supplies both -- and the operator's own
 * browser does the damage; the attacker's page only has to know an approval id.
 *
 * ### The CORS preflight is not what was stopping this
 *
 * `POST /api/knowledge/reindex` takes no body at all, and `fetch(url,
 * {method: "POST"})` with no explicit content type is a CORS-safelisted
 * "simple request": it is sent, no preflight is asked for, and the answer comes
 * back opaque. That is enough to approve a refund.
 *
 * It is worth being precise about the other side of this, because it is the
 * shape of the mistake. The dashboard's own components post
 * `Content-Type: application/json`, which is NOT safelisted, so a cross-origin
 * attempt at *those* components' requests does get preflighted and fails -- an
 * accident of what the honest client happens to send, not a property of the
 * server. The bodies here are parsed with `JSON.parse` without anyone looking at
 * the content type, so dropping it to `text/plain` removes the preflight and the
 * request lands. A control that depends on the client's headers is not a
 * control; this one is asserted server-side, against the header the browser
 * controls and the attacker cannot.
 *
 * ## Why a header is the right control, and what it does not do
 *
 * `Origin` is the header for this. It is set by the *browser*, from the origin
 * of the page making the request, and JavaScript cannot forge it: the Fetch
 * standard lists `Origin` among the forbidden header names, so a `headers.set`
 * of it is ignored rather than applied, and no HTML form encoding produces one.
 * So a page on another site cannot make its request look same-origin. That is
 * the entire class this defends against, and it is the same control Django's
 * CSRF middleware applies (its `Origin`/`Referer` host against
 * `request.get_host()`).
 *
 * What it does **not** do is stop a non-browser caller. `curl` can send any
 * header it likes, including an `Origin` that matches. No header comparison
 * ever will, and no amount of cleverness at this layer changes that: the only
 * credential a browser cannot be made to present is one it does not hold, and
 * that credential is the token, which by design never reaches it. An attacker
 * with network access to the dashboard is therefore stopped by the *network*,
 * not by this function -- which is why `docker-compose.yml` binds all three
 * published ports to `127.0.0.1`, and why the two are both here: the loopback
 * binding is the control that does not depend on this check being right, and
 * this check is the one that survives a future regression of that binding.
 *
 * `Sec-Fetch-Site` is deliberately **not** read. Nothing in this codebase reads
 * it today, so there is no existing pattern to follow and no behaviour that
 * would change if it were introduced; it is set by browsers only, so requiring
 * it would refuse every non-browser caller without stopping one that a browser
 * could be made to send anyway. One well-understood control, exercised by a
 * test, beats two half-asserted ones.
 *
 * ## Absent `Origin`: refused
 *
 * A request with no `Origin` header is refused, and that is a decision rather
 * than a default. The reasoning has two halves.
 *
 * First, the absence is *informative*. Chromium was verified sending
 * `Origin: <own origin>` on a same-origin `fetch` POST -- same origin, no
 * CORS involved -- which is the behaviour every current engine shares: `Origin`
 * accompanies every non-GET request, and a same-origin POST is not an
 * exception. So a well-behaved caller of these routes always sends one, and its
 * absence means the caller is not a browser at all.
 *
 * Second, and this is the part that decides it: these are **browser-only
 * routes**. Each of the three exists for exactly one caller -- the dashboard's
 * own page, posting to its own origin -- because the browser cannot hold the
 * token. Nothing in this repository issues a server-to-server request to them:
 * Server Components call `lib/api/client.ts` directly rather than fetching
 * these URLs, so there is no internal traffic to break. Refusing an absent
 * `Origin` therefore costs nothing real, and it keeps the contract honest --
 * "this endpoint is driven by the page it is served with, and by nothing else".
 *
 * The alternative was considered and rejected. Treating an absent `Origin` as
 * "probably a script, allow it" leaves the check doing nothing for the one
 * client that can issue requests without a browser's participation, and states
 * a rule the code then does not enforce. `web/e2e/write-origin.spec.ts` pins
 * this behaviour, so the decision is a tested one rather than a comment.
 */

/** A refusal the handler returns instead of doing the work it was asked to do. */
export type OriginRefusal = {
  /** Always 403. The request is well-formed; it is simply not from here. */
  readonly status: 403;
  /** Stable branch key, in the envelope the other handlers use. */
  readonly code: string;
  /** A sentence for the operator, rendered verbatim by `decision-error.ts`. */
  readonly message: string;
};

/**
 * Refuse a request that did not come from the dashboard's own page.
 *
 * @returns `null` when the request may proceed, or the refusal to return
 *   instead. The caller must return it **before** touching the API client: the
 *   property this function exists to guarantee is that a refused request makes
 *   no privileged call, and a refusal returned after the call has already been
 *   made is not a refusal at all.
 */
export function foreignOriginRefusal(request: Request): OriginRefusal | null {
  const origin = request.headers.get("origin");
  if (origin === null || origin === "") {
    return {
      status: 403,
      code: "origin_missing",
      message:
        "This endpoint is driven by the dashboard's own page and refused the " +
        "request because it carried no Origin header. A browser always sends " +
        "one on a POST, so this request did not come from one. Call the API " +
        "directly with the operator token if you are a script; the browser " +
        "cannot hold that token, which is why this proxy exists.",
    };
  }

  // The header this is compared against is the one the client actually
  // addressed, so "the same origin" means "the same host:port this server was
  // reached at" rather than a URL hardcoded here. That is what makes the check
  // survive `localhost:3000`, `127.0.0.1:3000` and `127.0.0.1:3100` -- the
  // three addresses the e2e suite drives the dashboard on -- without an
  // allowlist that would have to be updated every time one of them changes.
  const host = request.headers.get("host");
  if (host === null || host === "") {
    return {
      status: 403,
      code: "origin_unmatchable",
      message:
        "This endpoint is driven by the dashboard's own page and refused the " +
        "request because it carried no Host header to check the Origin against.",
    };
  }

  const originHost = hostOf(origin);
  if (originHost === null) {
    return {
      status: 403,
      code: "origin_unparseable",
      message:
        "This endpoint is driven by the dashboard's own page, and it refused a " +
        `request whose Origin (${render(origin)}) is not a URL it can compare.`,
    };
  }

  if (originHost !== host.trim().toLowerCase()) {
    return {
      status: 403,
      code: "origin_foreign",
      message:
        "This endpoint is driven by the dashboard's own page, and it refused a " +
        `request from ${render(origin)}. It serves ${render(host)} and accepts ` +
        "writes only from that origin. If you are reading this in a browser, " +
        "the page you are on is not the dashboard.",
    };
  }

  return null;
}

/**
 * The `host[:port]` an `Origin` names, lowercased, or `null` if it is not one.
 *
 * `URL` is the parser rather than a split on `"://"`, because it is what
 * decides these questions correctly: an `Origin` of `null` (a sandboxed frame)
 * throws, `http://localhost:3000@evil.example` resolves to host `evil.example`
 * rather than to the authority a naive split would read, and a trailing dot or
 * an upper-case host cannot be used to slip past the comparison below. Anything
 * it cannot parse is refused rather than guessed at.
 *
 * **Only the host is compared, never the scheme, and that is deliberate.** The
 * check is "is this the same place the request was addressed to", and the place
 * is the authority. Requiring the scheme to match as well would break the
 * moment the dashboard is put behind a TLS terminator -- `https` in the Origin,
 * plain HTTP to Node -- turning a secure deployment into one where every
 * approve button stops working. That is a failure mode an operator would try to
 * "fix" by loosening the check, which is the worst outcome available here. A
 * scheme that is not `http`/`https` is still refused outright, so the only
 * difference the omission makes is `https`-on-the-front, `http`-to-Node, which
 * is the deployment shape above.
 *
 * Case is folded because host names are case-insensitive and `Host` is not
 * required to match the casing the browser put in `Origin`. The port is part of
 * the comparison and is not folded: `localhost` and `localhost:3000` are
 * different origins.
 */
function hostOf(origin: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(origin);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return null;
  }
  return parsed.host.toLowerCase();
}

/**
 * A header value quoted into an operator-facing message, bounded.
 *
 * Bounded because these values are attacker-controlled and end up in a panel on
 * a page: an origin header can be kilobytes long, and a refusal that pastes one
 * of them in whole is a denial of service with extra steps. React escapes the
 * text it renders, so the quoting is for legibility rather than safety.
 */
function render(value: string): string {
  const trimmed = value.length > 120 ? `${value.slice(0, 117)}...` : value;
  return `"${trimmed}"`;
}