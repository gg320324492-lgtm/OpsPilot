import type { Metadata } from "next";
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

export default function RootLayout({ children }: LayoutProps<"/">) {
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