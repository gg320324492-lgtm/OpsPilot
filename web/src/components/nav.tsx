import { LocaleSwitcher } from "@/components/locale-switcher";
import { NavLinks } from "@/components/nav-links";
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
 * The link list itself is a Client Component (`nav-links.tsx`): current-page
 * highlight needs the live pathname, which only exists on the client. The
 * labels are resolved here and passed down, so the locale decision stays in
 * one place.
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
    <div data-titlebar className="sticky top-0 z-40 border-b border-separator bg-surface">
      <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
        <span className="text-sm font-semibold tracking-tight text-foreground">OpsPilot</span>
        {/*
          The language control uses role="group" rather than a second `<nav>`:
          two navigation landmarks made getByRole('navigation') ambiguous (the
          F4-D audit's P0-4 strict-mode failure), so the main nav is the only
          navigation landmark on the page.
        */}
        <nav aria-label={t("nav.label")}>
          <NavLinks labels={Object.fromEntries(ROUTES.map((route) => [route.key, t(`nav.${route.key}`)]))} />
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
