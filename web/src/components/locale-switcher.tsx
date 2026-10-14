"use client";

import { usePathname } from "next/navigation";

/**
 * The language control, as two links that are also buttons.
 *
 * ## WHY PLAIN `<a>` AND NOT `next/link`
 *
 * This link points at a **Route Handler**, not at a page, and `next/link` is the
 * wrong tool for that in a way that is not obvious:
 *
 * - **`next/link` prefetches.** The viewport-visible link is fetched on
 *   sight, and that fetch *executes `/api/locale`* -- setting the cookie before
 *   the operator has clicked anything, and racing the click's own request. Both
 *   links sit in the viewport, so switching to Chinese and then English could
 *   leave the dashboard in whichever locale's prefetch landed last.
 * - **`next/link` intercepts the navigation.** A client-side transition asks the
 *   App Router to resolve `/api/locale` as a route and stream the RSC payload
 *   from wherever its redirect lands. The cookie still gets set, but through a
 *   fetch the router owns rather than through a document navigation the browser
 *   owns, and the result is a page whose rendered language depends on which of
 *   several in-flight requests resolved first.
 *
 * A plain anchor is one request, one `Set-Cookie`, one document load, in that
 * order. For a control that changes server-rendered copy everywhere at once,
 * that is also what you *want*: a full navigation is the only thing guaranteed
 * to re-render every Server Component in the tree in the new locale, rather
 * than a client transition that leaves a few cached RSC payloads in the old one.
 *
 * ## WHY A CLIENT COMPONENT AT ALL
 *
 * Only to know where the operator is. `returnTo` is what makes switching
 * language land back on the screen they were reading rather than dumping them
 * on the dashboard, and a Server Component in the root layout has no way to read
 * the pathname -- so `usePathname` is the whole of the cost, and it is paid
 * only when this control is on screen. `usePathname` is resolved on the server
 * too, so the control is in the very first byte of HTML.
 *
 * The links are `href`s, not a `<select onChange>`, for the same reason the
 * Runs and Approvals filters are links rather than a select (`runs/page.tsx`
 * makes the argument in full): the state has to be a URL. A pasted link has to
 * open the dashboard in the language the sender was reading, and that is only
 * true if the locale is a cookie the server reads rather than a client-side
 * toggle nobody can share.
 *
 * `returnTo` is `encodeURIComponent`-escaped because it is a *path*, which
 * contains `?` and `&` in a filtered Runs URL. `/api/locale`'s docstring is the
 * argument that the value is re-validated there regardless.
 */
export function LocaleSwitcher({
  locales,
  current,
  labels,
  label,
}: {
  /** The shipped locale tags, in switcher order. */
  locales: readonly string[];
  /** The locale this request rendered in. */
  current: string;
  /** Each locale's own name, in its own script. */
  labels: Record<string, string>;
  /** The switch group's accessible name. */
  label: string;
}) {
  const pathname = usePathname();

  return (
    <nav aria-label={label} className="ml-auto flex items-center gap-1 text-xs">
      {locales.map((locale) => {
        const active = locale === current;
        return (
          <a
            key={locale}
            href={`/api/locale?locale=${encodeURIComponent(locale)}&returnTo=${encodeURIComponent(pathname ?? "/")}`}
            aria-current={active ? "true" : undefined}
            className={
              active
                ? "rounded-full bg-neutral-900 px-2.5 py-1 font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
                : "rounded-full px-2.5 py-1 text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
            }
          >
            {labels[locale] ?? locale}
          </a>
        );
      })}
    </nav>
  );
}