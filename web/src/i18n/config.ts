/**
 * The locales this dashboard ships, and how one is chosen.
 *
 * ## WHY THERE IS NO `Accept-Language` NEGOTIATION HERE
 *
 * `web/node_modules/next/dist/docs/01-app/02-guides/internationalization.md`
 * (Next 16.3.8) opens with "It's recommended to use the user's language
 * preferences in the browser to select which locale to use." That recommendation
 * is written for a public website. This is an operator dashboard that decides
 * whether money moves, and on a machine where it is bound to loopback
 * (`docker-compose.yml`, P2-A1). Two things follow, and both are reasons to
 * deviate deliberately rather than by default:
 *
 * 1. **A browser's language is not an operator's decision.** `Accept-Language`
 *    is a property of the browser profile, set long ago, on a machine someone
 *    else configured. The locale that governs "Approve"/"Reject" on a refund
 *    should be a choice the operator made on this screen and can see and change.
 * 2. **Silence is a bug here.** An operator who opens the dashboard after a
 *    browser update and finds it in a language they did not choose is reading a
 *    screen about parked refunds in a language that did not come from them.
 *
 * So the locale is an explicit, sticky, operator-set cookie. `defaultLocale`
 * is what a fresh clone renders with -- which is what DoD D8 requires, and it is
 * true here by construction rather than by configuration: see `server.ts`.
 *
 * ## WHAT A NEW LOCALE COSTS
 *
 * Add the JSON file, add one line to `LOCALES`, add one line to `DICTIONARIES`
 * in `server.ts`, and the locale appears in the switcher. `e2e/i18n.spec.ts`
 * runs whatever `LOCALES` lists, and `e2e/i18n-catalogue.spec.ts` fails on any
 * key the new file is missing, so an incomplete catalogue cannot ship silently.
 * Which languages to add is a product decision; nothing here presumes one.
 */

/** The locale a fresh clone renders in. English is also the source catalogue. */
export const DEFAULT_LOCALE = "en";

/**
 * The cookie the operator's choice is stored in.
 *
 * `httpOnly` because nothing in the browser ever reads it: the locale is
 * resolved on the server and shipped down as rendered copy. That keeps a stray
 * `document.cookie` read from being able to *influence* what a Server Component
 * renders, which is the one place in this app where an attacker-influenced
 * string would be rendered next to an Approve button.
 */
export const LOCALE_COOKIE = "opspilot_locale";

/**
 * Every locale the dashboard ships, in switcher order (source locale first).
 *
 * A closed tuple, not `string`, so `hasLocale` is a type guard and a typo in a
 * URL is a `notFound`-free fallback to English rather than an untyped catalogue
 * lookup that returns `undefined`.
 */
export const LOCALES = ["en", "zh-CN"] as const;

export type Locale = (typeof LOCALES)[number];

/** Narrow anything to a shipped locale, or answer "not one". */
export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/**
 * The cookie's lifetime, in seconds. A year.
 *
 * Long because re-choosing the locale is not something an operator does often,
 * and short enough that a cleared browser profile is the documented way back to
 * English. The cookie is not a security control, so there is nothing to gain
 * from expiring it sooner.
 */
export const LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

/**
 * The cookie's `SameSite` attribute.
 *
 * `Lax` is the setting that makes the locale route safe to leave without the
 * origin check the three privileged write routes carry. `Lax` cookies are not
 * sent on cross-site subresource requests, so a foreign page cannot make the
 * dashboard act on one; and the worst a foreign page can do is set the cookie,
 * which changes the reading language and nothing else. See the route's own
 * docstring for the full argument.
 */
export const LOCALE_COOKIE_SAME_SITE = "lax" as const;