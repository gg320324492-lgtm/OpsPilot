import Link from "next/link";

import { LocaleSwitcher } from "@/components/locale-switcher";
import { LOCALES } from "@/i18n/config";
import { getLocale, getT } from "@/i18n/server";

/**
 * The dashboard's navigation shell.
 *
 * The five top-level screens land in these routes; Run Detail is reached from a
 * run id rather than from here. Every route is reachable from any other, which
 * matters for the two things an operator has to be able to do without hunting:
 * see whether anything is parked, and see whether the knowledge index is empty.
 *
 * A Server Component, so `getT()` resolves the locale here rather than in the
 * client: the five labels are read on every page, and rendering them on the
 * server means the dashboard's chrome is correct in the very first byte of
 * HTML, with no hydration wait and no flash of the wrong language.
 *
 * The language control is last in the bar and pushed to the right. It is the one
 * item here that is not a destination, and putting it where a reader's eye
 * finishes is what keeps it from being mistaken for a sixth screen.
 */
const ROUTES = [
  { href: "/", key: "dashboard" },
  { href: "/tickets", key: "tickets" },
  { href: "/runs", key: "runs" },
  { href: "/approvals", key: "approvals" },
  { href: "/knowledge", key: "knowledge" },
] as const;

export async function Nav() {
  const [t, locale] = await Promise.all([getT(), getLocale()]);

  return (
    <div className="border-b border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950">
      <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
        <span className="text-sm font-semibold tracking-tight">OpsPilot</span>
        {/*
          The two landmarks are **siblings, not nested**. A `<nav>` inside a
          `<nav>` is legal HTML and ambiguous to a screen reader, and it made the
          main nav's link list include the two language links -- so an
          accessibility-tree reading of "where can I go" carried two answers
          that are not destinations.
        */}
        <nav aria-label={t("nav.label")}>
          <ul className="flex items-center gap-4 text-sm">
            {ROUTES.map((route) => (
              <li key={route.href}>
                <Link
                  href={route.href}
                  className="text-neutral-600 transition-colors hover:text-neutral-950 dark:text-neutral-400 dark:hover:text-neutral-50"
                >
                  {t(`nav.${route.key}`)}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <LocaleSwitcher
          locales={LOCALES}
          current={locale}
          labels={Object.fromEntries(
            LOCALES.map((option) => [option, t(`nav.locale.${option}`)]),
          )}
          label={t("nav.localeLabel")}
        />
      </div>
    </div>
  );
}