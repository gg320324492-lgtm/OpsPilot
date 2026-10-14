/**
 * Reading the locale on the server, and the catalogue registry.
 *
 * ## WHY NO `proxy.ts`, NO `[locale]` SEGMENT, AND NO FRAMEWORK
 *
 * `web/AGENTS.md` and Next 16.3.8's own
 * `01-app/02-guides/internationalization.md` describe two ways to internationalise
 * an App Router app: the guide's own minimal pattern (a `[lang]` dynamic segment
 * with everything nested under it, plus a `proxy.ts` that redirects
 * `/products` to `/en-US/products`), or a library — `next-intl`,
 * `next-international`, `lingui`, `tolgee` and others, all listed in that
 * guide's Resources section.
 *
 * Both were weighed against this repository and rejected, for reasons that are
 * about *this* app rather than about the pattern in general:
 *
 * - **A locale prefix changes every URL.** `/` becoming `/en` changes the five
 *   nav links, every `page.goto("/approvals")` in four specs, the `href`s the
 *   desktop shell hands the browser, and every URL an operator has bookmarked or
 *   pasted into a colleague's chat. The brief says "do not change behaviour",
 *   and the Runs screen's own docstring says the URL "has to mean the same
 *   thing" to someone else.
 * - **Mandatory negotiation is exactly what DoD D8 forbids.** A `[lang]`
 *   segment's `getDictionary` calls `notFound()` when the catalogue is missing,
 *   and `next-intl`'s `getRequestConfig` throws when a locale file is absent.
 *   Here, "no cookie" resolves to English and both catalogues ship in the
 *   repository, so a fresh clone renders English with nothing configured. That
 *   is a property of the code, not of a config file someone has to remember.
 * - **It is a Server Components app with four small Client Components.** The
 *   Next guide notes that server-only dictionaries do not weigh on the client
 *   bundle. Everything in `src/app/**` except those four is a Server Component,
 *   so reading the locale from a cookie and passing the resolved catalogue down
 *   gets the same result without a provider, a plugin in `next.config.ts`, a
 *   dependency, or a bundler plugin.
 * - **A dependency is a fresh-clone risk.** `next-intl` 4.x is installable here
 *   (the registry is reachable), but a build that needs a plugin is a build that
 *   can fail on a network-restricted clone for a reason unrelated to this
 *   application.
 *
 * The cost of the choice is that there is no framework to lean on: the locale
 * switcher, the catalogue registry, the plural rule and the "a missing key
 * throws" behaviour are all here, in about 200 lines across four files. That is
 * a real cost and it is the right trade for an app whose DoD is "a fresh clone
 * runs".
 *
 * ## WHAT THIS MODULE DOES NOT DO
 *
 * It does not touch what the API speaks and it does not touch the audit trail.
 * Switching the locale changes which catalogue a Server Component reads while
 * rendering, and nothing else: no query string, no header, no request body, no
 * `Accept-Language`. A run approved under `zh-CN` is the same row, and
 * `decision-error.ts` still switches on the API's own `code`.
 */

import { cookies } from "next/headers";

import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  isLocale,
  type Locale,
} from "@/i18n/config";
import {
  createT,
  type MessageKey,
  type Messages,
  type TFunction,
} from "@/i18n/format";

import en from "./dictionaries/en.json";
import zhCN from "./dictionaries/zh-CN.json";

/**
 * Every catalogue, keyed by locale.
 *
 * The registry is closed and exhaustive by hand rather than by directory scan:
 * a directory scan would make an accidentally-committed file a live locale, and
 * would make *removing* a locale depend on the filesystem. One line here is the
 * whole of "add a language".
 *
 * The Chinese file is typed as the English catalogue's shape so a missing key
 * is a `tsc --noEmit` failure in `npm run typecheck` rather than a blank string
 * in production. Plural entries are the exception that proves the rule -- see
 * `format.ts`'s `Messages` for why Chinese carries `other` alone.
 */
export const DICTIONARIES = {
  en,
  "zh-CN": zhCN,
} as const satisfies Record<Locale, Messages>;

/**
 * The locale this request renders in.
 *
 * **English unless the operator has said otherwise.** No cookie, an unreadable
 * cookie, or a cookie naming a locale this build does not ship all resolve to
 * `DEFAULT_LOCALE` -- silently and without a redirect, because a fresh clone
 * must render English with no configuration (D8) and a stale cookie must not
 * turn a dashboard into a 404 after a locale is withdrawn.
 *
 * This makes every route dynamic, which is not a change of rendering mode here:
 * every page in `src/app/**` already reads the API with `cache: "no-store"`
 * (`src/lib/api/client.ts`), so each is already rendered per request. What the
 * cookie adds is the *one* value the request-time store is read for.
 */
export async function getLocale(): Promise<Locale> {
  const store = await cookies();
  const chosen = store.get(LOCALE_COOKIE)?.value;
  return isLocale(chosen) ? chosen : DEFAULT_LOCALE;
}

/** The catalogue's `t` for this request. Call this where the copy is used. */
export async function getT(): Promise<TFunction> {
  const locale = await getLocale();
  return createT(DICTIONARIES[locale], locale);
}

/**
 * The locale, the `t`, and the catalogue sections a Client Component needs.
 *
 * One read of the cookie serves all three, so a page that renders both copy and
 * a `"use client"` control does not read the request twice or -- worse -- read
 * it twice and get two different answers.
 *
 * `sections` is the Client Component's prop, typed against the English
 * catalogue so a renamed section is a compile error at the page that passes it
 * rather than a blank button in production. Each page passes the *minimum* it
 * needs: `reindex-button.tsx` asks for `common` and `knowledge`, not the whole
 * dictionary, so the browser bundle carries the strings that component renders
 * and not the two hundred it does not.
 */
export async function getCopy<S extends readonly (keyof typeof en)[]>(
  ...sections: S
): Promise<{
  locale: Locale;
  t: TFunction;
  copy: Pick<typeof en, S[number]>;
}> {
  const locale = await getLocale();
  const dictionary = DICTIONARIES[locale];
  const t = createT(dictionary, locale);
  const picked: Record<string, unknown> = {};
  for (const key of sections) {
    picked[key] = dictionary[key];
  }
  return { locale, t, copy: picked as Pick<typeof en, S[number]> };
}

export type { Locale, MessageKey, Messages, TFunction };
export { createScopedT } from "@/i18n/format";
export { DEFAULT_LOCALE, LOCALE_COOKIE } from "@/i18n/config";