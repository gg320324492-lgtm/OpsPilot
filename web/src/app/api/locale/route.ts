import { NextResponse } from "next/server";

import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  LOCALE_COOKIE_MAX_AGE,
  LOCALE_COOKIE_SAME_SITE,
  isLocale,
} from "@/i18n/config";

/**
 * Set the operator's locale, and send them back where they were.
 *
 * ## WHY A ROUTE HANDLER AND NOT A SERVER ACTION
 *
 * The switcher has to work as a plain link. A Server Action is a POST that React
 * submits through its own runtime, which means a dashboard that has not finished
 * hydrating cannot use it and a reader with JavaScript disabled cannot either.
 * `GET /api/locale?locale=zh-CN&returnTo=/runs/abc` is a link: it works before
 * hydration, it works with the runtime off, it works if a browser prefetches it,
 * and `curl` can drive it. For a control that changes one cookie and changes
 * nothing else, that is the right amount of machinery.
 *
 * ## WHY THIS ROUTE DOES NOT CARRY AN ORIGIN CHECK
 *
 * `@/lib/request-origin` guards the three privileged write routes, and
 * `write-origin.spec.ts` holds that guard to a test per handler. This route is
 * deliberately not one of them, and the reasoning is worth stating rather than
 * leaving to a reader who has just been told that privileged routes check the
 * origin:
 *
 * - **It spends no credential.** `OPSPILOT_OPERATOR_TOKEN` is not read, not
 *   attached, and not forwarded. A refusal that costs nothing to bypass costs
 *   nothing, because there is nothing to protect on the other side of it.
 * - **It changes no operator data.** No approval, no run, no audit row and no
 *   request the API sees is affected. The only thing it writes is a
 *   presentation cookie for this browser.
 * - **The cookie is `httpOnly` and `SameSite=Lax`.** `httpOnly` means no script
 *   in this origin can read it, so nothing a hostile page could inject can learn
 *   or forge the operator's current language. `Lax` means the cookie is not
 *   *sent* on a cross-site request, so a foreign page cannot cause the dashboard
 *   to act on one.
 * - **The worst case is a changed reading language.** The realistic attack is an
 *   `<img src="http://localhost:3000/api/locale?locale=zh-CN">` on a page
 *   somewhere, which flips the dashboard's language. An operator who notices
 *   clicks "English". Nothing was approved, nothing was spent, nothing was
 *   written, and the state is one click from where it started.
 *
 * Adding the origin check here would mean the switcher stopped working for
 * exactly the browsers that need it most (no `Origin` header on a top-level GET
 * navigation), which is the trade this comment is here to make explicit.
 *
 * ## `returnTo` IS NOT A REDIRECT TARGET FROM THE INTERNET
 *
 * It is a path this handler normalises to a same-origin, non-absolute value
 * before using it, so `?returnTo=https://attacker.example` redirects nowhere
 * but here. Anything that is not a single-slash path is discarded and the
 * operator lands on the dashboard root instead.
 */
export function GET(request: Request): NextResponse {
  const url = new URL(request.url);

  // An unknown or absent locale falls back to the default rather than 404 or a
  // 400. "Pick a language I do not ship" is not an error worth showing an
  // operator over a control they clicked by accident; it is the English
  // dashboard.
  const chosen = url.searchParams.get("locale");
  const locale = isLocale(chosen) ? chosen : DEFAULT_LOCALE;

  const response = NextResponse.redirect(
    sameOriginUrl(request, url.searchParams.get("returnTo")),
    303,
  );

  response.cookies.set({
    name: LOCALE_COOKIE,
    value: locale,
    path: "/",
    maxAge: LOCALE_COOKIE_MAX_AGE,
    httpOnly: true,
    sameSite: LOCALE_COOKIE_SAME_SITE,
  });
  return response;
}

/**
 * The redirect target, on the origin the **client** is actually using.
 *
 * ## WHY NOT `url.origin`, AND WHAT THAT COST
 *
 * `new URL(request.url).origin` is the *bind* address, not the address the
 * operator typed. Node's `server.js` binds `0.0.0.0`, so `request.url` is
 * `http://0.0.0.0:3000/...` however the dashboard was reached, and a redirect
 * built from it sends the browser to `http://0.0.0.0:3000/`. That is a
 * **different origin** from `http://localhost:3000`, so the browser treats the
 * hop as cross-origin, drops the cookie that was set on the response, and
 * lands the operator on an empty page having changed nothing.
 *
 * It looked fine in development on port 3000 and would have looked fine in the
 * e2e suite too, had the suite not asserted the locale actually changed. So the
 * origin here comes from the `Host` header -- the same source
 * `@/lib/request-origin` compares an `Origin` against, and the only header that
 * says which name the operator used. `X-Forwarded-Proto` is honoured for the
 * scheme for the same reason: behind a TLS terminator the client is on `https`
 * and a relative redirect would otherwise bounce it to `http`.
 *
 * ## WHY `returnTo` STILL CANNOT LEAVE
 *
 * The raw value must be a single-slash path. `//attacker.example` is rejected
 * before the `new URL` ever sees it, so no scheme, host or authority can be
 * smuggled in, and the resolved URL is compared against this origin as a second
 * check. `303` rather than `302` because the browser should re-issue as GET
 * whatever it cached; this route has no non-GET to protect, but a temporary
 * redirect that survives a re-POST is a redirect that can.
 */
function sameOriginUrl(request: Request, returnTo: string | null): URL {
  const host = request.headers.get("host") ?? "localhost";
  const proto = request.headers.get("x-forwarded-proto") ?? "http";
  const base = new URL(`${proto.split(",")[0]?.trim() ?? proto}://${host}/`);
  if (returnTo === null || !returnTo.startsWith("/") || returnTo.startsWith("//")) {
    return base;
  }
  try {
    const resolved = new URL(returnTo, base);
    return resolved.origin === base.origin ? resolved : base;
  } catch {
    return base;
  }
}