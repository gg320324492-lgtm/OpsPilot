import type { ReactNode } from "react";
import { ApiStatus } from "@/components/api-status";
import { Nav } from "@/components/nav";
import { getLocale, getT } from "@/i18n/server";
import "./globals.css";

/**
 * No `next/font/google` here on purpose.
 *
 * The create-next-app default fetches Geist from fonts.googleapis.com at build
 * time, which makes the build depend on reaching Google -- a dependency that
 * fails behind a firewall or on an offline machine, and fails with a font error
 * rather than anything that says "you are offline". The system font stack has
 * no such dependency, and for an internal operator dashboard the difference is
 * not worth a build that only works on a machine with internet access.
 *
 * Typography is set in `globals.css`, where the stack can be overridden there
 * by a project that wants to self-host a font.
 */

/**
 * The root layout's props are written explicitly rather than through Next's
 * generated `LayoutProps<"/">` global on purpose. That global type is emitted
 * into `.next/types` by `next dev`/`next build`, so it exists only after a
 * build has run: CI's `web-typecheck` job runs `tsc --noEmit` on a fresh
 * checkout with no `.next`, where the global is undefined and the root layout
 * fails with `TS2304: Cannot find name 'LayoutProps'`. Typing the one prop the
 * root layout actually takes keeps the check independent of whether a build has
 * happened on this machine, matching the rest of the app (every `page.tsx`
 * spells out its own props). `params` is deliberately omitted: the root layout
 * has no route segment to receive, and the build accepts the signature without
 * it.
 *
 * ## WHY THE METADATA IS A FUNCTION NOW
 *
 * It was a `const`. A localised `<meta name="description">` has to be able to
 * read the locale, and `generateMetadata` is the App Router's supported way to
 * do that -- the static export cannot. The title stays `OpsPilot` in every
 * locale, which is a product name and not copy; the description is prose, and
 * prose is what a locale is for.
 *
 * ## WHY `<html lang>` IS THE LOCALE AND NOT `"en"`
 *
 * `lang` is how a screen reader picks a voice, how a browser picks a font
 * fallback, and how `Intl`-based formatting in the browser behaves. Hard-coding
 * `"en"` on a Chinese page is the one thing here that would be actively wrong
 * rather than merely incomplete, so it reads the same cookie the copy does. The
 * root layout is where it is set because it is the only element every page shares.
 */
export async function generateMetadata() {
  const t = await getT();
  return {
    title: "OpsPilot",
    description: t("layout.description"),
  };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  return (
    <html lang={locale} className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <Nav />
        {/* Rendered above every screen so a misconfigured token is stated in
            words wherever the operator happens to be, rather than only on the
            page that first happens to fetch something. */}
        <ApiStatus />
        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}