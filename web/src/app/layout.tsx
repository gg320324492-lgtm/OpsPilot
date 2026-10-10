import type { Metadata } from "next";
import type { ReactNode } from "react";
import { ApiStatus } from "@/components/api-status";
import { Nav } from "@/components/nav";
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

export const metadata: Metadata = {
  title: "OpsPilot",
  description: "A reliable AI operations agent for B2B support and billing.",
};

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
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
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